# Direct-only n test copies

Verified 2026-10-28. Erik explicitly requested syncing the original n zone onto
all four gdnsd test servers for direct queries, without registering them as
production n authorities. This authorizes test-copy writes only. Production
n records, delegation, glue, Aloha configuration and the source file remain
unchanged. No parent DNS, DNSControl or registrar changes were made.

| Region | Controller-qualified model | Direct query address | Result |
| --- | --- | --- | --- |
| US East | hyperborea:erik-lonroth@external/gdnsd-us | 44.201.71.32 | Existing n file already matched; validated no-op |
| Tokyo | hyperborea:erik-lonroth@external/gdnsd-ap | 18.181.231.202 | Existing n file already matched; validated no-op |
| Frankfurt | hyperborea:erik-lonroth@external/gdnsd-nl | 63.184.230.135 | Added n test zone |
| Oregon | hyperborea:erik-lonroth@external/gdnsd-us-west | 44.243.1.147 | Added n test zone |

All targets are retained test models with unit gdnsd-vm/0 on machine 0. None is
a production n server. Their existing port 53 exposure serves direct queries;
no source-IP restriction or new network listener was introduced.

## Data and application

The original GSLB source SHA-256 is
`10bdf49ba5f2ae9d14409bb58826406b01616b8b550c80d8bfbe2065f4ed5d6b`.
The strict investigation converter produced the n zone directly from that
source, independently of the x-zone files. It preserves all 198 dynamic owners,
203 CNAMEs and two nameserver address owners, plus apex NS/SOA. Source NS records
and addresses still identify production ns1.n and ns2.n, not the test servers.
Source SOA data and serial 1 were retained, including the source's relative
mailbox representation, which also matches the observed production SOA.

Dynamic answers use 10/10-second bounds, CNAME/default TTL is 60, nameserver A
TTL is 300, SOA TTL and negative caching are 900. The x zones retain their
separate 60-second answer policy.

US/Tokyo use their existing resource names. Frankfurt/Oregon use equivalent
x_import_ resource names already present in their global configuration. No
extra monitors, resources, global config changes or database replacement were
needed. The source conversion's previously documented Oceania/default-location
and monitor-threshold test policies remain unchanged; this is not a new proof
of full Aloha policy equivalence.

The guard setting was saved, temporarily relaxed on each authorized test unit,
and restored in a finally block. Native full-tree validation passed on all four
candidates. Apply returned an unchanged no-op on US/Tokyo and used reload-zones
on Frankfurt/Oregon. Readback hashes matched candidates and every unit returned
to active/running with its n-zone guard restored.

The n-zone hashes are:

- US/Tokyo: `37f0d33b61da19ee0a78bcc838436b0a3d3a57c965b02b79c1e03b8b725f9ed8`.
- Frankfurt/Oregon: `b6b27a700eff42b0210157de6fd2496b310ef8a8d105e45a84f4981866f72a38`.

The second hash differs only because its DYNA references use the existing
resource namespace. Wire records, TTLs and configured backend policy match.

## Before, target and actual

Scope is this authorized test-zone operation, not a whole-charm compliance
audit. No charm runtime code, artifact, package or workload version changed.

| Requirement | Authority | Before | Target | Actual |
| --- | --- | --- | --- | --- |
| n copies on four test servers | Task-required | Present on two, absent on two | Same source-equivalent n data everywhere | Added two and confirmed two |
| Existing x/global/GeoIP files | Task-required | Hash snapshots saved | Preserve | All pre-existing file hashes match |
| Running workloads | Repository-required | PID/package snapshots saved | Preserve | PIDs and packages unchanged on all four |
| Production n | Task-required | Parent NS/glue and public SOA/NS saved | Preserve; no registration | All four parent referrals match; both public resolvers still use production |
| n-zone guard | Repository-required | Protected by default | Relax only for authorized action, restore afterward | Restored and asserted on all four |
| Direct DNS behavior | Task-required | Earlier two-server copy | UDP/TCP authoritative inventory with source TTLs | 3,240 queries passed, nine required timeout retries |

## Verification

The direct inventory covered every one of the 403 source owners plus apex
NS/SOA on each server, over UDP and TCP with recursion disabled. Checks required
NOERROR, AA, exact CNAME/authority data or valid backend-pool membership, and
source TTLs. Every existing global/x/GeoIP file hash and service PID/package
remained unchanged. x static answers were also checked over UDP/TCP on every
server.

Production n NS/glue matched before/after on both AWS and both Google parent
nameservers. Production SOA and NS matched. Cloudflare and Google public
recursive NS lookups still return only ns1.n and ns2.n at the production
addresses. The source GSLB file hash is unchanged.

Existing converter unit tests passed six checks. The full charm suites were not
rerun because this operation changed no runtime code. Evidence is in
[evidence/n-direct](evidence/n-direct/), including
[direct verification](evidence/n-direct/verification.log),
[action results](evidence/n-direct/actions-nl.json), before/after file hashes,
production DNS snapshots and per-model health results. Full operational files
and per-query results remain in the investigation's n-direct-sync directory.

Use a test-server address explicitly:

```bash
dig @44.243.1.147 api-bsc-mainnet-full.n.dwellir.com A +norecurse
dig @63.184.230.135 n.dwellir.com SOA +norecurse
```

A lookup through a normal public resolver continues to query production n.
