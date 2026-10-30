# SSH configuration helper change record

Scope: the version 1 SSH configuration capability on charm base commit
`5f7258cf2922140ec189fa281f3ddf36f0d3b4d3`, assessed 2026-10-30. The approved
CLO-368 design supplies the task contract. This is a bounded change, not a full
charm compliance audit. No package upgrade, source build, account provisioning
or credential management is included.

| Requirement and source | Authority / applicability | Before | Disposition | Projected target | Actual evidence |
| --- | --- | --- | --- | --- | --- |
| Direct SSH JSON protocol, approved CLO-368 contract | task-required / capability | GAP, scripts use Juju | Implement `config_manager.py` | PASS for bounded local implementation | Protocol, strict payload/name/hash bounds and system-Python tests pass |
| Transaction lock, candidate validation, ownership, backups and rollback, existing `workload.py` | task-required / preservation | Present for single files | Reuse native helpers for multi-file SSH transaction | PASS locally; live validation conditional | Conflict, bridge-stage, invalid-native, rollback, partial-rollback and lock-held tests pass |
| Observe external deployment errors and repaired action errors, approved contract | task-required / capability | PARTIAL, only charm-local action errors | Add shared deployment checkpoint and status integration | PASS locally | SSH failure/repair and later action-error invalidation tests pass; GeoIP tests remain passing |
| Root-owned stable stdlib helper, approved contract | task-required / capability | GAP | Install copied source and isolated system-Python launcher on install/upgrade | PASS locally; real ownership conditional | Installation test checks requested uid/gid/modes; isolated system Python with site packages disabled imports helper |
| Module boundaries and `uv run tox -e lint,unit`, [AGENTS.md](../AGENTS.md) | repository-required / repository | Existing workload/charm split | Preserve split and dependency lock | PASS | Full lint and unit suite passes, 215 tests; dependency definitions unchanged |
| Preserve package/configuration/GeoIP/PID on refresh, [AGENTS.md](../AGENTS.md) | repository-required / preservation | Existing constraint | Add helper files without DNS activation on refresh | Static/local PASS; live UNKNOWN | Refresh event tests make no workload install/update/service calls; retained-model preservation check belongs to integration owner |
| Explicit retained nonproduction target, [AGENTS.md](../AGENTS.md) | repository-required / integration | Integration target not inspected in this implementation task | Defer live execution to parent operator task | Conditional | No live commands executed by this implementation agent |
| Built local artifact with pytest/Jubilant, skill DEPLOY-001 | required-baseline / integration | UNKNOWN for new capability | Defer build/live verification to parent operator task | Conditional | Not executed here; unit tests do not establish native daemon or SSH behavior |

Implementation improves the SSH management interface and retains existing actions
and relations. Live artifact, restricted SSH, refresh preservation and release
checks remain assigned to the operator task. Unrelated baseline areas were not
assessed. No additional decisions are needed for these dispositions.

Local verification: `uv run tox -e lint,unit` passed with 215 unit tests. TDD red
runs first established the missing helper, missing charm lifecycle/status wiring,
missing checkpoint aliases and failure handling. Follow-up tests cover request
boundaries and existing transaction protections. `git diff --check` is the final
whitespace check. No credentials or host state were modified.

Manual verification is described in [DEVELOPING.md](../DEVELOPING.md#ssh-helper-verification).
The full protocol is in [README.md](../README.md#restricted-ssh-configuration-management).


Independent review fixes add mandatory snapshot deployment-token comparison to
validate/apply, scoped SSH error clearing, persistent rollback-recovery markers
and action-outcome writes under the original native transaction lock. Red tests
reproduced stale identity acceptance, unrelated error clearing, no-op rollback
uncertainty clearing and missing in-transaction action bookkeeping. Full lint and
unit verification passes with 215 tests after these corrections.
