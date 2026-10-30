"""Read-only configured and authoritative SOA observation."""

import socket
import struct
import threading

import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest

import soa


def zone(tmp_path, text=None, name="example.org"):
    zones = tmp_path / "zones"
    zones.mkdir(exist_ok=True)
    (zones / name).write_text(text or "@ IN SOA ns hostmaster 0 1h 2m 3d 4m\n")
    return tmp_path


def response(query, serial=0):
    answer = dns.message.make_response(query)
    answer.flags |= dns.flags.AA
    answer.answer.append(
        dns.rrset.from_text(
            query.question[0].name,
            60,
            "IN",
            "SOA",
            f"ns.example.org. hostmaster.example.org. {serial} 1 2 3 4",
        )
    )
    return answer


def serve(monkeypatch, serial=0):
    monkeypatch.setattr(soa.dns.query, "tcp", lambda query, *a, **kw: response(query, serial))


def test_fresh_installation(tmp_path):
    result = soa.inspect_zones(tmp_path)
    assert result == {
        "zone-serials": {},
        "zone-serials-ok": True,
        "zone-serials-summary": "",
        "zone-serials-error": "",
    }


def test_multiline_serial_zero_and_gdnsd_records(tmp_path, monkeypatch):
    zone(
        tmp_path,
        "$TTL 1h\n$ORIGIN sub.example.org.\nwww 600/10 DYNA geoip!x\n"
        "$ORIGIN @Z\n@F IN 1h SOA ns hostmaster (\n0 ; serial\n1h 2m 3d 4m )\n"
        'txt TXT "SOA is not a record; neither is ("\n',
    )
    serve(monkeypatch)
    result = soa.inspect_zones(tmp_path)
    assert result["zone-serials-ok"]
    assert result["zone-serials"]["example.org"] == {
        "in-sync": True,
        "configured-serial": 0,
        "served-serial": 0,
    }


def test_mismatch_is_diagnostic(tmp_path, monkeypatch, caplog):
    zone(tmp_path)
    serve(monkeypatch, 12)
    result = soa.inspect_zones(tmp_path)
    item = result["zone-serials"]["example.org"]
    assert not result["zone-serials-ok"]
    assert not item["in-sync"]
    assert item["configured-serial"] == 0
    assert item["served-serial"] == 12
    assert "serial" in item["error"]
    assert "example.org" in caplog.text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: setattr(r, "flags", r.flags & ~dns.flags.AA),
        lambda r: r.set_rcode(dns.rcode.NXDOMAIN),
        lambda r: r.set_rcode(dns.rcode.REFUSED),
        lambda r: r.answer.clear(),
        lambda r: setattr(r, "id", r.id ^ 1),
        lambda r: r.question.clear(),
        lambda r: r.answer.__setitem__(
            0,
            dns.rrset.from_text("other.example.org.", 60, "IN", "SOA", "ns. hostmaster. 0 1 2 3 4"),
        ),
        lambda r: r.answer.__setitem__(
            0, dns.rrset.from_text("example.org.", 60, "CH", "SOA", "ns. hostmaster. 0 1 2 3 4")
        ),
        lambda r: r.answer.append(
            dns.rrset.from_text("example.org.", 60, "IN", "SOA", "ns. hostmaster. 2 1 2 3 4")
        ),
    ],
)
def test_invalid_answer_never_in_sync(tmp_path, monkeypatch, mutate):
    zone(tmp_path)

    def query(q, *a, **kw):
        answer = response(q)
        mutate(answer)
        return answer

    monkeypatch.setattr(soa.dns.query, "tcp", query)
    item = soa.inspect_zones(tmp_path)["zone-serials"]["example.org"]
    assert not item["in-sync"]
    assert item["error"]
    assert "served-serial" not in item


@pytest.mark.parametrize(
    "text",
    [
        "@ SOA ns hostmaster not-a-serial 1 2 3 4\n",
        "@ SOA ns hostmaster 4294967296 1 2 3 4\n",
        "other SOA ns hostmaster 0 1 2 3 4\n",
        "$INCLUDE /secret\n",
        "@ SOA ns hostmaster 0 1 2 3 4\n@ SOA ns hostmaster 0 1 2 3 4\n",
    ],
)
def test_unsupported_or_bad_disk_data_is_diagnostic(tmp_path, monkeypatch, text):
    zone(tmp_path, text)
    serve(monkeypatch)
    item = soa.inspect_zones(tmp_path)["zone-serials"]["example.org"]
    assert not item["in-sync"]
    assert item["error"]
    assert "configured-serial" not in item
    assert item["served-serial"] == 0


def test_safe_paths(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    zone(outside)
    config = tmp_path / "config"
    config.symlink_to(outside, target_is_directory=True)
    serve(monkeypatch)
    result = soa.inspect_zones(config)
    assert not result["zone-serials-ok"]
    assert "symlink" in result["zone-serials-error"].lower() or result["zone-serials-error"]
    config.unlink()
    config.mkdir()
    (config / "zones").symlink_to(outside / "zones", target_is_directory=True)
    assert not soa.inspect_zones(config)["zone-serials-ok"]


def test_symlink_zone_not_read_and_invalid_names_ignored(tmp_path, monkeypatch):
    zone(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("@ SOA ns hostmaster 123 1 2 3 4\n")
    (tmp_path / "zones" / "linked.example").symlink_to(secret)
    (tmp_path / "zones" / ".temporary").write_text("invalid")
    (tmp_path / "zones" / "include_files").mkdir()
    serve(monkeypatch)
    result = soa.inspect_zones(tmp_path)
    assert set(result["zone-serials"]) == {"example.org", "linked.example"}
    assert "configured-serial" not in result["zone-serials"]["linked.example"]
    assert not result["zone-serials-ok"]


def test_timeout_and_budget_bound(tmp_path, monkeypatch):
    zone(tmp_path, name="a.example")
    zone(tmp_path, name="b.example")
    now = [0.0]
    monkeypatch.setattr(soa.time, "monotonic", lambda: now[0])
    calls = []

    def timeout(q, where, **kwargs):
        calls.append((q, where, kwargs))
        now[0] = 1.0
        raise dns.exception.Timeout

    monkeypatch.setattr(soa.dns.query, "tcp", timeout)
    result = soa.inspect_zones(tmp_path, timeout=2, budget=0.5)
    assert len(calls) == 1
    assert calls[0][1] == "127.0.0.1"
    assert calls[0][2]["timeout"] == 0.5
    assert not calls[0][0].flags & dns.flags.RD
    assert "budget" in result["zone-serials"]["b.example"]["error"].lower()
    assert not result["zone-serials-ok"]


def test_summaries_are_bounded(tmp_path, monkeypatch):
    for index in range(30):
        zone(tmp_path, name=f"zone-{index:02}.example.org")
    serve(monkeypatch, 1)
    result = soa.inspect_zones(tmp_path)
    assert len(result["zone-serials-summary"]) <= 240
    assert len(result["zone-serials-error"]) <= 300
    assert "more" in result["zone-serials-summary"]


def test_real_tcp_exchange_on_loopback(tmp_path, monkeypatch):
    zone(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    port = listener.getsockname()[1]
    failures = []

    def server():
        try:
            with listener, listener.accept()[0] as client:
                size = struct.unpack("!H", client.recv(2))[0]
                wire = b""
                while len(wire) < size:
                    wire += client.recv(size - len(wire))
                query = dns.message.from_wire(wire)
                assert not query.flags & dns.flags.RD
                wire = response(query).to_wire()
                client.sendall(struct.pack("!H", len(wire)) + wire)
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    real_tcp = soa.dns.query.tcp
    monkeypatch.setattr(
        soa.dns.query, "tcp", lambda q, where, **kw: real_tcp(q, where, port=port, **kw)
    )
    assert soa.inspect_zones(tmp_path)["zone-serials-ok"]
    thread.join(3)
    assert not failures
    assert not thread.is_alive()


def test_include_after_explicit_soa_is_not_followed(tmp_path, monkeypatch):
    zone(tmp_path, "@ SOA ns hostmaster 0 1 2 3 4\n$INCLUDE /secret\n")
    serve(monkeypatch)
    assert soa.inspect_zones(tmp_path)["zone-serials-ok"]


def test_truncated_tcp_answer_is_diagnostic(tmp_path, monkeypatch):
    zone(tmp_path)

    def query(q, *a, **kw):
        answer = response(q)
        answer.flags |= dns.flags.TC
        return answer

    monkeypatch.setattr(soa.dns.query, "tcp", query)
    assert not soa.inspect_zones(tmp_path)["zone-serials-ok"]


def test_duplicate_question_is_diagnostic(tmp_path, monkeypatch):
    zone(tmp_path)

    def query(q, *a, **kw):
        answer = response(q)
        answer.question.append(answer.question[0])
        return answer

    monkeypatch.setattr(soa.dns.query, "tcp", query)
    assert not soa.inspect_zones(tmp_path)["zone-serials-ok"]


def test_long_zone_still_named_in_bounded_summary(tmp_path, monkeypatch):
    name = ".".join(["a" * 60] * 4)
    zone(tmp_path, name=name)
    serve(monkeypatch)
    result = soa.inspect_zones(tmp_path)
    assert result["zone-serials-summary"].startswith("a" * 60)
    assert len(result["zone-serials-summary"]) <= 240


@pytest.mark.parametrize(
    ("filename", "apex"),
    [
        ("ROOT_ZONE", "."),
        ("0@25.2.0.192.in-addr.arpa", "0/25.2.0.192.in-addr.arpa."),
        (r"odd\.label.example", r"odd\.label.example."),
        (r"\097.example", "a.example."),
    ],
)
def test_gdnsd_zone_filename_conventions(tmp_path, monkeypatch, filename, apex):
    zone(tmp_path, name=filename)
    queries = []

    def query(q, *a, **kw):
        queries.append(q.question[0].name)
        return response(q)

    monkeypatch.setattr(soa.dns.query, "tcp", query)
    result = soa.inspect_zones(tmp_path)
    assert queries == [dns.name.from_text(apex)]
    assert result["zone-serials"] == {
        filename: {"in-sync": True, "configured-serial": 0, "served-serial": 0}
    }


def test_oversized_zone_file_is_diagnostic(tmp_path, monkeypatch):
    zone(tmp_path, "@ SOA ns. hostmaster. 0 1 2 3 4\n;" + " " * (2 * 1024 * 1024))
    serve(monkeypatch)
    item = soa.inspect_zones(tmp_path)["zone-serials"]["example.org"]
    assert not item["in-sync"]
    assert "configured-serial" not in item
    assert "2 MiB" in item["error"]


def test_exhausted_budget_skips_remaining_file_reads(tmp_path, monkeypatch):
    zone(tmp_path, name="a.example")
    zone(tmp_path, name="b.example")
    zone(tmp_path, name="c.example")
    now = [0.0]
    monkeypatch.setattr(soa.time, "monotonic", lambda: now[0])
    real_read = soa._read_serial
    reads = []

    def read(directory, filename, apex, **kwargs):
        reads.append(filename)
        return real_read(directory, filename, apex, **kwargs)

    def query(q, *a, **kw):
        now[0] = 1.0
        return response(q)

    monkeypatch.setattr(soa, "_read_serial", read)
    monkeypatch.setattr(soa.dns.query, "tcp", query)
    result = soa.inspect_zones(tmp_path, budget=0.5)
    assert reads == ["a.example"]
    assert result["zone-serials"]["a.example"]["in-sync"]
    for filename in ["b.example", "c.example"]:
        item = result["zone-serials"][filename]
        assert not item["in-sync"]
        assert "configured-serial" not in item
        assert "served-serial" not in item
        assert "budget" in item["error"]


def test_tokenization_stops_when_inspection_budget_expires(tmp_path, monkeypatch):
    zone(tmp_path, "@ SOA ns. hostmaster. 0 1 2 3 4\n" + "www A 192.0.2.1\n" * 100)
    now = [0.0]
    monkeypatch.setattr(soa.time, "monotonic", lambda: now[0])
    real_get = soa.dns.tokenizer.Tokenizer.get
    tokens = []

    def get(tokenizer, *args, **kwargs):
        token = real_get(tokenizer, *args, **kwargs)
        tokens.append(token)
        if len(tokens) == 5:
            now[0] = 1.0
        return token

    monkeypatch.setattr(soa.dns.tokenizer.Tokenizer, "get", get)
    serve(monkeypatch)
    result = soa.inspect_zones(tmp_path, budget=0.5)
    assert len(tokens) == 5
    assert not result["zone-serials-ok"]
    item = result["zone-serials"]["example.org"]
    assert "configured-serial" not in item
    assert "budget" in item["error"]


def test_partial_discovery_is_bounded_and_diagnostic(tmp_path, monkeypatch):
    for filename in ["a.example", "b.example", "c.example"]:
        zone(tmp_path, name=filename)
    with soa.os.scandir(tmp_path / "zones") as stream:
        entries = sorted(stream, key=lambda entry: entry.name)
    now = [0.0]
    seen = []
    monkeypatch.setattr(soa.time, "monotonic", lambda: now[0])

    class SlowEntries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for entry in entries:
                seen.append(entry.name)
                if len(seen) == 2:
                    now[0] = 1.0
                yield entry

    monkeypatch.setattr(soa.os, "scandir", lambda fd: SlowEntries())
    serve(monkeypatch)
    result = soa.inspect_zones(tmp_path, budget=0.5)
    assert seen == ["a.example", "b.example"]
    assert set(result["zone-serials"]) == set(seen)
    assert not result["zone-serials-ok"]
    assert "discovery" in result["zone-serials-error"]
    assert "incomplete" in result["zone-serials-error"]
