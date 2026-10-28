"""Retained-model checks for a local gdnsd-vm artifact and x-only updates."""

import base64
import os
import re
import subprocess
from pathlib import Path

import jubilant
import pytest


@pytest.fixture(scope="module")
def target():
    """Select an explicitly opted-in test model and retain every resource."""
    model = os.environ.get("TEST_MODEL", "")
    artifact = Path(os.environ.get("CHARM_PATH", "missing.charm")).resolve()
    if not model or ":" not in model or os.environ.get("GDNSD_NONPRODUCTION") != "1":
        pytest.skip("set TEST_MODEL=CONTROLLER:MODEL and GDNSD_NONPRODUCTION=1")
    if not artifact.is_file():
        pytest.fail("CHARM_PATH must identify a built local charm")
    app = os.environ.get("TEST_APPLICATION", "gdnsd-vm")
    juju = jubilant.Juju(model=model, wait_timeout=600)
    status = juju.status()
    assert status.model.name == model.split(":", 1)[1].split("/")[-1]
    if os.environ.get("GDNSD_DEPLOY") == "1":
        assert app not in status.apps, "refusing to replace an existing application"
        juju.deploy(artifact, app, base="ubuntu@24.04", to=os.environ.get("TEST_MACHINE", "0"))
    juju.wait(lambda s: app in s.apps and jubilant.all_active(s, app), timeout=600)
    return juju, app, artifact


def action(juju, app, name, params):
    """Run one synchronous action with time for full native validation."""
    results = juju.run(f"{app}/0", name, params, wait=300).results
    for key in ("exists", "changed", "validated", "active"):
        if key in results and isinstance(results[key], str):
            assert results[key].lower() in {"true", "false"}
            results[key] = results[key].lower() == "true"
    return results


def protected_hash(juju, app):
    """Inspect an existing protected file without creating or editing it."""
    return juju.ssh(
        f"{app}/0",
        "if sudo test -f /etc/gdnsd/zones/n.dwellir.com; then "
        "sudo sha256sum /etc/gdnsd/zones/n.dwellir.com; else echo absent; fi",
    )


def test_zone_roundtrip_validation_and_conflicts(target):
    """Accept a valid x update and reject invalid or stale candidates."""
    juju, app, _ = target
    before_n = protected_hash(juju, app)
    old = action(juju, app, "get-config", {"scope": "zone", "zone": "x.dwellir.com"})
    content = base64.b64decode(old["content-b64"]).decode()
    if not old["exists"]:
        content = (
            "$TTL 60\n@ 10 SOA ns1.x.dwellir.com. ops.dwellir.com. "
            "2026102801 60 30 86400 10\n@ 60 NS ns1.x.dwellir.com.\n"
            "@ 60 NS ns2.x.dwellir.com.\nns1 60 A 44.201.71.32\n"
            "ns2 60 A 18.181.231.202\nstatic A 192.0.2.100\n"
        )
    # Use a static test label so existing health/routing resources stay untouched.
    content = re.sub(r"^charm-check\s+.*\n", "", content, flags=re.MULTILINE)
    content += "charm-check 60 A 192.0.2.201\n"
    serial = re.search(r"(SOA\s+\S+\s+\S+\s+)(\d+)", content)
    assert serial
    content = content[: serial.start(2)] + str(int(serial[2]) + 1) + content[serial.end(2) :]
    params = {
        "scope": "zone",
        "zone": "x.dwellir.com",
        "expected-sha256": old["sha256"],
        "content-b64": base64.b64encode(content.encode()).decode(),
    }
    preview = action(juju, app, "validate-config", params)
    assert preview["validated"] and not preview["changed"]
    assert action(juju, app, "get-config", {"zone": "x.dwellir.com"})["sha256"] == old["sha256"]
    applied = action(juju, app, "apply-config", params)
    assert applied["changed"]
    current = action(juju, app, "get-config", {"zone": "x.dwellir.com"})
    assert current["sha256"] == applied["sha256"]
    assert base64.b64decode(current["content-b64"]).decode() == content
    with pytest.raises(jubilant.TaskError):
        action(juju, app, "apply-config", params)
    bad = dict(
        params,
        **{
            "expected-sha256": current["sha256"],
            "content-b64": base64.b64encode(b"not a valid zone file\n").decode(),
        },
    )
    with pytest.raises(jubilant.TaskError):
        action(juju, app, "apply-config", bad)
    assert action(juju, app, "get-config", {"zone": "x.dwellir.com"}) == current
    assert protected_hash(juju, app) == before_n
    assert "192.0.2.201" in juju.ssh(
        f"{app}/0", "dig @127.0.0.1 charm-check.x.dwellir.com A +short"
    )


def test_script_pull_validate_push(target, tmp_path):
    """Exercise the shipped command-line workflow against the real actions."""
    juju, app, _ = target
    root = Path(__file__).resolve().parents[2]
    zone = tmp_path / "x.zone"
    args = [
        "--model",
        juju.model,
        "--unit",
        f"{app}/0",
        "--zone",
        "x.dwellir.com",
        "--file",
        str(zone),
    ]
    for script, extra in [
        ("pull_gdnsd_config.py", []),
        ("push_gdnsd_config.py", ["--validate-only"]),
        ("push_gdnsd_config.py", []),
    ]:
        if script == "push_gdnsd_config.py" and extra:
            text = zone.read_text()
            serial = re.search(r"(SOA\s+\S+\s+\S+\s+)(\d+)", text)
            assert serial
            text = text[: serial.start(2)] + str(int(serial[2]) + 1) + text[serial.end(2) :]
            zone.write_text(text + "; round-trip through the operator scripts\n")
        result = subprocess.run(
            ["python3", str(root / "scripts" / script), *args, *extra],
            text=True,
            capture_output=True,
            timeout=330,
        )
        assert result.returncode == 0, result.stderr or result.stdout


def test_global_configuration_handoff_only_on_fresh_model(target):
    """Change a harmless global option only where no protected n copy exists."""
    if os.environ.get("GDNSD_TEST_GLOBAL") != "1":
        pytest.skip("global configuration testing requires GDNSD_TEST_GLOBAL=1")
    juju, app, _ = target
    assert protected_hash(juju, app).strip() == "absent"
    old = action(juju, app, "get-config", {"scope": "global"})
    content = base64.b64decode(old["content-b64"]).decode() + "\n# integration config handoff\n"
    old_pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    action(
        juju,
        app,
        "apply-config",
        {
            "scope": "global",
            "expected-sha256": old["sha256"],
            "content-b64": base64.b64encode(content.encode()).decode(),
        },
    )
    assert juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID") != old_pid
    assert action(juju, app, "get-info", {})["active"]


def test_refresh_preserves_process_files_and_package(target):
    """Refresh the charm only when requested and verify the running workload."""
    if os.environ.get("GDNSD_REFRESH") != "1":
        pytest.skip("set GDNSD_REFRESH=1 to exercise charm refresh")
    juju, app, artifact = target
    command = (
        "systemctl show gdnsd.service --property MainPID; "
        "sudo sha256sum /etc/gdnsd/config /etc/gdnsd/zones/*; dpkg-query -W gdnsd"
    )
    before = juju.ssh(f"{app}/0", command)
    juju.refresh(app, path=artifact)
    juju.wait(
        lambda s: jubilant.all_active(s, app) and jubilant.all_agents_idle(s, app), timeout=600
    )
    assert juju.ssh(f"{app}/0", command) == before


def test_local_health_and_running_workload_version(target):
    """Expose the daemon version and running state after the health check."""
    juju, app, _ = target
    info = action(juju, app, "get-info", {})
    assert info["active"] and info["state"] == "running"
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", info["workload-version"])
    assert info["package-version"]
    status = juju.status()
    assert status.apps[app].version == info["workload-version"]
    assert status.apps[app].units[f"{app}/0"].workload_status.message == "running"


def test_health_reports_stopped_and_recovers_only_on_fresh_model(target):
    """Stop and restore only the fresh test service, leaving retained n copies alone."""
    if os.environ.get("GDNSD_TEST_GLOBAL") != "1":
        pytest.skip("service-stop check is limited to the fresh model without n")
    juju, app, _ = target
    assert protected_hash(juju, app).strip() == "absent"
    try:
        result = action(juju, app, "service", {"action": "stop"})
        assert not result["active"]
        info = action(juju, app, "get-info", {})
        assert not info["active"] and info["state"] == "inactive"
        assert juju.status().apps[app].units[f"{app}/0"].workload_status.message == "inactive"
    finally:
        action(juju, app, "service", {"action": "start"})
    assert action(juju, app, "get-info", {})["state"] == "running"


def test_geoip_sync_remembers_url_and_preserves_valid_data_on_failure(target):
    """Exercise URL reuse, database activation and waiting/recovery on Frankfurt only."""
    if os.environ.get("GDNSD_TEST_GEOIP") != "1":
        pytest.skip("GeoIP replacement requires GDNSD_TEST_GEOIP=1 on the fresh model")
    juju, app, artifact = target
    assert protected_hash(juju, app).strip() == "absent"
    import json

    provenance = json.loads((Path(__file__).parents[1] / "data/provenance.json").read_text())
    url = provenance["url"]
    expected = provenance["sha256"]
    database = "/etc/gdnsd/geoip/GeoLite2-City.mmdb"
    before_pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    result = action(juju, app, "sync-geoip", {"url": url, "sha256": expected})
    assert result["url"] == url and result["sha256"] == expected
    assert result["database-type"] == "GeoIP2-City"
    after_pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    if result["changed"]:
        assert after_pid != before_pid
    assert juju.ssh(f"{app}/0", f"sudo sha256sum {database}").split()[0] == expected
    reused = action(juju, app, "sync-geoip", {})
    assert reused["url"] == url and not reused["changed"]
    assert juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID") == after_pid
    bad_url = url.rsplit("/test-data/", 1)[0] + "/README.md"
    with pytest.raises(jubilant.TaskError) as failure:
        action(juju, app, "sync-geoip", {"url": bad_url})
    assert failure.value.task.results["url"] == bad_url
    assert "MaxMind" in failure.value.task.message
    waiting = juju.status().apps[app].units[f"{app}/0"].workload_status
    assert waiting.current == "waiting" and "valid GeoIP" in waiting.message
    assert juju.ssh(f"{app}/0", f"sudo sha256sum {database}").split()[0] == expected
    # The invalid supplied URL replaced the prior setting, so an omitted URL retries it.
    with pytest.raises(jubilant.TaskError) as failure:
        action(juju, app, "sync-geoip", {})
    assert failure.value.task.results["url"] == bad_url
    logs = juju.ssh(f"{app}/0", "sudo tail -n 300 /var/log/juju/unit-gdnsd-vm-0.log")
    assert "MaxMind database validation failed" in logs
    fixed = action(juju, app, "sync-geoip", {"url": url})
    assert not fixed["changed"]
    assert juju.status().apps[app].units[f"{app}/0"].workload_status.current == "active"
    juju.refresh(app, path=artifact)
    juju.wait(
        lambda s: jubilant.all_active(s, app) and jubilant.all_agents_idle(s, app), timeout=600
    )
    assert action(juju, app, "sync-geoip", {})["url"] == url
    assert protected_hash(juju, app).strip() == "absent"
