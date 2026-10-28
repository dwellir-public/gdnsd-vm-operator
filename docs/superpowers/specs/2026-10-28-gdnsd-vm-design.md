# gdnsd-vm charm design

Review status: approved by the user on 2026-10-28, after documenting the configuration workflow in README.md.
Evidence checked on 2026-10-28.

## Scope

Create an independent Git repository named `gdnsd-vm-operator` in the user-selected
registry location. The Juju charm name is `gdnsd-vm`, for a generic authoritative
DNS service on Ubuntu 24.04 amd64. Use the installed dwellir-charm-development
skill and the registry's AGENTS.md. Do not apply blockchain-specific contracts.

Deploy to the retained test models on controller `hyperborea`:

- `erik-lonroth@external/gdnsd-us`, existing machine 0, AWS us-east-1.
- `erik-lonroth@external/gdnsd-ap`, existing machine 0, AWS ap-northeast-1.
- New model `gdnsd-nl`, AWS eu-central-1, Frankfurt. The user replaced Amsterdam
  with the closest region to Frankfurt; eu-central-1 is Frankfurt itself.

Preserve the existing x experiment, including its fixed 60-second dynamic
answer TTLs. Do not modify production n, its delegation, its Aloha configuration,
or the existing n zone files on the test machines. All parent dwellir.com DNS
changes must use its separate DNSControl repository. Adding a third authority
to the public delegation is separate work; deploying a server does not add it
to public DNS automatically.

## Installation source: prebuilt packages only

Use Ubuntu's official, signed APT repositories. Check that gdnsd is available
from the configured repositories and report how to enable `universe` if it is absent. No source
compilation, source-build helper, source-based CI build, or source archive fallback
is part of this charm. The earlier proposed source-to-binary packaging script is
cancelled. The charm artifact itself still needs normal charm packaging.

Both existing machines currently have `gdnsd 3.8.2-1build2`, which is also their
APT candidate from Ubuntu noble/universe. Both packages are held. Preserve that
version during initial adoption. The official Noble package is documented at
[Ubuntu's gdnsd package page](https://packages.ubuntu.com/noble/net/gdnsd).

The upstream v3.8.3 GitHub release provides a source tarball and its signature,
not a precompiled binary release. The example Codeberg archive is also source.
Neither is an installation source for this charm.

Install gdnsd from Ubuntu's signed repositories only when it is absent. On an
existing installation, adopt the installed package without reinstalling or
changing its version. Preserve the existing package holds. Do not add repositories
for another Ubuntu release or Debian.

Workload upgrades are outside this charm's scope. Do not implement an
`upgrade-workload` action, a `package-version` option, binary download options,
package upgrade/downgrade automation, or package rollback. Charm refresh must
preserve the installed gdnsd version and must not restart or replace the daemon.
Package maintenance remains an external operator responsibility. Configuration
application and its rollback remain in scope and do not change the workload
binary or package version.

## Configuration and zone operations

Keep orchestration and Juju status in `src/charm.py`. Separate configuration
transactions, first-install package handling and systemd control into testable helpers.
Preserve upstream `/etc/gdnsd/config`, `/etc/gdnsd/zones/` and `gdnsd.service`.
Adopt valid existing configuration without replacing it on install or refresh.
A fresh instance binds localhost and its Juju private address, avoiding the
systemd-resolved and Juju dnsmasq wildcard conflicts seen in this experiment.

Provide separate read, validate and apply actions for the global plugin
configuration and for an explicitly named zone. Zone operations only replace
that zone's file; they never delete or rewrite other zones. Global plugin
configuration updates are explicit operations because resources may be shared
between zones. Preserve existing geographic databases and references.

Provide `scripts/pull_gdnsd_config.py` and `scripts/push_gdnsd_config.py` in the
charm repository. Require explicit controller-qualified model and unit. Require
`--zone` for zone operations and a distinct explicit global-config mode. Use
Juju authentication rather than the old Aloha password constants. Pull saves
editable content plus its original SHA-256 and target identity. Push rejects
concurrent remote changes and mismatched target identities. Support validation
without applying. Multi-unit pushes validate every selected unit before applying
sequentially and report any partial success rather than claiming fleet atomicity.

Stage a complete candidate configuration tree outside the served directory,
including unchanged supporting files, then run the installed gdnsd's own
`checkconf` validator. Invalid candidates must not reach the live files or
trigger a reload. Lock application transactions, recheck the original digest,
back up the selected live file, replace it atomically, and use the appropriate
gdnsd control command. For zone-file changes, call `gdnsdctl reload-zones`.
For global plugin/server configuration changes, call `gdnsdctl replace` using
the same installed binary. This is a configuration handoff to a new process,
not a workload version change. Verify success and restore
the previous configuration on application failure. Record applied digests only
after successful application. Reject unsafe zone names and candidate paths.

The installed gdnsd 3.8.2 has a control protocol, exposed by `gdnsdctl` over
its local Unix socket and optionally TCP. It is a command interface for reload,
process replacement, inspection and temporary ACME challenges, not a general
CRUD API for persistent records or server/plugin configuration. Persistent
configuration changes use files. Use the local socket through Juju actions;
do not enable a remote TCP control listener. The upstream TCP control protocol
has no authentication or encryption.

Prefer explicit synchronous reloads to relying on filesystem change detection:
`reload-zones` confirms that zone data is visible to clients, and `replace`
confirms the configuration handoff. Validate the staged tree with
`gdnsd -c <candidate-directory> checkconf` before committing live files. Preserve
other zones byte-for-byte. Reloading zones does not reconfigure plugin resources;
changes to the global plugin configuration require the configuration handoff.

Use gdnsd's native validation as the acceptance gate. DNSControl can generate
ordinary RFC 1035 zones, but its BIND provider does not validate gdnsd's global
plugin configuration or implement gdnsd's dynamic resources. It remains the
required tool for the separate parent DNS repository.

Expose service control and operator diagnostics as generic operator conveniences.
Do not add blockchain metadata, chain configuration or blockchain libraries.
Telemetry integration and alert delivery are deferred, with no observability
compliance claim for unimplemented interfaces.

## Deployment and verification

Before adoption, capture read-only configuration hashes and service state.
Deploy the built local charm first on US machine 0, check the current x answers,
then repeat on Tokyo. Preserve mock-health service ownership outside this charm.
Use the new Frankfurt machine for fresh-install and configuration checks.
Copy only the intended x configuration and its required supporting resources
to the fresh machine; never deploy the n zone there. Retain all models.

Use pytest with ops.testing for event tests, direct helper tests for failed
validation, conflict detection, per-zone isolation, configuration rollback,
first-install-only package handling and refresh without restart. Use
pytest/Jubilant and a built local charm for nonproduction integration checks.
Accept controller-qualified model input and never destroy caller-owned models.
Cover valid/invalid zone updates, round-trip push/pull, service state, restart-free
charm refresh, and unchanged installed package version. There are no workload
upgrade tests because workload upgrades are explicitly excluded.

Use pyproject.toml, a tracked uv.lock, the charmcraft uv plugin, consolidated
charmcraft.yaml definitions, explicit ops runtime and ops[testing] dependencies,
and tox format/lint/unit/integration entrypoints. Include README.md,
ARCHITECTURE.md, DEVELOPING.md and repository AGENTS.md.

## Local health and workload version

The user added this requirement during implementation. Every update-status
checks the local systemd state, queries gdnsd's local control socket, and probes
UDP and TCP DNS on localhost. The root SOA probe accepts NOERROR, NXDOMAIN or
REFUSED as a valid response from an authoritative-only server. It never queries
or modifies the production n zone. Report `running` when these checks pass;
otherwise report the observed service state or the failed check. Publish the
running daemon's version in Juju's workload version field. Checks do not restart
the service or modify configuration. Package version remains a separate diagnostic.

## Before and target compliance matrix

The target directory is empty. Projected PASS means planned, not verified.

| Requirement and source | Authority and applicability | Before | Disposition | Target | Actual evidence |
| --- | --- | --- | --- | --- | --- |
| BUILD-001, skill generic standards | Required-baseline, shared | GAP, no charm | Implement consolidated definitions, uv lock and dependencies | PASS after pack and dependency checks | Not executed |
| UPGRADE-001, skill Juju concepts | Required-baseline, shared | GAP, no charm | Preserve installed version and running process during charm refresh | PASS after PID/config preservation checks | Not executed |
| DEPLOY-001, skill generic testing | Required-baseline, shared | GAP, no charm | Built artifact plus pytest/Jubilant behavior checks | PASS for tested Ubuntu 24.04 amd64 only | Not executed |
| Generic workload, requested name and repository | Task-required, this charm | GAP | Create independent repository and gdnsd-vm charm | PASS after metadata and Git checks | Not executed |
| Prebuilt repository installation only | Task-required, latest user instruction | Existing apt package verified | Install only if absent; preserve existing package and holds; no source path | PASS after package/source tests | Installed and candidate 3.8.2-1build2 verified on both existing VMs |
| Per-zone push/pull, validation, rollback | Task-required, this charm | GAP | Implement actions and Juju-based scripts | PASS after invalid-update and isolation checks | Not executed |
| Protect n and preserve x TTL 60 | Task-required, all deployment steps | Existing experiment | Preserve and compare hashes/wire answers | PASS after deployment evidence | No deployment yet |
| Three regional deployments | Task-required, retained test models | US and Tokyo machine-only | Adopt both and add Frankfurt gdnsd-nl | PASS after live checks | Not executed |
| CONFIG-001, OPS-001/002/003, METADATA-001 | Required-baseline with blockchain applicability | N/A, generic DNS workload | No blockchain contracts; generic package/service helpers still planned | N/A with applicability reason preserved | Generic scope confirmed |
| OBS-001, skill topology | Required-baseline when publishing telemetry | N/A, no telemetry integration requested | Defer telemetry interface and backend validation | No telemetry compliance claim | Not assessed |
| CONFIG-002/003, skill blockchain configuration | Recommended-default with blockchain applicability | N/A, generic DNS workload | Adopt only useful digest tracking as a design choice | No blockchain configuration compliance claim | Not executed |
| Local health and running workload version | Task-required, added during implementation | PARTIAL, systemd observation only | Add local control and UDP/TCP checks on every update-status and publish running version | PASS after event tests and live status checks | Not executed |
| Module boundaries and operator documentation | Recommended-default, shared | GAP | Testable helper modules and current docs | PASS after lint, tests and link review | Not executed |

## Design choices and sources

Native gdnsd validation and signed Ubuntu repository packages are the recommended
approach. DNSControl-only management cannot represent the complete dynamic gdnsd
configuration. Upstream source release installation conflicts with the user's
explicit prohibition and is excluded.

- [Dwellir charm skill](https://github.com/dwellir-public/agent-skills/tree/main/skills/dwellir-charm-development)
- [gdnsd upstream website](https://gdnsd.org/)
- [Upstream v3.8.3 release](https://github.com/gdnsd/gdnsd/releases/tag/v3.8.3)
- [Ubuntu Noble binary package](https://packages.ubuntu.com/noble/net/gdnsd)
- [gdnsd 3.8.2 control commands](https://github.com/gdnsd/gdnsd/blob/v3.8.2/docs/gdnsdctl.podin)
- [gdnsd 3.8.2 control protocol](https://github.com/gdnsd/gdnsd/blob/v3.8.2/docs/ControlSocket.md)
- [DNSControl BIND provider](https://docs.dnscontrol.org/provider/bind)

Implementation and deployment evidence is recorded separately in the implementation plan and validation report.
