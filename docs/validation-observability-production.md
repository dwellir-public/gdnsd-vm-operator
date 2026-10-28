# Production COS monitoring migration

The retained DNS models now send monitoring to
`hyperborea:zerosoftwere@external/cos`. The previous `observability-stack` model
is the separate test instance. This report supersedes the backend and browser
limitations in the [initial monitoring report](validation-observability.md).
Production DNS remains unchanged.

Open [Grafana](https://cos1-eu.monitoring.dwellir.tech/cos-grafana/) or the
[gdnsd dashboard](https://cos1-eu.monitoring.dwellir.tech/cos-grafana/d/826f00b525b56478a185aca83b14018968364ae7/gdnsd-native-server-metrics).
The `get-admin-password` action completed on this stack's grafana/0 unit and
its credentials authenticated successfully. Passwords are not stored here.

## Relations and firewall

All four erik-lonroth@external models on hyperborea, gdnsd-us, gdnsd-ap,
gdnsd-nl and gdnsd-us-west, consume these production offers:

| Local alias | Offer | Local endpoint |
| --- | --- | --- |
| cos-prometheus | zerosoftwere@external/cos.prometheus | alloy-sub:send-remote-write |
| cos-loki | zerosoftwere@external/cos.loki | alloy-sub:send-loki-logs |
| cos-grafana | zerosoftwere@external/cos.grafana | gdnsd-vm:grafana-dashboard |

Old test-stack monitoring relations and consumed aliases were removed. The old
Grafana reference remained dying because its provider reported a failed hook.
Only that stale consumed reference was force-removed where necessary. No stack
application, offer, machine or other consumer was removed.

The production ingress resolves to 56.228.58.5 on EC2 i-05b1f7f64a83d931f in
eu-north-1. Added TCP 443 rules to its microk8s group sg-0a0ad3d605efad1ee:

| Source | CIDR | Rule ID |
| --- | --- | --- |
| gdnsd-us | 44.201.71.32/32 | sgr-0195a4f8ee4740a7a |
| gdnsd-ap | 18.181.231.202/32 | sgr-06c5f28622b280495 |
| gdnsd-nl | 63.184.230.135/32 | sgr-0c5e305e675bfe899 |
| gdnsd-us-west | 44.243.1.147/32 | sgr-0cc6491f0bb1d5eb1 |
| Erik laptop | 185.127.230.198/32 | sgr-0fcbbc28646b72a6e |

Each server rule describes its full controller-qualified source Juju model.
The laptop rule describes its Grafana purpose. Existing permissions remain
unchanged. No Kubernetes API or general Internet access was added. Retired only
the five rules previously added for this setup on the old test-stack host.
If a source public IP changes, update its rule. Rollback of new access can use
the five rule IDs above without touching other entries.

## Validation

- HTTPS readiness passed from all four DNS regions. Grafana health and the login
  page responded successfully from the laptop's current public IP.
- Prometheus returned four successful gdnsd collection series and eight TCP/UDP
  request-counter series with correct principal Juju labels. Sample ages in the
  recorded check were below 17 seconds.
- Loki returned a fresh exporter validation marker from every model with the
  correct source identity. The marker was a harmless request to an absent local
  exporter URL. No DNS record or daemon restart was needed.
- Chromium rendered the dashboard with default All filters and with the
  Frankfurt model/application/unit selected. Both views returned data in all
  seven panels, with no query errors or No data panels. The specific view
  displayed only Frankfurt series and journal entries.
- The first All-filter check exposed an overly broad log query on the shared
  stack. A fixed `juju_charm="gdnsd-vm"` matcher now restricts the log panel to
  gdnsd while retaining the model/UUID/application/unit selectors. A regression
  test failed before this fix, then passed. A read-only review confirmed the
  matcher agrees with the selected collector's source labels.
- Lint and all 173 unit tests passed. The updated local charm was packed and
  refreshed on all four DNS test applications. The workload runtime is
  unchanged; the source change is the dashboard selector.
- Before/after DNS snapshots match for PID, installed gdnsd package version,
  global configuration, both test zone files and GeoIP database checksum.
  Production n delegation, glue, source configuration and authorities were not
  modified. No workload upgrade occurred.

The updated local artifact SHA-256 is
`76694aaca8245f82c9c2573e03a3fc20bd7a6f46d54d984e2e7c6e79d0de33fb`.
Native counters remain server-wide and include both zones, tests and health
probes. No zone-specific usage, per-query logs or alert thresholds were added.

[Bundled evidence](evidence/observability-production) contains the firewall
changes, backend query results, browser results/screenshots, unit output and
retained-model snapshots. Historical data in the old test stack was retained;
the production backend starts its own monitoring history for these sources.
