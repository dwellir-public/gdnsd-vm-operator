# Validation record

Verified on 2026-10-28. Runtime source revision is
`d358caa4a729790017ebe11328122f556f653b9a` on branch `feat/initial-charm`.
The Git repository and charm artifact are local. Nothing was published to GitHub
or Charmhub. Documentation/evidence commits after this revision do not change
the deployed Python payload.

## Artifact and dependencies

- Artifact: `gdnsd-vm_ubuntu@24.04-amd64.charm`.
- SHA-256: `34956e2965470472401b72ff032c52abeef317aa4b902fa4c33aa235a0f8a2ec`.
- Built in a Charmcraft LXD Ubuntu 24.04 environment using the uv plugin.
- Tested deployment base/architecture: Ubuntu 24.04 amd64 only.
- Running workload: gdnsd 3.8.2, Ubuntu package `3.8.2-1build2`.
- Python dependencies are tracked in `uv.lock`, including ops and maxminddb.
- The workload comes from Ubuntu's signed prebuilt packages. No gdnsd source
  compilation or workload-upgrade feature was implemented.

The ZIP payload and each deployed unit's three Python modules were checked
against the recorded source revision. See [artifact identity](evidence/artifact.json),
[build log](evidence/build.txt) and deployed [US](evidence/source-us.txt),
[Tokyo](evidence/source-ap.txt) and [Frankfurt](evidence/source-nl.txt) hashes.

## Retained deployments

All applications are `gdnsd-vm`, unit `gdnsd-vm/0`, on machine `0` of controller
`hyperborea`. All three report workload version `3.8.2`, active status, DNS ports
53/UDP and 53/TCP, and a running message with installed GeoIP metadata.

| Model | Region | Public address | Installed database build |
| --- | --- | --- | --- |
| `erik-lonroth@external/gdnsd-us` | AWS us-east-1 | `44.201.71.32` | GeoLite2-City, 2025-05-23T11:21:08Z |
| `erik-lonroth@external/gdnsd-ap` | AWS ap-northeast-1 | `18.181.231.202` | GeoLite2-City, 2025-05-23T11:21:08Z |
| `erik-lonroth@external/gdnsd-nl` | AWS eu-central-1, Frankfurt | `63.184.230.135` | GeoIP2-City test fixture, 2026-10-22T18:52:57Z |

Frankfurt's model name retains the requested `gdnsd-nl` spelling. Its location
is Frankfurt, following the user's replacement of Amsterdam. Its public address
is automatically assigned, and all models/machines are retained. Frankfurt is
available for direct tests; it has not been added to the public x delegation.
No parent DNS change was made by this charm work.

[Deployment and get-info snapshots](evidence/deployments.json) record the final
unit status, messages, package version and installed database metadata.

## Checks and outcomes

| Check | Actual result | Evidence |
| --- | --- | --- |
| Ruff lint and format | PASS | [Unit/tooling log](evidence/unit.txt) |
| Unit tests, Python 3.13 | 138 PASS | [Unit log](evidence/unit.txt) |
| Unit tests, target runtime Python 3.12 | 138 PASS | [Python 3.12 log](evidence/unit-python312.txt) |
| Core US integration suite | 4 PASS, 3 intentional skips | [US suite](evidence/integration-us.txt) |
| Core Tokyo integration suite | 4 PASS, 3 intentional skips | [Tokyo suite](evidence/integration-ap.txt) |
| Core Frankfurt integration suite | 7 PASS | [Frankfurt suite](evidence/integration-nl.txt) |
| Latest installed-version metadata checks, US/Tokyo | 2 PASS per model, protected-model tests skipped | [US metadata](evidence/metadata-us.txt), [Tokyo metadata](evidence/metadata-ap.txt) |
| Latest metadata, recovery and GeoIP checks, Frankfurt | 4 PASS | [Frankfurt metadata](evidence/metadata-nl.txt) |
| Existing x controlled routing checks | 27 PASS per US/Tokyo server, all mocks restored healthy | [US routing](evidence/routing-us.jsonl), [Tokyo routing](evidence/routing-ap.jsonl) |
| Public static answers, UDP and TCP | PASS at all three addresses, TTL 60 | [Wire checks](evidence/wire-checks.json) |
| Skill audit-rule source/catalog inventory | PASS, not a substitute for charm tests | [Rule inventory](evidence/skill-rule-inventory.txt) |

Core/GeoIP regional suites completed against the preceding runtime revision
`9afb81c78f8209361acbd8c840caa306a5587362`. The installed-version change then
received fresh unit tests and targeted live refresh, metadata, recovery and
GeoIP checks against the recorded final payload. Configuration transaction and
GeoIP replacement behavior were preserved by that change.

Live tests cover selected-zone read/validate/apply, rejection of invalid input
and stale checksums, command-line pull/edit/validate/push, configuration handoff,
workload-preserving charm refresh, local UDP/TCP health and stopped-service
recovery on Frankfurt. GeoIP tests verify checksums, activation of changed data,
no reload for identical data, remembered URLs, replacing the remembered URL even
on failure, omitted-URL retries, waiting/logging on invalid downloads, preservation
of the previous file, recovery and URL persistence across refresh.

Global configuration changes, service-stop tests and database replacement tests
were intentionally restricted to Frankfurt, where no n zone exists. They were
not silently counted as passing on US/Tokyo. Multi-unit sequencing and rollback
failure handling have unit coverage; no multi-unit same-model live rollout or
real failed-handoff injection was performed.

## Protection and corrected first-adoption failure

The US/Tokyo n-zone hash remains:

`37f0d33b61da19ee0a78bcc838436b0a3d3a57c965b02b79c1e03b8b725f9ed8`

Their shared MaxMind database hash remains:

`bc6e2e504b3a59164dae621e3c4d1f807809f820ab986508b210107b45eb407c`

Their global configuration hashes match the initial snapshots. All four parent
authorities still advertise the original n delegation and glue. Production n,
Aloha configuration and parent DNS were not changed. See the before/after
[US](evidence/before-us.txt), [US final](evidence/after-us.txt),
[Tokyo](evidence/before-ap.txt), [Tokyo final](evidence/after-ap.txt) snapshots
and [parent read checks](evidence/wire-checks.json).

The first US adoption test exposed a held-package detection bug. dpkg reports
`hold ok installed`, and the initial implementation only recognized
`install ok installed`. This caused one restart of the US test service, without
changing its package version, global configuration or n-zone file. The detector
was corrected before Tokyo adoption and received a regression test. Tokyo
retained its original PID `20227` through adoption and all charm refreshes.
The US PID remained `136556` after the correction. The final code's adoption
and refresh paths do not restart existing workloads.

## Compliance and remaining scope

The [before/target/actual matrix](superpowers/specs/2026-10-28-gdnsd-vm-design.md#before-and-target-compliance-matrix)
preserves the original observations and planned targets. Applicable generic
required-baseline packaging, artifact/deployment and refresh checks passed.
Blockchain contracts are not applicable to this generic DNS workload. No
telemetry interface or backend identity compliance is claimed. The skill source
is identified by [file digests](evidence/skill-digests.json).

Frankfurt's GeoIP file is the pinned official MaxMind test fixture, with
[provenance](../tests/data/provenance.json) and accompanying upstream license
files. It is not a production geolocation database. Supply an authorized raw
City/Country database URL for real data. The sync action refuses shared-database
replacement where a protected zone or its descendant exists.

Version messages describe installed-file metadata. gdnsd has no separate
loaded-database-version query. Successful sync validates and reloads the file;
external writes and alternate database paths are outside that observation.
The managed database path is `/etc/gdnsd/geoip/GeoLite2-City.mmdb`, and local
health expects DNS on `127.0.0.1:53`.

Full source n-to-x migration, regional client-latency benchmarking, production
cutover, telemetry integration, capacity and other bases/architectures remain
outside this charm delivery. No regional latency improvement is claimed.

## Manual checks

```bash
juju status --model hyperborea:erik-lonroth@external/gdnsd-us
juju run --model hyperborea:erik-lonroth@external/gdnsd-nl gdnsd-vm/0 get-info
# On the isolated Frankfurt test instance, retry its remembered GeoIP URL.
juju run --model hyperborea:erik-lonroth@external/gdnsd-nl gdnsd-vm/0 sync-geoip
```

Use [README](../README.md) for zone/global push and pull commands and database
updates. Use [DEVELOPING](../DEVELOPING.md) for explicit retained-model test inputs.
