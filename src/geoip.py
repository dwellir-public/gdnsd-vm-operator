"""Download, validate and atomically replace a gdnsd MaxMind database."""

from __future__ import annotations

import gzip
import hashlib
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import maxminddb

from workload import WorkloadError

MAX_DATABASE_BYTES = 256 * 1024 * 1024


def validate_url(url: str) -> None:
    """Allow HTTP database endpoints without accepting local file URLs."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WorkloadError("GeoIP URL must be an HTTP or HTTPS database endpoint")


def _copy_limited(source, destination, initial: bytes = b"") -> str:
    """Copy one bounded payload and return its content digest."""
    digest = hashlib.sha256()
    total = 0
    block = initial
    while True:
        if block:
            total += len(block)
            if total > MAX_DATABASE_BYTES:
                raise WorkloadError("GeoIP data exceeds the 256 MiB size limit")
            digest.update(block)
            destination.write(block)
        block = source.read(1024 * 1024)
        if not block:
            break
    if not total:
        raise WorkloadError("GeoIP database is empty")
    return digest.hexdigest()


def download_database(url: str, destination: Path) -> str:
    """Download bounded bytes, resolving recognizable Nextcloud share pages once."""
    validate_url(url)
    current = url
    try:
        for attempt in range(2):
            request = urllib.request.Request(current, headers={"User-Agent": "gdnsd-vm/0.1"})
            with urllib.request.urlopen(request, timeout=30) as response:
                final = response.geturl()
                validate_url(final)
                if url.startswith("https:") and not final.startswith("https:"):
                    raise WorkloadError("GeoIP download redirected from HTTPS to insecure HTTP")
                prefix = response.read(65536)
                parsed = urllib.parse.urlsplit(final)
                if (
                    attempt == 0
                    and "text/html" in response.headers.get("Content-Type", "")
                    and re.fullmatch(r".*/s/[^/]+/?", parsed.path)
                    and b"nextcloud" in prefix.lower()
                ):
                    current = urllib.parse.urlunsplit(
                        parsed._replace(path=parsed.path.rstrip("/") + "/download", fragment="")
                    )
                    continue
                with destination.open("wb") as out:
                    return _copy_limited(response, out, prefix)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise WorkloadError(f"GeoIP download failed: {exc}") from exc
    raise WorkloadError("GeoIP share did not supply a downloadable file")


def _safe_tar_headers(path: Path) -> bool:
    """Bound headers before tarfile can process recursive extension metadata."""
    size = path.stat().st_size
    total = 0
    count = 0
    with path.open("rb") as stream:
        while block := stream.read(tarfile.BLOCKSIZE):
            if not block.strip(b"\0"):
                return count > 0
            try:
                member = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
            except tarfile.HeaderError as exc:
                if count == 0:
                    return False
                raise WorkloadError(f"GeoIP archive has an invalid header: {exc}") from exc
            count += 1
            try:
                member.name.encode("utf-8")
            except UnicodeError as exc:
                raise WorkloadError("GeoIP archive contains a non-UTF-8 name") from exc
            name = PurePosixPath(member.name)
            if (
                count > 1024
                or member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}
                or name.is_absolute()
                or ".." in name.parts
                or "\\" in member.name
            ):
                raise WorkloadError("GeoIP archive contains unsafe or unsupported metadata")
            if member.size < 0 or (member.isdir() and member.size != 0):
                raise WorkloadError("GeoIP archive has an invalid member size")
            total += member.size
            if total > MAX_DATABASE_BYTES:
                raise WorkloadError("GeoIP archive exceeds the 256 MiB size limit")
            if stream.tell() + member.size > size:
                raise WorkloadError("GeoIP archive is truncated")
            stream.seek(((member.size + 511) // 512) * 512, 1)
    return count > 0


def unpack_database(download: Path, destination: Path) -> dict:
    """Decode raw/gzip data or copy exactly one safe regular MMDB from a tar archive."""
    with download.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    try:
        with tempfile.TemporaryDirectory(prefix="geoip-unpack-", dir=destination.parent) as folder:
            payload = download
            if compressed:
                payload = Path(folder) / "uncompressed"
                with gzip.open(download, "rb") as source, payload.open("wb") as out:
                    _copy_limited(source, out)
            member_name = ""
            archive = tarfile.open(payload, "r:") if _safe_tar_headers(payload) else None
            if archive is not None:
                kind = "tar.gz" if compressed else "tar"
                with archive:
                    selected = []
                    total = 0
                    for index, member in enumerate(archive):
                        name = PurePosixPath(member.name)
                        if (
                            index >= 1024
                            or name.is_absolute()
                            or ".." in name.parts
                            or "\\" in member.name
                            or not (member.isfile() or member.isdir())
                        ):
                            raise WorkloadError("GeoIP archive contains an unsafe entry")
                        if member.size < 0:
                            raise WorkloadError("GeoIP archive has an invalid member size")
                        total += member.size
                        if total > MAX_DATABASE_BYTES:
                            raise WorkloadError("GeoIP archive exceeds the 256 MiB size limit")
                        if member.isfile() and name.suffix.lower() == ".mmdb":
                            selected.append(member)
                    if len(selected) != 1:
                        raise WorkloadError(
                            "GeoIP archive must contain exactly one regular .mmdb file"
                        )
                    member_name = selected[0].name
                    source = archive.extractfile(selected[0])
                    if source is None:
                        raise WorkloadError("GeoIP archive database cannot be read")
                    with source, destination.open("wb") as out:
                        digest = _copy_limited(source, out)
            else:
                kind = "gzip" if compressed else "raw"
                with payload.open("rb") as source, destination.open("wb") as out:
                    digest = _copy_limited(source, out)
    except (OSError, EOFError, tarfile.TarError, zlib.error) as exc:
        raise WorkloadError(f"GeoIP gzip/archive decoding failed: {exc}") from exc
    return {"sha256": digest, "download-format": kind, "archive-member": member_name}


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


def inspect_database(path: Path, include_sha256: bool = False) -> dict:
    """Describe the installed file without claiming gdnsd exposes its loaded version."""
    info = {
        "geoip-database-path": str(path),
        "geoip-version-source": "installed-file-metadata",
        "geoip-state": "absent",
        "geoip-database-type": "",
        "geoip-build-epoch": 0,
        "geoip-build-time": "",
        "geoip-summary": "GeoIP not installed",
    }
    if not path.exists():
        return info
    try:
        metadata = validate_database(path)
        built = (
            datetime.fromtimestamp(metadata["build-epoch"], UTC).isoformat().replace("+00:00", "Z")
        )
        info.update(
            {
                "geoip-state": "installed",
                "geoip-database-type": metadata["database-type"],
                "geoip-build-epoch": metadata["build-epoch"],
                "geoip-build-time": built,
                "geoip-summary": f"GeoIP installed: {metadata['database-type']} {built}",
            }
        )
        if include_sha256:
            with path.open("rb") as stream:
                info["geoip-sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    except (WorkloadError, OSError, ValueError, OverflowError) as exc:
        info.update(
            {
                "geoip-state": "invalid",
                "geoip-summary": "GeoIP installed: invalid database",
                "geoip-error": str(exc),
            }
        )
    return info


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
        artifact = Path(directory) / "database.download"
        download_digest = download_database(url, artifact)
        if expected_sha256 and download_digest != expected_sha256.lower():
            raise WorkloadError("GeoIP SHA-256 mismatch; the active database was not changed")
        downloaded = Path(directory) / "database.mmdb"
        payload = unpack_database(artifact, downloaded)
        digest = payload["sha256"]
        metadata = validate_database(downloaded)
        with workload._lock():
            check_protected_zones(workload)
            before = database.read_bytes() if database.exists() else None
            if before is not None and hashlib.sha256(before).hexdigest() == digest:
                return {
                    "url": url,
                    **payload,
                    "download-sha256": download_digest,
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
            return {
                "url": url,
                **payload,
                "download-sha256": download_digest,
                "changed": True,
                "validated": True,
                **metadata,
            }
