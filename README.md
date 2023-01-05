# kube-utils
Collection of useful Kubernetes utilities

## kube-create-admin-user
Creates service account with specified name and `cluster-admin` ClusterRole in kube-system namespace
```
Create service account with cluster-admin ClusterRole in kube-system namespace

kube-create-admin-user [options]

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl
options:
  -u, --user                 Name of the admin user to create (default: admin-user)
  -c, --context              The name of the kubeconfig context to use.
      --help                 Display this help and exit
      --                     End of options
```
## kube-docker-env

kube-docker-env connects to the remote docker daemon running in the Kubernetes cluster.

```
Connect to the docker daemon running in the Kubernetes cluster.
Similar to '$(minikube docker-env)' and '$(docker-machine env)'.

kube-docker-env [options]

This script creates connector pod which connects local machine to the remote docker server.

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl

options:
  -n, --namespace=''         Namespace of the connector pod (default: kubeflow)
  -p, --pod=''               Name of the connector pod (default: docker-gateway)
  -h, --host=''              Run pod on a specific host
                             Note: In this case, the host name is appended to the Pod name.
      --context=''           The name of the kubeconfig context to use.
                             Has precedence over KUBE_CONTEXT variable.
      --help                 Display this help and exit
      --                     End of options
```
## kube-get-bearer-token

```
Get bearer token from kube-system:admin-user service account.
Bearer token can be used to login into Kubernetes Dashboard

kube-get-bearer-token [options]

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl
options:
  -c, --context              The name of the kubeconfig context to use.
      --help                 Display this help and exit
      --                     End of options
```

## kube-get-node-services

Get list of Kubernetes services available on node ports,
similar to `$(minikube service)`.

## kube-list-all-images

Print list of all images running in pods in the Kubernetes cluster.

## kube-match-pod

Prints first found Kubernetes pods that matches a specified prefix.

## kube-merge-config

Merges Kubernetes configuration file into ~/.kube/config configuration file.

```
Usage: ./kube-merge-config kubernetes-config-file

Merges kubernetes-config-file into ~/.kube/config configuration file
```

## kube-rsync

Transfer files and directories with rsync to and from running container. 
`rsync` tool must be available in the container.

```
Rsync to/from the k8s pod

kube-rsync [options] [--] [rsync-options] src_path dest_path

paths starting with 'pod-name-prefix[@pod-namespace]:' are remote on specified pod

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl

options:
  -n, --namespace=''         Namespace of the pod
      --context=''           The name of the kubeconfig context to use.
                             Has precedence over KUBE_CONTEXT variable.
  -c, --container=''         Container name. If omitted, the first container in the pod will be chosen
      --help                 Display this help and exit
      --                     End of options
```

## kube-wait-for-pod

```
./kube-wait-for-pod [options] pod_name_prefix

Wait until Kubernetes pod runs with the name starting with pod_name_prefix.

-n | --namespace  NAMESPACE  Set pod namespace (default: kubeflow)
     --trials     N          Number of trials (0 - infinite, default: 0)
     --run-checks N          Number of checks to perform until pod is assumed to be running (default: 5)
     --delay      SECONDS    Delay in seconds between checks (default: 2)
     --debug                 Enable debug output
```

## kube-test-network

Performs network operations such as pinging the IP address 8.8.8.8 and DNS lookup of google.com to test network connectivity in the cluster.

```
Test Kubernetes network

kube-test-network [options]

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl
options:
  -u, --user                 Name of the user service account in namespace kube-system (default: )
  -c, --context              The name of the kubeconfig context to use.
      --help                 Display this help and exit
      --                     End of options
```

## kube-backup
Backup Kubernetes state to set of YAML files. This tool is based on https://github.com/pieterlange/kube-backup .
```
kube-backup [options] [dest-dir]

When dest-dir is not specified current directory is used (/home/rubinste/Kubernetes/kube-utils)

environment variables:
  KUBECTL                    Name of the kubectl command to use
  KUBECTL_BIN                Full path to the kubectl binary. This variable has precedence over KUBECTL
  KUBE_CONTEXT               The name of the kubeconfig context to use
  KUBECTL_OPTS               Additional options for kubectl

options:
  -c, --context              The name of the kubeconfig context to use.
                             Has precedence over KUBE_CONTEXT variable.
  -n, --namespace(s)=        Namespaces to backup separted by spaces.
                             Multiple namespace arguments are concatenated
  -r, --resourcetype(s)=     Resource types to backup separted by spaces.
                             Multiple resourcetype arguments are concatenated
  -g, --globalresource(s)=   Global resources to backup separted by spaces.
                             Multiple global resource arguments are concatenated
      --include-tiller-configmaps
                             Include Tiller configmaps into backup
      --help                 Display this help and exit
      --                     End of options

```

## kube-get-gpu-pods

Get the list of Kubernetes pods that use GPUs, currently only NVIDIA GPUs are supported.

## kube-get-gpu-nodes

Get the list of Kubernetes nodes that contain and use GPUs, currently only NVIDIA GPUs are supported.

## kube-nvidia-get-processes.py

Get the list of Kubernetes pods that use NVIDIA GPUs

```
usage: kube-nvidia-get-processes.py [-h] [-l LOGLEVEL] [--kubeconfig KUBECONFIG] [--context CONTEXT]

Get pod processes that use NVIDIA GPU

optional arguments:
  -h, --help            show this help message and exit
  -l LOGLEVEL, --log LOGLEVEL
                        log level (use one of CRITICAL,ERROR,WARNING,INFO,DEBUG) (default: WARNING)
  --kubeconfig KUBECONFIG
                        Path to the kubeconfig file to use for CLI requests. (default: None)
  --context CONTEXT     The name of the kubeconfig context to use (default: None)
```

## kube-get-volume-info.py

Get information about used storage volumes.

```
usage: kube-get-volume-info.py [-h] [-l LOGLEVEL] [--kubeconfig KUBECONFIG] [--context CONTEXT] [-H] [-o {pvc-table,usage-table,yaml,json}] [-s] [-f] [-v VOLUME_TYPE] [-e EXPR] [-d]

Get Kubernetes volume information

optional arguments:
  -h, --help            show this help message and exit
  -l LOGLEVEL, --log LOGLEVEL
                        log level (use one of CRITICAL,ERROR,WARNING,INFO,DEBUG) (default: WARNING)
  --kubeconfig KUBECONFIG
                        Path to the kubeconfig file to use for CLI requests. (default: None)
  --context CONTEXT     The name of the kubeconfig context to use (default: None)
  -H, --no-headers      Don't print headers (default: False)
  -o {pvc-table,usage-table,yaml,json}, --output {pvc-table,usage-table,yaml,json}
                        Output format of PV / PVC information (default: usage-table)
  -s, --separate-pvc    Output separately PV and PVC files (default: False)
  -f, --output-to-files
                        Output PV / PVC to files (default: False)
  -v VOLUME_TYPE, --select-volume VOLUME_TYPE
                        Select only persistent volumes of TYPE (e.g. nfs, hostPath) (default: [])
  -e EXPR, --select-expr EXPR
                        Select volume by using Python-like expression (e.g. '"nfs" in volume') (default: None)
  -d, --show-dependent  Output dependent Kubernetes objects (e.g. Pods controlled by ReplicaSet, Jobs controlled by CronJob, etc.) (default: False)
```

## kube-shlib.sh

Bash shell Library with common functions used by all utilities.
