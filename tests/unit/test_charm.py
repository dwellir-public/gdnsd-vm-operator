"""Exercise Juju lifecycle and action contracts without host mutations."""

from unittest.mock import patch

import ops
import pytest
from ops.testing import Address, BindAddress, Context, Network, State

from charm import GdnsdCharm


@pytest.fixture
def workload():
    """Replace host operations at the workload boundary."""
    with patch("charm.Workload") as factory:
        instance = factory.return_value
        instance.health.return_value = {
            "healthy": True,
            "message": "running",
            "workload-version": "3.8.2",
        }
        instance.read.return_value = {"content-b64": "eA==", "sha256": "a" * 64, "exists": True}
        instance.update.return_value = {"sha256": "b" * 64, "changed": True, "validated": True}
        instance.service.return_value = {"active": True, "enabled": True}
        instance.info.return_value = {"version": "3.8.0", "active": True}
        yield factory, instance


@pytest.fixture
def ctx():
    """Load definitions from the consolidated charmcraft file."""
    return Context(GdnsdCharm)


def test_install_uses_private_address_and_opens_dns_ports(ctx, workload):
    _, host = workload
    network = Network(
        binding_name="juju-info",
        bind_addresses=[BindAddress(addresses=[Address(value="10.20.30.40")])],
    )
    state = ctx.run(ctx.on.install(), State(networks={network}))
    host.install.assert_called_once_with("10.20.30.40")
    assert {(port.protocol, port.port) for port in state.opened_ports} == {("tcp", 53), ("udp", 53)}
    assert state.unit_status == ops.ActiveStatus("running")
    assert state.workload_version == "3.8.2"


@pytest.mark.parametrize("event", ["upgrade_charm", "update_status", "config_changed"])
@pytest.mark.parametrize("active", [True, False])
def test_reconciliation_only_observes_service(ctx, workload, event, active):
    factory, host = workload
    host.health.return_value = {
        "healthy": active,
        "message": "running" if active else "gdnsd service is stopped",
        "workload-version": "3.8.2",
    }
    state = ctx.run(getattr(ctx.on, event)(), State())
    factory.assert_called_once_with(protected_zones={"n.dwellir.com"})
    host.health.assert_called_once_with()
    host.is_active.assert_not_called()
    host.install.assert_not_called()
    host.update.assert_not_called()
    host.service.assert_not_called()
    assert isinstance(state.unit_status, ops.ActiveStatus if active else ops.BlockedStatus)


def test_config_reconciles_protected_names_without_workload_writes(ctx, workload):
    factory, host = workload
    ctx.run(
        ctx.on.config_changed(),
        State(config={"protected-zones": " N.DWELLIR.COM., example.org  second.org "}),
    )
    factory.assert_called_once_with(protected_zones={"n.dwellir.com", "example.org", "second.org"})
    host.update.assert_not_called()
    host.install.assert_not_called()


def test_get_config_returns_content_and_digest(ctx, workload):
    _, host = workload
    ctx.run(ctx.on.action("get-config", params={"zone": "example.org"}), State())
    host.read.assert_called_once_with("zone", "example.org")
    assert ctx.action_results == host.read.return_value


def test_get_global_config(ctx, workload):
    _, host = workload
    ctx.run(ctx.on.action("get-config", params={"scope": "global"}), State())
    host.read.assert_called_once_with("global", "")


@pytest.mark.parametrize(
    "action,validate_only", [("validate-config", True), ("apply-config", False)]
)
def test_configuration_actions_pass_concurrency_token(ctx, workload, action, validate_only):
    _, host = workload
    ctx.run(
        ctx.on.action(
            action,
            params={"zone": "example.org", "content-b64": "eA==", "expected-sha256": "a" * 64},
        ),
        State(),
    )
    host.update.assert_called_once_with(
        "zone", "example.org", "eA==", "a" * 64, validate_only=validate_only
    )
    assert ctx.action_results == host.update.return_value


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status", "enable", "disable"])
def test_service_actions_are_explicit(ctx, workload, action):
    _, host = workload
    ctx.run(ctx.on.action("service", params={"action": action}), State())
    host.service.assert_called_once_with(action)
    assert ctx.action_results == host.service.return_value


def test_get_info_returns_workload_details(ctx, workload):
    _, host = workload
    ctx.run(ctx.on.action("get-info"), State())
    host.info.assert_called_once_with()
    assert ctx.action_results == host.info.return_value


@pytest.mark.parametrize(
    "name,params,method",
    [
        ("get-config", {"zone": "example.org"}, "read"),
        (
            "apply-config",
            {"zone": "example.org", "content-b64": "eA==", "expected-sha256": "a" * 64},
            "update",
        ),
        ("service", {"action": "restart"}, "service"),
        ("get-info", {}, "info"),
    ],
)
def test_rejected_actions_preserve_healthy_unit_status(ctx, workload, name, params, method):
    _, host = workload
    getattr(host, method).side_effect = ValueError("rejected input")
    with pytest.raises(ops.testing.ActionFailed, match="rejected input") as result:
        ctx.run(ctx.on.action(name, params=params), State(unit_status=ops.ActiveStatus()))
    assert result.value.state.unit_status == ops.ActiveStatus()
    assert any("rejected input" in entry.message for entry in ctx.juju_log)


def test_install_failure_sets_blocked(ctx, workload):
    _, host = workload
    host.install.side_effect = ValueError("installation failed")
    state = ctx.run(ctx.on.install(), State())
    assert state.unit_status == ops.BlockedStatus("installation failed")


def test_workload_error_is_a_clean_action_failure(ctx, workload):
    from workload import WorkloadError

    _, host = workload
    host.read.side_effect = WorkloadError("zone is protected")
    with pytest.raises(ops.testing.ActionFailed, match="zone is protected") as result:
        ctx.run(
            ctx.on.action("get-config", params={"zone": "example.org"}),
            State(unit_status=ops.ActiveStatus()),
        )
    assert result.value.state.unit_status == ops.ActiveStatus()


@pytest.mark.parametrize("event", ["update_status", "upgrade_charm", "config_changed"])
def test_stop_remains_stopped_on_following_hooks(ctx, workload, event):
    _, host = workload
    host.health.return_value = {
        "healthy": False,
        "message": "gdnsd service is stopped",
        "workload-version": "3.8.2",
    }
    state = ctx.run(ctx.on.action("service", params={"action": "stop"}), State())
    assert isinstance(state.unit_status, ops.BlockedStatus)
    host.reset_mock()
    state = ctx.run(getattr(ctx.on, event)(), state)
    assert isinstance(state.unit_status, ops.BlockedStatus)
    host.service.assert_not_called()
    host.install.assert_not_called()
    host.update.assert_not_called()


def test_health_check_failure_sets_blocked_without_changing_service(ctx, workload):
    from workload import WorkloadError

    _, host = workload
    host.health.side_effect = WorkloadError("systemd unavailable")
    state = ctx.run(ctx.on.update_status(), State())
    assert state.unit_status == ops.BlockedStatus("systemd unavailable")
    host.service.assert_not_called()


@pytest.mark.parametrize(
    "name,params,missing",
    [
        ("apply-config", {"content-b64": "eA=="}, "expected-sha256"),
        ("validate-config", {"expected-sha256": "absent"}, "content-b64"),
        ("service", {}, "action"),
    ],
)
def test_missing_action_parameter_fails_cleanly(ctx, workload, name, params, missing):
    _, host = workload
    with pytest.raises(
        ops.testing.ActionFailed, match=f"Missing required action parameter: {missing}"
    ) as result:
        ctx.run(ctx.on.action(name, params=params), State(unit_status=ops.ActiveStatus()))
    assert result.value.state.unit_status == ops.ActiveStatus()
    host.update.assert_not_called()
    host.service.assert_not_called()


def test_update_status_checks_health_and_sets_version_every_time(ctx, workload):
    _, host = workload
    state = State()
    for expected_calls in range(1, 4):
        state = ctx.run(ctx.on.update_status(), state)
        assert host.health.call_count == expected_calls
        assert state.workload_version == "3.8.2"
        assert state.unit_status == ops.ActiveStatus("running")
    host.service.assert_not_called()
    host.install.assert_not_called()
    host.update.assert_not_called()


@pytest.mark.parametrize("message", ["UDP DNS probe failed", "TCP DNS probe failed"])
def test_unhealthy_dns_reports_reason_and_known_version(ctx, workload, message):
    _, host = workload
    host.health.return_value = {
        "healthy": False,
        "message": message,
        "workload-version": "3.8.2",
    }
    state = ctx.run(ctx.on.update_status(), State())
    assert state.unit_status == ops.BlockedStatus(message)
    assert state.workload_version == "3.8.2"
    host.service.assert_not_called()


def test_unknown_version_preserves_last_known_workload_version(ctx, workload):
    _, host = workload
    host.health.return_value = {
        "healthy": False,
        "message": "gdnsd control socket unavailable",
        "workload-version": "",
    }
    state = ctx.run(ctx.on.update_status(), State(workload_version="3.8.2"))
    assert state.workload_version == "3.8.2"
    assert state.unit_status == ops.BlockedStatus("gdnsd control socket unavailable")


@pytest.mark.parametrize(
    "error", [ValueError("invalid version output"), ValueError("probe failed")]
)
def test_health_error_blocks_and_preserves_known_version(ctx, workload, error):
    _, host = workload
    host.health.side_effect = error
    state = ctx.run(ctx.on.update_status(), State(workload_version="3.8.2"))
    assert state.workload_version == "3.8.2"
    assert state.unit_status == ops.BlockedStatus(str(error))
    host.service.assert_not_called()
    host.install.assert_not_called()


@pytest.mark.parametrize("name", ["apply-config", "service"])
def test_successful_mutating_action_observes_health(ctx, workload, name):
    _, host = workload
    params = (
        {"action": "start"}
        if name == "service"
        else {"zone": "example.org", "content-b64": "eA==", "expected-sha256": "a" * 64}
    )
    state = ctx.run(ctx.on.action(name, params=params), State())
    host.health.assert_called_once_with()
    assert state.unit_status == ops.ActiveStatus("running")
    assert state.workload_version == "3.8.2"
