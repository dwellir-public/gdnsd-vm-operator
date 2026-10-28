"""Keep the DNS dashboard scoped to its workload when filters select All."""

import json
from pathlib import Path


def test_all_logs_remain_scoped_to_gdnsd():
    dashboard = json.loads(Path("src/grafana_dashboards/gdnsd.json").read_text())
    logs = next(p for p in dashboard["panels"] if p["type"] == "logs")
    query = logs["targets"][0]["expr"]
    assert 'juju_charm="gdnsd-vm"' in query
    assert 'juju_model=~"$juju_model"' in query
    assert 'juju_unit=~"$juju_unit"' in query
