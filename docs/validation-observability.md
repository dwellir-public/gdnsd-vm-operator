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

## Backend verification after firewall correction

The initial ingress timeout came from missing AWS ingress permissions for the
DNS servers. The stack resolves to 100.31.68.170, EC2 instance
i-09fdba8694a50de1b in us-east-1. Its microk8s security group,
sg-0ebac75aa246c291c, lacked HTTPS access from the four DNS model addresses.
That group currently attaches only to this instance.

Added TCP 443 ingress for each individual public IPv4 address. Each AWS rule
description contains its full controller-qualified Juju model name, followed
by `observability HTTPS`. Existing rules remain unchanged. No Kubernetes API,
workstation or general Internet ingress was added.

| Model suffix | Source CIDR | AWS rule ID |
| --- | --- | --- |
| gdnsd-us | 44.201.71.32/32 | sgr-086e798b27b034e66 |
| gdnsd-ap | 18.181.231.202/32 | sgr-0d832f03418925d71 |
| gdnsd-nl | 63.184.230.135/32 | sgr-0e5059bb7224e8151 |
| gdnsd-us-west | 44.243.1.147/32 | sgr-0e6169609613e7819 |

The model prefix is `hyperborea:erik-lonroth@external/`. HTTPS readiness checks
passed from all four DNS servers. Prometheus now contains four successful
native-scrape series and eight UDP/TCP counter series, with the expected
principal model/UUID/application/unit/charm labels. Freshness was checked with
`time() - timestamp(gdnsd_scrape_success{juju_application="gdnsd-vm"})`.

A request to an intentionally absent exporter path generated a harmless HTTP
404 journal marker on each server. Loki returned the fresh marker from all
four models with the expected source Juju labels. This did not modify or
restart DNS. The selected Prometheus and Loki delivery paths are verified.
Historical samples during the outage may have expired from the bounded buffer.

Dashboard provisioning and browser rendering with both All and specific filters
remain unverified. The Grafana get-admin-password action timed out after 120
seconds. No additional firewall permissions were added for this read attempt.
The existing pending dashboard validation must not be treated as complete.
No alert thresholds or alert artifacts are part of this implementation.

These source IPs are automatically assigned AWS public addresses. Update the
allowlist if any instance address changes. To undo this monitoring access,
revoke only the four rule IDs listed above.

## Evidence

The [evidence directory](evidence/observability) contains regional integration
outputs, before/after snapshots, final status, unit results and a concrete
remote-write error. [The plan](plans/native-observability.md) records the scope,
actual disposition and vendored library origins/checksums.
