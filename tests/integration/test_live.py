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
    # Recover the persisted validation warning using the known-good live zone.
    # Later retained-model tests require a healthy application at their entry.
    repaired = action(
        juju,
        app,
        "validate-config",
        {
            "scope": "zone",
            "zone": "x.dwellir.com",
            "expected-sha256": current["sha256"],
            "content-b64": current["content-b64"],
        },
    )
    assert repaired["validated"] and not repaired["changed"]
    juju.wait(lambda state: jubilant.all_active(state, app), timeout=60)


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
    assert info["geoip-state"] in {"installed", "absent"}
    assert info["geoip-version-source"] == "installed-file-metadata"
    if info["geoip-state"] == "installed":
        assert info["geoip-build-time"].endswith("Z")
        assert info["geoip-sha256"]
        assert (
            info["geoip-build-time"] in status.apps[app].units[f"{app}/0"].workload_status.message
        )
    assert status.apps[app].units[f"{app}/0"].workload_status.message.startswith("running;")


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
        assert (
            juju.status()
            .apps[app]
            .units[f"{app}/0"]
            .workload_status.message.startswith("inactive;")
        )
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


def test_compressed_geoip_operator_download(target, tmp_path):
    """Upgrade the isolated test server using an operator-supplied compressed URL."""
    url = os.environ.get("GDNSD_COMPRESSED_GEOIP_URL")
    if not url:
        pytest.skip("set GDNSD_COMPRESSED_GEOIP_URL for the operator-supplied data test")
    if os.environ.get("GDNSD_TEST_GEOIP") != "1":
        pytest.fail("compressed database test requires explicit GDNSD_TEST_GEOIP=1")
    juju, app, _ = target
    assert protected_hash(juju, app).strip() == "absent"
    # A single always-up x resource ensures native gdnsd actually uses this DB.
    current = action(juju, app, "get-config", {"scope": "global"})
    config = base64.b64decode(current["content-b64"]).decode()
    if "geoip_compressed_test" not in config:
        assert not re.search(r"\bplugins\s*=>", config), (
            "use a dedicated test config without other plugin declarations"
        )
        config += """
plugins => {
  geoip => {
    maps => {
      geoip_compressed_test => {
        geoip2_db => /etc/gdnsd/geoip/GeoLite2-City.mmdb
        datacenters => [eu, us, ap]
        map => { default => [eu, us, ap], NA => [us, eu, ap], AS => [ap, eu, us] }
      }
    }
    resources => {
      geoip_compressed_test => {
        map => geoip_compressed_test
        service_types => up
        dcmap => { eu => 192.0.2.21, us => 192.0.2.22, ap => 192.0.2.23 }
      }
    }
  }
}
"""
        action(
            juju,
            app,
            "apply-config",
            {
                "scope": "global",
                "expected-sha256": current["sha256"],
                "content-b64": base64.b64encode(config.encode()).decode(),
            },
        )
    current = action(juju, app, "get-config", {"zone": "x.dwellir.com"})
    zone = base64.b64decode(current["content-b64"]).decode()
    if "geoip-upgrade-check " not in zone:
        serial = re.search(r"(SOA\s+\S+\s+\S+\s+)(\d+)", zone)
        assert serial
        zone = zone[: serial.start(2)] + str(int(serial[2]) + 1) + zone[serial.end(2) :]
        zone += "geoip-upgrade-check 60/60 DYNA geoip!geoip_compressed_test\n"
        action(
            juju,
            app,
            "apply-config",
            {
                "zone": "x.dwellir.com",
                "expected-sha256": current["sha256"],
                "content-b64": base64.b64encode(zone.encode()).decode(),
            },
        )
    before = action(juju, app, "get-info", {})
    before_pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    params = {"url": url}
    if checksum := os.environ.get("GDNSD_COMPRESSED_GEOIP_SHA256"):
        params["sha256"] = checksum
    installed = action(juju, app, "sync-geoip", params)
    assert installed["url"] == url
    assert installed["download-format"] == "tar.gz"
    assert installed["database-type"] == "GeoLite2-City"
    after = action(juju, app, "get-info", {})
    assert after["geoip-sha256"] == installed["sha256"]
    if expected := os.environ.get("GDNSD_COMPRESSED_DATABASE_SHA256"):
        assert after["geoip-sha256"] == expected
    if expected := os.environ.get("GDNSD_COMPRESSED_BUILD_TIME"):
        assert after["geoip-build-time"] == expected
    if before.get("geoip-sha256") != after["geoip-sha256"]:
        assert installed["changed"]
        assert juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID") != before_pid
    status = juju.status().apps[app].units[f"{app}/0"].workload_status
    assert status.current == "active" and after["geoip-build-time"] in status.message
    checks = []
    for address, expected in [
        ("8.8.8.8", "192.0.2.22"),
        ("81.2.69.142", "192.0.2.21"),
        ("18.181.231.202", "192.0.2.23"),
    ]:
        answer = juju.ssh(
            f"{app}/0",
            "dig @127.0.0.1 geoip-upgrade-check.x.dwellir.com "
            f"A +subnet={address}/32 +noall +answer",
        )
        assert expected in answer
        checks.append({"ecs": address, "answer": answer.strip()})
    after_pid = juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID")
    reused = action(juju, app, "sync-geoip", {})
    assert reused["url"] == url and not reused["changed"]
    assert juju.ssh(f"{app}/0", "systemctl show gdnsd.service --property MainPID") == after_pid
    assert protected_hash(juju, app).strip() == "absent"
    # Keep the private share token out of portable evidence and version control.
    import json

    evidence = {
        "installed": {k: v for k, v in installed.items() if k != "url"},
        "reused": {k: v for k, v in reused.items() if k != "url"},
        "info": after,
        "ecs_checks": checks,
        "status": status.message,
    }
    if output := os.environ.get("GDNSD_COMPRESSED_RESULT_FILE"):
        Path(output).write_text(json.dumps(evidence, indent=2) + "\n")


def test_full_global_config_read(target, tmp_path):
    """Pull the deployed global config across the real Juju action boundary."""
    import hashlib

    juju, app, _ = target
    before_n = protected_hash(juju, app)
    expected = juju.ssh(f"{app}/0", "sudo sha256sum /etc/gdnsd/config").split()[0]
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "global.config"
    subprocess.run(
        [
            "python3",
            str(root / "scripts/pull_gdnsd_config.py"),
            "--model",
            juju.model,
            "--unit",
            f"{app}/0",
            "--global-config",
            "--file",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert hashlib.sha256(output.read_bytes()).hexdigest() == expected
    assert protected_hash(juju, app) == before_n
