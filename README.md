# gdnsd-vm

A Juju machine charm for the gdnsd authoritative DNS server on Ubuntu 24.04.
It installs the prebuilt package from Ubuntu's signed APT repositories when the
package is absent. It adopts an existing installation and preserves its files.
Package upgrades remain an operator responsibility. Refreshing the charm does
not upgrade gdnsd or restart the service.

## How configuration updates work

Persistent gdnsd configuration lives in files. Its control socket supports
commands to apply those files, inspect the service and manage temporary ACME
challenges. It does not provide a general API for editing persistent records.

| File changed | Apply command | Effect |
| --- | --- | --- |
| `/etc/gdnsd/zones/<zone>` | `gdnsdctl reload-zones` | Load zone data without replacing the daemon |
| `/etc/gdnsd/config` | `gdnsdctl replace` | Hand service to a new process that reads the new server and plugin configuration |

A configuration replacement uses the installed binary and preserves its version.
The charm uses the local Unix control socket. It does not expose a TCP management
listener. Juju provides authenticated access to the charm's configuration actions.

For every update, the charm:

1. Checks the selected file's SHA-256 against the version saved by pull.
2. Copies the current configuration into a staging directory and replaces only
   the selected candidate file there.
3. Runs `gdnsd -c <staging-directory> checkconf`. Invalid input leaves live files
   and the running service unchanged.
4. Saves a backup outside the served zone directory and atomically replaces
   the selected live file while holding a transaction lock.
5. Calls `gdnsdctl reload-zones` for a zone or `gdnsdctl replace` for the global
   configuration, then checks that gdnsd is running.
6. Restores and reapplies the previous file if activation fails. An unsuccessful
   rollback is reported as a failure requiring operator attention.

`--validate-only` performs validation without applying. A zone operation changes
only the named zone file. Plugin resources are defined in the global configuration;
adding a zone that refers to new resources also requires a separate global update.
Keep global resources compatible with existing zone references during that sequence.

DNSControl can generate ordinary zone files, but cannot replace gdnsd's validator
for its dynamic records and plugin configuration. Use the native validator for
these updates.

## Pull and push

Run the scripts from this repository with Python 3.10 or later and Juju 3.6.
Each target must include its controller and model. Pull saves an editable file
and a `.orig.json` file containing the original checksum and target identity.

```bash
python3 scripts/pull_gdnsd_config.py \
  --model CONTROLLER:MODEL --unit gdnsd-vm/0 \
  --zone x.example.com --file x.zone
# Edit x.zone, including its SOA serial when the zone changes.
python3 scripts/push_gdnsd_config.py \
  --model CONTROLLER:MODEL --unit gdnsd-vm/0 \
  --zone x.example.com --file x.zone --validate-only
python3 scripts/push_gdnsd_config.py \
  --model CONTROLLER:MODEL --unit gdnsd-vm/0 \
  --zone x.example.com --file x.zone
```

Use `--global-config` instead of `--zone` to pull or push `/etc/gdnsd/config`.
Pull again if someone else changed the remote file. Scripts reject conflicting
updates rather than overwriting them.

Repeat `--unit` to select multiple units in the same explicit model. Use the same
selection for pull and push:

```bash
python3 scripts/pull_gdnsd_config.py \
  --model CONTROLLER:MODEL --unit gdnsd-vm/0 --unit gdnsd-vm/1 \
  --zone x.example.com --file shared.zone
# Edit shared.zone, including its SOA serial when the zone changes.
python3 scripts/push_gdnsd_config.py \
  --model CONTROLLER:MODEL --unit gdnsd-vm/0 --unit gdnsd-vm/1 \
  --zone x.example.com --file shared.zone
```

A multi-unit pull requires identical file contents on every selected unit. If the
sources differ, pull each unit individually into separate files. Metadata records
each unit's original digest. A multi-unit push validates every selected unit before
applying to any unit, then applies sequentially. `--validate-only` validates all
selected units without applying. If an application fails, the error identifies the
failed target and units already applied. Their updated digests remain in the local
metadata. There is no distributed rollback; inspect the failed target before retrying.
Run separate commands for different models. There is no automatic replication
between models.

Large configuration reads use numbered base64 result parts when the encoded content
exceeds 64 KiB. Each part is sent in a separate Juju result call to avoid the
operating system argument limit. The bundled pull script reassembles the parts
and verifies their SHA-256 digest before writing the file. Use the updated script
with this charm revision. Small reads retain the existing `content-b64` field.
Direct action consumers must join `content-b64-part-0000` and subsequent numbered
fields according to `content-b64-part-count`, then decode the combined base64.

## Deployment

Deploy the published charm from Charmhub:

```bash
juju deploy gdnsd-vm --channel latest/edge \
  --model CONTROLLER:MODEL --base ubuntu@24.04
juju expose gdnsd-vm --model CONTROLLER:MODEL
```

For a local build, run `charmcraft pack` and deploy the generated
`gdnsd-vm_amd64.charm` file instead.

Verify the actual artifact filename emitted by charmcraft. The charm opens
UDP/TCP port 53. Fresh installations bind localhost and the unit's private IP;
existing listener configuration is preserved. Supply a zone before expecting
useful public answers. Configure delegation separately in the parent zone.

Zone actions accept any valid zone name. Global configuration and shared GeoIP
changes can affect every served zone. Manage parent-zone records and delegation
through your normal DNS management process.

## Update the GeoIP database

`sync-geoip` downloads a MaxMind City or Country database to a staging
directory. It accepts raw `.mmdb`, gzip-compressed `.mmdb`, and standard tar or
tar.gz archives containing exactly one regular `.mmdb` file. Compression is
detected from the content, so the URL does not need a filename extension. It checks the database metadata and sample lookups, then validates
the candidate with gdnsd. If the data changed, it atomically installs the file
at `/etc/gdnsd/geoip/GeoLite2-City.mmdb` and runs `gdnsdctl replace` to load it.
A failed activation restores the previous file and tries to load it again.
An unchanged database does not replace the daemon.

```bash
juju run gdnsd-vm/0 sync-geoip --model CONTROLLER:MODEL \
  url=https://DATABASE-HOST/GeoLite2-City.mmdb
# Reuse the remembered URL on this unit.
juju run gdnsd-vm/0 sync-geoip --model CONTROLLER:MODEL
```

The first call needs `url`. The charm remembers the last nonempty URL supplied
on each unit, even when the attempt fails. Later calls reuse it unless a new URL
is supplied. The action displays the URL in its results and records the installed
file's SHA-256, database type and build timestamp. Stored URLs survive hooks,
reboots and charm refreshes. Units do not share this setting automatically.

An optional `sha256` parameter verifies the downloaded bytes for that call. For
a compressed download, use the archive checksum. Results report the installed
MMDB checksum as `sha256`, the downloaded file checksum as `download-sha256`,
and the detected packaging as `download-format`.
It is not retained for later calls, which allows a stable URL to provide a new
database revision. Prefer HTTPS endpoints. Recognized Nextcloud public-share URLs are resolved to their download endpoint;
the original share URL remains the remembered setting. HTML login pages still
fail validation. Downloads and expanded data are each limited to 256 MiB.
Archives with links, traversal paths, multiple databases or extension metadata
are rejected before extraction. A MaxMind license or a private download endpoint may be needed for
production databases; the charm does not obtain a license or generate a database.

A failed download, validation or configuration application leaves the unit
`waiting` with a message to supply a valid version. The failed action and Juju
logs contain the cause. Status hooks keep checking the running service but retain
the waiting state until the matching configuration problem is resolved. A real
service health failure takes priority and reports `blocked`. Correcting another
zone or restarting the service does not hide an unresolved configuration error.

The database is shared by configured GeoIP resources. Database replacement can
affect every zone using those resources.

## Local health and workload version

Every `update-status` checks the local systemd state and gdnsd control socket,
then sends a root SOA query over both UDP and TCP to `127.0.0.1:53`. The DNS
check accepts a normal answer, NXDOMAIN or REFUSED because this server is
authoritative only. Each DNS transport has a two-second socket timeout.

A healthy unit reports `active` with a message such as
`running; GeoIP installed: GeoLite2-City 2025-05-23T11:21:08Z`.
MaxMind identifies database builds by their type and UTC build timestamp.
This describes the installed file. gdnsd does not expose a separate loaded-database
version through its control API. A successful sync validates and reloads that file.
A fresh unit without a database reports `GeoIP not installed`. Invalid installed
metadata produces a diagnostic log and waiting status requesting a valid file. A stopped service or
failed health check reports `blocked` with its observed state or failure reason. Checks
do not restart the service. Juju's workload version comes from the running daemon,
for example `3.8.2`. `get-info` also reports the Ubuntu package version, for example
`3.8.2-1build2`. It also returns `geoip-database-type`, `geoip-build-time`,
`geoip-build-epoch`, `geoip-database-path`, `geoip-sha256` and
`geoip-version-source=installed-file-metadata`. Status inspection reads metadata;
the full file hash is calculated for `get-info` rather than on every status hook.
An unavailable running version does not erase the last version
Juju observed. Install, refresh and successful service/configuration actions
also refresh the local health status.

## Actions and diagnostics

Read and apply configuration through `get-config`, `validate-config` and
`apply-config`. Each accepts an explicit zone or global scope. The scripts provide
file handling for these actions. Use `service` with `action=start`, `stop`,
`restart`, `status`, `enable` or `disable` for the gdnsd systemd unit. `status`
is observational. `get-info` reports the running and package versions and service state.
`sync-geoip` manages the GeoIP database using its remembered per-unit URL.
Configuration backups are local recovery material, not an off-machine backup.

See [architecture](ARCHITECTURE.md) for implementation details and
[development](DEVELOPING.md) for build, test and release commands.

## References

- [gdnsd control commands](https://github.com/gdnsd/gdnsd/blob/v3.8.2/docs/gdnsdctl.podin)
- [Ubuntu Noble gdnsd package](https://packages.ubuntu.com/noble/net/gdnsd)

## Metrics and logs

The charm exposes native gdnsd statistics at `127.0.0.1:9153/metrics` through
`gdnsd-exporter.service`. The exporter runs as the gdnsd user and reads the
local control socket. It does not open a public port. Refreshing the charm
updates this auxiliary service without restarting or upgrading gdnsd.

Counters cover every zone served by the daemon, including direct test queries
and local health probes. They do not distinguish traffic by zone. This implementation does not record client addresses or query names.
`gdnsd_scrape_success` is zero when collection or validation fails. Failed
scrapes omit native counters rather than reporting false zeros. The exporter
logs the failure to its journal. Native uptime and counters reset with gdnsd.

Attach a compatible alloy-sub over both `juju-info` and `machine-observability`.
Connect its `send-remote-write` and `send-loki-logs` endpoints to your Prometheus
and Loki offers. Connect the principal's `grafana-dashboard` endpoint directly
to the Grafana offer. For example, with your controller-qualified test model:

```sh
juju deploy -m "$MODEL" alloy-sub --channel latest/edge --revision 36
juju integrate -m "$MODEL" gdnsd-vm:juju-info alloy-sub:juju-info
juju integrate -m "$MODEL" gdnsd-vm:machine-observability alloy-sub:machine-observability
juju consume -m "$MODEL" CONTROLLER:OWNER/MODEL.prometheus cos-prometheus
juju consume -m "$MODEL" CONTROLLER:OWNER/MODEL.loki cos-loki
juju consume -m "$MODEL" CONTROLLER:OWNER/MODEL.grafana cos-grafana
juju integrate -m "$MODEL" alloy-sub:send-remote-write cos-prometheus:receive-remote-write
juju integrate -m "$MODEL" alloy-sub:send-loki-logs cos-loki:logging
juju integrate -m "$MODEL" gdnsd-vm:grafana-dashboard cos-grafana:grafana-dashboard
```

The v3 declaration includes gdnsd and exporter journals and no alert artifacts.
Metrics scrape every 15 seconds. Alloy derives each principal unit from its
juju-info relation and forwards the source model, model UUID, application,
unit and charm labels. The leader publishes the shared source declaration.

The `gdnsd native server metrics` dashboard displays request rates, response
codes, transport errors, uptime, collection status and journals. It has model
and unit selectors. No alert thresholds are configured.

Useful PromQL, scoped further with the source Juju labels as needed:

```promql
sum by (juju_model, protocol) (rate(gdnsd_requests_total[5m]))
sum by (juju_model, rcode) (rate(gdnsd_responses_total[5m]))
sum by (juju_model, protocol, operation) (rate(gdnsd_transport_errors_total[5m]))
gdnsd_scrape_success
```

To diagnose local collection, run `curl -fsS http://127.0.0.1:9153/metrics` on
the unit and inspect `journalctl -u gdnsd-exporter.service`. A healthy local
endpoint does not prove backend delivery. Check recent Prometheus samples and
Loki entries with the principal's Juju labels before relying on monitoring.

## Restricted SSH configuration management

Install and charm refresh reconcile `/usr/local/sbin/gdnsd-config-manager` and
root-owned Python sources under `/usr/local/lib/gdnsd-vm`. Refresh installs these
files without restarting gdnsd or changing its package, configuration or GeoIP
database. The helper runs with the system Python and uses only its standard
library. Existing Juju actions and relations remain available.

Provision deployment accounts, keys, sudo policy and trusted SSH host keys
separately. Restrict each deployment key to the exact forced command
`sudo -n /usr/local/sbin/gdnsd-config-manager ssh`, with forwarding and PTYs
disabled. The charm does not create accounts or credentials.

The command reads one bounded JSON request on stdin and writes one JSON response
on stdout. Every request requires `"version":1` and an `operation`. Responses are
`{"version":1,"ok":true,"result":{...}}`, or
`{"version":1,"ok":false,"error":"..."}` with a nonzero exit status.

- `snapshot` accepts an optional `zones` list, defaulting to `n.dwellir.com` and
  `x.dwellir.com`. `result.files` contains `config` and selected `zones/<fqdn>`
  entries with `sha256`, `content-b64` and `exists`. A missing file has digest
  `absent`. `result.deployment` contains the last release checkpoint, or `{}`
  before initialization.
- `status` reports `healthy`, control socket output, deployment state and an
  error message. It checks systemd and local UDP/TCP DNS without restarting.
- `validate` and `apply` require `files`, mapping exact configuration names to
  base64 UTF-8 contents, and `expected`, mapping exactly those names to their
  snapshot digests or `absent`. Each file is limited to 2 MiB; the decoded
  request is limited to 8 MiB, including an optional `bridge-b64` global config.
  Responses include final digest maps in `files` and `checksums`, plus `changed`
  and `validated` booleans. Validation changes no live files or checkpoint.

Optional `release` fields are `commit` with 40 lowercase hexadecimal characters,
`source-sha256` with 64, a uint32 `serial`, and an `id` of up to 128 letters,
digits, dots, underscores, colons or hyphens. Successful apply records release
metadata and final hashes in `deployment.release` and `deployment.files`, even
when contents are identical. Identical contents cause no native activation.
Deployment state also includes error and repair information used by charm status.

For resource renames, supply `bridge-b64` containing both old and new global
resource definitions. The helper validates the bridge with existing zones,
each selected new zone under the bridge, then the final global configuration.
All validation precedes live writes. Apply activates those same stages in order,
with `replace` for globals and `reload-zones` for zones. Failed activation restores
all affected files and their ownership/modes, then activates the original tree.
A failed rollback reports partial failure and the backup directory for recovery.
