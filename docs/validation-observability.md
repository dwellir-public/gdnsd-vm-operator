# Native monitoring validation

Recorded on 2026-10-28. This report covers the monitoring change only.
Production n delegation, glue, source configuration and real authorities were
not changed. No stable addresses or production canary were created.

The local charm artifact SHA-256 was
`1b5679ac23438cf8160bba5ac24b1a74a597d5df46ffeb1750e9dc1a6087b10d`.
The previous artifact was retained locally for rollback, with SHA-256
`4f6626da0b134c55f80ffe3c6389051a4122fc2bbcd90fa3b7cae727b8f26d73`.
The exporter runtime code is the same on all four servers. Charm model revisions
are local revision numbers and therefore differ across models.

## Checks completed

- `uv run tox -e lint,unit` passed, with 172 unit tests on Python 3.13.
- `uv run --python 3.12 --group unit pytest -q tests/unit` passed all 172 tests.
- `charmcraft pack --use-lxd` produced the deployed local artifact.
- A read-only review found a nonleader application-databag write bug. A local
  leader guard and leader-elected refresh fixed it. The reviewer confirmed that
  the selected collector prioritizes the actual principal unit from juju-info.
  The regression test covers nonleader publication without host operations.
- Eight retained-model pytest/Jubilant checks passed, two in each model. They
  verified workload health/version, a successful native stats scrape, localhost
  binding and an increased UDP counter after a successful x SOA query.
- Before/after snapshots match for each DNS PID, installed gdnsd package version,
  global configuration, n and x test zone files and GeoIP database checksum.
- All four gdnsd, exporter and Alloy services are active. Rendered Alloy configs
  scrape localhost every 15 seconds, collect gdnsd/exporter journals and carry
  the principal model, model UUID, application, unit and charm labels.
- All four models consume the selected Prometheus, Loki and Grafana offers and
  have metrics, logging and dashboard relations configured. Alloy revision 36
  from Charmhub has the same machine-observability library hash as the pinned
  reference. No gdnsd package build or upgrade occurred.

| Region | Model | Local charm revision | DNS PID preserved | Local checks |
| --- | --- | --- | --- | --- |
| Oregon | gdnsd-us-west | 2 | 23238 | 2 passed |
| US East | gdnsd-us | 9 | 157898 | 2 passed |
| Tokyo | gdnsd-ap | 9 | 157996 | 2 passed |
| Frankfurt | gdnsd-nl | 12 | 30840 | 2 passed |

Every model is owned by erik-lonroth@external on hyperborea. Models, applications
and machines were retained. The tests did not modify either served zone.

## Backend checks pending

The selected stack is hyperborea:zerosoftwere@external/observability-stack.
Its ingress `https://cos-test.monitoring.dwellir.tech` timed out from both the
workstation and Oregon. Juju SSH to Prometheus also failed to reach the stack's
Kubernetes API at 100.31.68.170:16443. These observations establish a connectivity
failure, not its root cause. Cached Juju application status still says active.

Oregon Alloy logged a remote-write request failure with `context deadline
exceeded`. Local scraping and valid Alloy configuration do not prove remote
storage. The following remain unverified:

- Fresh Prometheus series with the expected source Juju labels.
- Fresh Loki journal entries with the same source identity.
- Dashboard provisioning and browser rendering, both default All and specific
  model/unit selections, with data and no panel errors.

Restore reachability of the selected stack, then check recent metrics and logs,
open the dashboard with both filter modes and record the results. Alloy has a
bounded buffer; historical samples from the outage may expire. Generate new
queries after recovery rather than assuming the outage period will backfill.
No alert thresholds or alert artifacts are part of this implementation.

## Evidence

The [evidence directory](evidence/observability) contains regional integration
outputs, before/after snapshots, final status, unit results and a concrete
remote-write error. [The plan](plans/native-observability.md) records the scope,
actual disposition and vendored library origins/checksums.
