#!/usr/bin/env python3
"""Expose server-wide native gdnsd counters to a colocated collector."""

from __future__ import annotations

import json
import logging
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)


def render(data: dict) -> str:
    """Validate the complete required sample before emitting any native values."""
    lines = []

    def metric(name, kind, help_text, samples):
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"))
        for labels, value in samples:
            if type(value) is not int or value < 0:
                raise ValueError(f"Invalid nonnegative integer for {name}")
            lines.append(f"{name}{labels} {value}")

    metric("gdnsd_uptime_seconds", "gauge", "Native daemon uptime.", [("", data["uptime"])])
    metric(
        "gdnsd_requests_total",
        "counter",
        "Server-wide requests including probes.",
        [(f'{{protocol="{p}"}}', data[p]["reqs"]) for p in ("udp", "tcp")],
    )
    metric(
        "gdnsd_responses_total",
        "counter",
        "Server-wide responses by code.",
        [
            (f'{{rcode="{code.upper()}"}}', data["stats"][code])
            for code in ("noerror", "refused", "nxdomain", "notimp", "badvers", "formerr")
        ],
    )
    metric(
        "gdnsd_dropped_requests_total",
        "counter",
        "Dropped requests.",
        [("", data["stats"]["dropped"])],
    )
    metric(
        "gdnsd_transport_errors_total",
        "counter",
        "Native transport errors.",
        [
            (f'{{protocol="{p}",operation="{op}"}}', data[p][f"{op}fail"])
            for p, op in (
                ("udp", "recv"),
                ("udp", "send"),
                ("tcp", "recv"),
                ("tcp", "send"),
                ("tcp", "accept"),
            )
        ],
    )
    metric(
        "gdnsd_tcp_connections_total",
        "counter",
        "Accepted TCP connections.",
        [("", data["tcp"]["conns"])],
    )
    metric(
        "gdnsd_udp_truncated_responses_total",
        "counter",
        "Truncated UDP responses.",
        [("", data["udp"]["tc"])],
    )
    return "\n".join(lines) + "\n"


def collect() -> str:
    """Fail visibly without fabricating zero native counters."""
    success = 0
    native = ""
    try:
        result = subprocess.run(
            ["/usr/bin/gdnsdctl", "-t", "5", "-o", "stats"],
            capture_output=True,
            text=True,
            timeout=6,
            check=True,
        )
        if len(result.stdout) > 65536:
            raise ValueError("Oversized native stats response")
        native = render(json.loads(result.stdout))
        success = 1
    except (OSError, subprocess.SubprocessError, TimeoutError, ValueError, KeyError, TypeError):
        logger.exception("Unable to collect or validate gdnsd native stats")
    return (
        "# HELP gdnsd_scrape_success Native stats collection succeeded.\n"
        "# TYPE gdnsd_scrape_success gauge\n"
        f"gdnsd_scrape_success {success}\n" + native
    )


class Handler(BaseHTTPRequestHandler):
    """Serve metrics only over the local loopback listener."""

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = collect().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Successful periodic scrapes do not need one journal entry each.
        if args and str(args[1]) != "200":
            logger.info(format, *args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    HTTPServer(("127.0.0.1", 9153), Handler).serve_forever()
