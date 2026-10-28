"""Read-only monitoring checks against retained, explicitly selected test models."""

from test_live import target  # noqa: F401


def test_native_metrics_are_loopback_only_and_count_queries(target):  # noqa: F811
    juju, app, _ = target
    unit = f"{app}/0"

    def counter():
        output = juju.ssh(unit, "curl -fsS http://127.0.0.1:9153/metrics")
        assert "gdnsd_scrape_success 1" in output
        return int(
            next(
                line.rsplit(" ", 1)[1]
                for line in output.splitlines()
                if line.startswith('gdnsd_requests_total{protocol="udp"}')
            )
        )

    before = counter()
    output = juju.ssh(unit, "dig @127.0.0.1 x.dwellir.com SOA +tries=1 +time=2")
    assert "status: NOERROR" in output
    assert counter() > before
    sockets = juju.ssh(unit, "ss -ltn sport = :9153")
    assert "127.0.0.1:9153" in sockets
    assert "0.0.0.0:9153" not in sockets
    assert "[::]:9153" not in sockets
