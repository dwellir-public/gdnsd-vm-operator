"""Download, validate and atomically replace a gdnsd MaxMind database."""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import maxminddb

from workload import WorkloadError

MAX_DATABASE_BYTES = 256 * 1024 * 1024


def validate_url(url: str) -> None:
    """Allow HTTP database endpoints without accepting local file URLs."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WorkloadError("GeoIP URL must be an HTTP or HTTPS database endpoint")


def download_database(url: str, destination: Path) -> str:
    """Stream a bounded raw database download and compute its SHA-256."""
    validate_url(url)
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "gdnsd-vm/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as out:
            validate_url(response.geturl())
            if url.startswith("https:") and not response.geturl().startswith("https:"):
                raise WorkloadError("GeoIP download redirected from HTTPS to insecure HTTP")
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_DATABASE_BYTES:
                    raise WorkloadError("GeoIP download exceeds the 256 MiB limit")
                digest.update(block)
                out.write(block)
            if total == 0:
                raise WorkloadError("GeoIP download is empty")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise WorkloadError(f"GeoIP download failed: {exc}") from exc
    return digest.hexdigest()


def validate_database(path: Path) -> dict:
    """Read MaxMind metadata and representative records before native validation."""
    try:
        with maxminddb.open_database(path, mode=maxminddb.MODE_MMAP) as reader:
            metadata = reader.metadata()
            if metadata.database_type not in {
                "GeoLite2-City",
                "GeoIP2-City",
                "GeoLite2-Country",
                "GeoIP2-Country",
            }:
                raise ValueError(f"unsupported database type {metadata.database_type!r}")
            if metadata.binary_format_major_version != 2 or metadata.ip_version not in {4, 6}:
                raise ValueError("unsupported MaxMind format or IP version")
            addresses = ["1.1.1.1", "8.8.8.8", "81.2.69.142", "127.0.0.1", "255.255.255.255"]
            if metadata.ip_version == 6:
                addresses += ["2001:4860:4860::8888", "::1"]
            for address in addresses:
                reader.get(address)
            return {"database-type": metadata.database_type, "build-epoch": metadata.build_epoch}
    except Exception as exc:
        raise WorkloadError(
            f"MaxMind database validation failed: {exc}. Supply a raw City/Country .mmdb file"
        ) from exc


def validate_candidate(workload, database: Path, download: Path) -> None:
    """Make native gdnsd validate references to the candidate rather than the old DB."""
    for entry in workload.config_dir.rglob("*"):
        if entry.is_symlink():
            raise WorkloadError("cannot stage configuration containing symlinks")
    with tempfile.TemporaryDirectory(
        prefix="geoip-candidate-", dir=workload.state_dir
    ) as directory:
        candidate = Path(directory) / "gdnsd"
        shutil.copytree(workload.config_dir, candidate)
        selected = candidate / database.relative_to(workload.config_dir)
        selected.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(download, selected)
        pattern = re.compile(
            rb"(?<![A-Za-z0-9_./-])" + re.escape(str(database).encode()) + rb"(?![A-Za-z0-9_./-])"
        )
        # Include fragments can live anywhere in the configuration tree. Only
        # staged text is rewritten; live configuration and zone files stay intact.
        for file in candidate.rglob("*"):
            if file.is_file() and file != selected:
                data = file.read_bytes()
                if str(database).encode() in data:
                    file.write_bytes(pattern.sub(lambda _: str(selected).encode(), data))
        workload.command(["gdnsd", "-c", str(candidate), "checkconf"])


def check_protected_zones(workload) -> None:
    """Refuse shared database changes for protected names and descendants."""
    zones = workload.config_dir / "zones"
    if not zones.is_dir():
        return
    protected = {name.lower().rstrip(".") for name in workload.protected_zones}
    for entry in zones.iterdir():
        name = entry.name.lower().rstrip(".")
        if any(name == zone or name.endswith("." + zone) for zone in protected):
            raise WorkloadError(
                f"GeoIP replacement would affect protected zone {name}; use an isolated model"
            )


def sync_database(workload, url: str, expected_sha256: str = "") -> dict:
    """Install a verified database and restore it if gdnsd cannot activate it."""
    validate_url(url)
    if expected_sha256 and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise WorkloadError("sha256 must be a 64-character SHA-256 digest")
    database = workload.config_dir / "geoip/GeoLite2-City.mmdb"
    if database.is_symlink() or database.parent.is_symlink():
        raise WorkloadError("GeoIP database path must not be a symlink")
    check_protected_zones(workload)
    workload.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="geoip-download-", dir=workload.state_dir) as directory:
        downloaded = Path(directory) / "database.mmdb"
        digest = download_database(url, downloaded)
        if expected_sha256 and digest != expected_sha256.lower():
            raise WorkloadError("GeoIP SHA-256 mismatch; the active database was not changed")
        metadata = validate_database(downloaded)
        with workload._lock():
            check_protected_zones(workload)
            before = database.read_bytes() if database.exists() else None
            if before is not None and hashlib.sha256(before).hexdigest() == digest:
                return {
                    "url": url,
                    "sha256": digest,
                    "changed": False,
                    "validated": True,
                    **metadata,
                }
            validate_candidate(workload, database, downloaded)
            attributes = database.stat() if before is not None else None
            mode = stat.S_IMODE(attributes.st_mode) if attributes else 0o644
            owner = (attributes.st_uid, attributes.st_gid) if attributes else None
            backups = workload.state_dir / "backups"
            backups.mkdir(mode=0o700, exist_ok=True)
            if before is not None:
                old_digest = hashlib.sha256(before).hexdigest()
                backup = backups / f"GeoLite2-City.mmdb-{old_digest}"
                if not backup.exists():
                    backup.write_bytes(before)
                    backup.chmod(0o600)
            database.parent.mkdir(parents=True, exist_ok=True)
            workload._write(database, downloaded.read_bytes(), mode, owner)
            try:
                workload._activate("global")
            except Exception as apply_error:
                try:
                    if before is None:
                        database.unlink()
                    else:
                        workload._write(database, before, mode, owner)
                    workload._activate("global")
                except Exception as rollback_error:
                    raise WorkloadError(
                        f"GeoIP activation failed: {apply_error}; rollback failed: {rollback_error}"
                    ) from rollback_error
                raise WorkloadError(
                    f"GeoIP activation failed: {apply_error}; previous database restored"
                ) from apply_error
            return {"url": url, "sha256": digest, "changed": True, "validated": True, **metadata}
