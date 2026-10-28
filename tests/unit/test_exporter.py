"""Native counters must remain trustworthy when collection fails."""

from unittest.mock import patch

import pytest

from exporter import collect, render


def stats():
    return {
        "uptime": 10,
        "stats": {
            key: 0
            for key in ("noerror", "refused", "nxdomain", "notimp", "badvers", "formerr", "dropped")
        },
        "udp": {"reqs": 7, "recvfail": 0, "sendfail": 0, "tc": 0},
        "tcp": {"reqs": 3, "recvfail": 0, "sendfail": 0, "acceptfail": 0, "conns": 3},
    }


def test_native_counter_types_and_protocols():
    output = render(stats())
    assert "# TYPE gdnsd_requests_total counter" in output
    assert 'gdnsd_requests_total{protocol="udp"} 7' in output
    assert 'gdnsd_requests_total{protocol="tcp"} 3' in output
    assert "gdnsd_uptime_seconds 10" in output


@pytest.mark.parametrize("value", [-1, True, "1", None])
def test_invalid_counter_rejected(value):
    data = stats()
    data["udp"]["reqs"] = value
    with pytest.raises(ValueError):
        render(data)


def test_failed_collection_does_not_publish_zero_counters():
    with patch("exporter.subprocess.run", side_effect=TimeoutError("timeout")):
        output = collect()
    assert "gdnsd_scrape_success 0" in output
    assert "gdnsd_requests_total" not in output


def test_successful_collection():
    import json
    from subprocess import CompletedProcess

    result = CompletedProcess([], 0, json.dumps(stats()), "")
    with patch("exporter.subprocess.run", return_value=result) as run:
        output = collect()
    assert "gdnsd_scrape_success 1" in output
    assert "gdnsd_requests_total" in output
    assert run.call_args.kwargs["timeout"] == 6
