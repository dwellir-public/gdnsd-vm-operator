# Compressed GeoIP download support

Scope: accept the operator's Nextcloud public share, bounded gzip and tar.gz
GeoIP downloads, then validate and install on the retained Frankfurt test unit.
The US/Tokyo shared database and n remain unchanged. No workload upgrades or
source builds are involved.

| Requirement and source | Authority / applicability | Before | Disposition | Target | Actual |
| --- | --- | --- | --- | --- | --- |
| Compressed database URL, user request | Task-required, sync-geoip | GAP: raw MMDB only; supplied share returns HTML | Implement bounded decoding and Nextcloud download resolution | Valid City DB installed from supplied link | PASS: supplied tar.gz installed on Frankfurt; original share URL reused. |
| Safe selected-file extraction, user n protection | Task-required, existing workflow | Raw download limit and protected-zone guard pass | Preserve guards; extract one regular MMDB without filesystem archive extraction | Reject links, traversal, ambiguous DBs, truncated or oversized input | PASS: 156 unit tests including archive bounds, metadata and unsafe entries. |
| Native validation and rollback, existing contract | Task-required, configuration flow | PASS for raw DB | Preserve transaction; checksum and validate extracted file before activation | Invalid input keeps active DB; changed file reloads through ctl | PASS: download/MMDB checksums, native activation and three ECS answers verified. |
| DEPLOY-001, generic skill | Required-baseline, bounded deployment | Prior local artifact passes | Pack local artifact and exercise retained nonproduction Frankfurt model | Live compressed update, metadata and URL reuse pass | PASS: local artifact deployed; operator-download integration and six regional refresh/health checks passed. |
| UPGRADE-001, generic skill | Required-baseline, charm refresh | PID/files preserved | Preserve refresh; no workload upgrades | Refresh changes charm only | PASS: all three refresh checks preserved workload/configuration/package; US/Tokyo data unchanged. |

- [x] Add failing tests for raw/gzip/tar.gz normalization, limits and invalid archives.
- [x] Implement normalization before existing metadata/native validation.
- [x] Resolve Nextcloud share pages to their download endpoint without losing the remembered URL.
- [x] Document compression and checksum semantics; run lint/unit checks and review.
- [x] Pack, refresh Frankfurt, install from the supplied share URL, and verify metadata, native health and repeated omitted-URL sync.
- [x] Record artifact/source identity, protected hashes and actual results; retain all infrastructure.

Detailed results: [compressed upgrade validation](../../validation-compressed-geoip.md).
