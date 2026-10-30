# SSH configuration helper change record

Scope: the version 1 SSH configuration capability on charm base commit
`5f7258cf2922140ec189fa281f3ddf36f0d3b4d3`, assessed 2026-10-30. The approved
SSH deployment design supplies the task contract. This is a bounded change, not a full
charm compliance audit. No package upgrade, source build, account provisioning
or credential management is included.

| Requirement and source | Authority / applicability | Before | Disposition | Projected target | Actual evidence |
| --- | --- | --- | --- | --- | --- |
| Direct SSH JSON protocol, approved SSH contract | task-required / capability | GAP, scripts use Juju | Implement `config_manager.py` | PASS for bounded local implementation | Protocol, strict payload/name/hash bounds and system-Python tests pass |
| Transaction lock, candidate validation, ownership, backups and rollback, existing `workload.py` | task-required / preservation | Present for single files | Reuse native helpers for multi-file SSH transaction | PASS locally and on the retained nonproduction unit | Conflict, bridge-stage, invalid-native, rollback, partial-rollback and lock-held tests pass |
| Observe external deployment errors and repaired action errors, approved contract | task-required / capability | PARTIAL, only charm-local action errors | Add shared deployment checkpoint and status integration | PASS locally | SSH failure/repair and later action-error invalidation tests pass; GeoIP tests remain passing |
| Root-owned stable stdlib helper, approved contract | task-required / capability | GAP | Install copied source and isolated system-Python launcher on install/upgrade | PASS with installed helper ownership verified | Installation test checks requested uid/gid/modes; isolated system Python with site packages disabled imports helper |
| Module boundaries and `uv run tox -e lint,unit`, [AGENTS.md](../AGENTS.md) | repository-required / repository | Existing workload/charm split | Preserve split and dependency lock | PASS | Full lint and unit suite passes, 218 tests; dependency definitions unchanged |
| Preserve package/configuration/GeoIP/PID on refresh, [AGENTS.md](../AGENTS.md) | repository-required / preservation | Existing constraint | Add helper files without DNS activation on refresh | PASS for built-artifact refresh | Refresh event tests make no workload install/update/service calls; the built-artifact refresh preserved file hashes, package and process ID |
| Explicit retained nonproduction target, [AGENTS.md](../AGENTS.md) | repository-required / integration | Integration target not inspected in this implementation task | Use an explicitly selected retained nonproduction model | PASS | pytest/Jubilant retained the caller-owned model, application and machine |
| Built local artifact with pytest/Jubilant, skill DEPLOY-001 | required-baseline / integration | UNKNOWN for new capability | Pack and test the installed helper | PASS | Two pytest/Jubilant tests passed: refresh preservation and snapshot/status/validation/apply/conflict recovery against the installed helper |

Implementation improves the SSH management interface and retains existing actions
and relations. The built artifact and restricted SSH path have been tested on the retained
nonproduction unit. Production refresh verification also preserved configuration,
GeoIP, package and process ID on four servers. Unrelated baseline areas were not
assessed. No additional decisions are needed for these dispositions.

Local verification: `uv run tox -e lint,unit` passed with 218 unit tests. TDD red
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
unit verification passes with 218 tests after these corrections.


Follow-up recovery review adds regression coverage for unchanged uncertain global
configuration bundled with changed zones, action rollback failures entering
shared recovery state, and alternate base64 encodings of unchanged action data.
Global recovery cannot be cleared by zone-only activation. Identical action data
is recognized by its decoded digest before allowing no-op completion.


Built-artifact verification used `charmcraft pack --platform amd64`, followed by
pytest/Jubilant with an explicit nonproduction model and refresh opt-in. The
selected refresh-preservation and installed-helper tests both passed. A separate
restricted deployment identity completed direct SSH snapshot, status and no-op
apply operations, and rejected an arbitrary shell request. The temporary test key
was revoked and removed after those checks. Caller-owned resources were retained.
No activation-failure injection ran against production.
