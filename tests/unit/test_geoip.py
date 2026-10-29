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
    (directory / "zones" / "n.dwellir.com").write_text("existing zone\n")
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
    assert (work.config_dir / "zones/n.dwellir.com").read_text() == "existing zone\n"


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


@pytest.mark.parametrize(
    "name", ["n.dwellir.com", "N.Dwellir.Com.", "n.dwellir.com.", "sub.n.dwellir.com"]
)
def test_existing_zones_do_not_block_database_updates(setup, name):
    """Database validation and activation work regardless of served zone names."""
    work, database, calls = setup
    (work.config_dir / "zones/n.dwellir.com").unlink()
    path = work.config_dir / "zones" / name
    path.write_text("existing zone\n")
    result = work.sync_geoip("https://example.com/city.mmdb")
    assert result["changed"] and result["validated"]
    assert database.read_bytes() == FIXTURE.read_bytes()
    assert path.read_text() == "existing zone\n"
    assert ["gdnsdctl", "replace"] in calls


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


def compressed_payload(tmp_path, kind, members=None):
    """Build small data-only test archives without invoking external tools."""
    import gzip
    import io
    import tarfile

    target = tmp_path / "payload"
    if kind == "gzip":
        target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    elif kind == "raw":
        target.write_bytes(FIXTURE.read_bytes())
    else:
        mode = "w:gz" if kind == "tar.gz" else "w"
        with tarfile.open(target, mode) as archive:
            for name, value in members or [
                ("release/GeoLite2-City.mmdb", FIXTURE.read_bytes()),
                ("release/LICENSE.txt", b"license"),
            ]:
                header = tarfile.TarInfo(name)
                if value == "symlink":
                    header.type = tarfile.SYMTYPE
                    header.linkname = "/etc/shadow"
                    archive.addfile(header)
                else:
                    header.size = len(value)
                    archive.addfile(header, io.BytesIO(value))
    return target


@pytest.mark.parametrize("kind", ["raw", "gzip", "tar", "tar.gz"])
def test_unpack_database_accepts_supported_content(tmp_path, kind):
    source = compressed_payload(tmp_path, kind)
    destination = tmp_path / "installed.mmdb"
    result = geoip.unpack_database(source, destination)
    assert destination.read_bytes() == FIXTURE.read_bytes()
    assert result["sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert result["download-format"] == kind


@pytest.mark.parametrize(
    "members",
    [
        [("a.mmdb", b"first"), ("b.mmdb", b"second")],
        [("README.txt", b"no database")],
        [("../outside.mmdb", b"unsafe")],
        [("/absolute.mmdb", b"unsafe")],
        [("linked.mmdb", "symlink")],
    ],
)
def test_unpack_rejects_ambiguous_or_unsafe_archives(tmp_path, members):
    source = compressed_payload(tmp_path, "tar.gz", members)
    with pytest.raises(WorkloadError, match="archive"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")
    assert not (tmp_path.parent / "outside.mmdb").exists()


def test_gzip_expansion_is_bounded(tmp_path, monkeypatch):
    import gzip

    source = tmp_path / "huge.gz"
    source.write_bytes(gzip.compress(b"A" * 10000))
    monkeypatch.setattr(geoip, "MAX_DATABASE_BYTES", 100)
    with pytest.raises(WorkloadError, match="limit"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")


def test_truncated_gzip_is_rejected(tmp_path):
    source = compressed_payload(tmp_path, "gzip")
    source.write_bytes(source.read_bytes()[:-8])
    with pytest.raises(WorkloadError, match="gzip"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")


def test_sync_installs_database_from_archive_and_checks_download_digest(setup, monkeypatch):
    work, database, calls = setup
    archive_digest = []
    source = compressed_payload(work.state_dir.parent, "tar.gz")
    expected_download = hashlib.sha256(source.read_bytes()).hexdigest()

    def download(url, target):
        shutil.copyfile(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        archive_digest.append(digest)
        return digest

    monkeypatch.setattr(geoip, "download_database", download)
    result = work.sync_geoip("https://example.com/city.tar.gz", expected_download)
    assert database.read_bytes() == FIXTURE.read_bytes()
    assert result["download-sha256"] == archive_digest[0]
    assert result["sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert result["download-format"] == "tar.gz"
    assert ["gdnsdctl", "replace"] in calls


def test_nextcloud_share_download_resolution(tmp_path, monkeypatch):
    import io

    opened = []
    share = "https://cloud.example.com/s/SHARE"

    class Response(io.BytesIO):
        def __init__(self, url, body, content_type):
            super().__init__(body)
            self.url = url
            self.headers = {"Content-Type": content_type}

        def geturl(self):
            return self.url

    def open_url(request, **kwargs):
        opened.append(request.full_url)
        if len(opened) == 1:
            return Response(share, b"<html>Nextcloud</html>", "text/html")
        return Response(share + "/download", FIXTURE.read_bytes(), "application/octet-stream")

    monkeypatch.setattr(geoip.urllib.request, "urlopen", open_url)
    destination = tmp_path / "database"
    geoip.download_database(share, destination)
    assert opened == [share, share + "/download"]
    assert destination.read_bytes() == FIXTURE.read_bytes()


def test_corrupt_deflate_stream_is_a_validation_error(tmp_path):
    source = tmp_path / "corrupt.gz"
    source.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x07" + b"\x00" * 8)
    with pytest.raises(WorkloadError, match="gzip"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")


@pytest.mark.parametrize("kind", ["pax-chain", "pax-large", "gnu-longname"])
def test_tar_metadata_is_rejected_before_recursive_parser(tmp_path, monkeypatch, kind):
    import tarfile

    header = tarfile.TarInfo("metadata")
    header.type = tarfile.GNUTYPE_LONGNAME if kind == "gnu-longname" else tarfile.XHDTYPE
    header.size = 1024**3 if kind == "pax-large" else 0
    payload = header.tobuf(format=tarfile.USTAR_FORMAT)
    if kind == "pax-chain":
        payload *= 1500
    source = tmp_path / "metadata.tar"
    source.write_bytes(payload + b"\x00" * 1024)
    monkeypatch.setattr(
        geoip.tarfile, "open", lambda *a, **kw: pytest.fail("unsafe metadata reached tar parser")
    )
    with pytest.raises(WorkloadError, match="archive"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")


def test_non_utf8_archive_name_is_rejected_before_install(tmp_path):
    import tarfile

    header = tarfile.TarInfo("bad\udcff.mmdb")
    data = FIXTURE.read_bytes()
    header.size = len(data)
    payload = header.tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="surrogateescape")
    source = tmp_path / "bad-name.tar"
    source.write_bytes(payload + data + b"\x00" * ((-len(data)) % 512) + b"\x00" * 1024)
    with pytest.raises(WorkloadError, match="archive"):
        geoip.unpack_database(source, tmp_path / "installed.mmdb")
