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

```bash
charmcraft pack
juju deploy ./gdnsd-vm_ubuntu@24.04-amd64.charm \
  --model CONTROLLER:MODEL --to 0
juju expose gdnsd-vm --model CONTROLLER:MODEL
```

Verify the actual artifact filename emitted by charmcraft. The charm opens
UDP/TCP port 53. Fresh installations bind localhost and the unit's private IP;
existing listener configuration is preserved. Supply a zone before expecting
useful public answers. Configure delegation separately in the parent zone.

For the Dwellir experiment, `x.dwellir.com` is writable. `n.dwellir.com` is
protected by the charm's `protected-zones` setting and must remain unchanged.
Do not change its production records, delegation or Aloha configuration.
Every change to the parent `dwellir.com` zone must use the separate
[dwellir-dns repository](https://github.com/dwellir-public/dwellir-dns).

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

The database is shared by configured GeoIP resources. The action refuses a
replacement when a protected zone file is present. For this experiment, use the
Frankfurt instance for database replacement tests; the existing US/Tokyo n copies
and their shared database remain unchanged.

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

See the latest [compressed GeoIP upgrade validation](docs/validation-compressed-geoip.md),
[architecture](ARCHITECTURE.md), [development](DEVELOPING.md) and the
[approved design](docs/superpowers/specs/2026-10-28-gdnsd-vm-design.md).

## References

- [gdnsd control commands](https://github.com/gdnsd/gdnsd/blob/v3.8.2/docs/gdnsdctl.podin)
- [Ubuntu Noble gdnsd package](https://packages.ubuntu.com/noble/net/gdnsd)

US West deployment and the large configuration read repair are recorded in
[docs/validation-us-west.md](docs/validation-us-west.md).
