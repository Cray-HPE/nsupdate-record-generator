#!/usr/bin/env sh

set -e

if [ "$1" = 'update-records' ]
then
  # Sanity check, always need a DNS server defined.
  if [ -z "${DEST_DNS_SERVER}" ]
  then
    echo "Destination DNS server not set!"
    exit 1
  fi

  # First generate the nsupdate commands file.
  python3 generate-dns-records.py --dns_server "$DEST_DNS_SERVER"

  # DEBUG
  cat ./nsupdate-commands.txt

  # nsupdate commands file now exists at ./nsupdate-commands.txt. Run nsupdate (with TSIG if configured).
  if [ -n "$TSIG_ALGORITHM" ] && [ -n "$TSIG_KEYNAME" ] && [ -n "$TSIG_KEYVALUE" ]
  then
    nsupdate -y "$TSIG_ALGORITHM:$TSIG_KEYNAME:$TSIG_KEYVALUE" nsupdate-commands.txt
  else
    nsupdate nsupdate-commands.txt
  fi

  exit
fi

exec "$@"