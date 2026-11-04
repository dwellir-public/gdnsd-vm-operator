"""Verify telemetry identity and refresh preservation."""

import base64
import gzip
import json
from unittest.mock import patch

from ops.testing import Context, State, SubordinateRelation

from charm import GdnsdCharm
from observability import Exporter


def test_relation_publishes_principal_topology():
    ctx = Context(GdnsdCharm)
    relation = SubordinateRelation("machine-observability", remote_app_name="alloy-sub")
    with patch("charm.Workload"), patch("charm.Exporter"):
        state = ctx.run(ctx.on.relation_joined(relation), State(relations={relation}, leader=True))
    data = json.loads(state.get_relation(relation.id).local_app_data["payload"])
    assert data["schema_version"] == 3
    assert data["source_topology"]["application"] == "gdnsd-vm"
    assert data["source_topology"]["unit"] == ""
    assert data["source_topology"]["model_uuid"] == state.model.uuid
    assert data["metrics_endpoints"][0]["targets"] == ["127.0.0.1:9153"]
    assert len(data["artifacts"]) == 1
    artifact = data["artifacts"][0]
    assert artifact["artifact_type"] == "prometheus_alert_rules"
    rules = json.loads(gzip.decompress(base64.b64decode(artifact["content"])))
    rule = rules["groups"][0]["rules"][0]
    assert rule["alert"] == "GdnsdDown"
    selector = f'juju_model_uuid="{state.model.uuid}",juju_application="gdnsd-vm"'
    assert f"up{{{selector}}} == 0" in rule["expr"]
    assert f"gdnsd_scrape_success{{{selector}}} == 0" in rule["expr"]
    assert rule["for"] == "2m"


def test_exporter_refresh_changes_only_auxiliary_service(tmp_path):
    with (
        patch("observability.SCRIPT", tmp_path / "exporter.py"),
        patch("observability.UNIT", tmp_path / "exporter.service"),
        patch.object(Exporter, "_run") as run,
    ):
        Exporter().reconcile()
        assert run.call_args_list[-1].args == ("restart", "gdnsd-exporter.service")
        run.reset_mock()
        Exporter().reconcile()
        assert [c.args for c in run.call_args_list] == [
            ("enable", "gdnsd-exporter.service"),
            ("start", "gdnsd-exporter.service"),
        ]


def test_nonleader_does_not_write_application_databag():
    ctx = Context(GdnsdCharm)
    relation = SubordinateRelation("machine-observability", remote_app_name="alloy-sub")
    with patch("charm.Workload"), patch("charm.Exporter"):
        state = ctx.run(ctx.on.relation_joined(relation), State(relations={relation}, leader=False))
    assert not state.get_relation(relation.id).local_app_data
