"""Exercise the bounded SSH protocol and multi-file transactions."""

import base64
import json
from pathlib import Path

import pytest

from workload import Workload, WorkloadError


def enc(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def manager(tmp_path):
    from config_manager import Manager

    config = tmp_path / "etc"
    (config / "zones").mkdir(parents=True)
    (config / "config").write_text("old global")
    (config / "zones/example.org").write_text("old zone")
    calls = []

    def command(argv):
        calls.append(argv)
        return "active" if argv[0] == "systemctl" else ""

    return Manager(Workload(config, tmp_path / "state", command, lambda: None)), calls


def request(manager, **extra):
    files = {"config": enc("new global"), "zones/example.org": enc("new zone")}
    before = manager.handle({"version": 1, "operation": "snapshot", "zones": ["example.org"]})
    return {
        "version": 1,
        "operation": "apply",
        "files": files,
        "expected": {key: value["sha256"] for key, value in before["files"].items()},
        **extra,
    }


def test_protocol_capability_is_available():
    import importlib.util

    assert importlib.util.find_spec("config_manager") is not None


def test_apply_preserves_unselected_and_is_idempotent(manager):
    host, calls = manager
    other = host.workload.config_dir / "zones/other.org"
    other.write_text("untouched")
    payload = request(host)
    result = host.handle(payload)
    assert result["changed"] and result["validated"]
    assert other.read_text() == "untouched"
    assert ["gdnsdctl", "replace"] in calls
    assert ["gdnsdctl", "reload-zones"] in calls
    calls.clear()
    payload["expected"] = result["checksums"]
    assert not host.handle(payload)["changed"]
    assert not calls


@pytest.mark.parametrize(
    "change",
    [
        {"files": {"../config": enc("bad")}},
        {"files": {"config": "!bad"}},
        {"expected": {"config": "invalid"}},
        {"version": True},
        {"operation": "execute"},
        {"command": "id"},
    ],
)
def test_invalid_requests_do_not_write(manager, change):
    host, calls = manager
    payload = request(host)
    payload.update(change)
    with pytest.raises(WorkloadError):
        host.handle(payload)
    assert (host.workload.config_dir / "config").read_text() == "old global"
    assert not calls


def test_conflict_and_symlink_rejected(manager, tmp_path):
    host, calls = manager
    payload = request(host)
    payload["expected"]["config"] = "0" * 64
    with pytest.raises(WorkloadError, match="changed"):
        host.handle(payload)
    assert not calls
    (host.workload.config_dir / "zones/link.org").symlink_to(tmp_path / "outside")
    with pytest.raises(WorkloadError, match="symlink"):
        host.handle(request(host))


def test_invalid_native_candidate_never_writes(manager):
    host, calls = manager
    payload = request(host)

    def reject(argv):
        raise WorkloadError("invalid native configuration")

    host.workload.command = reject
    with pytest.raises(WorkloadError, match="invalid native"):
        host.handle(payload)
    assert (host.workload.config_dir / "config").read_text() == "old global"
    assert (host.workload.config_dir / "zones/example.org").read_text() == "old zone"


def test_bridge_validates_every_transition_before_writes(manager):
    host, calls = manager
    payload = request(host, **{"bridge-b64": enc("old and new global")})
    stages = []

    def command(argv):
        if argv[-1] == "checkconf":
            candidate = Path(argv[2])
            stages.append(
                ((candidate / "config").read_text(), (candidate / "zones/example.org").read_text())
            )
        return "active"

    host.workload.command = command
    host.handle(payload)
    assert stages == [
        ("old and new global", "old zone"),
        ("old and new global", "new zone"),
        ("new global", "new zone"),
    ]


def test_activation_failure_restores_all_files_and_metadata(manager):
    host, calls = manager
    payload = request(host, **{"bridge-b64": enc("bridge")})
    path = host.workload.config_dir / "config"
    path.chmod(0o640)
    normal = host.workload.command
    failures = []

    def command(argv):
        if argv == ["gdnsdctl", "reload-zones"] and not failures:
            failures.append(True)
            raise WorkloadError("reload failed")
        return normal(argv)

    host.workload.command = command
    with pytest.raises(WorkloadError, match="restored"):
        host.handle(payload)
    assert path.read_text() == "old global"
    assert path.stat().st_mode & 0o777 == 0o640
    assert (host.workload.config_dir / "zones/example.org").read_text() == "old zone"
    assert "restored" in host.workload.deployment_state()["error"]
    host.handle(payload)
    assert not host.workload.deployment_state()["error"]


def test_protocol_envelope_and_bounded_input(manager):
    from io import StringIO

    from config_manager import serve

    host, calls = manager
    output = StringIO()
    assert serve(StringIO('{"version":1,"operation":"status"}'), output, host) == 0
    assert json.loads(output.getvalue())["ok"]
    output = StringIO()
    assert serve(StringIO('{"version":1,"operation":"shell"}'), output, host) == 1
    assert not json.loads(output.getvalue())["ok"]


def test_helper_installation_uses_stable_root_owned_stdlib_sources(manager, tmp_path, monkeypatch):
    host, calls = manager
    owners = []
    monkeypatch.setattr("workload.os.chown", lambda path, uid, gid: owners.append((uid, gid)))
    monkeypatch.setattr("workload.os.fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    host.workload.install_manager(tmp_path / "usr/local")
    assert owners and all(owner == (0, 0) for owner in owners)
    launcher = tmp_path / "usr/local/sbin/gdnsd-config-manager"
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert "/usr/local/lib/gdnsd-vm" in launcher.read_text()
    for name in ("workload.py", "config_manager.py"):
        source = tmp_path / "usr/local/lib/gdnsd-vm" / name
        assert source.is_file()
        assert source.stat().st_mode & 0o777 == 0o644
    assert not calls


def test_noop_records_release_checkpoint_without_activation(manager):
    host, calls = manager
    payload = request(host)
    result = host.handle(payload)
    payload["expected"] = result["checksums"]
    payload["release"] = {
        "commit": "a" * 40,
        "serial": 2026103001,
        "source-sha256": "b" * 64,
        "id": "release-1",
    }
    calls.clear()
    result = host.handle(payload)
    state = host.workload.deployment_state()
    assert state["release"] == payload["release"]
    assert state["files"] == result["files"]
    assert not result["changed"] and not calls


def test_validate_only_does_not_publish_checkpoint(manager):
    host, calls = manager
    payload = request(host, operation="validate")
    assert host.handle(payload)["validated"]
    assert not host.workload.deployment_state()
    assert not any(call[0] == "gdnsdctl" for call in calls)


def test_failed_rollback_reports_partial_state(manager):
    host, calls = manager
    payload = request(host)
    normal = host.workload.command

    def fail_control(argv):
        if argv[0] == "gdnsdctl":
            raise WorkloadError("control unavailable")
        return normal(argv)

    host.workload.command = fail_control
    with pytest.raises(WorkloadError, match="rollback failed"):
        host.handle(payload)
    assert "rollback failed" in host.workload.deployment_state()["error"]


@pytest.mark.parametrize(
    "release",
    [
        {"commit": "a"},
        {"serial": True},
        {"id": "../escape"},
        {"source-sha256": "bad"},
        {"unknown": "value"},
    ],
)
def test_release_metadata_is_strict(manager, release):
    host, calls = manager
    with pytest.raises(WorkloadError, match="release"):
        host.handle(request(host, release=release))
    assert not calls


def test_payload_total_and_empty_content_are_bounded(manager):
    host, calls = manager
    payload = request(host)
    payload["files"]["config"] = ""
    with pytest.raises(WorkloadError):
        host.handle(payload)
    files = {f"zones/a{index}.org": enc("x" * (2 * 1024 * 1024)) for index in range(5)}
    with pytest.raises(WorkloadError, match="8 MiB"):
        host.handle(
            {
                "version": 1,
                "operation": "apply",
                "files": files,
                "expected": dict.fromkeys(files, "absent"),
            }
        )
    assert not calls


def test_new_action_error_invalidates_prior_ssh_repair(manager):
    host, calls = manager
    host.handle(request(host))
    host.workload.configuration_failed("zone:example.org")
    assert "zones/example.org" not in host.workload.deployment_state()["repaired"]
    assert "config" in host.workload.deployment_state()["repaired"]


def test_shared_lock_held_during_validation_activation_and_checkpoint(manager, monkeypatch):
    import fcntl

    host, calls = manager
    payload = request(host)
    checked = []

    def assert_locked():
        with (host.workload.state_dir / "transaction.lock").open("a") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checked.append(True)

    normal_command = host.workload.command
    normal_write = host.workload._write

    def command(argv):
        assert_locked()
        return normal_command(argv)

    def write(*args):
        assert_locked()
        return normal_write(*args)

    host.workload.command = command
    monkeypatch.setattr(host.workload, "_write", write)
    host.handle(payload)
    assert len(checked) >= 8


def test_system_python_import_needs_no_optional_modules(tmp_path):
    import subprocess

    for name in ("config_manager.py", "workload.py"):
        (tmp_path / name).write_bytes((Path("src") / name).read_bytes())
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        'from config_manager import main; sys.argv = ["manager", "ssh"]; '
        "raise SystemExit(main())"
    )
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-S", "-c", code, str(tmp_path)],
        input='{"version":1,"operation":"invalid"}',
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "version": 1,
        "ok": False,
        "error": "unsupported operation",
    }
    assert not result.stderr


def test_successful_action_repairs_failed_ssh_scopes_independently(manager):
    host, calls = manager
    with host.workload._lock():
        host.workload.save_deployment_state(
            {"error": "failed release", "error-files": ["config", "zones/example.org"]}
        )
    host.workload.configuration_succeeded("global")
    assert host.workload.deployment_state()["error"] == "failed release"
    host.workload.configuration_succeeded("zone:example.org")
    assert host.workload.deployment_state()["error"] == ""
