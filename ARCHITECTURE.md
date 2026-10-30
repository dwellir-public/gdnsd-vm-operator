# Architecture

`src/charm.py` handles Juju events and status. `src/workload.py` owns package
adoption, native command execution and configuration transactions. `src/geoip.py` handles bounded database downloads, MaxMind validation and
transactional database replacement. The scripts
in `scripts/` call actions through Juju and keep pull-time conflict metadata.

## Install and refresh

Installation checks the package's actual dpkg status. It adopts an existing
installation without changing files or restarting the service. A fresh install
uses Ubuntu's APT package and configures listeners for localhost and the Juju
private address. The workload keeps upstream paths and the `gdnsd.service` unit.
Only the install event may install a missing package. Charm refresh reconciles the auxiliary exporter and observes DNS service state.
Config-changed and update-status observe service state. They do not install,
upgrade, restart or rewrite workload files. An explicitly stopped service stays
stopped until an operator starts it. There is no workload-upgrade feature.

## Local health

Every status observation reads systemd ActiveState and runs `gdnsdctl -t 5 status`.
The control response supplies the running version for Juju. UDP and TCP root SOA
probes on localhost confirm that both DNS listeners respond. The probe accepts
NOERROR, NXDOMAIN and REFUSED, rejects malformed transactions and SERVFAIL, and
never changes records. It checks DNS service availability, not backend pool health
or recursive latency. Each observation also inspects the managed MaxMind file's
metadata and includes its database type and UTC build timestamp in the message.
Invalid metadata is a configuration issue and produces waiting status while
healthy DNS continues. Diagnostic get-info adds its SHA-256. These are installed
file observations because gdnsd has no loaded-database-version query.
Status checks do not attempt a restart or package changes.

## Compressed GeoIP downloads

GeoIP download normalization precedes the existing MaxMind and gdnsd validation.
The original URL remains in per-unit state. Recognized Nextcloud share pages
resolve to their download endpoint, with at most one retry. Raw files, gzip MMDB,
and standard tar/tar.gz packages are normalized into one staging MMDB. Download
and expanded sizes are bounded separately. Tar headers are checked before the
stdlib parser can process extension metadata. No archive path is extracted to
the filesystem; only one selected regular MMDB is copied to a fixed staging path.
Checksum input verifies the transport artifact, while action results distinguish
its hash from the installed MMDB hash. Native validation, atomic application,
and rollback then use the normalized file.

## Configuration transactions

Persistent configuration uses `/etc/gdnsd/config` and `/etc/gdnsd/zones/<zone>`.
Each read supplies a SHA-256 conflict token. A missing zone uses `absent`.
Large reads send base64 in separate 64 KiB action result fields, with a part
count. The CLI reassembles and verifies the digest before writing, avoiding
Linux process-argument limits without changing stored configuration.
Actions hold a local transaction lock, stage the complete configuration tree,
validate with the installed gdnsd, and recheck the selected live file before
atomic replacement. Staging and backups live under `/var/lib/gdnsd-vm`.
Backups are outside the served zones directory. Operators should include this
state directory in normal host backups and manage its retention.

A zone update calls `gdnsdctl reload-zones`; a global configuration update calls
`gdnsdctl replace`, which hands service to a process using the same binary.
These operations can read all zones, but only the selected file is written.
Zone actions use the same validation and transaction checks for every valid
zone name. Global plugin resources and GeoIP databases can affect all served
zones when changed.

Validation failures leave live files unchanged. Application failures restore
the old file and attempt reapplication. Both application and rollback failures
are reported. A successful transaction stores a small last-applied checkpoint.
The checkpoint is not a complete audit log or proof of every record's wire answer.
Transaction locking coordinates charm actions; direct root edits remain outside
that lock. A second checksum check detects edits during validation, but cannot
make unrelated external writers participate in the transaction.

## Service and interfaces

The charm opens UDP and TCP port 53. Juju exposure and parent delegation are
separate operator steps. Local `gdnsdctl` calls use its Unix socket. No TCP
management listener or external configuration API is added. Metrics and logs
are declared over machine-observability, and dashboards use grafana-dashboard.
No storage declaration or automatic zone replication is provided. GeoIP updates use the explicit sync-geoip action; mock-health endpoints remain
externally managed. The per-unit StoredState retains the URL and scoped
configuration errors across hooks and refreshes. Failed configuration requests
produce waiting status until a matching valid candidate resolves the problem.
Healthy status observations do not clear that record.

## Observability

`src/exporter.py` is a stdlib HTTP exporter for the installed daemon's
`gdnsdctl -t 5 -o stats` output. Its subprocess has a six-second timeout, within
the collector's ten-second scrape timeout. It validates native nonnegative
integer counters before exposing any native sample. Collection failure exposes
only `gdnsd_scrape_success 0`, and logs the exception. It never changes DNS state.

`src/observability.py` owns the auxiliary systemd unit and installed exporter
script. The service runs as gdnsd, binds to loopback and reads gdnsd's private
control socket. Install and charm refresh reconcile this service only.
Update-status does not write host files or restart either service.

The vendored v3 library publishes an application-wide declaration. A local
leader guard prevents nonleaders from writing the application databag. Leadership
changes republish the declaration. All units declare the same local endpoint
and journals. The compatible Alloy collector obtains the actual colocated
principal unit from juju-info before considering the payload topology fallback.
This preserves per-unit source labels even for a multiunit application.
The payload contains no alert artifacts. Grafana dashboards use the separate
Grafana relation. Library origins and hashes are recorded in
[vendored libraries](docs/libraries.md).

## SSH host helper

`src/config_manager.py` implements the bounded version 1 JSON protocol documented
in [README.md](README.md#restricted-ssh-configuration-management). Its only local
module dependency is `workload.py`; the installed helper never imports from a
Juju agent directory or requires `maxminddb`. `Workload.install_manager` installs
root-owned mode-644 copies into `/usr/local/lib/gdnsd-vm` and a mode-755 isolated
system-Python launcher in `/usr/local/sbin`. Install and upgrade reconcile the
helper without a workload restart. Credential provisioning is separate.

All selected-file snapshot reads and validate/apply transactions share
`/var/lib/gdnsd-vm/transaction.lock` with existing configuration and GeoIP actions.
The lock covers hash comparisons, complete candidate-tree validation, backups,
activation, rollback and release checkpoint writes. A bridge configuration
supports resource renames across zone reloads. Unselected zones remain intact.
Existing `Workload` path, atomic-write, ownership, activation and lock helpers
are reused without calling the single-file action transaction recursively.

`/var/lib/gdnsd-vm/deployment.json` stores the last successful release and file
hashes, error text and repaired file hashes. `update-status` observes this state.
A successful SSH apply clears old action errors for repaired files whose live
hash still matches. A later failed action invalidates that file's repair marker.
Successful apply actions also clear SSH errors after all affected file scopes
have been repaired. GeoIP and exporter errors retain their existing independent
status behavior.
Backups remain private under `/var/lib/gdnsd-vm/backups`.
