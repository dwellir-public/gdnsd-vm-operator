"""Expose SOA observations through workload health and action diagnostics."""

import json

from workload import Workload


def observation(ok=True):
    """Use distinct serials to detect lost configured/served information."""
    return {
        "zone-serials": {
            "example.test": {
                "configured-serial": 42,
                "served-serial": 42 if ok else 41,
                "in-sync": ok,
            }
        },
        "zone-serials-ok": ok,
        "zone-serials-summary": "SOA example.test 42/42 in sync"
        if ok
        else "SOA example.test 42/41 error",
        "zone-serials-error": "" if ok else "example.test: configured 42, served 41",
    }


def work(tmp_path, monkeypatch, *, ok=True):
    """Isolate service commands, GeoIP and SOA observation from the host."""
    calls = []

    def command(argv):
        calls.append(argv)
        if argv[0] == "systemctl":
            return "active"
        if argv[0] == "dpkg-query":
            return "3.8.2-1build2"
        return "info: version 3.8.2 running at PID 1234"

    instance = Workload(tmp_path / "etc", tmp_path / "state", command=command, probe=lambda: None)
    monkeypatch.setattr(instance, "zone_info", lambda: observation(ok), raising=False)
    monkeypatch.setattr(
        instance, "geoip_info", lambda **kwargs: {"geoip-summary": "GeoIP not installed"}
    )
    return instance, calls


def test_health_exposes_zone_serials_without_control_operations(tmp_path, monkeypatch):
    instance, calls = work(tmp_path, monkeypatch)
    health = instance.health()
    assert health["zone-serials"] == observation()["zone-serials"]
    assert health["zone-serials-ok"] is True
    assert health["message"].count(observation()["zone-serials-summary"]) == 1
    assert health["message"].count("GeoIP not installed") == 1
    assert health["healthy"] is True
    assert all("reload-zones" not in call and "replace" not in call for call in calls)


def test_serial_mismatch_does_not_claim_daemon_is_stopped(tmp_path, monkeypatch):
    instance, _ = work(tmp_path, monkeypatch, ok=False)
    health = instance.health()
    assert health["healthy"] is True
    assert health["zone-serials-ok"] is False
    assert "configured 42, served 41" in health["zone-serials-error"]


def test_info_keeps_literal_zone_names_in_json(tmp_path, monkeypatch):
    instance, _ = work(tmp_path, monkeypatch)
    info = instance.info()
    assert json.loads(info["zone-serials-json"]) == observation()["zone-serials"]
    assert info["zone-serials-ok"] is True
    assert "SOA" in info["zone-serials-summary"]
    assert info["zone-serials-error"] == ""
    assert info["workload-version"] == "3.8.2"
