# US West deployment and large configuration reads

Verified 2026-10-28. Runtime source is commit
`86b583f09bb8a26bb4f9ddb84d956f50128c5da2`, after initial source `607044a`.
The locally packed Ubuntu 24.04 amd64 artifact has SHA-256
`4f6626da0b134c55f80ffe3c6389051a4122fc2bbcd90fa3b7cae727b8f26d73`.
Runtime files in the archive were compared with the checkout. No Charmhub release
or charm GitHub repository was published.

## Deployment

Added retained model `hyperborea:erik-lonroth@external/gdnsd-us-west` in AWS
`us-west-2`, Oregon, availability zone `us-west-2d`. Machine `0`, EC2 instance
`i-0049a2874c7f69d08`, is a t3.small with Ubuntu 24.04 and a 16 GiB root disk.
Public IPv4 is `44.243.1.147`; private IPv4 is `172.31.50.210`. It runs
`gdnsd-vm/0`, using Ubuntu package `3.8.2-1build2`, workload version `3.8.2`.
The instance remains running and incurs normal AWS charges. Its public IP is
automatically allocated, matching the other test instances.

The new server adopted Frankfurt's x-specific routing resources with its own
listener address. It has no n zone. Its sync-geoip action used the original
operator-supplied URL and retains it for future calls. The private share URL is
omitted from this evidence. All four authorities use GeoLite2 City built
2026-10-27T17:52:02Z, installed SHA-256
`aea6c345eb914bc137252fb720aacbd518e3991a869143f9743ce827d00751f2`.

All four x zones advertise ns1 through ns4, serial `2026102809`, answer TTL 60
and negative TTL 10. Existing global resources, protected n-zone files and GeoIP
databases on US East, Tokyo and Frankfurt were preserved. Native full-tree
validation preceded zone application. The three existing instances received only
x NS/A and serial changes. Source policies and resolver-source geolocation
limitations were not modified.

[DNSControl PR 1395](https://github.com/dwellir-public/dwellir-dns/pull/1395)
added ns4 NS and A glue at TTL 60. Erik authorized merging. Merge commit is
`8a2059bbf1f983103473832006667cfe54f942d7`; the
[main deployment](https://github.com/dwellir-public/dwellir-dns/actions/runs/40706042467)
passed. Preview contained only the x NS/A additions on AWS and Google. All four
parent nameservers return the four-authority delegation and correct ns4 glue.
Cloudflare and Google recursively resolved ns4 and an imported x service name.
No production n change occurred.

## Large read repair

Reading the full Frankfurt global configuration exposed E2BIG from action-set.
The 273,593-byte file encoded to a single base64 argument larger than Linux's
per-argument limit. A checksum-verified SSH snapshot allowed provisioning to
continue, while the failed action left a scoped read error in waiting status.

Large get-config results now send separate numbered base64 fields in 64 KiB
parts, each through its own set-results call. The final call supplies the count
and existing metadata. The CLI bounds the count and sizes, validates base64 and
the SHA-256 digest, and reconstructs the original content-b64 result. Small reads
retain their original shape. Direct action consumers must support the framing
for large reads. This repair changes no workload files or package behavior.

New regressions failed before implementation, including the oversized result
argument and missing/corrupt/mixed part handling. Independent read-only review
reported no significant findings. The fixed charm was refreshed on all four
models. Successful global pulls of 259,845–273,594 bytes cleared the Frankfurt
read error. Every refresh preserved the workload PID, package and all config,
zone and GeoIP file hashes.

## Before, target and actual

Assessed scope is this bounded deployment and transport repair. Classes below
are task-required from Erik's instructions, repository-required from AGENTS.md,
or contract-required from the existing action/script interface. This is not a
whole-charm compliance audit. Skill-source hashes are retained with the evidence.

| Requirement | Class | Before | Target | Actual |
| --- | --- | --- | --- | --- |
| Fourth authority | Task-required | Three regions | Retained US West machine and charm | Active Oregon instance, public UDP/TCP verified |
| Full x policy and GeoIP | Task-required | Three synchronized authorities | Fourth matches x records and database | Full inventory passed; all four installed MMDB hashes match |
| Parent DNS via canonical repo | Repository-required | ns1/ns2/ns3 | Add ns4 through DNSControl | PR merged; workflow and four parent checks passed |
| n protection | Repository-required | Existing n test copies on US/Tokyo | Preserve files and production n | Protected file/global hashes unchanged; no n copy on new model |
| Large config pull | Contract-required | Full get-config fails E2BIG | Bounded transport and verified reconstruction | Successful live global pulls on all four models |
| Existing small action results | Contract-required | Single content-b64 field | Preserve small-result callers | Existing unit coverage passed |
| Refresh preservation | Repository-required | Package/files/PIDs recorded | Preserve running workload | Before/after refresh evidence matches on all four |
| Prebuilt workload | Repository-required | Apt package on existing machines | Apt package only on new server | 3.8.2-1build2; no source build or workload upgrade |

## Checks and evidence

- Lint passed. All 162 unit tests passed under Python 3.12 and 3.13.
- The retained-model Jubilant checks for full global reads and local
  health/version passed twice per model, eight checks total. Only these
  read-only checks were selected. The broader test suite that changes lab
  configuration and GeoIP fixtures was not run against these retained models.
- New-server public checks passed 812 queries without retries. Four-authority
  checks passed 3,248 queries, with five timeout retries. They cover all 403
  imported A/CNAME/dynamic owners plus apex NS and ns3/ns4 A over UDP/TCP.
  Assertions require AA, NOERROR, expected pools or exact CNAME targets and TTL 60.
- Parent delegation/glue and recursive resolution checks passed after deployment.
- All units report active/running and installed database metadata. n protection
  remains at its default setting.

Evidence is in [evidence/us-west](evidence/us-west/). Start with
[artifact identity](evidence/us-west/artifact.json),
[refresh results](evidence/us-west/refresh.log),
[public inventory](evidence/us-west/verification.log), and
[delegation checks](evidence/us-west/delegation-verification.log).
Per-model before/after refresh hashes, get-info output, large-pull and integration
logs are alongside them. The saved before/candidate/after operational files and
DNSControl logs are retained in the investigation's us-west-deployment directory.

A manual check is:

```bash
juju status -m hyperborea:erik-lonroth@external/gdnsd-us-west
dig @44.243.1.147 x.dwellir.com NS +norecurse
dig @1.1.1.1 ns4.x.dwellir.com A
```

Production n, regional routing priorities, Cloudflare overrides and the longer
controlled latency benchmark remain outside this deployment. Earlier regional
measurement results are historical and are not a four-authority benchmark.
