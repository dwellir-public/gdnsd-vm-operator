# Native gdnsd observability

Approved scope on 2026-10-28: native server-wide metrics first, using alloy-sub
and hyperborea:zerosoftwere@external/observability-stack Prometheus/Loki/Grafana.
Production n changes and a production canary are recommendations only.

## Before, target and actual

| Requirement | Authority | Before | Target | Actual |
| --- | --- | --- | --- | --- |
| Usage counters | Task-required | Native gdnsdctl stats only | Local Prometheus endpoint for query rate, responses/errors, uptime and transport counters | Implemented; live local counters verified on all four servers |
| Native scope | Task-required | Counters are server-wide | Clearly identify totals across n/x/probes; no per-zone/client labels | Documented server-wide scope; no zone or client labels |
| Source identity | Required-baseline OBS-001 | No telemetry relation | Runtime model/UUID/app/unit/charm preserved to backend | Principal labels verified in rendered configs and fresh backend samples for all four models |
| Collector contract | Contract-required | No endpoint | Vendored authoritative v3 library and compatible alloy-sub | Reference bytes preserved; Charmhub alloy-sub revision 36 uses identical v3 library |
| Metrics/log pipeline | Task-required | No collector in DNS models | Co-located alloy-sub to selected Prometheus/Loki offers | All four collectors active; fresh Prometheus metrics and Loki logs verified after AWS firewall correction |
| Dashboard | Task-required monitor view | None | Basic query/error/uptime panels and service journal, model/unit filters | Dashboard published through relations; browser and data validation pending backend recovery |
| Refresh preservation | Repository-required | DNS PID/files recorded | Auxiliary exporter only; preserve gdnsd PID, package, config and n/x data | All four before/after snapshots identical for DNS PID, package, config, zones and GeoIP |
| Alerts | Recommended-default disposition | No approved thresholds | Explicit empty artifacts list; no alert rules | Explicit empty artifacts list; no alerts configured |
| Live validation | Required-baseline DEPLOY-001 | Unit/live baseline reports exist | New unit tests, local artifact, Jubilant checks, fresh backend data and dashboard browser | 172 unit tests on Python 3.12/3.13, eight retained-model checks and backend metrics/log verification passed; browser pending |

## Design and implementation plan

- Add a stdlib exporter reading gdnsdctl stats with bounded subprocess timeouts.
  Bind HTTP to 127.0.0.1:9153 only. Export stable counters and uptime; report
  scrape failure without substituting zero-valued native counters or restarting
  DNS. Do not collect query names, client IPs or packets.
- Manage an auxiliary gdnsd-exporter.service and stable installed Python script.
  Reconcile it during install/refresh only; update-status remains observational.
  A stopped gdnsd remains stopped. Ancillary failures are reported separately.
- Publish typed machine-observability v3 sources at 15s intervals, gdnsd and
  exporter journald units, explicit source topology and artifacts=[] using the
  reference bytes pinned below. Exporter endpoint remains private to the
  co-located collector and is never opened through Juju exposure.
- Publish a Grafana dashboard directly through grafana_dashboard. Use dynamic
  datasource/topology variables; show query volume, QPS, response codes,
  transport errors, uptime and journal logs. Do not invent alert thresholds.
- Test parsers, invalid stats, failed command, exporter lifecycle preservation,
  relation topology and dashboard selectors before packing. Deploy first to
  Oregon, then the other authorized test models after the initial pipeline
  succeeds. Use normal x queries to demonstrate counter increments. Verify
  fresh backend series/logs and dashboard data with All and specific selection.
- Document operator queries and caveats. No production n delegation/glue,
  Elastic IP assignment, DNS routing override or workload upgrade is included.

## Provenance

Reference origin: github.com/dwellir-public/dwellir-observability-reference,
commit bc5df52238e35c74681637884065e51709d83bf8. Copy its machine_observability
and Grafana dashboard libraries byte-for-byte and record SHA-256 hashes.
Collector origin: github.com/dwellir-public/alloy-sub-operator, inspected source
5c61cf76efa26d0227b52094774a8cdb9e904cd0, compatible with v3. Backend offers are
zerosoftwere@external/observability-stack.prometheus, .loki and .grafana on
hyperborea. Models and applications are retained.

This is a bounded feature implementation, not a whole-charm compliance audit.

## Validation outcome

Local monitoring was rolled out to all four test models after the Oregon local
checks passed. The original backend-first rollout gate could not be satisfied:
the chosen stack's ingress timed out from the workstation and Oregon, and Juju
could not reach its Kubernetes API. Continuing the auxiliary local rollout
allowed validation without changing DNS routing or touching production n.
No backend configuration or firewall changes were attempted. See the
[validation report](../validation-observability.md) for evidence and pending checks.

Follow-up: the user identified the AWS allowlist requirement. Four TCP 443 /32
rules with controller-qualified source model descriptions restored connectivity.
Fresh metrics and journal markers were verified for all four models. Grafana
browser checks remain pending because the credential action timed out. The
validation report records rule IDs, sources, evidence and rollback scope.
