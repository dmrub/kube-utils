#!/usr/bin/env python3

# https://github.com/NVIDIA/nvidia-docker/issues/179
# https://github.com/microsoft/pai/issues/2001
# https://stackoverflow.com/questions/8223811/a-top-like-utility-for-monitoring-cuda-activity-on-a-gpu
# https://stackoverflow.com/questions/39931316/what-is-the-pid-in-the-host-of-a-process-running-inside-a-docker-container
# https://github.com/kubernetes-client/python/blob/master/examples/pod_exec.py

# In the container run sh -c 'echo process_ruxu01_pytroch && nvidia-smi --query-compute-apps=gpu_name,gpu_bus_id,pid,process_name,used_memory --format=csv,noheader && read val'
# It will wait so the process keep running
# At the host you can now compare all processes to check which
# procinfo() { echo $1;  readlink /proc/$1/ns/pid; tr \\0 " " < "/proc/$1/cmdline"; echo; };

import argparse
import csv
import logging
import os
import random
import shlex
import string
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from io import StringIO
from typing import Optional, Sequence, List

import yaml
from kubernetes import config
from kubernetes.client.api import core_v1_api
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from openshift.dynamic import DynamicClient

LOG = logging.getLogger(__name__)

GPU_CHECK_PROC_PREFIX = 'X-GPUPROC'


@dataclass
class ContainerProcess:
    pid: int
    cmdline: str
    host_pid: Optional[int] = None
    gpu_name: Optional[str] = None
    gpu_used_memory: Optional[str] = None
    gpu_pci_address: Optional[str] = None


@dataclass
class GpuContainer:
    pod_name: str
    pod_namespace: str
    container_name: str
    node_name: str
    gpu_usage_list: list
    processes: List[ContainerProcess] = field(default_factory=list)
    host_pid_ns: Optional[str] = None


def randstr(size=10, chars='_' + string.ascii_uppercase + string.ascii_lowercase + string.digits):
    return ''.join(random.SystemRandom().choice(chars) for _ in range(size))


# https://stackoverflow.com/questions/9535954/printing-lists-as-tabular-data
def print_table(table: Sequence):
    longest_cols = [(max([len(str(row[i])) for row in table]) + 1) for i in range(len(table[0]))]
    row_format = "".join(["{:<" + str(longest_col) + "}" for longest_col in longest_cols])
    for row in table:
        print(row_format.format(*row))


def k8s_pod_ready(pod):
    return (pod.status and pod.status.containerStatuses is not None and
            all([container.ready for container in pod.status.containerStatuses]))


def k8s_begin_exec(api_instance, pod_name, pod_namespace, command, container=None,
                   stdout=True, stderr=True, stdin=False, tty=False):
    resp = stream(
        api_instance.connect_get_namespaced_pod_exec,
        pod_name,
        pod_namespace,
        container=container,
        command=shlex.split(command),
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        tty=tty,
        _preload_content=False)
    return resp


def k8s_end_exec(resp):
    stdout, stderr, rc = [], [], 0
    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            stdout.append(resp.read_stdout())
        if resp.peek_stderr():
            stderr.append(resp.read_stderr())
    err = resp.read_channel(3)
    err = yaml.safe_load(err)
    if err['status'] == 'Success':
        rc = 0
    else:
        rc = int(err['details']['causes'][0]['message'])
    stdout = "".join(stdout)
    stderr = "".join(stderr)
    return stdout, stderr, rc


def k8s_exec(api, pod_name, pod_namespace, command, container=None):
    resp = k8s_begin_exec(api, pod_name, pod_namespace, command, container,
                          stdout=True, stderr=True, stdin=False, tty=False)
    return k8s_end_exec(resp)


def main():
    parser = argparse.ArgumentParser(
        description="Get pod processes that use NVIDIA GPU", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-l",
        "--log",
        dest="loglevel",
        default="WARNING",
        help="log level (use one of CRITICAL,ERROR,WARNING,INFO,DEBUG)",
    )
    parser.add_argument("--kubeconfig", default=None, help="Path to the kubeconfig file to use for CLI requests.")
    parser.add_argument("--context", default=None, help="The name of the kubeconfig context to use")

    args = parser.parse_args()

    numeric_level = getattr(logging, args.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        print(
            "Invalid log level: {}, use one of CRITICAL, ERROR, WARNING, INFO, DEBUG".format(args.loglevel),
            file=sys.stderr,
        )
        return 1

    debug_mode = numeric_level == logging.DEBUG

    if debug_mode:
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(pathname)s:%(lineno)s: %(message)s",
            level=numeric_level,
        )
    else:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=numeric_level)

    kubeconfig = args.kubeconfig or os.getenv('KUBECONFIG')
    context = args.context

    k8s_client = config.new_client_from_config(config_file=kubeconfig, context=context)
    dyn_client = DynamicClient(k8s_client)
    api = core_v1_api.CoreV1Api(k8s_client)

    # v1_nodes = dyn_client.resources.get(api_version='v1', kind='Node')
    #
    # node_list = v1_nodes.get()
    #
    # for node in node_list.items:
    #    print(node.metadata.name)

    pods = dyn_client.resources.get(api_version='v1', kind='Pod')

    pod_list = pods.get(field_selector='status.phase=Running')

    gpu_containers: List[GpuContainer] = []
    gpu_node_to_containers_map = defaultdict(list)
    gpu_containers_map = {}

    exec_processes = {}

    print('Search for pods that use GPU...')

    try:
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            pod_namespace = pod.metadata.namespace
            pod_node_name = pod.spec.nodeName

            if not k8s_pod_ready(pod):
                LOG.info('Pod %s in namespace %s is not ready', pod_name, pod_namespace)
                continue

            for container in pod.spec.containers:
                LOG.info('Checking pod %s with container %s in namespace %s on node %s for GPU usage',
                         pod_name, container.name, pod_namespace, pod_node_name)

                command = 'nvidia-smi --query-compute-apps=gpu_name,gpu_bus_id,pid,process_name,used_memory ' \
                          '--format=csv,noheader'

                try:
                    stdout, stderr, rc = k8s_exec(api, pod_name, pod_namespace, command, container.name)
                    # print('stdout = {}, stderr = {}, rc = {}'.format(stdout, stderr, rc))
                    if rc == 0:
                        print(
                            'Pod {} in namespace {} on node {} uses GPU'.format(pod_name, pod_namespace, pod_node_name))
                        f = StringIO(stdout)
                        reader = csv.reader(f, delimiter=',')
                        rows = []
                        for row in reader:
                            rows.append([s.strip() for s in row])

                        command = 'sh -c \'for p in /proc/*; do if [ -e "$p/cmdline" ]; then ' \
                                  'printf "%s\\t%s\\n" "$p" "$(tr \\\\0 " " < "$p/cmdline")"; fi; done\' '

                        stdout, stderr, rc = k8s_exec(api, pod_name, pod_namespace, command, container.name)
                        # print('processes: {} {} {}'.format(stdout, stderr, rc))
                        if rc == 0:
                            container_processes = []
                            for line in stdout.splitlines(keepends=False):
                                proc_path, cmdline = line.split('\t', maxsplit=1)
                                if not proc_path.startswith('/proc/'):
                                    LOG.error('Unexpected command result, string should start with "/proc/": %s',
                                              proc_path)
                                    continue
                                try:
                                    pid = int(proc_path[6:])
                                except ValueError:
                                    # pid is not a number, just ignore
                                    continue
                                container_process = ContainerProcess(pid=pid, cmdline=cmdline)
                                container_processes.append(container_process)

                            gpu_container = GpuContainer(pod_name=pod_name,
                                                         pod_namespace=pod_namespace,
                                                         node_name=pod_node_name,
                                                         container_name=container.name,
                                                         gpu_usage_list=rows,
                                                         processes=container_processes)
                            gpu_containers.append(gpu_container)
                            gpu_node_to_containers_map[pod_node_name].append(gpu_container)

                            key = '{}|{}|{}|{}|{}|{}'.format(GPU_CHECK_PROC_PREFIX, randstr(),
                                                             pod_name, pod_namespace, pod_node_name,
                                                             container.name)
                            command = 'sh -c "echo \'{}\' && read val"'.format(key)
                            gpu_containers_map[key] = gpu_container

                            resp = k8s_begin_exec(api, pod_name, pod_namespace, command,
                                                  container=container.name,
                                                  stdout=True, stderr=True, stdin=True, tty=False)
                            exec_processes[key] = resp
                        else:
                            LOG.error('Could not get process informations from pod %s container %s, namespace %s',
                                      pod_name, container.name, pod_namespace)
                    else:
                        LOG.debug('Could not run nvidia-smi in the pod %s container %s, namespace %s',
                                  pod_name, container.name, pod_namespace)
                except Exception:
                    LOG.exception('Could not exec nvidia-smi in the pod %s container %s, namespace %s',
                                  pod_name, container.name, pod_namespace)

        for gpu_node in gpu_node_to_containers_map.keys():
            print('Checking GPU node {} ...'.format(gpu_node))
            node_pod_name = 'x-gpuproc-{}-{}'.format(
                randstr(chars=string.ascii_lowercase + string.digits), gpu_node)
            node_pod_namespace = 'default'
            node_pod_image = "docker.io/library/alpine"  # "busybox"
            node_pod_container_name = 'gpucheck'
            node_pod_manifest = {
                'apiVersion': 'v1',
                'kind': 'Pod',
                'metadata': {
                    'name': node_pod_name
                },
                'spec': {
                    'nodeName': gpu_node,
                    'hostPID': True,
                    # 'hostNetwork': True,
                    'containers': [
                        {
                            'name': node_pod_container_name,
                            # "securityContext": {
                            #    "privileged": True
                            # },
                            'image': node_pod_image,
                            "command": ["/bin/sh"],
                            "args": [
                                "-c",
                                "trap exit INT TERM; while true; do sleep 5; done"
                            ]
                        }
                    ]
                }
            }
            LOG.info('Create GPU checking pod %s on node %s', node_pod_name, gpu_node)
            try:
                resp = api.delete_namespaced_pod(node_pod_name, node_pod_namespace)
            except ApiException as e:
                if e.status != 404:
                    LOG.exception('Unexpected error')
                    sys.exit(1)

            while True:
                try:
                    resp = api.create_namespaced_pod(body=node_pod_manifest,
                                                     namespace=node_pod_namespace)
                    break
                except ApiException as e:
                    if e.status != 409:  # Conflict
                        LOG.exception('Unexpected error')
                        sys.exit(1)
                    time.sleep(1)

            while True:
                resp = api.read_namespaced_pod(name=node_pod_name,
                                               namespace=node_pod_namespace)
                if resp.status.phase != 'Pending':
                    break
                time.sleep(1)

            LOG.info('Checking GPU node %s with pod %s in namespace %s for GPU usage',
                     gpu_node, node_pod_name, node_pod_namespace)

            command = 'sh -c \'for p in /proc/*; do if [ -e "$p/cmdline" ]; then printf "%s\\t%s\\t%s\\t%s\\n" "$p" "$(' \
                      'readlink "$p/ns/pid")" "$(grep NSpid: "$p/status" | tr "\\t" " ")" "$(tr \\\\0 " " < ' \
                      '"$p/cmdline")"; fi; done\' '

            stdout, stderr, rc = k8s_exec(api, node_pod_name, node_pod_namespace, command, node_pod_container_name)
            # print('processes: {} {} {}'.format(stdout, stderr, rc))
            if rc == 0:
                host_process_information = []
                pid_ns_to_container_map = {}
                for line in stdout.splitlines(keepends=False):
                    proc_path, pid_ns, nspid, cmdline = line.split('\t')
                    if not proc_path.startswith('/proc/'):
                        LOG.error('Unexpected command result, string should start with "/proc/": %s', proc_path)
                        continue
                    try:
                        pid = int(proc_path[6:])
                    except ValueError:
                        # pid is not a number, just ignore
                        continue
                    # Parse nspid
                    nspid = nspid.strip()
                    if not nspid.startswith('NSpid:'):
                        LOG.error('Unexpected command result, string should start with "NSpid:": %s', nspid)
                        continue
                    nspid_list = nspid.split()[1:]
                    try:
                        nspid_list = [int(i) for i in nspid_list]
                    except ValueError:
                        LOG.exception('Unexpected command result, NSpid: entry should contain only integers: %s', nspid)
                        continue
                    if pid != nspid_list[0]:
                        LOG.error('Unexpected command result, first NSpid id should be equal to PID: %d != %d', pid,
                                  nspid_list[0])
                        continue
                    if len(nspid_list) > 1:
                        pid_in_container = nspid_list[1]
                    else:
                        pid_in_container = None
                    if GPU_CHECK_PROC_PREFIX in cmdline:
                        container_key = None
                        for param in shlex.split(cmdline):
                            if param.startswith(GPU_CHECK_PROC_PREFIX):
                                container_key = param
                                break
                        if container_key is None:
                            raise Exception(
                                'Internal error, could not parse parameter with prefix {} from command line {}'
                                    .format(GPU_CHECK_PROC_PREFIX, cmdline))
                        gpu_container = gpu_containers_map.get(container_key)
                        gpu_container.host_pid_ns = pid_ns
                        pid_ns_to_container_map[pid_ns] = gpu_container
                    host_process_information.append((pid, pid_ns, pid_in_container, cmdline))
                # Post-process process information
                for pinfo in host_process_information:
                    pid, pid_ns, pid_in_container, cmdline = pinfo
                    gpu_container = pid_ns_to_container_map.get(pid_ns)
                    if gpu_container is not None:
                        for container_process in gpu_container.processes:
                            if container_process.pid == pid_in_container:
                                container_process.host_pid = pid
                                break
                # Post process gpu containers
                for gpu_container in gpu_containers:
                    for gpu_usage in gpu_container.gpu_usage_list:
                        gpu_name, pci_address, host_pid, proc_name, used_gpu_memory = gpu_usage
                        host_pid = int(host_pid)
                        for container_process in gpu_container.processes:
                            if container_process.host_pid == host_pid:
                                container_process.gpu_name = gpu_name
                                container_process.gpu_pci_address = pci_address
                                container_process.gpu_used_memory = used_gpu_memory

            else:
                LOG.error('Could not get process informations from pod %s container %s, namespace %s on node %s',
                          node_pod_name, node_pod_container_name, node_pod_namespace, gpu_node)

            resp = api.delete_namespaced_pod(node_pod_name, node_pod_namespace)
    finally:
        print('Cleanup...')
        # Stop all processes
        for key, resp in exec_processes.items():
            # End process by providing input for the read command
            try:
                resp.write_stdin('\n')
                stdout, stderr, rc = k8s_end_exec(resp)
                LOG.info('Finished process with ID %s, rc: %s', key, rc)
            except Exception:
                LOG.exception('Could not end exec for process with ID %s', key)

    # pp = pprint.PrettyPrinter(indent=4)
    # pp.pprint(gpu_node_to_containers_map)

    # Collect information into table and print
    header = ("NODE", "POD", "NAMESPACE", "NODE_PID", "PID", "GPU", "PCI_ADDRESS", "GPU_MEMORY", "CMDLINE")
    table = [header]
    for gpu_container in gpu_containers:
        for process in gpu_container.processes:
            if process.gpu_used_memory is not None:
                table.append((gpu_container.node_name, gpu_container.pod_name, gpu_container.pod_namespace,
                              process.host_pid, process.pid, process.gpu_name, process.gpu_pci_address,
                              process.gpu_used_memory, process.cmdline))

    print_table(table)


if __name__ == "__main__":
    sys.exit(main())
