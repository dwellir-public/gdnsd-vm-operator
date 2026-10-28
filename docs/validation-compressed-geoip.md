# Compressed MaxMind upgrade

Verified on 2026-10-28 using the operator-supplied Nextcloud share.
Runtime source commit: `4027def5ef6051af054fa02634ee72a858dbbe55`.
The source and deployed Python payload hashes match on all three units.
[Artifact identity](evidence/geoip-compressed/artifact.json) records the local
Ubuntu 24.04 amd64 charm build and checksum.

## Installed result

The database update ran on `hyperborea:erik-lonroth@external/gdnsd-nl`, unit
`gdnsd-vm/0`, machine `0`, in AWS Frankfurt. It replaced the prior MaxMind test
fixture with the supplied real GeoLite2-City database.

```text
running; GeoIP installed: GeoLite2-City 2026-10-27T17:52:02Z
```

| Property | Observed value |
| --- | --- |
| Download packaging | tar.gz |
| Archive filename | GeoLite2-City_20261027.tar.gz |
| Archive size | 28,064,871 bytes |
| Selected member | GeoLite2-City_20261027/GeoLite2-City.mmdb |
| Installed size | 50,762,235 bytes |
| Database type | GeoLite2-City |
| Build time | 2026-10-27T17:52:02Z |
| Download SHA-256 | `8dd3673002aeecb01221008c0cb6cb10cef4d40586cf50069741fb986ecf8cb7` |
| Installed MMDB SHA-256 | `aea6c345eb914bc137252fb720aacbd518e3991a869143f9743ce827d00751f2` |

The first sync changed the database and performed a native `gdnsdctl replace`.
A second call omitted the URL, reused the original share link, validated the
same contents and returned `changed=false` without replacing the daemon.
The share URL remains in unit runtime state and appears in action results.
It is intentionally omitted from portable repository evidence.

## Native GeoIP checks

Frankfurt's x configuration now contains a controlled GeoIP resource using the
managed database path. The zone record is
`geoip-upgrade-check.x.dwellir.com`, with fixed TTL 60 and always-up documentation
addresses. This makes gdnsd consume the database for actual authoritative answers.
The new database was staged and checked against that configuration before apply.

| ECS client | Configured region | Returned address |
| --- | --- | --- |
| 8.8.8.8 | US | 192.0.2.22 |
| 81.2.69.142 | Europe | 192.0.2.21 |
| 18.181.231.202 | Asia | 192.0.2.23 |

These are controlled DNS answers, not RPC endpoints or a client-latency benchmark.
Frankfurt remains a direct-test authority outside the public x delegation.

## Validation and retained scope

- 156 unit tests passed under Python 3.12 and Python 3.13; Ruff checks passed.
- The operator-download pytest/Jubilant integration test passed. It checked
  archive and installed-file checksums, build metadata, native activation,
  geographic answers, remembered URL reuse and no-op behavior.
- Two live refresh/health checks passed in each of US, Tokyo and Frankfurt.
  Charm refresh preserved workload PID, configuration and package version.
- Archive tests cover raw/gzip/tar/tar.gz inputs, ambiguous databases, links,
  traversal paths, size limits, truncated/corrupt gzip, non-UTF-8 filenames and
  extension-header allocation/recursion attempts.
- Standard tar headers are bounded before invoking the tar parser. PAX/GNU
  extension metadata is rejected. Only the selected regular MMDB is copied to
  a fixed staging path; archive paths are never extracted to the filesystem.
- The installed workload remains Ubuntu's `gdnsd 3.8.2-1build2`. No workload
  upgrade, source compilation or parent DNS change occurred.

US/Tokyo retain their previous global configuration, n zone and shared GeoIP
database hashes. Their dataset remains the 2025-05-23 build. The n-zone hash is
`37f0d33b61da19ee0a78bcc838436b0a3d3a57c965b02b79c1e03b8b725f9ed8`;
their database hash is
`bc6e2e504b3a59164dae621e3c4d1f807809f820ab986508b210107b45eb407c`.
The existing PIDs `136556` in US and `20227` in Tokyo were preserved.

The [bounded change matrix](superpowers/plans/2026-10-28-geoip-compressed.md)
records the before/target/actual outcomes. Evidence is under
[evidence/geoip-compressed](evidence/geoip-compressed), including the
[operator upgrade result](evidence/geoip-compressed/live-result.json),
[integration log](evidence/geoip-compressed/integration.log),
[unit checks](evidence/geoip-compressed/unit.log),
[target Python checks](evidence/geoip-compressed/unit-python312.log),
[US final](evidence/geoip-compressed/final-us.log),
[Tokyo final](evidence/geoip-compressed/final-ap.log) and
[Frankfurt final](evidence/geoip-compressed/final-nl.log).
The previous baseline artifact is retained locally as
`artifacts/gdnsd-vm-before-compressed.charm`.

## Repeat the action

The original share URL is already remembered on Frankfurt:

```bash
juju run --model hyperborea:erik-lonroth@external/gdnsd-nl gdnsd-vm/0 sync-geoip
juju run --model hyperborea:erik-lonroth@external/gdnsd-nl gdnsd-vm/0 get-info
dig @63.184.230.135 geoip-upgrade-check.x.dwellir.com A +subnet=8.8.8.8/32
```

See [README](../README.md) for compressed-input and checksum semantics and the
[earlier validation record](validation.md) for initial charm deployment history.
