"""Exercise the installed SSH protocol on the explicit retained nonproduction unit."""

import base64
import json
import subprocess

import pytest
from test_live import action, target  # noqa: F401


def request(juju, app, payload, *, success=True):
    """Send JSON on stdin, preserving the actual host helper process boundary."""
    result = subprocess.run(
        [
            "juju",
            "ssh",
            "-m",
            juju.model,
            f"{app}/0",
            "sudo /usr/local/sbin/gdnsd-config-manager ssh",
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=330,
        check=False,
    )
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        pytest.fail("installed helper did not return JSON: " + result.stderr[-1000:])
    assert envelope["version"] == 1
    assert envelope["ok"] is success, envelope
    assert bool(result.returncode == 0) is success
    return envelope.get("result", envelope)


def test_installed_helper_snapshot_status_and_transaction(target):  # noqa: F811
    """Reject invalid data and stale release identities, then repair a test zone."""
    juju, app, _ = target
    zone = "ssh-ci.example.test"
    baseline = request(juju, app, {"version": 1, "operation": "snapshot", "zones": [zone]})
    assert "deployment-token" in baseline
    assert request(juju, app, {"version": 1, "operation": "status"})["healthy"]
    name = "zones/" + zone
    content = (
        "$TTL 60\n@ SOA ns.ssh-ci.example.test. ops.example.test. "
        "2026103001 60 30 86400 10\n@ NS ns.ssh-ci.example.test.\n"
        "ns A 127.0.0.1\nprobe A 192.0.2.55\n"
    )

    def payload(snapshot, text, operation="apply", release_id="integration"):
        return {
            "version": 1,
            "operation": operation,
            "files": {name: base64.b64encode(text.encode()).decode()},
            "expected": {name: snapshot["files"][name]["sha256"]},
            "expected-deployment": snapshot["deployment-token"],
            "release": {
                "id": release_id,
                "commit": "1" * 40,
                "serial": 2026103001,
                "source-sha256": "2" * 64,
            },
        }

    invalid = payload(baseline, "not a valid zone !!!\n", operation="validate")
    error = request(juju, app, invalid, success=False)
    assert "checkconf" in error["error"] or "failed" in error["error"]
    after_invalid = request(juju, app, {"version": 1, "operation": "snapshot", "zones": [zone]})
    assert baseline["files"] == after_invalid["files"]
    request(juju, app, payload(after_invalid, content))
    info = action(juju, app, "get-info", {})
    assert info["active"]
    installed = request(juju, app, {"version": 1, "operation": "snapshot", "zones": [zone]})
    assert base64.b64decode(installed["files"][name]["content-b64"]).decode() == content
    pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    same = payload(installed, content, release_id="integration-noop")
    assert request(juju, app, same)["changed"] is False
    assert juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID") == pid
    stale = request(juju, app, same, success=False)
    assert "deployment" in stale["error"] or "identity" in stale["error"]
    latest = request(juju, app, {"version": 1, "operation": "snapshot", "zones": [zone]})
    request(juju, app, payload(latest, content, release_id="integration-repaired"))
    assert request(juju, app, {"version": 1, "operation": "status"})["healthy"]
    assert "192.0.2.55" in juju.ssh(f"{app}/0", "dig @127.0.0.1 probe." + zone + " A +short")
