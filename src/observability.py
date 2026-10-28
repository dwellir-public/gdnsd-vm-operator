"""Manage the auxiliary exporter without changing the DNS daemon."""

import subprocess
from pathlib import Path

from charms.dwellir_observability.v0.machine_observability import (
    MachineObservabilityPayload,
    MachineObservabilityProvider,
    MetricsEndpoint,
    SourceTopology,
)

SERVICE = "gdnsd-exporter.service"
SCRIPT = Path("/usr/local/lib/gdnsd-vm/exporter.py")
UNIT = Path("/etc/systemd/system") / SERVICE
UNIT_CONTENT = """[Unit]
Description=gdnsd native statistics exporter
After=network.target
[Service]
User=gdnsd
Group=gdnsd
ExecStart=/usr/bin/python3 /usr/local/lib/gdnsd-vm/exporter.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_UNIX AF_INET
[Install]
WantedBy=multi-user.target
"""


class Exporter:
    """Own only the exporter script and its systemd unit."""

    def reconcile(self):
        source = Path(__file__).with_name("exporter.py").read_bytes()
        changed = not SCRIPT.exists() or SCRIPT.read_bytes() != source
        unit_changed = not UNIT.exists() or UNIT.read_text() != UNIT_CONTENT
        SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        if changed:
            SCRIPT.write_bytes(source)
            SCRIPT.chmod(0o644)
        if unit_changed:
            UNIT.write_text(UNIT_CONTENT)
            self._run("daemon-reload")
        self._run("enable", SERVICE)
        self._run("restart" if changed or unit_changed else "start", SERVICE)

    @staticmethod
    def _run(*args):
        subprocess.run(["systemctl", *args], check=True, capture_output=True, timeout=15)


def payload(charm):
    """Declare principal identity rather than the subordinate collector identity."""
    return MachineObservabilityPayload(
        schema_version=3,
        charm_name=charm.meta.name,
        source_topology=SourceTopology(
            model=charm.model.name,
            model_uuid=charm.model.uuid,
            application=charm.app.name,
            unit=charm.unit.name,
            charm_name=charm.meta.name,
        ),
        metrics_endpoints=[
            MetricsEndpoint(
                targets=["127.0.0.1:9153"],
                interval="15s",
                timeout="10s",
            )
        ],
        systemd_units=["gdnsd.service", SERVICE],
        artifacts=[],
    )


class LeaderProvider(MachineObservabilityProvider):
    """Publish application-wide source declarations from the leader only.

    Alloy obtains each colocated principal unit from juju-info before using
    the payload topology fallback. Endpoints and journals are identical on units.
    """

    def publish(self, declaration):
        if self._charm.unit.is_leader():
            super().publish(declaration)
