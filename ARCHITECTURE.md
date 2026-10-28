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
Only the install event may install a missing package. Charm refresh,
config-changed and update-status observe service state. They do not install,
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

## Configuration transactions

Persistent configuration uses `/etc/gdnsd/config` and `/etc/gdnsd/zones/<zone>`.
Each read supplies a SHA-256 conflict token. A missing zone uses `absent`.
Actions hold a local transaction lock, stage the complete configuration tree,
validate with the installed gdnsd, and recheck the selected live file before
atomic replacement. Staging and backups live under `/var/lib/gdnsd-vm`.
Backups are outside the served zones directory. Operators should include this
state directory in normal host backups and manage its retention.

A zone update calls `gdnsdctl reload-zones`; a global configuration update calls
`gdnsdctl replace`, which hands service to a process using the same binary.
These operations can read all zones, but only the selected file is written.
Protected zones and their children cannot be updated through zone actions.
The protection setting does not isolate global plugin resources: an operator
must assess their effect on all zones before changing shared resources.

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
management listener or external HTTP API is added. No telemetry integrations,
blockchain interfaces, storage declarations or automatic zone replication are
provided. GeoIP updates use the explicit sync-geoip action; mock-health endpoints remain
externally managed. The per-unit StoredState retains the URL and scoped
configuration errors across hooks and refreshes. Failed configuration requests
produce waiting status until a matching valid candidate resolves the problem.
Healthy status observations do not clear that record.

For this experiment, n is protected, x is writable, and parent dwellir.com
changes must use the DNSControl repository. A fresh Frankfurt instance receives
only x test data. The existing n test copies stay on US/Tokyo unchanged.
