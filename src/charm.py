#!/usr/bin/env python3
"""Juju event orchestration for the gdnsd machine workload."""

from __future__ import annotations

import logging
import subprocess
from functools import cached_property

import ops
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider

from observability import Exporter, LeaderProvider, payload
from workload import ZONE_PATTERN, Workload, WorkloadError

logger = logging.getLogger(__name__)


class GdnsdCharm(ops.CharmBase):
    """Manage gdnsd through explicit configuration and service actions."""

    _stored = ops.StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self._stored.set_default(geoip_url="", config_errors={}, exporter_error="")
        self.observability = LeaderProvider(
            self,
            payload_factory=lambda: payload(self),
            refresh_events=[self.on.config_changed, self.on.upgrade_charm, self.on.leader_elected],
        )
        self.dashboards = GrafanaDashboardProvider(self)
        self.framework.observe(self.on.upgrade_charm, self._on_exporter_refresh)
        self.framework.observe(self.on.install, self._on_install)
        for event in (self.on.upgrade_charm, self.on.update_status, self.on.config_changed):
            self.framework.observe(event, self._on_observe)
        for event in (
            self.on.get_config_action,
            self.on.validate_config_action,
            self.on.apply_config_action,
            self.on.service_action,
            self.on.get_info_action,
            self.on.sync_geoip_action,
        ):
            self.framework.observe(event, self._on_action)

    @cached_property
    def workload(self) -> Workload:
        """Build the native workload helper."""
        return Workload()

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install or adopt gdnsd and expose its DNS listener ports."""
        try:
            binding = self.model.get_binding("juju-info")
            if binding is None or binding.network.bind_address is None:
                raise WorkloadError("No private address is available for gdnsd")
            self.workload.install(str(binding.network.bind_address))
            self.workload.install_manager()
            Exporter().reconcile()
            self.unit.open_port("tcp", 53)
            self.unit.open_port("udp", 53)
            self._observe_status()
        except (WorkloadError, ValueError, OSError, subprocess.SubprocessError) as exc:
            logger.exception("gdnsd installation failed")
            self.unit.status = ops.BlockedStatus(str(exc))

    def _on_exporter_refresh(self, event: ops.HookEvent) -> None:
        """Refresh only the auxiliary exporter, preserving the DNS process."""
        try:
            self.workload.install_manager()
            Exporter().reconcile()
            self._stored.exporter_error = ""
        except (WorkloadError, OSError, subprocess.SubprocessError):
            logger.exception("Unable to reconcile gdnsd exporter")
            self._stored.exporter_error = (
                "DNS management helper or exporter installation failed; see logs"
            )

    def _on_observe(self, event: ops.HookEvent) -> None:
        """Observe service health without changing host files or service state."""
        self._observe_status()

    def _observe_status(self, request_error: bool = False) -> None:
        """Publish the observed workload version and local service health."""
        try:
            health = self.workload.health()
            deployment = self.workload.deployment_state()
            if version := health["workload-version"]:
                self.unit.set_workload_version(version)
        except (WorkloadError, ValueError) as exc:
            self.unit.status = ops.BlockedStatus(str(exc))
            return
        errors = dict(self._stored.config_errors)
        try:
            for name, digest in deployment.get("repaired", {}).items():
                scope, zone = ("global", "") if name == "config" else ("zone", name[6:])
                if self.workload.read(scope, zone)["sha256"] == digest:
                    key = "global" if scope == "global" else f"zone:{zone}"
                    errors.pop(key, None)
                    errors.pop(f"read:{key}", None)
        except (WorkloadError, OSError) as exc:
            self.unit.status = ops.BlockedStatus(str(exc))
            return
        if inspection_error := health.get("geoip-error"):
            logger.warning("GeoIP database inspection failed: %s", inspection_error)
            errors["geoip-inspection"] = str(inspection_error)
        else:
            errors.pop("geoip-inspection", None)
        self._stored.config_errors = errors
        summary = str(health.get("geoip-summary", ""))
        if not health["healthy"]:
            self.unit.status = ops.BlockedStatus(
                self._with_geoip_summary(health["message"], summary)
            )
        elif deployment.get("error"):
            self.unit.status = ops.WaitingStatus(
                self._with_geoip_summary(
                    f"SSH configuration deployment failed: {deployment['error'][:300]}", summary
                )
            )
        elif self._stored.config_errors:
            key = next(iter(self._stored.config_errors)).removeprefix("read:")
            message = (
                "Supply a valid GeoIP database via sync-geoip; see action logs"
                if key in {"geoip", "geoip-inspection"}
                else f"Supply a valid configuration for {key[:100]}; see action logs"
            )
            self.unit.status = ops.WaitingStatus(self._with_geoip_summary(message, summary))
        elif self._stored.exporter_error:
            self.unit.status = ops.WaitingStatus(self._stored.exporter_error)
        elif request_error:
            self.unit.status = ops.WaitingStatus(
                self._with_geoip_summary(
                    "Supply a valid configuration request; see action logs",
                    summary,
                )
            )
        else:
            self.unit.status = ops.ActiveStatus(
                self._with_geoip_summary(health["message"], summary)
            )

    @staticmethod
    def _with_geoip_summary(message: str, summary: str) -> str:
        """Include installed database metadata once in each reported status."""
        return f"{message}; {summary}" if summary and summary not in message else message

    def _on_action(self, event: ops.ActionEvent) -> None:
        """Dispatch explicit operator requests and report clean action failures."""
        name = event.handle.kind
        config_key = self._configuration_key(event)
        try:
            if name == "get_config_action":
                result = self.workload.read(
                    str(event.params.get("scope", "zone")), str(event.params.get("zone", ""))
                )
            elif name in {"validate_config_action", "apply_config_action"}:
                result = self.workload.update(
                    str(event.params.get("scope", "zone")),
                    str(event.params.get("zone", "")),
                    str(event.params["content-b64"]),
                    str(event.params["expected-sha256"]),
                    validate_only=name == "validate_config_action",
                )
            elif name == "sync_geoip_action":
                result = self._sync_geoip(event)
            elif name == "service_action":
                result = self.workload.service(str(event.params["action"]))
            else:
                result = self.workload.info()
            self._set_results(event, result)
            if name in {
                "get_config_action",
                "validate_config_action",
                "apply_config_action",
                "sync_geoip_action",
            }:
                errors = dict(self._stored.config_errors)
                errors.pop(config_key, None)
                self._stored.config_errors = errors
            if name in {
                "service_action",
                "get_config_action",
                "validate_config_action",
                "apply_config_action",
                "sync_geoip_action",
            }:
                self._observe_status()
        except (WorkloadError, ValueError, KeyError, OSError) as exc:
            message = (
                f"Missing required action parameter: {exc.args[0]}"
                if isinstance(exc, KeyError)
                else str(exc)
            )
            logger.warning("Rejected %s: %s", event.handle.kind, message)
            if config_key:
                errors = dict(self._stored.config_errors)
                errors[config_key] = message
                self._stored.config_errors = errors
                self._observe_status()
            elif name in {"get_config_action", "validate_config_action", "apply_config_action"}:
                # Invalid targets cannot identify persistent configuration to repair.
                self._observe_status(request_error=True)
            event.fail(message)

    @staticmethod
    def _set_results(event: ops.ActionEvent, result: dict) -> None:
        """Send large configuration reads without oversized process arguments."""
        content = result.get("content-b64", "")
        if len(content) <= 65536:
            event.set_results(result)
            return
        parts = [content[i : i + 65536] for i in range(0, len(content), 65536)]
        for index, part in enumerate(parts):
            event.set_results({f"content-b64-part-{index:04d}": part})
        event.set_results(
            {key: value for key, value in result.items() if key != "content-b64"}
            | {"content-b64-part-count": len(parts)}
        )

    @staticmethod
    def _configuration_key(event: ops.ActionEvent) -> str:
        """Identify the exact configuration scope affected by an action."""
        if event.handle.kind == "sync_geoip_action":
            return "geoip"
        if event.handle.kind in {
            "get_config_action",
            "validate_config_action",
            "apply_config_action",
        }:
            scope = str(event.params.get("scope", "zone"))
            zone = str(event.params.get("zone", ""))
            if scope == "global" and not zone:
                key = "global"
            elif scope == "zone" and len(zone) <= 253 and ZONE_PATTERN.fullmatch(zone):
                key = f"zone:{zone}"
            else:
                return ""
            return f"read:{key}" if event.handle.kind == "get_config_action" else key
        return ""

    def _sync_geoip(self, event: ops.ActionEvent) -> dict:
        """Remember the selected URL before attempting a database update."""
        if supplied := str(event.params.get("url", "")):
            self._stored.geoip_url = supplied
        effective = self._stored.geoip_url
        event.set_results({"url": effective})
        if not effective:
            raise ValueError("Supply a GeoIP database URL using the url action parameter")
        return self.workload.sync_geoip(effective, str(event.params.get("sha256", "")))


if __name__ == "__main__":
    ops.main(GdnsdCharm)
