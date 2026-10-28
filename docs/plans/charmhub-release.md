# Initial public charm release

User-authorized scope: create dwellir-public/gdnsd-vm-operator, publish the charm
using the Loki VM release workflow to latest/edge, then refresh the existing
gdnsd application on localhost-localhost. Retain all models and configuration.

| Requirement | Authority/applicability | Before | Target | Actual |
| --- | --- | --- | --- | --- |
| Public source repository | task-required / publication | Local Git, no remote | Public GitHub repository with charm source/history | Pending |
| Release workflow | task-required / CI | No workflow | Exact Loki release workflow with gdnsd name substitution | Pending |
| Build platform | contract-required / artifact | Ubuntu 24.04 amd64, generated platform name | Same base/architecture named amd64 to match workflow artifact | Pending |
| Authentication | task-required / release | Dwellir login; registered gdnsd-vm | Charm-scoped exported credential installed as CHARMHUB_TOKEN | Pending |
| Validation | repository-required / CI | 173 tests previously passed | Lint/unit and matching platform pack pass; all action jobs succeed | Pending |
| Published channel | task-required / release | No workflow release | Verified latest/edge mapping and GitHub artifact/release | Pending |
| Local refresh | task-required / retained model | localhost-localhost:admin/machine-observability-v3-reference, new machine 64 | Existing local application refreshed from released edge revision, healthy and preserved files/package | Pending |

The source workload remains the Ubuntu binary package. No workload upgrade,
source compilation, production DNS change or additional release channel is
included. Existing AWS DNS deployments remain unchanged. Historical evidence
is retained. Broader charm compliance is outside this release change.

The user selected a new machine in the existing local reference model because
no gdnsd application was present. A local artifact is tested there first, then
the same application will be refreshed to the action-published edge revision.
The machine and application will be retained. The platform label changes from
ubuntu@24.04:amd64 to amd64; base and architecture remain unchanged.

Loki reference release workflow blob:
ec8c5033accd5baefc94c01cfd108956ebbe89f0. The copied release workflow differs
only by substituting gdnsd-vm for loki-vm. CI also runs the existing lint/unit
commands. The token is scoped to gdnsd-vm, latest/edge, package-manage and
package-view with a one-year expiry, and is stored only as a GitHub secret.
