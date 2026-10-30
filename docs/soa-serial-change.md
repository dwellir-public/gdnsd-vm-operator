# SOA serial observation change record

Bounded scope: per-zone SOA visibility on charm revision
`7cf8fe9264b86444745d0a6dc3db9098aca07453`, assessed 2026-10-30.
The operator approved implementation, edge publication and refresh. This record
covers the touched capability; unrelated full-charm compliance is unassessed.

| Requirement and source | Authority / applicability | Before | Disposition | Projected target | Verification |
| --- | --- | --- | --- | --- | --- |
| Served and configured serials per zone, approved feature | task-required / capability | GAP: no per-zone serial observation | Add read-only zone inspection and live authoritative localhost SOA query | PASS | Parser and DNS response tests, mismatch/error tests, live local unit |
| Status and get-info, approved feature | task-required / capability | Status reports running state and GeoIP; action has no SOA data | Append compact SOA summary; expose full per-zone JSON diagnostics | PASS | ops.testing hooks/action results, real get-info |
| Preserve error priority and runtime state, existing charm | task-required / preservation | Existing service/config/GeoIP/SSH failure states | Preserve current priority, add recoverable serial-observation waiting state | PASS | Error priority and recovery regression tests |
| Module boundaries and Python dependency ownership, [AGENTS.md](../AGENTS.md) and BUILD-001 | repository-required and required-baseline / packaging | Workload and Juju code separated; tracked lock and uv plugin present | Keep DNS parsing in its own module; add pure-Python DNS dependency to pyproject and lock | PASS | uv lint/unit checks, package build and installed helper regression |
| No workload upgrade or source compilation, [AGENTS.md](../AGENTS.md) | repository-required / preservation | Existing package-preserving refresh | Keep package, DNS files, GeoIP and process unchanged | PASS | Built-artifact refresh and deployment readback |
| Built artifact and explicit retained nonproduction model, DEPLOY-001 and [AGENTS.md](../AGENTS.md) | required-baseline and repository-required / integration | Existing pytest/Jubilant explicit-model guards | Test user-visible SOA action/status on retained local artifact, then verify authorized production refresh | PASS | pytest/Jubilant; no production fault injection |

No additional operator decisions are needed. Publication is limited to latest/edge.
The helper's existing isolated system-Python SSH protocol remains supported;
SOA observation must not introduce top-level third-party imports into its modules.

## Implementation contract

Discover locally managed zones without hard-coded domain names. Inspect zone files
safely and compare their SOA serials with authoritative, nonrecursive SOA answers
from 127.0.0.1. Use bounded query and overall inspection timeouts. Unknown values
and invalid responses must be diagnostic errors, never a synchronized result.
A fresh installation with no zones remains healthy.

The internal health result adds `zone-serials`, `zone-serials-ok`,
`zone-serials-summary` and `zone-serials-error`. Each zone has `in-sync`, known
integer `configured-serial` / `served-serial` fields and an `error` when needed.
Missing values are omitted, not represented as a zero serial. Serial zero is a
valid observed value.

The public get-info action exposes the map as `zone-serials-json`, preserving
literal domain names across Juju's flattened action-result keys. It also reports
`zone-serials-ok`, `zone-serials-summary` and an error summary. The status message
includes a bounded summary, with full details available in the action. SOA
inspection errors use waiting status when the daemon itself is healthy, while
existing service and configuration errors keep priority. Read-only hooks never
reload, restart or rewrite configuration.

## Execution plan

1. Add zone-file/live-SOA inspection with focused red/green parser/network tests.
2. Wire it into workload health/info and charm status, with hook/action tests.
3. Update README, architecture and development guidance using generic examples.
4. Run lint/unit, build the amd64 Ubuntu 24.04 artifact, then test refresh and SOA
   visibility using an explicitly selected retained nonproduction model.
5. Review, merge, publish via the existing edge release action and refresh the
   authorized units sequentially. Verify package/files/GeoIP/PID preservation and
   actual served/configured serial agreement. Record actual evidence below.

## Actual results

Implementation passed lint and all 261 unit tests. Specification and code reviews
found no remaining blockers after fixes for deadline enforcement, native zone
filename conventions, status priority and test-zone ownership.

The amd64 Ubuntu 24.04 artifact built successfully with SHA-256
`6baf9b54f56797ec656f094c08bfe3e73de4b76f194a57169dc76ec9552f2e5a`.
Three built-artifact integration checks passed on the explicitly selected retained
nonproduction unit: refresh preserves files/package/PID, reported serials match
local authoritative DNS, and a test-owned invalid candidate produces Waiting
before repair restores Active. The first run exposed Juju's string boolean
serialization in the test helper; normalizing that value fixed the assertions.
No caller-owned zone was altered by the mismatch test.

The touched requirements above are verified locally. Edge publication and
sequential fleet refresh are the remaining deployment checks; their source,
revision and preservation evidence will be recorded on the release PR.
