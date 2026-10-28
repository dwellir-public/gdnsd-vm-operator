#!/usr/bin/env python3
"""Juju event orchestration for the gdnsd machine workload."""

from __future__ import annotations

import logging
import re
from functools import cached_property

import ops

from workload import Workload, WorkloadError

logger = logging.getLogger(__name__)


class GdnsdCharm(ops.CharmBase):
    """Manage gdnsd through explicit configuration and service actions."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        for event in (self.on.upgrade_charm, self.on.update_status, self.on.config_changed):
            self.framework.observe(event, self._on_observe)
        for event in (
            self.on.get_config_action,
            self.on.validate_config_action,
            self.on.apply_config_action,
            self.on.service_action,
            self.on.get_info_action,
        ):
            self.framework.observe(event, self._on_action)

    @cached_property
    def workload(self) -> Workload:
        """Build a workload helper using the current zone protection policy."""
        names = re.split(r"[\s,]+", str(self.config["protected-zones"]))
        protected = {name.lower().rstrip(".") for name in names if name}
        return Workload(protected_zones=protected)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install or adopt gdnsd and expose its DNS listener ports."""
        try:
            binding = self.model.get_binding("juju-info")
            if binding is None or binding.network.bind_address is None:
                raise WorkloadError("No private address is available for gdnsd")
            self.workload.install(str(binding.network.bind_address))
            self.unit.open_port("tcp", 53)
            self.unit.open_port("udp", 53)
            self._observe_status()
        except (WorkloadError, ValueError) as exc:
            logger.exception("gdnsd installation failed")
            self.unit.status = ops.BlockedStatus(str(exc))

    def _on_observe(self, event: ops.HookEvent) -> None:
        """Observe service health without changing host files or service state."""
        self._observe_status()

    def _observe_status(self) -> None:
        """Publish the observed workload version and local service health."""
        try:
            health = self.workload.health()
            if version := health["workload-version"]:
                self.unit.set_workload_version(version)
        except (WorkloadError, ValueError) as exc:
            self.unit.status = ops.BlockedStatus(str(exc))
            return
        self.unit.status = (
            ops.ActiveStatus("running")
            if health["healthy"]
            else ops.BlockedStatus(health["message"])
        )

    def _on_action(self, event: ops.ActionEvent) -> None:
        """Dispatch explicit operator requests and report clean action failures."""
        try:
            name = event.handle.kind
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
            elif name == "service_action":
                result = self.workload.service(str(event.params["action"]))
            else:
                result = self.workload.info()
            event.set_results(result)
            if name in {"service_action", "apply_config_action"}:
                self._observe_status()
        except (WorkloadError, ValueError, KeyError) as exc:
            message = (
                f"Missing required action parameter: {exc.args[0]}"
                if isinstance(exc, KeyError)
                else str(exc)
            )
            logger.warning("Rejected %s: %s", event.handle.kind, message)
            event.fail(message)


if __name__ == "__main__":
    ops.main(GdnsdCharm)
