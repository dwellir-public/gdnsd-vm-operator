"""Manage gdnsd files and its local control socket without Juju dependencies."""

from __future__ import annotations

import base64
import binascii
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

MAX_CONTENT = 2 * 1024 * 1024
ZONE_PATTERN = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


class WorkloadError(RuntimeError):
    """An operation could not be completed without operator attention."""


def run_command(argv: list[str]) -> str:
    """Execute a bounded native command without invoking a shell."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=240, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc))[-6000:]
        raise WorkloadError(f"{argv[0]} failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkloadError(f"{argv[0]} failed: {exc}") from exc
    return result.stdout or result.stderr


def check_dns_response(query: bytes, response: bytes) -> None:
    """Accept a matching DNS response without requiring authority for the root."""
    if len(response) < 12 or response[:2] != query[:2]:
        raise WorkloadError("local DNS returned an invalid transaction")
    flags = int.from_bytes(response[2:4], "big")
    if not flags & 0x8000 or flags & 0x7800 or (flags & 0xF) not in {0, 3, 5}:
        raise WorkloadError("local DNS returned an unhealthy response")


def probe_local_dns(address: str = "127.0.0.1", port: int = 53, timeout: float = 2) -> None:
    """Check both local DNS transports using a nonrecursive root SOA question."""
    query = os.urandom(2) + struct.pack("!HHHHH", 0, 1, 0, 0, 0) + b"\x00\x00\x06\x00\x01"
    transport = "UDP"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(timeout)
            connection.connect((address, port))
            connection.send(query)
            check_dns_response(query, connection.recv(4096))
        transport = "TCP"
        with socket.create_connection((address, port), timeout=timeout) as connection:
            connection.sendall(len(query).to_bytes(2, "big") + query)

            def receive(count: int) -> bytes:
                data = b""
                while len(data) < count:
                    part = connection.recv(count - len(data))
                    if not part:
                        raise WorkloadError("local TCP DNS closed its connection early")
                    data += part
                return data

            length = int.from_bytes(receive(2), "big")
            if not 12 <= length <= 4096:
                raise WorkloadError("local TCP DNS returned an invalid length")
            check_dns_response(query, receive(length))
    except (OSError, WorkloadError) as exc:
        raise WorkloadError(f"local {transport} DNS check failed: {exc}") from exc


class Workload:
    """Apply one selected configuration file as a validated transaction."""

    def __init__(
        self,
        config_dir: Path = Path("/etc/gdnsd"),
        state_dir: Path = Path("/var/lib/gdnsd-vm"),
        command: Callable[[list[str]], str] = run_command,
        probe: Callable[[], None] = probe_local_dns,
    ):
        self.config_dir = config_dir
        self.state_dir = state_dir
        self.command = command
        self.probe = probe

    def _path(self, scope: str, zone: str) -> Path:
        """Resolve an exact configuration target and reject indirect paths."""
        if scope == "global":
            if zone:
                raise WorkloadError("global scope cannot include a zone")
            path = self.config_dir / "config"
        elif scope == "zone":
            if len(zone) > 253 or not ZONE_PATTERN.fullmatch(zone):
                raise WorkloadError(
                    "zone must be a lowercase fully qualified name without a trailing dot"
                )
            path = self.config_dir / "zones" / zone
        else:
            raise WorkloadError("scope must be zone or global")
        if self.config_dir.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise WorkloadError("configuration paths must not be symlinks")
        if path.exists() and not path.is_file():
            raise WorkloadError("configuration target is not a regular file")
        return path

    def read(self, scope: str, zone: str = "") -> dict:
        """Read the selected file, returning a conflict token for later updates."""
        path = self._path(scope, zone)
        exists = path.exists()
        data = path.read_bytes() if exists else b""
        if len(data) > MAX_CONTENT:
            raise WorkloadError("configuration exceeds the 2 MiB action limit")
        return {
            "content-b64": base64.b64encode(data).decode("ascii"),
            "sha256": hashlib.sha256(data).hexdigest() if exists else "absent",
            "exists": exists,
        }

    @contextlib.contextmanager
    def _lock(self):
        """Serialize charm transactions, including validation and rollback."""
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.state_dir / "transaction.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _validate(self, path: Path, data: bytes) -> None:
        """Validate the complete candidate tree without changing served files."""
        for entry in self.config_dir.rglob("*"):
            if entry.is_symlink():
                raise WorkloadError(
                    f"cannot safely stage symlink {entry.relative_to(self.config_dir)}"
                )
        with tempfile.TemporaryDirectory(prefix="candidate-", dir=self.state_dir) as directory:
            candidate = Path(directory) / "gdnsd"
            shutil.copytree(self.config_dir, candidate)
            selected = candidate / path.relative_to(self.config_dir)
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_bytes(data)
            self.command(["gdnsd", "-c", str(candidate), "checkconf"])

    @staticmethod
    def _write(path: Path, data: bytes, mode: int, owner: tuple[int, int] | None = None) -> None:
        """Replace a regular file atomically on its own filesystem."""
        fd, name = tempfile.mkstemp(prefix=".gdnsd-vm-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                if owner is not None:
                    os.fchown(stream.fileno(), *owner)
                os.fchmod(stream.fileno(), mode)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _activate(self, scope: str) -> None:
        """Ask the running daemon to load data and confirm it remains active."""
        self.command(["gdnsdctl", "reload-zones" if scope == "zone" else "replace"])
        self.command(["gdnsdctl", "status"])
        if not self.is_active():
            raise WorkloadError("gdnsd systemd service is not active after application")

    def update(
        self,
        scope: str,
        zone: str,
        content_b64: str,
        expected_sha256: str,
        validate_only: bool = False,
    ) -> dict:
        """Validate and optionally apply one file, restoring it on failure."""
        path = self._path(scope, zone)
        if len(content_b64) > (MAX_CONTENT * 4 // 3) + 8:
            raise WorkloadError("configuration exceeds the 2 MiB action limit")
        try:
            data = base64.b64decode(content_b64, validate=True)
            data.decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise WorkloadError(
                "content-b64 must contain base64-encoded UTF-8 configuration"
            ) from exc
        if not data or len(data) > MAX_CONTENT:
            raise WorkloadError("configuration must contain between 1 byte and 2 MiB")
        new_digest = hashlib.sha256(data).hexdigest()
        with self._lock():
            before = self.read(scope, zone)
            if before["sha256"] != expected_sha256:
                raise WorkloadError(
                    "remote file changed since pull; pull again and rebase your edit"
                )
            if before["sha256"] == new_digest and not validate_only:
                return {"sha256": new_digest, "changed": False, "validated": False}
            self._validate(path, data)
            if validate_only:
                return {"sha256": new_digest, "changed": False, "validated": True}
            if self.read(scope, zone)["sha256"] != expected_sha256:
                raise WorkloadError("remote file changed during validation; pull again")
            old = base64.b64decode(before["content-b64"])
            attributes = path.stat() if before["exists"] else None
            mode = stat.S_IMODE(attributes.st_mode) if attributes else 0o644
            owner = (attributes.st_uid, attributes.st_gid) if attributes else None
            backups = self.state_dir / "backups"
            backups.mkdir(mode=0o700, exist_ok=True)
            backup = backups / f"{path.name}-{expected_sha256}"
            if before["exists"] and not backup.exists():
                backup.write_bytes(old)
                backup.chmod(0o600)
            self._write(path, data, mode, owner)
            try:
                self._activate(scope)
            except Exception as apply_error:
                try:
                    if before["exists"]:
                        self._write(path, old, mode, owner)
                    else:
                        path.unlink()
                    self._activate(scope)
                except Exception as rollback_error:
                    raise WorkloadError(
                        f"application failed: {apply_error}; rollback failed: {rollback_error}; "
                        f"inspect {backup}"
                    ) from rollback_error
                raise WorkloadError(
                    f"application failed: {apply_error}; previous configuration restored"
                ) from apply_error
            checkpoint = {"scope": scope, "zone": zone, "sha256": new_digest}
            (self.state_dir / "last-applied.json").write_text(json.dumps(checkpoint) + "\n")
            return {"sha256": new_digest, "changed": True, "validated": True}

    def deployment_state(self) -> dict:
        """Read shared SSH deployment state without importing charm dependencies."""
        path = self.state_dir / "deployment.json"
        if not path.exists():
            return {}
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise ValueError("expected an object")
            return state
        except (ValueError, OSError) as exc:
            raise WorkloadError(f"cannot read deployment state: {exc}") from exc

    def save_deployment_state(self, state: dict) -> None:
        """Atomically save shared state while the caller holds the transaction lock."""
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._write(self.state_dir / "deployment.json", (json.dumps(state) + "\n").encode(), 0o600)

    def configuration_failed(self, key: str) -> None:
        """Invalidate an earlier SSH repair when a later action fails for that file."""
        key = key.removeprefix("read:")
        name = "config" if key == "global" else "zones/" + key.removeprefix("zone:")
        with self._lock():
            state = self.deployment_state()
            repaired = state.get("repaired", {})
            if name in repaired:
                repaired.pop(name)
                self.save_deployment_state(state)

    def configuration_succeeded(self, key: str) -> None:
        """Clear SSH failure scopes repaired by a successful apply action."""
        name = "config" if key == "global" else "zones/" + key.removeprefix("zone:")
        with self._lock():
            state = self.deployment_state()
            affected = state.get("error-files", [])
            if name in affected:
                affected.remove(name)
                state["error-files"] = affected
                if not affected:
                    state["error"] = ""
                self.save_deployment_state(state)

    def install_manager(self, prefix: Path = Path("/usr/local")) -> None:
        """Reconcile a root-owned standalone helper without changing the DNS service."""
        library = prefix / "lib/gdnsd-vm"
        binary = prefix / "sbin"
        for directory in (prefix, prefix / "lib", library, binary):
            if directory.is_symlink():
                raise WorkloadError("helper directories must not be symlinks")
            directory.mkdir(mode=0o755, parents=True, exist_ok=True)
            directory.chmod(0o755)
            os.chown(directory, 0, 0)
        for name in ("workload.py", "config_manager.py"):
            self._write(library / name, Path(__file__).with_name(name).read_bytes(), 0o644, (0, 0))
        launcher = (
            "#!/usr/bin/python3 -I\nimport sys\n"
            "sys.path.insert(0, '/usr/local/lib/gdnsd-vm')\n"
            "from config_manager import main\nraise SystemExit(main())\n"
        )
        self._write(binary / "gdnsd-config-manager", launcher.encode(), 0o755, (0, 0))

    def install(self, address: str) -> None:
        """Install only when absent, adopting existing installations unchanged."""
        try:
            status = self.command(["dpkg-query", "-W", "-f=${Status}", "gdnsd"])
            installed = status.split()[-2:] == ["ok", "installed"]
        except WorkloadError:
            installed = False
        if installed:
            if not (self.config_dir / "config").is_file():
                raise WorkloadError(
                    "existing package has no gdnsd configuration; restore it before adoption"
                )
            return
        bind = str(ipaddress.ip_address(address))
        preserve_config = (self.config_dir / "config").is_file()
        self.command(["apt-get", "update"])
        try:
            self.command(["apt-cache", "show", "gdnsd"])
        except WorkloadError as exc:
            raise WorkloadError(
                "gdnsd is unavailable; enable Ubuntu universe and retry install"
            ) from exc
        self.command(["apt-get", "install", "-y", "gdnsd"])
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "zones").mkdir(exist_ok=True)
        text = f"options => {{ listen => [127.0.0.1, {bind}] }}\n"
        if not preserve_config:
            (self.config_dir / "config").write_text(text)
        self.command(["gdnsd", "checkconf"])
        self.command(["systemctl", "enable", "gdnsd.service"])
        # APT may have started the default service before this initial config existed.
        self.command(["systemctl", "restart", "gdnsd.service"])

    def sync_geoip(self, url: str, sha256: str = "") -> dict:
        """Download and validate a database using the shared transaction lock."""
        from geoip import sync_database

        return sync_database(self, url, sha256)

    def geoip_info(self, include_sha256: bool = False) -> dict:
        """Report the installed database's type, UTC build timestamp and optional hash."""
        from geoip import inspect_database

        return inspect_database(
            self.config_dir / "geoip/GeoLite2-City.mmdb", include_sha256=include_sha256
        )

    def health(self) -> dict:
        """Observe systemd, the running version and local DNS without mutation."""
        version = ""
        state = "unknown"
        geoip = self.geoip_info()

        def result(healthy: bool, state: str, message: str) -> dict:
            return {
                "healthy": healthy,
                "state": state,
                "message": f"{message}; {geoip['geoip-summary']}",
                "workload-version": version,
                **geoip,
            }

        try:
            state = (
                self.command(
                    ["systemctl", "show", "gdnsd.service", "--property=ActiveState", "--value"]
                ).strip()
                or "unknown"
            )
            if state != "active":
                return result(False, state, state)
            status = self.command(["gdnsdctl", "-t", "5", "status"])
            match = re.search(r"version\s+(\S+)\s+running\s+at\s+PID\s+\d+", status)
            if not match:
                raise WorkloadError("cannot determine running gdnsd version from control socket")
            version = match[1]
            self.probe()
        except WorkloadError as exc:
            return result(False, state, f"{state}; {exc}")
        return result(True, "running", "running")

    def is_active(self) -> bool:
        """Observe systemd state without attempting recovery or a restart."""
        try:
            return self.command(["systemctl", "is-active", "gdnsd.service"]).strip() == "active"
        except WorkloadError:
            return False

    def service(self, action: str) -> dict:
        """Apply an allowlisted operation to the single managed systemd unit."""
        if action not in {"start", "stop", "restart", "status", "enable", "disable"}:
            raise WorkloadError("action must be start, stop, restart, status, enable or disable")
        if action != "status":
            self.command(["systemctl", action, "gdnsd.service"])
        return {"service-name": "gdnsd.service", "active": self.is_active()}

    def info(self) -> dict:
        """Return installed package identity and observational service state."""
        version = self.command(["dpkg-query", "-W", "-f=${Version}", "gdnsd"]).strip()
        health = self.health()
        return {
            "package-version": version,
            "workload-version": health["workload-version"],
            "service-name": "gdnsd.service",
            "active": health["healthy"],
            "state": health["state"],
            "message": health["message"],
            **self.geoip_info(include_sha256=True),
        }
