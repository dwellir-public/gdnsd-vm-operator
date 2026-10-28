"""Exercise configuration transactions without a running Juju model."""

import base64

import pytest

from workload import Workload, WorkloadError


def encode(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def instance(tmp_path):
    config = tmp_path / "etc"
    (config / "zones").mkdir(parents=True)
    (config / "config").write_text("options => {}\n")
    (config / "zones" / "x.example.com").write_text("old zone\n")
    (config / "zones" / "n.dwellir.com").write_text("protected\n")
    calls = []

    def command(argv):
        calls.append(argv)
        if argv[0] == "dpkg-query":
            return "install ok installed"
        if argv[:2] == ["systemctl", "is-active"]:
            return "active\n"
        return ""

    return Workload(config_dir=config, state_dir=tmp_path / "state", command=command), calls


def test_selected_zone_update_preserves_other_files(instance):
    work, calls = instance
    previous = work.read("zone", "x.example.com")
    result = work.update("zone", "x.example.com", encode("new zone\n"), previous["sha256"])
    assert result["changed"]
    assert work.read("zone", "x.example.com")["content-b64"] == encode("new zone\n")
    assert (work.config_dir / "zones/n.dwellir.com").read_text() == "protected\n"
    assert (work.config_dir / "config").read_text() == "options => {}\n"
    assert ["gdnsdctl", "reload-zones"] in calls
    assert ["gdnsdctl", "replace"] not in calls


def test_invalid_candidate_never_changes_live_file(instance):
    work, calls = instance
    original = work.read("zone", "x.example.com")
    normal = work.command

    def reject(argv):
        if argv[-1] == "checkconf":
            raise WorkloadError("invalid zone")
        return normal(argv)

    work.command = reject
    with pytest.raises(WorkloadError, match="invalid zone"):
        work.update("zone", "x.example.com", encode("bad"), original["sha256"])
    assert work.read("zone", "x.example.com") == original
    assert ["gdnsdctl", "reload-zones"] not in calls


def test_stale_push_is_rejected_before_validation(instance):
    work, calls = instance
    with pytest.raises(WorkloadError, match="changed"):
        work.update("zone", "x.example.com", encode("new"), "0" * 64)
    assert not calls


def test_protected_zone_is_not_writable(instance):
    work, calls = instance
    old = work.read("zone", "n.dwellir.com")
    with pytest.raises(WorkloadError, match="protected"):
        work.update("zone", "n.dwellir.com", encode("new"), old["sha256"])
    assert not calls
    assert work.read("zone", "n.dwellir.com") == old


def test_validate_only_does_not_apply(instance):
    work, calls = instance
    old = work.read("zone", "x.example.com")
    r = work.update("zone", "x.example.com", encode("new"), old["sha256"], validate_only=True)
    assert r["validated"] and not r["changed"]
    assert work.read("zone", "x.example.com") == old
    assert all(argv[0] != "gdnsdctl" for argv in calls)


def test_reload_failure_restores_original_file(instance):
    work, calls = instance
    old = work.read("zone", "x.example.com")
    normal = work.command
    failures = []

    def fail_once(argv):
        if argv == ["gdnsdctl", "reload-zones"] and not failures:
            failures.append(True)
            raise WorkloadError("reload failed")
        return normal(argv)

    work.command = fail_once
    with pytest.raises(WorkloadError, match="restored"):
        work.update("zone", "x.example.com", encode("new"), old["sha256"])
    assert work.read("zone", "x.example.com") == old


def test_global_uses_process_replacement(instance):
    work, calls = instance
    old = work.read("global")
    work.update("global", "", encode("options => { debug => true }"), old["sha256"])
    assert ["gdnsdctl", "replace"] in calls


@pytest.mark.parametrize("zone", ["../config", "x/y", "", "x;touch", ".hidden", "X.example.com"])
def test_bad_zone_names_are_rejected(instance, zone):
    work, calls = instance
    with pytest.raises(WorkloadError):
        work.read("zone", zone)
    assert not calls


def test_noop_does_not_reload(instance):
    work, calls = instance
    old = work.read("zone", "x.example.com")
    r = work.update("zone", "x.example.com", old["content-b64"], old["sha256"])
    assert not r["changed"]
    assert not calls


def test_new_zone_can_be_created_without_changing_existing(instance):
    work, calls = instance
    old = work.read("zone", "new.example.com")
    assert old["sha256"] == "absent" and not old["exists"]
    work.update("zone", "new.example.com", encode("new zone"), "absent")
    assert work.read("zone", "new.example.com")["exists"]


def test_existing_installation_does_not_run_apt_or_change_config(instance):
    work, calls = instance
    old = work.read("global")
    work.install("10.1.2.3")
    assert work.read("global") == old
    assert all(argv[0] not in ("apt-get", "gdnsdctl") for argv in calls)
    assert not any(argv[:2] == ["systemctl", "restart"] for argv in calls)


def test_symlink_zone_is_rejected(instance, tmp_path):
    work, _ = instance
    path = work.config_dir / "zones/link.example.com"
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(WorkloadError):
        work.read("zone", "link.example.com")


def test_fresh_install_creates_private_listener_and_starts(tmp_path):
    calls = []

    def command(argv):
        calls.append(argv)
        if argv[0] == "dpkg-query":
            raise WorkloadError("not installed")
        return ""

    work = Workload(config_dir=tmp_path / "etc", state_dir=tmp_path / "state", command=command)
    work.install("10.2.3.4")
    assert "10.2.3.4" in (work.config_dir / "config").read_text()
    assert ["apt-get", "install", "-y", "gdnsd"] in calls
    assert ["systemctl", "restart", "gdnsd.service"] in calls


def test_residual_package_state_is_not_installed(tmp_path):
    calls = []

    def command(argv):
        calls.append(argv)
        return "deinstall ok config-files" if argv[0] == "dpkg-query" else ""

    work = Workload(config_dir=tmp_path / "etc", state_dir=tmp_path / "state", command=command)
    work.install("10.2.3.4")
    assert ["apt-get", "install", "-y", "gdnsd"] in calls


def test_fresh_package_preserves_preexisting_operator_configuration(instance):
    work, calls = instance
    before = work.read("global")
    normal = work.command

    def command(argv):
        if argv[0] == "dpkg-query":
            raise WorkloadError("not installed")
        return normal(argv)

    work.command = command
    work.install("10.2.3.4")
    assert work.read("global") == before


def test_failed_rollback_is_reported(instance):
    work, _ = instance
    before = work.read("zone", "x.example.com")
    normal = work.command

    def command(argv):
        if argv[0] == "gdnsdctl":
            raise WorkloadError("control unavailable")
        return normal(argv)

    work.command = command
    with pytest.raises(WorkloadError, match="rollback failed"):
        work.update("zone", "x.example.com", encode("new"), before["sha256"])
    assert work.read("zone", "x.example.com") == before


def test_fresh_install_applies_configuration_even_if_apt_started_daemon(tmp_path):
    calls = []

    def command(argv):
        calls.append(argv)
        if argv[0] == "dpkg-query":
            raise WorkloadError("not installed")
        return ""

    work = Workload(config_dir=tmp_path / "etc", state_dir=tmp_path / "state", command=command)
    work.install("10.2.3.4")
    assert ["systemctl", "restart", "gdnsd.service"] in calls
    assert ["apt-cache", "show", "gdnsd"] in calls


def test_held_package_is_adopted_without_apt_or_restart(instance):
    work, calls = instance
    normal = work.command

    def command(argv):
        if argv[0] == "dpkg-query":
            return "hold ok installed"
        return normal(argv)

    work.command = command
    work.install("10.2.3.4")
    assert not calls


def test_validate_identical_content_still_runs_native_validator(instance):
    work, calls = instance
    old = work.read("zone", "x.example.com")
    work.update("zone", "x.example.com", old["content-b64"], old["sha256"], validate_only=True)
    assert any(argv[-1] == "checkconf" for argv in calls)


def test_atomic_update_preserves_file_ownership(instance, monkeypatch):
    work, calls = instance
    owner_calls = []
    monkeypatch.setattr("workload.os.fchown", lambda fd, uid, gid: owner_calls.append((uid, gid)))
    path = work.config_dir / "zones/x.example.com"
    expected = (path.stat().st_uid, path.stat().st_gid)
    old = work.read("zone", "x.example.com")
    work.update("zone", "x.example.com", encode("new"), old["sha256"])
    assert owner_calls == [expected]


def test_rollback_preserves_ownership_on_both_writes(instance, monkeypatch):
    work, _ = instance
    path = work.config_dir / "zones/x.example.com"
    owner = (path.stat().st_uid, path.stat().st_gid)
    before = work.read("zone", "x.example.com")
    owners = []
    monkeypatch.setattr("workload.os.fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    normal = work.command
    failures = []

    def command(argv):
        if argv == ["gdnsdctl", "reload-zones"] and not failures:
            failures.append(True)
            raise WorkloadError("activation failed")
        return normal(argv)

    work.command = command
    with pytest.raises(WorkloadError, match="restored"):
        work.update("zone", "x.example.com", encode("new"), before["sha256"])
    assert owners == [owner, owner]


def test_health_reports_running_version_and_probes_dns(tmp_path):
    probes = []

    def command(argv):
        if argv[0] == "systemctl":
            return "active\n"
        return "info: version 3.8.2 running at PID 1234\n"

    work = Workload(
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "state",
        command=command,
        probe=lambda: probes.append(True),
    )
    result = work.health()
    assert result["healthy"] and result["message"].startswith("running;")
    assert result["workload-version"] == "3.8.2"
    assert probes == [True]


def test_health_stopped_does_not_probe_or_restart(tmp_path):
    calls = []

    def command(argv):
        calls.append(argv)
        return "inactive\n"

    work = Workload(command=command, probe=lambda: pytest.fail("must not probe stopped service"))
    result = work.health()
    assert not result["healthy"] and result["message"].startswith("inactive;")
    assert len(calls) == 1


def test_health_dns_failure_is_reported_without_restart():
    def command(argv):
        return "active" if argv[0] == "systemctl" else "info: version 3.8.2 running at PID 1234"

    def probe():
        raise WorkloadError("local TCP DNS timed out")

    result = Workload(command=command, probe=probe).health()
    assert not result["healthy"] and "TCP DNS" in result["message"]
    assert result["workload-version"] == "3.8.2"


@pytest.mark.parametrize("flags", [0x8000, 0x8003, 0x8005])
def test_dns_probe_accepts_valid_authoritative_responses(flags):
    from workload import check_dns_response

    query = b"\x12\x34" + b"\x00" * 10
    response = b"\x12\x34" + flags.to_bytes(2, "big") + b"\x00" * 8
    check_dns_response(query, response)


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"\x99\x99\x80\x00" + b"\x00" * 8,
        b"\x12\x34\x80\x02" + b"\x00" * 8,
        b"\x12\x34\x00\x00" + b"\x00" * 8,
    ],
)
def test_dns_probe_rejects_bad_or_servfail_responses(response):
    from workload import check_dns_response

    with pytest.raises(WorkloadError):
        check_dns_response(b"\x12\x34" + b"\x00" * 10, response)


def test_info_distinguishes_package_version_from_running_version():
    def command(argv):
        if argv[0] == "dpkg-query":
            return "3.8.2-1build2"
        if argv[0] == "systemctl":
            return "active"
        return "info: version 3.8.2 running at PID 1234"

    info = Workload(command=command, probe=lambda: None).info()
    assert info["package-version"] == "3.8.2-1build2"
    assert info["workload-version"] == "3.8.2"
    assert info["state"] == "running"
