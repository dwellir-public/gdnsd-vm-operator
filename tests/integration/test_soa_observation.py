"""Exercise real SOA diagnostics on the explicit retained nonproduction unit."""

import base64
import json
import re
import shlex

from test_live import action, target  # noqa: F401


def serials(juju, app):
    """Read structured per-zone serials through the installed action."""
    info = action(juju, app, "get-info", {})
    return info, json.loads(info["zone-serials-json"])


def test_soa_observation_matches_local_dns(target):  # noqa: F811
    """Expose each configured zone's actual served serial and compact status."""
    juju, app, _ = target
    info, zones = serials(juju, app)
    assert zones and info["zone-serials-ok"] in (True, "true")
    for zone, item in zones.items():
        assert item["in-sync"]
        assert item["configured-serial"] == item["served-serial"]
        for transport in ["", "+tcp"]:
            answer = juju.ssh(
                f"{app}/0", f"dig @127.0.0.1 {shlex.quote(zone)} SOA +short {transport}"
            )
            assert int(answer.split()[2]) == item["served-serial"]
    # An existing observational action refreshes status without a fabricated hook.
    action(juju, app, "get-config", {"zone": next(iter(zones))})
    message = juju.status().apps[app].units[f"{app}/0"].workload_status.message
    assert message.startswith("running;") and "SOA " in message


def test_soa_observation_mismatch_waits_and_recovers(target):  # noqa: F811
    """Keep the last served zone while an invalid on-disk candidate is diagnosed."""
    juju, app, _ = target
    zone = "soa-observation.example.test"
    original = action(juju, app, "get-config", {"zone": zone})
    marker = "; owned by gdnsd-vm SOA observation integration test\n"
    valid = marker + (
        "$TTL 60\n@ 10 SOA ns.soa-observation.example.test. ops.example.test. "
        "2026103001 60 30 86400 10\n@ NS ns.soa-observation.example.test.\n"
        "ns A 127.0.0.1\nprobe A 192.0.2.77\n"
    )
    if original["exists"]:
        content = base64.b64decode(original["content-b64"]).decode()
        assert content.startswith(marker), "refusing to overwrite a caller-owned zone"
        old = int(re.search(r"SOA\s+\S+\s+\S+\s+(\d+)", content)[1])
        valid = valid.replace("2026103001", str(max(old + 1, 2026103001)))
    initial = int(re.search(r"SOA\s+\S+\s+\S+\s+(\d+)", valid)[1])
    final = valid.replace(str(initial), str(initial + 2), 1)
    other_before = juju.ssh(
        f"{app}/0", "sudo sha256sum /etc/gdnsd/config /etc/gdnsd/zones/x.dwellir.com"
    )
    try:
        action(
            juju,
            app,
            "apply-config",
            {
                "zone": zone,
                "expected-sha256": original["sha256"],
                "content-b64": base64.b64encode(valid.encode()).decode(),
            },
        )
        _, zones = serials(juju, app)
        assert zones[zone]["served-serial"] == initial
        bad = valid.replace(str(initial), str(initial + 1), 1) + "broken 60 A not-an-address\n"
        path = "/etc/gdnsd/zones/" + zone
        script = (
            "from pathlib import Path; Path("
            + repr(path)
            + ").write_bytes("
            + repr(bad.encode())
            + ")"
        )
        juju.ssh(f"{app}/0", "sudo python3 -c " + shlex.quote(script))
        # Native autoload/reload must retain the last valid data for this bad zone.
        reload_script = (
            "import subprocess,json; "
            "r=subprocess.run(['gdnsdctl','reload-zones'],capture_output=True,text=True); "
            "print(json.dumps({'returncode':r.returncode,'stderr':r.stderr}))"
        )
        juju.ssh(f"{app}/0", "sudo python3 -c " + shlex.quote(reload_script))
        info, zones = serials(juju, app)
        item = zones[zone]
        assert item["configured-serial"] == initial + 1
        assert item["served-serial"] == initial
        assert not item["in-sync"]
        assert info["zone-serials-ok"] in (False, "false")
        action(juju, app, "get-config", {"zone": zone})
        status = juju.status().apps[app].units[f"{app}/0"].workload_status
        assert status.current == "waiting" and "SOA" in status.message
    finally:
        # Repair only this test-owned zone, including after an assertion failure.
        old = action(juju, app, "get-config", {"zone": zone})
        action(
            juju,
            app,
            "apply-config",
            {
                "zone": zone,
                "expected-sha256": old["sha256"],
                "content-b64": base64.b64encode(final.encode()).decode(),
            },
        )
    info, zones = serials(juju, app)
    assert zones[zone]["configured-serial"] == zones[zone]["served-serial"] == initial + 2
    assert info["zone-serials-ok"] in (True, "true")
    assert juju.status().apps[app].units[f"{app}/0"].workload_status.current == "active"
    assert (
        juju.ssh(f"{app}/0", "sudo sha256sum /etc/gdnsd/config /etc/gdnsd/zones/x.dwellir.com")
        == other_before
    )
