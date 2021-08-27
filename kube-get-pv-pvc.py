#!/usr/bin/env python3

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

LOG = logging.getLogger(__name__)


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


def kube_to_python(value, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    if (
        isinstance(value, openshift.dynamic.ResourceField)
        or isinstance(value, openshift.dynamic.ResourceInstance)
        or isinstance(value, collections.abc.Mapping)
    ):
        return kube_object_to_python(value, ignore_fields=ignore_fields, path=path)
    elif isinstance(value, (list, tuple)):
        return kube_list_to_python(value, ignore_fields=ignore_fields, path=path)
    return value


def kube_list_to_python(list_value, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    result = []
    for index, item in enumerate(list_value):
        cur_path = path + "." + str(index) if path else str(index)
        if not ignore_fields or cur_path not in ignore_fields:
            result.append(kube_to_python(item, ignore_fields=ignore_fields, path=cur_path))
    return result


def kube_object_to_python(obj, ignore_fields: Optional[List] = None, path: Optional[str] = None):
    result = {}
    for k, v in obj.items():
        cur_path = path + "." + str(k) if path else str(k)
        if not ignore_fields or cur_path not in ignore_fields:
            result[k] = kube_to_python(v, ignore_fields=ignore_fields, path=cur_path)
    return result


# https://stackoverflow.com/questions/9535954/printing-lists-as-tabular-data
def print_table(table: Sequence):
    longest_cols = [(max([len(str(row[i])) for row in table]) + 1) for i in range(len(table[0]))]
    row_format = "".join(["{:<" + str(longest_col) + "}" for longest_col in longest_cols])
    for row in table:
        print(row_format.format(*row))


def if_none(value, none_value=None):
    return none_value if value is None else value


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
        description="Get PersistentVolumes and corresponding PersistentVolumeClaims",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-l",
        "--log",
        dest="loglevel",
        default="WARNING",
        help="log level (use one of CRITICAL,ERROR,WARNING,INFO,DEBUG)",
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
        default="table",
        help="Output PV / PVC in YAML format",
        choices=["table", "yaml", "json"],
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

    kubeconfig = args.kubeconfig or os.getenv("KUBECONFIG")
    context = args.context

    k8s_client = config.new_client_from_config(config_file=kubeconfig, context=context)
    dyn_client = DynamicClient(k8s_client)
    api = core_v1_api.CoreV1Api(k8s_client)

    pvs = dyn_client.resources.get(api_version="v1", kind="PersistentVolume")
    pv_list = pvs.get()

    pvpvc_infos = []

    output_table = args.output == "table"
    output_yaml = args.output == "yaml"
    output_json = args.output == "json"

    if output_table:
        if args.no_headers:
            table = []
        else:
            header = ("PV", "PVC NAME", "PVC NAMESPACE")
            table = [header]

    for pv in pv_list.items:
        if args.volume_types and all(pv.spec.get(vol, None) is None for vol in args.volume_types):
            continue
        pvpvc_info = PVPVCInfo(pv)
        pvpvc_infos.append(pvpvc_info)
        if (
            pv.spec is not None
            and pv.spec.claimRef is not None
            and pv.spec.claimRef.kind is not None
            and pv.spec.claimRef.apiVersion is not None
            and pv.spec.claimRef.name is not None
            and pv.spec.claimRef.namespace is not None
        ):
            pvcs = dyn_client.resources.get(api_version=pv.spec.claimRef.apiVersion, kind=pv.spec.claimRef.kind)
            pvc = pvcs.get(name=pv.spec.claimRef.name, namespace=pv.spec.claimRef.namespace)
            pvpvc_info.pvc = pvc

        if output_table:
            table.append(
                (
                    pvpvc_info.pv_name,
                    if_none(pvpvc_info.pvc_name, ""),
                    if_none(pvpvc_info.pvc_namespace, ""),
                )
            )

    if output_table:
        print_table(table)

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
            pv = kube_to_python(pvpvc_info.pv, ignore_fields=ignore_fields)
            if pvpvc_info.pvc is None:
                pvc = None
            else:
                pvc = kube_to_python(pvpvc_info.pvc, ignore_fields=ignore_fields)

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
