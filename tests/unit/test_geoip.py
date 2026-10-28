"""Validate GeoIP downloads, native candidate checks and transactional rollback."""

import hashlib
import shutil
from pathlib import Path

import pytest

import geoip
from workload import Workload, WorkloadError

FIXTURE = Path(__file__).parents[1] / "data/GeoIP2-City-Test.mmdb"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    directory = tmp_path / "etc"
    (directory / "zones").mkdir(parents=True)
    (directory / "geoip").mkdir()
    (directory / "config").write_text("options => {}\n")
    (directory / "zones" / "n.dwellir.com").write_text("protected\n")
    database = directory / "geoip/GeoLite2-City.mmdb"
    database.write_bytes(b"previous database")
    calls = []

    def command(argv):
        calls.append(argv)
        return "active" if argv[0] == "systemctl" else ""

    def download(url, path):
        shutil.copyfile(FIXTURE, path)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(geoip, "download_database", download)
    return (
        Workload(
            config_dir=directory,
            state_dir=tmp_path / "state",
            command=command,
            protected_zones=set(),
        ),
        database,
        calls,
    )


def test_download_validates_installs_and_replaces_daemon(setup):
    work, database, calls = setup
    result = work.sync_geoip("https://example.com/city.mmdb")
    assert database.read_bytes() == FIXTURE.read_bytes()
    assert result["changed"] and result["database-type"] == "GeoIP2-City"
    assert result["sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert ["gdnsdctl", "replace"] in calls
    assert (work.config_dir / "zones/n.dwellir.com").read_text() == "protected\n"


def test_invalid_mmdb_preserves_old_database(setup, monkeypatch):
    work, database, calls = setup

    def download(url, path):
        path.write_bytes(b"<html>login required</html>")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(geoip, "download_database", download)
    with pytest.raises(WorkloadError, match="MaxMind"):
        work.sync_geoip("https://example.com/bad.mmdb")
    assert database.read_bytes() == b"previous database"
    assert not calls


def test_wrong_checksum_preserves_old_database(setup):
    work, database, calls = setup
    with pytest.raises(WorkloadError, match="SHA-256"):
        work.sync_geoip("https://example.com/city.mmdb", "0" * 64)
    assert database.read_bytes() == b"previous database"
    assert not calls


def test_native_validation_failure_preserves_old_database(setup):
    work, database, calls = setup

    def command(argv):
        if argv[-1] == "checkconf":
            raise WorkloadError("native configuration rejected")
        calls.append(argv)
        return ""

    work.command = command
    with pytest.raises(WorkloadError, match="native configuration rejected"):
        work.sync_geoip("https://example.com/city.mmdb")
    assert database.read_bytes() == b"previous database"
    assert ["gdnsdctl", "replace"] not in calls


def test_failed_activation_restores_database(setup):
    work, database, calls = setup
    normal = work.command
    attempts = []

    def command(argv):
        if argv == ["gdnsdctl", "replace"] and not attempts:
            attempts.append(True)
            raise WorkloadError("replacement failed")
        return normal(argv)

    work.command = command
    with pytest.raises(WorkloadError, match="restored"):
        work.sync_geoip("https://example.com/city.mmdb")
    assert database.read_bytes() == b"previous database"


def test_identical_database_does_not_reload(setup):
    work, database, calls = setup
    database.write_bytes(FIXTURE.read_bytes())
    result = work.sync_geoip("https://example.com/city.mmdb")
    assert not result["changed"]
    assert not calls


def test_candidate_native_validation_uses_new_database_reference(setup):
    work, database, calls = setup
    config = f'plugins => {{ geoip => {{ geoip2_db => "{database}" }} }}'
    (work.config_dir / "config").write_text(config)
    normal = work.command
    observed = []

    def command(argv):
        if argv[-1] == "checkconf":
            candidate = Path(argv[2])
            text = (candidate / "config").read_text()
            assert str(candidate / "geoip/GeoLite2-City.mmdb") in text
            assert str(database) not in text
            assert (candidate / "geoip/GeoLite2-City.mmdb").read_bytes() == FIXTURE.read_bytes()
            observed.append(True)
        return normal(argv)

    work.command = command
    work.sync_geoip("https://example.com/city.mmdb")
    assert observed == [True]
    assert (work.config_dir / "config").read_text() == config


@pytest.mark.parametrize("url", ["", "file:///etc/shadow", "ftp://example.com/city.mmdb"])
def test_rejects_non_http_downloads(setup, url):
    work, database, calls = setup
    with pytest.raises(WorkloadError, match="HTTP"):
        work.sync_geoip(url)
    assert database.read_bytes() == b"previous database"
    assert not calls


def test_shared_protected_zone_prevents_database_change(setup):
    work, database, calls = setup
    work.protected_zones = {"n.dwellir.com"}
    with pytest.raises(WorkloadError, match="protected zone"):
        work.sync_geoip("https://example.com/city.mmdb")
    assert database.read_bytes() == b"previous database"
    assert not calls


@pytest.mark.parametrize("name", ["N.Dwellir.Com.", "n.dwellir.com.", "sub.n.dwellir.com"])
def test_protected_equivalent_or_child_zone_prevents_database_change(setup, name):
    work, database, calls = setup
    (work.config_dir / "zones/n.dwellir.com").unlink()
    (work.config_dir / "zones" / name).write_text("protected")
    work.protected_zones = {"n.dwellir.com"}
    with pytest.raises(WorkloadError, match="protected zone"):
        work.sync_geoip("https://example.com/city.mmdb")
    assert database.read_bytes() == b"previous database"
    assert not calls


def test_installed_database_metadata_has_utc_build_time(setup):
    work, database, _ = setup
    database.write_bytes(FIXTURE.read_bytes())
    info = work.geoip_info()
    assert info["geoip-state"] == "installed"
    assert info["geoip-database-type"] == "GeoIP2-City"
    assert info["geoip-build-time"].endswith("Z")
    assert info["geoip-build-epoch"] > 0
    assert info["geoip-build-time"] in info["geoip-summary"]
    assert "geoip-sha256" not in info
    assert (
        work.geoip_info(include_sha256=True)["geoip-sha256"]
        == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    )


def test_missing_database_is_reported_without_validation_error(setup):
    work, database, _ = setup
    database.unlink()
    info = work.geoip_info()
    assert info["geoip-state"] == "absent"
    assert info["geoip-summary"] == "GeoIP not installed"
    assert not info.get("geoip-error")


def test_invalid_installed_database_is_a_diagnosable_configuration_error(setup):
    work, _, _ = setup
    info = work.geoip_info()
    assert info["geoip-state"] == "invalid"
    assert "MaxMind" in info["geoip-error"]
    assert "invalid" in info["geoip-summary"]
