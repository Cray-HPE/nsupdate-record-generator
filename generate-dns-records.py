# This script will read from SLS and external DNS all records on the CAN network and compute forward and reverse
# commands in the format that nsupdate expects. It will check to make sure that all addresses fit inside the main CAN
# subnet as well ensuring that only those records that can be resolved externally are created.
#
# To run it requires 3 things:
#   1) The FQDN of the server to send the update to.
#   2) A TOKEN environment variable exported for authentication with the API gateway.
#   3) A kubeconfig that can be read by the Kubernetes Python API.
#
# By default when it is run it will output a file in the current directory that contains all the commands necessary
# to update the forward and reverse zones for the CAN addresses.

import base64
import random
import json
import ast
import os
import requests
import urllib3
import argparse
import ipaddress
from kubernetes import config, client
from kubernetes.stream import stream
import yaml

TTL = 86400


def format_nsupdate_record(name, host, type):
    return "update add {} {} {} {}".format(name, TTL, type, host)


def add_host(fqdn, ipaddress):
    # Check first to make sure this IP address fits into the supernet.
    if ipaddress not in supernet:
        return

    forward_record = "{}".format(format_nsupdate_record(fqdn, ipaddress, "A"))
    forward_records.append(forward_record)

    arpa_host = ipaddress.reverse_pointer
    reverse_record = "{}".format(format_nsupdate_record(arpa_host, fqdn, "PTR"))
    reverse_records.append(reverse_record)


parser = argparse.ArgumentParser(description='Utility to build nsupdate compatible external DNS records.')
parser.add_argument('--dns_server', type=str, action="store", required=True,
                    help='FQDN of the DNS server to nsupdate to.')
parser.add_argument('--server_port', type=int, action="store", default=53,
                    help='Port of the DNS server.')
parser.add_argument('--output_file', type=str, action="store", default="./nsupdate-commands.txt",
                    help="File to output nsupdate commands to.")

parser.add_argument('--base_api_address', type=str, action="store", default="https://api-gw-service-nmn.local/apis/sls",
                    help="Base address for API gateway.")


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

token = os.environ.get('TOKEN')
if token is None:
    print("TOKEN can not be empty!")
    exit(1)

args = parser.parse_args()

NAMESPACE = "services"

try:
    config.load_incluster_config()
except config.ConfigException:
    try:
        config.load_kube_config()
    except config.ConfigException:
        raise Exception("Could not configure Kubernetes Python client!")

core_v1 = client.CoreV1Api()

# Read the site-init secret and get the domain name.
site_init_data = core_v1.read_namespaced_secret("site-init", "loftsman")
customizations_data = base64.b64decode(site_init_data.data['customizations.yaml'])
customizations_yaml = yaml.safe_load(customizations_data)
external_dns = customizations_yaml['spec']['network']['dns']['external']

# Build up all the records before writing them out so we can do post-processing on them.
forward_records = []
reverse_records = []

###
# SLS
###
url = "{}/v1/networks/CAN".format(args.base_api_address)
headers = {"Authorization": "Bearer {}".format(token)}
response = requests.get(url, headers=headers, verify=False).json()

# Need to know what the supernet all the addresses should fit into.
supernet = ipaddress.ip_network(response['IPRanges'][0])

for subnet in response['ExtraProperties']['Subnets']:
    if "IPReservations" in subnet:
        for reservation in subnet['IPReservations']:
            fqdn = "{}.{}".format(reservation['Name'], external_dns)
            host_ipaddress = ipaddress.ip_address(reservation['IPAddress'])
            add_host(fqdn, host_ipaddress)

            if "Aliases" in reservation:
                for alias in reservation['Aliases']:
                    fqdn = "{}.{}".format(alias, external_dns)
                    add_host(fqdn, host_ipaddress)

###
# External DNS
###
DUMP_COMMAND = ['sh', '-c', 'ETCDCTL_API=3 etcdctl get --prefix "" -w json']

pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="etcd_cluster=cray-externaldns-etcd")

# Pick a random member.
target_etcd_member = random.choice(pods.items)

# Get the records JSON.
dump_output = stream(core_v1.connect_get_namespaced_pod_exec,
                     target_etcd_member.metadata.name,
                     NAMESPACE,
                     container='etcd',
                     command=DUMP_COMMAND,
                     stdout=True)

# This is supremely dumb: https://stackoverflow.com/a/55854788/293256
json_output = json.loads(json.dumps(ast.literal_eval(dump_output)))
kvs = json_output['kvs']

for kv in kvs:
    key = str(base64.b64decode(kv['key']))
    key_parts = key.split("/")

    # Now for the fun part, we have to build the FQDN off the key parts going from end to beginning. For example:
    # ["b'", 'skydns', 'com', 'cray', 'dev', 'shandy', 'vcs', "3f7d78ec'"]
    # We need to turn that into vcs.shandy.dev.cray.com. Reverse it and whack off the first 2 and last 1 elements.
    key_parts.reverse()
    fqdn_parts = key_parts[1:-2]
    fqdn = ".".join(fqdn_parts)

    # Now for the actual host information...same basic idea actually, split, reverse, and format.
    value_json = json.loads(base64.b64decode(kv['value']))
    host_ipaddress = ipaddress.ip_address(value_json['host'])

    add_host(fqdn, host_ipaddress)


###
# Output
###
output_file = open(args.output_file, "w")
output_file.write("server {} {}\n".format(args.dns_server, args.server_port))

# We have updates for two zones, forward and reverse. Start with forward.
forward_records.sort()
output_file.write("\n\nzone {}\n".format(external_dns))
for record in forward_records:
    output_file.write("{}\n".format(record))

# Send the update.
output_file.write("\n\nshow\nsend\nanswer\n")

# Now reverse. First we have to compute what this zone is called.
reverse_pointer = supernet.network_address.reverse_pointer
zone_reverse = reverse_pointer.lstrip("0.")

reverse_records.sort()
output_file.write("\n\nzone {}\n".format(zone_reverse))
for record in reverse_records:
    output_file.write("{}\n".format(record))

# Send the update.
output_file.write("\n\nshow\nsend\nanswer\n")

# Close the output file.
output_file.close()
