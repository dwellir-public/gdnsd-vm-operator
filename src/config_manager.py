"""Bounded stdlib-only SSH configuration protocol and native transactions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from workload import MAX_CONTENT, Workload, WorkloadError

MAX_TOTAL = 8 * 1024 * 1024
MAX_REQUEST = MAX_TOTAL * 4 // 3 + 65536
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class Manager:
    """Manage selected files while sharing the charm's transaction lock."""

    def __init__(self, workload: Workload | None = None):
        self.workload = workload or Workload()

    def target(self, name: str) -> tuple[str, str]:
        """Resolve only protocol configuration names, never caller paths."""
        if not isinstance(name, str):
            raise WorkloadError("file names must be strings")
        scope, zone = ("global", "") if name == "config" else ("zone", name[6:])
        if name != "config" and not name.startswith("zones/"):
            raise WorkloadError("file name must be config or zones/<fqdn>")
        self.workload._path(scope, zone)
        return scope, zone

    @staticmethod
    def decode(value: str) -> bytes:
        """Decode bounded, nonempty UTF-8 native configuration."""
        if not isinstance(value, str) or len(value) > MAX_CONTENT * 4 // 3 + 8:
            raise WorkloadError("content must be bounded base64 UTF-8")
        try:
            data = base64.b64decode(value, validate=True)
            data.decode("utf-8")
        except (UnicodeError, ValueError, binascii.Error) as exc:
            raise WorkloadError("content must be base64 UTF-8") from exc
        if not 1 <= len(data) <= MAX_CONTENT or b"\x00" in data:
            raise WorkloadError("content must contain 1 byte to 2 MiB without NUL")
        return data

    def handle(self, request: dict) -> dict:
        """Validate a complete request before touching host deployment state."""
        if not isinstance(request, dict) or type(request.get("version")) is not int:
            raise WorkloadError("version must be integer 1")
        if request["version"] != 1:
            raise WorkloadError("unsupported protocol version")
        operation = request.get("operation")
        if operation not in ("snapshot", "status", "validate", "apply"):
            raise WorkloadError("unsupported operation")
        allowed = {"version", "operation"}
        if operation == "snapshot":
            allowed |= {"zones"}
        elif operation in ("validate", "apply"):
            allowed |= {"files", "expected", "bridge-b64", "release"}
        if request.keys() - allowed:
            raise WorkloadError("unknown request fields")
        if operation == "status":
            return self.status()
        if operation == "snapshot":
            zones = request.get("zones", ["n.dwellir.com", "x.dwellir.com"])
            if not isinstance(zones, list) or len(zones) > 64:
                raise WorkloadError("zones must be a list of at most 64 names")
            names = ["config"]
            for zone in zones:
                if not isinstance(zone, str):
                    raise WorkloadError("zone names must be strings")
                names.append("zones/" + zone)
            with self.workload._lock():
                files = {name: self.workload.read(*self.target(name)) for name in names}
                if sum(len(v["content-b64"]) for v in files.values()) > MAX_REQUEST:
                    raise WorkloadError("snapshot exceeds total payload limit")
                return {"files": files, "deployment": self.workload.deployment_state()}
        files, expected = request.get("files"), request.get("expected")
        if not isinstance(files, dict) or not 1 <= len(files) <= 65:
            raise WorkloadError("files must contain 1 to 65 selected files")
        if not isinstance(expected, dict) or expected.keys() != files.keys():
            raise WorkloadError("expected must cover exactly the selected files")
        data = {}
        for name, content in files.items():
            self.target(name)
            digest = expected[name]
            if not isinstance(digest, str) or (digest != "absent" and not DIGEST.fullmatch(digest)):
                raise WorkloadError("expected hashes must be sha256 or absent")
            data[name] = self.decode(content)
        bridge = self.decode(request["bridge-b64"]) if "bridge-b64" in request else None
        if bridge is not None and "config" not in data:
            raise WorkloadError("bridge requires a selected final config")
        if sum(map(len, data.values())) + len(bridge or b"") > MAX_TOTAL:
            raise WorkloadError("decoded payload exceeds 8 MiB")
        release = request.get("release", {})
        self.validate_release(release)
        with self.workload._lock():
            try:
                return self.transact(data, expected, bridge, release, operation == "validate")
            except (WorkloadError, OSError) as exc:
                state = self.workload.deployment_state()
                state["error"] = str(exc)
                state["error-files"] = list(data)
                self.workload.save_deployment_state(state)
                raise

    @staticmethod
    def validate_release(release: dict) -> None:
        """Accept only bounded release identity metadata."""
        if not isinstance(release, dict) or release.keys() - {
            "commit",
            "serial",
            "source-sha256",
            "id",
        }:
            raise WorkloadError("invalid release metadata fields")
        for key, value in release.items():
            valid = False
            if key == "serial":
                valid = type(value) is int and 0 <= value <= 4294967295
            elif isinstance(value, str):
                if key == "commit":
                    valid = re.fullmatch("[0-9a-f]{40}", value) is not None
                elif key == "source-sha256":
                    valid = DIGEST.fullmatch(value) is not None
                else:
                    valid = re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None
            if not valid:
                raise WorkloadError(f"invalid release {key}")

    def status(self) -> dict:
        """Observe service and DNS without optional GeoIP Python dependencies."""
        work = self.workload
        healthy, error = False, ""
        try:
            if not work.is_active():
                raise WorkloadError("gdnsd systemd service is not active")
            control = work.command(["gdnsdctl", "-t", "5", "status"])
            work.probe()
            healthy = True
        except WorkloadError as exc:
            error, control = str(exc), ""
        return {
            "healthy": healthy,
            "error": error,
            "control-status": control,
            "deployment": work.deployment_state(),
        }

    def transact(self, data, expected, bridge, release, validate_only):
        """Validate every transition, apply atomically per file and roll back all."""
        work = self.workload
        before = {name: work.read(*self.target(name)) for name in data}
        if any(before[name]["sha256"] != expected[name] for name in data):
            raise WorkloadError("remote file changed since snapshot; pull again")
        checksums = {name: hashlib.sha256(content).hexdigest() for name, content in data.items()}
        changed = [name for name in data if checksums[name] != expected[name]]
        if not changed and not validate_only:
            self.success(checksums, release)
            return {
                "changed": False,
                "validated": False,
                "checksums": checksums,
                "files": checksums,
            }
        # Always order zone transitions deterministically between compatibility and final config.
        stages = []
        if bridge is not None:
            stages.append(("config", bridge))
        stages.extend((name, data[name]) for name in sorted(data) if name != "config")
        if "config" in data:
            stages.append(("config", data["config"]))
        for entry in work.config_dir.rglob("*"):
            if entry.is_symlink():
                raise WorkloadError("cannot safely stage symlink configuration")
        with tempfile.TemporaryDirectory(prefix="release-", dir=work.state_dir) as directory:
            candidate = Path(directory) / "gdnsd"
            shutil.copytree(work.config_dir, candidate)
            for name, content in stages:
                selected = candidate / name
                selected.parent.mkdir(parents=True, exist_ok=True)
                selected.write_bytes(content)
                work.command(["gdnsd", "-c", str(candidate), "checkconf"])
        if validate_only:
            return {"changed": False, "validated": True, "checksums": checksums, "files": checksums}
        if any(work.read(*self.target(name))["sha256"] != expected[name] for name in data):
            raise WorkloadError("remote file changed during validation; pull again")
        attributes = {}
        backups = work.state_dir / "backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        for name in data:
            path = work._path(*self.target(name))
            info = path.stat() if path.exists() else None
            attributes[name] = (
                (stat.S_IMODE(info.st_mode), (info.st_uid, info.st_gid)) if info else (0o644, None)
            )
            if before[name]["exists"]:
                backup = backups / (name.replace("/", "_") + "-" + expected[name])
                work._write(backup, base64.b64decode(before[name]["content-b64"]), 0o600)
        touched = []
        try:
            for name, content in stages:
                path = work._path(*self.target(name))
                if path.exists() and path.read_bytes() == content:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                touched.append(name)
                work._write(path, content, *attributes[name])
                work._activate(self.target(name)[0])
        except Exception as exc:
            failures = []
            # Restore every selected file before asking gdnsd to load the original tree.
            for name in reversed(list(dict.fromkeys(touched))):
                try:
                    path = work._path(*self.target(name))
                    if before[name]["exists"]:
                        work._write(
                            path, base64.b64decode(before[name]["content-b64"]), *attributes[name]
                        )
                    else:
                        path.unlink(missing_ok=True)
                except Exception as rollback:
                    failures.append(f"{name}: {rollback}")
            try:
                work._activate("global" if "config" in touched else "zone")
            except Exception as rollback:
                failures.append(str(rollback))
            if failures:
                raise WorkloadError(
                    f"application failed: {exc}; rollback failed: {failures}; inspect {backups}"
                ) from exc
            raise WorkloadError(
                f"application failed: {exc}; previous configuration restored"
            ) from exc
        self.success(checksums, release)
        return {
            "changed": bool(touched),
            "validated": True,
            "checksums": checksums,
            "files": checksums,
        }

    def success(self, checksums, release):
        """Publish repaired configuration scopes and last successful release."""
        state = self.workload.deployment_state()
        repaired = dict(state.get("repaired", {}))
        repaired.update(checksums)
        state.update(error="", repaired=repaired, checksums=checksums, files=checksums)
        state["error-files"] = []
        if release:
            state["release"] = release
        self.workload.save_deployment_state(state)


def serve(stream, output, manager: Manager | None = None) -> int:
    """Read one bounded JSON request and emit exactly one protocol envelope."""
    try:
        raw = stream.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise WorkloadError("request exceeds input limit")
        request = json.loads(raw)
        result = (manager or Manager()).handle(request)
        envelope = {"version": 1, "ok": True, "result": result}
        code = 0
    except (WorkloadError, ValueError, OSError, TypeError, RecursionError) as exc:
        envelope = {"version": 1, "ok": False, "error": str(exc)}
        code = 1
    output.write(json.dumps(envelope) + "\n")
    return code


def main() -> int:
    """Permit only the forced-command SSH entry point."""
    if sys.argv[1:] != ["ssh"]:
        print(json.dumps({"version": 1, "ok": False, "error": "expected ssh entry point"}))
        return 1
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
