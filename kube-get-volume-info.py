#!/usr/bin/env python3

import pprint
import argparse
import collections
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional, Sequence, List

import openshift
import yaml
from kubernetes import config
from kubernetes.client.api import core_v1_api
from openshift.dynamic import DynamicClient
import asteval

LOG = logging.getLogger(__name__)

def k8s_full_name(name: str, namespace: str) -> str:
    return "{}/{}".format(name, namespace)


@dataclass
class PVPVCInfo:
    pv: object
    pvc: Optional = None

    @property
    def has_pvc(self) -> bool:
        return self.pvc is not None

    @property
    def pv_name(self) -> str:
        return self.pv.metadata.name

    @property
    def pvc_name(self) -> Optional[str]:
        return self.pvc.metadata.name if self.pvc is not None else None

    @property
    def pvc_namespace(self) -> Optional[str]:
        return self.pvc.metadata.namespace if self.pvc is not None else None

    @property
    def pvc_full_name(self) -> Optional[str]:
        return (
            k8s_full_name(name=self.pvc.metadata.name, namespace=self.pvc.metadata.namespace)
            if self.pvc is not None
            else None
        )


def k8s_to_python(value, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    if (
        isinstance(value, openshift.dynamic.ResourceField)
        or isinstance(value, openshift.dynamic.ResourceInstance)
        or isinstance(value, collections.abc.Mapping)
    ):
        return k8s_resource_to_python(value, ignore_fields=ignore_fields, path=path)
    elif isinstance(value, (list, tuple)):
        return k8s_list_to_python(value, ignore_fields=ignore_fields, path=path)
    return value


def k8s_list_to_python(list_value, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    result = []
    for index, item in enumerate(list_value):
        cur_path = path + "." + str(index) if path else str(index)
        if not ignore_fields or cur_path not in ignore_fields:
            result.append(k8s_to_python(item, ignore_fields=ignore_fields, path=cur_path))
    return result


def k8s_resource_to_python(obj, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    result = {}
    for k, v in obj.items():
        cur_path = path + "." + str(k) if path else str(k)
        if not ignore_fields or cur_path not in ignore_fields:
            result[k] = k8s_to_python(v, ignore_fields=ignore_fields, path=cur_path)
    return result


# https://stackoverflow.com/questions/9535954/printing-lists-as-tabular-data
def print_table(table: Sequence):
    longest_cols = [(max([len(str(row[i])) for row in table]) + 1) for i in range(len(table[0]))]
    row_format = "".join(["{:<" + str(longest_col) + "}" for longest_col in longest_cols])
    for row in table:
        print(row_format.format(*row))


def if_none(value, none_value=None):
    return none_value if value is None else value


def k8s_get_by_path(k8s_resource, path: str, default=None):
    path_list = path.split(".")
    obj = k8s_resource
    for path_elem in path_list:
        obj = obj.get(path_elem, None)
        if obj is None:
            return default
    return obj


def k8s_get_pod_volumes(k8s_resource):
    if k8s_resource.kind == "Pod":
        return k8s_get_by_path(k8s_resource, "spec.volumes", [])
    elif k8s_resource.kind == "CronJob":
        return k8s_get_by_path(k8s_resource, "spec.jobTemplate.spec.template.spec.volumes", [])
    else:  # e.g. Deployment, ReplicaSet, Job
        return k8s_get_by_path(k8s_resource, "spec.template.spec.volumes", [])


def k8s_get_volume_type(k8s_resource) -> str:
    kind = k8s_resource.get("kind", None)
    if kind == "PersistentVolume":
        volume = k8s_resource.spec
    elif kind is not None:
        raise ValueError("Argument is not a k8s volume")
    else:
        volume = k8s_resource
    for key in volume.keys():
        if (
            key
            not in (
                "name",
                "accessModes",
                "capacity",
                "claimRef",
                "mountOptions",
                "nodeAffinity",
                "persistentVolumeReclaimPolicy",
                "storageClassName",
                "volumeMode",
            )
            and getattr(volume.get(key), "get", None) is not None
        ):
            return key
    raise ValueError("Argument is not a k8s volume")

# https://v1-18.docs.kubernetes.io/docs/concepts/workloads/controllers/garbage-collection/
def k8s_has_owner(k8s_resource):
    owner_refs = k8s_get_by_path(k8s_resource, "metadata.ownerReferences", [])
    if not owner_refs:
        return False
    return any(bool(owner_ref.name) for owner_ref in owner_refs)


def main():
    ignore_fields = [
        "metadata.managedFields",
        "metadata.selfLink",
        "metadata.uid",
        "metadata.resourceVersion",
        "metadata.creationTimestamp",
        "spec.claimRef.resourceVersion",
        "spec.claimRef.uid",
        "status",
    ]
    parser = argparse.ArgumentParser(
        description="Get Kubernetes volume information",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-l",
        "--log",
        dest="loglevel",
        default="WARNING",
        help="log level (use one of CRITICAL,ERROR,WARNING,INFO,DEBUG) (default: %(default)s)",
    )
    parser.add_argument(
        "--kubeconfig",
        default=None,
        help="Path to the kubeconfig file to use for CLI requests.",
    )
    parser.add_argument("--context", default=None, help="The name of the kubeconfig context to use")
    parser.add_argument(
        "-H",
        "--no-headers",
        default=False,
        help="Don't print headers",
        action="store_true",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="usage-table",
        help="Output format of PV / PVC information (default: %(default)s)",
        choices=["pvc-table", "usage-table", "yaml", "json"],
    )
    parser.add_argument(
        "-s",
        "--separate-pvc",
        help="Output separately PV and PVC files",
        action="store_true",
    )
    parser.add_argument(
        "-f",
        "--output-to-files",
        default=False,
        help="Output PV / PVC to files",
        action="store_true",
    )
    parser.add_argument(
        "-v",
        "--select-volume",
        metavar="VOLUME_TYPE",
        default=[],
        dest="volume_types",
        action="append",
        help="Select only persistent volumes of TYPE (e.g. nfs, hostPath)",
    )
    parser.add_argument(
        "-e",
        "--select-expr",
        metavar="EXPR",
        default=None,
        dest="volume_expr",
        help="Select the volume with a Python-like expression (implemented by the asteval module).\n"
             "The expression has access to the variables \"volume\", \"namespace\" and \"resource\",\n"
             "which can be used as dictionaries except for the operation \"in\". \n"
             "Use the DEBUG protocol level to print all variables to stderr or the print function of the expression.\n"
             "Examples:\n"
             " print(\"resource is\", resource)                                  - print resource variable\n"
             " \"nfs\" in keys(volume)                                           - select NFS volumes only\n"
             " namespace != \"kube-system\" and \"configMap\" not in volume.keys() - select non-configMap volumes in "
             "non kube-system namespaces\n\n"
    )
    parser.add_argument(
        "-d",
        "--show-dependent",
        default=False,
        help="Output dependent Kubernetes objects (e.g. Pods controlled by ReplicaSet, Jobs controlled by CronJob, "
        "etc.)",
        action="store_true",
    )

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

    if args.volume_expr and args.volume_types:
        print("-v / --select-volume and -e / --select-expr cannot be specified at the same time", file=sys.stderr)
        sys.exit(1)

    pvpvc_infos = []
    pvc_full_name_to_pvpvc_info_map = {}

    if args.volume_expr:
        aeval = asteval.Interpreter(use_numpy=False, writer=sys.stderr)
        try:
            code = aeval.parse(args.volume_expr)
        except:
            errmsg = sys.exc_info()[1]
            if len(aeval.error) > 0:
                errmsg = "\n".join(aeval.error[0].get_error())
            print("Error: Could not compile expression: {}".format(errmsg), file=sys.stderr)
            sys.exit(1)

    def is_pod_volume_selected_and_info(pod_volume, resource: Optional = None):
        if resource:
            k8s_namespace = resource.metadata.namespace
        else:
            k8s_namespace = None
        pvc = pod_volume.get("persistentVolumeClaim")
        if pvc is not None and k8s_namespace:
            pvc_name = pvc.claimName
            pvpvc_info = pvc_full_name_to_pvpvc_info_map.get(k8s_full_name(pvc_name, k8s_namespace), None)
            return pvpvc_info is not None, pvpvc_info

        if not args.volume_types and not args.volume_expr:
            # All volumes are selected
            return True, None

        if args.volume_expr:
            LOG.debug('variable volume = %s', pprint.pformat(pod_volume, width=80, indent=2, compact=True))
            LOG.debug('variable resource = %s', pprint.pformat(resource, width=80, indent=2, compact=True))
            LOG.debug('variable namespace = %s', pprint.pformat(k8s_namespace, width=80, indent=2, compact=True))

            aeval.symtable["volume"] = pod_volume
            aeval.symtable["resource"] = resource
            aeval.symtable["namespace"] = k8s_namespace
            try:
                result = aeval.run(code, expr=args.volume_expr, lineno=0)
            except:
                errmsg = sys.exc_info()[1]
                if len(aeval.error) > 0:
                    errmsg = "\n".join(aeval.error[0].get_error())
                print("Error: Evaluation failed: \n{}".format(errmsg), file=sys.stderr)
                sys.exit(1)
            return bool(result), None

        if args.volume_types:
            for volume_type in args.volume_types:
                # ignore invalid types
                if volume_type == "name":
                    continue
                if pod_volume.get(volume_type, None) is not None:
                    return True, None
        return False, None

    def is_persistent_volume_selected(pv):
        assert pv.kind == "PersistentVolume"
        volume = pv.spec
        resource = pv
        if pv.spec.claimRef:
            k8s_namespace = pv.spec.claimRef.namespace
        else:
            k8s_namespace = None

        if not args.volume_types and not args.volume_expr:
            # All volumes are selected
            return True

        if args.volume_expr:
            LOG.debug('variable volume = %s', pprint.pformat(volume, width=80, indent=2, compact=True))
            LOG.debug('variable resource = %s', pprint.pformat(resource, width=80, indent=2, compact=True))
            LOG.debug('variable namespace = %s', pprint.pformat(k8s_namespace, width=80, indent=2, compact=True))

            aeval.symtable["volume"] = volume
            aeval.symtable["resource"] = resource
            aeval.symtable["namespace"] = k8s_namespace
            try:
                result = aeval.run(code, expr=args.volume_expr, lineno=0)
            except:
                errmsg = sys.exc_info()[1]
                if len(aeval.error) > 0:
                    errmsg = "\n".join(aeval.error[0].get_error())
                print("Error: Evaluation failed: \n{}".format(errmsg), file=sys.stderr)
                sys.exit(1)
            return bool(result)

        if args.volume_types:
            for volume_type in args.volume_types:
                # ignore invalid types
                if volume_type == "name":
                    continue
                if volume.get(volume_type, None) is not None:
                    return True
        return False


    kubeconfig = args.kubeconfig or os.getenv("KUBECONFIG")
    context = args.context

    k8s_client = config.new_client_from_config(config_file=kubeconfig, context=context)
    dyn_client = DynamicClient(k8s_client)
    api = core_v1_api.CoreV1Api(k8s_client)

    pvs = dyn_client.resources.get(api_version="v1", kind="PersistentVolume")
    pv_list = pvs.get()

    output_pvc_table = args.output == "pvc-table"
    output_usage_table = args.output == "usage-table"
    output_yaml = args.output == "yaml"
    output_json = args.output == "json"

    for pv in pv_list.items:
        if not is_persistent_volume_selected(pv):
            continue
        pvpvc_info = PVPVCInfo(pv)
        pvpvc_infos.append(pvpvc_info)
        if (
            pv.spec is not None
            and pv.spec.claimRef is not None
            and pv.spec.claimRef.name is not None
            and pv.spec.claimRef.namespace is not None
        ):
            pvcs = dyn_client.resources.get(kind=pv.spec.claimRef.kind or "PersistentVolumeClaim")
            pvc = pvcs.get(name=pv.spec.claimRef.name, namespace=pv.spec.claimRef.namespace)
            pvpvc_info.pvc = pvc
            pvc_full_name_to_pvpvc_info_map[pvpvc_info.pvc_full_name] = pvpvc_info

    if output_usage_table:
        if args.no_headers:
            pvpvc_usage_table = []
        else:
            header = ("KIND", "NAMESPACE", "NAME", "PVC NAME", "PV", "VOL TYPE", "VOL PATH")
            pvpvc_usage_table = [header]

        def process_pod_volume(resource, pod_volume):
            pod_volume_selected, pvpvc_info = is_pod_volume_selected_and_info(pod_volume, resource)
            if pod_volume_selected:
                if pvpvc_info is not None:
                    pvc_name = pvpvc_info.pvc_name
                    pv_name = pvpvc_info.pv_name
                    pv_volume_type = k8s_get_volume_type(pvpvc_info.pv)
                    if pv_volume_type:
                        pv_volume = pvpvc_info.pv.spec.get(pv_volume_type)
                        pv_volume_path = pv_volume.get("path", None)
                    else:
                        pv_volume_path = None
                else:
                    pvc_name = ""
                    pv_name = ""
                    pv_volume_type = k8s_get_volume_type(pod_volume)
                    if pv_volume_type:
                        pv_volume_path = pod_volume.get(pv_volume_type, {}).get("path", None)
                    else:
                        pv_volume_path = None

                pvpvc_usage_table.append(
                    (
                        resource.kind,
                        resource.metadata.namespace,
                        resource.metadata.name,
                        pvc_name,
                        pv_name,
                        if_none(pv_volume_type, ""),
                        if_none(pv_volume_path, ""),
                    )
                )

        for kind in ("Deployment", "CronJob", "ReplicaSet", "Job", "Pod"):
            resources_list_by_kind = dyn_client.resources.search(kind=kind)
            for resource_by_kind in resources_list_by_kind:
                resource_list = resource_by_kind.get()
                for resource in resource_list.items:
                    if args.show_dependent or not k8s_has_owner(resource):
                        pod_volumes = k8s_get_pod_volumes(resource)
                        for volume in pod_volumes:
                            process_pod_volume(resource, volume)

        print_table(pvpvc_usage_table)

    if output_pvc_table:
        if args.no_headers:
            pvpvc_table = []
        else:
            header = ("PV", "PVC NAME", "PVC NAMESPACE", "VOL TYPE", "VOL PATH")
            pvpvc_table = [header]

        for pvpvc_info in pvpvc_infos:
            if output_pvc_table:
                pv_volume_type = k8s_get_volume_type(pvpvc_info.pv.spec)
                if pv_volume_type:
                    volume = pvpvc_info.pv.spec.get(pv_volume_type)
                    pv_volume_path = volume.get("path", None)
                else:
                    pv_volume_path = None

                pvpvc_table.append(
                    (
                        pvpvc_info.pv_name,
                        if_none(pvpvc_info.pvc_name, ""),
                        if_none(pvpvc_info.pvc_namespace, ""),
                        if_none(pv_volume_type, ""),
                        if_none(pv_volume_path, ""),
                    )
                )

        print_table(pvpvc_table)

    if output_yaml or output_json:

        if output_yaml:

            def write_one_to_file(obj, f):
                yaml.safe_dump(obj, f)

            def write_all_to_file(objects, f):
                yaml.safe_dump_all(objects, f)

        else:

            def write_one_to_file(obj, f):
                json.dump(obj, f, indent=2)

            def write_all_to_file(objects, f):
                json.dump(objects, f, indent=2)

        all_pvpvc = []
        for pvpvc_info in pvpvc_infos:
            pv = k8s_to_python(pvpvc_info.pv, ignore_fields=ignore_fields)
            if pvpvc_info.pvc is None:
                pvc = None
            else:
                pvc = k8s_to_python(pvpvc_info.pvc, ignore_fields=ignore_fields)

            if args.output_to_files:
                if args.separate_pvc:
                    fn = "{}.pv.{}".format(pvpvc_info.pv_name, args.output)
                    with open(fn, "w") as f:
                        print("Write PV to file {}".format(fn), file=sys.stderr)
                        write_one_to_file(pv, f)
                    if pvc is not None:
                        fn = "{}.pvc.{}".format(pvpvc_info.pvc_name, args.output)
                        with open(fn, "w") as f:
                            print("Write PVC to file {}".format(fn), file=sys.stderr)
                            write_one_to_file(pvc, f)
                else:
                    pvpvc = [pv]
                    if pvc is not None:
                        pvpvc.append(pvc)
                    fn = "{}.pvpvc.{}".format(pvpvc_info.pv_name, args.output)
                    with open(fn, "w") as f:
                        print("Write PV and PVC to file {}".format(fn), file=sys.stderr)
                        write_all_to_file(pvpvc, f)
            else:
                # Output all to stdout
                all_pvpvc.append(pv)
                if pvc is not None:
                    all_pvpvc.append(pvc)

        if not args.output_to_files and all_pvpvc:
            write_all_to_file(all_pvpvc, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
