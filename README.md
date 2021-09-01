# DNS Record Generator 

This repo contains everything necessary to build and deploy a Kubernetes cronjob that will read from the system in 
various places to build an `nsupdate` command list for synchronizing other DNS servers.

## DNS Server Config

The total config will ultimately be up to the site admins to configure but at a minimum these are the requirements 
for a BIND DNS server.

### Zone Config

A sample configuration which contains two zones (one forward and one reverse) looks like:

```text
key "shandy-key" {
	algorithm hmac-sha256;
	secret "VIhSgR4ZKB0sVp5Y2uEBjeeNT5dxbUB7e1Tgfxkvczo=";
};
zone "shandy.dev.cray.com" {
  type master;
  allow-query { any; };
  allow-update { key "shandy-key"; };
  file "/var/lib/bind/db.shandy.dev.cray.com";
};
zone "8.103.10.in-addr.arpa" {
  type master;
  allow-query { any; };
  allow-update { key "shandy-key"; };
  file "/var/lib/bind/db.8.103.10.in-addr.arpa";
};
```

Here TSIG is enabled and enforced using the `shandy-key` key. Again, this is meant to be a minimal configuration, 
there are likely many more options desirable in a production deployment.

BIND will reject updates if at least a skeleton zone database file does not exist. Therefore, for every zone you define
in the config above make sure the `file` has at least a definition:

```text
$ORIGIN .
$TTL 86400	; 1 day
shandy.dev.cray.com	IN SOA	primary.shandy.dev.cray.com. admin.shandy.dev.cray.com. (
				2012080705 ; serial
				28800      ; refresh (8 hours)
				7200       ; retry (2 hours)
				604800     ; expire (1 week)
				86400      ; minimum (1 day)
				)
			NS	primary.shandy.dev.cray.com.
```

If the nameserver you are defining happens to be in this zone also be sure to include it's associated A record 
otherwise the zone database will be invalid.

With that configuration the updater job should be able to push updates to the DNS server.

## Building

The project contains a `Makefile`, so just run `make` from the top level. The resulting Helm chart and Docker image 
will be output to a new folder `build`. These can be copied to a system where they can be deployed.

### Sync Docker Image

The Docker image should be synced to Nexus so it is available cluster wide. Assuming you are on an NCN you can use
Skopeo to sync the built tarball:

```
skopeo copy docker-archive:./image/nsupdate-record-generator_image.tar docker://registry.local/cray/nsupdate-record-generator:1.0.0
```

Be sure to tag the image with the proper `appVersion` found in the Helm chart.

## Deployment

The first requirement is to define a secret that has the TSIG key's algorithm, name, and value in a customization file 
(if you have an already existing one you would like to add to that works as well). A minimal config would look like:

```yaml
apiVersion: customizations/v1
metadata:
  name: nsupdate-record-generator
spec:
  kubernetes:
    sealed_secrets:
      nsupdate_tsig_key:
        generate:
          name: nsupdate-tsig-key
          data:
            - type: static
              args:
                name: algorithm
                value: hmac-sha256
            - type: static
              args:
                name: keyname
                value: shandy-key
            - type: static
              args:
                name: secret
                value: VIhSgR4ZKB0sVp5Y2uEBjeeNT5dxbUB7e1Tgfxkvczo=
    services:
      nsupdate-record-generator:
        destinationDNSServer: "x0c0s7n0.ice.next.cray.com"
        tsigSealedSecrets:
          - '{{ kubernetes.sealed_secrets.nsupdate_tsig_key | toYaml }}'
```

All the key related values are obtained from the BIND config file which is shown in a later section. There is one 
other necessary config option which is `destinationDNSServer`, this should be the FQDN of the server you want to push 
updates to.

You will also need a minimal manifest (again, if you prefer to include this as part of another manifest feel free):

```yaml
apiVersion: manifests/v1beta1
metadata:
  name: nsupdate-record-generator
spec:
  charts:
  - name: nsupdate-record-generator
    version: 1.0.0
    namespace: services
```

Then you can seed the secret data:

```text
/mnt/pitdata/prep/site-init/utils/secrets-seed-customizations.sh ./customizations.yaml
```

Generate a deployable Loftsman manifest:

```text
manifestgen -c ./customizations.yaml -i manifest.yaml -o manifest.deployable.yaml
```

And finally ship the deployment:

```text
loftsman ship --charts-path build/chart/ --manifest-path build/manifest.deployable.yaml
```

By default the chart is setup to run the cronjob hourly. If you want to overwrite this to be more or less frequent you 
can do that in your customizations.yaml file (for example, every 5 minutes):

```yaml
apiVersion: customizations/v1
metadata:
  name: nsupdate-record-generator
spec:
  kubernetes:
    services:
      nsupdate-record-generator:
        updateJob:
          schedule: "*/5 * * * *"
```

Eventually Kubernetes should schedule the job and at the bottom if everything went well you'll see output like this:

```text
Outgoing update query:
;; ->>HEADER<<- opcode: UPDATE, status: NOERROR, id:      0
;; flags:; ZONE: 0, PREREQ: 0, UPDATE: 0, ADDITIONAL: 0
;; ZONE SECTION:
;shandy.dev.cray.com.		IN	SOA

Answer:
;; ->>HEADER<<- opcode: UPDATE, status: NOERROR, id:  30035
;; flags: qr; ZONE: 1, PREREQ: 0, UPDATE: 0, ADDITIONAL: 1
;; ZONE SECTION:
;shandy.dev.cray.com.		IN	SOA

;; TSIG PSEUDOSECTION:
shandy-key.		0	ANY	TSIG	hmac-sha256. 1630529707 300 32 zwVz0SaKlNob+1W1oFoGFMNe2nyYNildpHqS8uJEfCU= 30035 NOERROR 0

Outgoing update query:
;; ->>HEADER<<- opcode: UPDATE, status: NOERROR, id:      0
;; flags:; ZONE: 0, PREREQ: 0, UPDATE: 0, ADDITIONAL: 0
;; ZONE SECTION:
;8.103.10.in-addr.arpa.		IN	SOA

Answer:
;; ->>HEADER<<- opcode: UPDATE, status: NOERROR, id:  29524
;; flags: qr; ZONE: 1, PREREQ: 0, UPDATE: 0, ADDITIONAL: 1
;; ZONE SECTION:
;8.103.10.in-addr.arpa.		IN	SOA

;; TSIG PSEUDOSECTION:
shandy-key.		0	ANY	TSIG	hmac-sha256. 1630529707 300 32 YPLk/YijFNPPBvws2pLCV+wPWKpikAxcQYidj7Ym1ME= 29524 NOERROR 0
```

The key to look for in that output is the `NOERROR` for each `Answer`.
