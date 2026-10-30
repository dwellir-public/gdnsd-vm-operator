"""Read-only, bounded observation of locally configured and served SOA serials.

Only the main zone file is read. Included files are deliberately not opened;
when the main file has no apex SOA this limitation is reported explicitly.
"""

import logging
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
import dns.ttl

logger = logging.getLogger(__name__)
MAX_ZONE_BYTES = 2 * 1024 * 1024  # Same bound as the native configuration manager.


def _zone_name(filename: str) -> dns.name.Name | None:
    """Apply gdnsd(8)'s zone filename conventions with DNS escape handling."""
    if filename.startswith("."):
        return None
    if filename == "ROOT_ZONE":
        return dns.name.root
    try:
        # gdnsd maps @ to / for classless reverse zones before DNS parsing.
        # The DNS parser handles escaped dots/decimal octets and trailing dots.
        return dns.name.from_text(filename.replace("@", "/"), origin=dns.name.root)
    except dns.exception.DNSException:
        return None


@contextmanager
def _directory(path: Path):
    """Walk through directory descriptors so symlinks cannot redirect reads."""
    path = path.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component == "..":
                raise ValueError("indirect configuration path is unsupported")
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _expand_name(text: str, origin: dns.name.Name, apex: dns.name.Name) -> dns.name.Name:
    # In a main zone file @F and @Z have the same value, even after $ORIGIN.
    if text.upper() in {"@Z", "@F"}:
        return apex
    if text.upper().endswith((".@Z", ".@F")):
        text = text[:-2] + apex.to_text()
    return dns.name.from_text(text, origin=origin)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("inspection time budget exhausted")


def _records(text: str, *, deadline: float):
    """Yield logical records using DNS quoting, comment and parenthesis rules."""
    tokenizer = dns.tokenizer.Tokenizer(text)

    def get(**kwargs):
        _check_deadline(deadline)
        token = tokenizer.get(**kwargs)
        _check_deadline(deadline)
        return token

    while True:
        first = get(want_leading=True)
        inherited_owner = first.is_whitespace()
        if inherited_owner:
            first = get()
        if first.is_eof():
            return
        if first.is_eol():
            continue
        tokens = [first]
        while True:
            token = get()
            if token.is_eol_or_eof():
                break
            tokens.append(token)
        yield inherited_owner, tokens
        if token.is_eof():
            return


def _configured_serial(text: str, apex: dns.name.Name, *, deadline: float) -> int:
    origin = apex
    owner = None
    serial = None
    included = False
    for inherited, tokens in _records(text, deadline=deadline):
        _check_deadline(deadline)
        values = [token.value for token in tokens]
        directive = values[0].upper()
        if directive.startswith("$"):
            if directive == "$ORIGIN" and len(values) == 2:
                origin = _expand_name(values[1], origin, apex)
                if not origin.is_subdomain(apex):
                    raise ValueError("$ORIGIN is outside the zone")
            elif directive == "$INCLUDE":
                included = True
            elif directive == "$TTL" and len(values) == 2:
                dns.ttl.from_text(values[1])
            else:
                raise ValueError(f"unsupported or malformed directive {values[0]}")
            continue
        if not inherited:
            owner = _expand_name(values.pop(0), origin, apex)
            tokens = tokens[1:]
        if owner is None:
            raise ValueError("record has no owner")
        rdclass = dns.rdataclass.IN
        for _ in range(2):
            if not values:
                break
            try:
                candidate = dns.rdataclass.from_text(values[0])
            except dns.exception.DNSException:
                try:
                    dns.ttl.from_text(values[0].split("/", 1)[0])
                except dns.exception.DNSException:
                    break
            else:
                rdclass = candidate
            values.pop(0)
            tokens = tokens[1:]
        if not values or values[0].upper() != "SOA":
            continue  # gdnsd's DYNA/DYNC and unrelated RDATA need no interpretation.
        if owner != apex:
            continue
        if rdclass != dns.rdataclass.IN:
            raise ValueError("apex SOA is not class IN")
        if serial is not None:
            raise ValueError("multiple apex SOA records")
        if any(not token.is_identifier() for token in tokens[1:]):
            raise ValueError("invalid quoted SOA data")
        data = values[1:]
        if len(data) != 7:
            raise ValueError("apex SOA must have seven RDATA fields")
        data[:2] = [_expand_name(name, origin, apex).to_text() for name in data[:2]]
        record = dns.rdata.from_text(rdclass, dns.rdatatype.SOA, " ".join(data))
        _check_deadline(deadline)
        serial = record.serial
    if serial is None:
        suffix = "; $INCLUDE inspection is unsupported" if included else ""
        raise ValueError(f"no apex SOA in main zone file{suffix}")
    return serial


def _read_serial(directory: int, filename: str, apex: dns.name.Name, *, deadline: float) -> int:
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("zone path is not a regular file")
        _check_deadline(deadline)
        data = stream.read(MAX_ZONE_BYTES + 1)
        _check_deadline(deadline)
        if len(data) > MAX_ZONE_BYTES:
            raise ValueError("zone file exceeds the 2 MiB inspection limit")
        return _configured_serial(data.decode("utf-8"), apex, deadline=deadline)


def _served_serial(apex: dns.name.Name, timeout: float) -> int:
    query = dns.message.make_query(apex, dns.rdatatype.SOA, dns.rdataclass.IN)
    query.flags &= ~dns.flags.RD
    answer = dns.query.tcp(query, "127.0.0.1", timeout=timeout)
    if len(answer.question) != 1 or not query.is_response(answer):
        raise ValueError("SOA response ID or question does not match query")
    if answer.rcode() != dns.rcode.NOERROR:
        raise ValueError(f"SOA query returned {dns.rcode.to_text(answer.rcode())}")
    if answer.flags & dns.flags.TC:
        raise ValueError("SOA response is truncated")
    if not answer.flags & dns.flags.AA:
        raise ValueError("SOA response is not authoritative")
    records = [
        record
        for rrset in answer.answer
        if rrset.name == apex
        and rrset.rdtype == dns.rdatatype.SOA
        and rrset.rdclass == dns.rdataclass.IN
        for record in rrset
    ]
    if len(records) != 1:
        raise ValueError("SOA response must contain exactly one apex IN SOA")
    serial = records[0].serial
    if not isinstance(serial, int) or not 0 <= serial <= 0xFFFFFFFF:
        raise ValueError("served SOA serial is not a uint32")
    return serial


def _compact(parts: list[str], limit: int) -> str:
    """Keep complete first entries plus a count of omitted entries."""
    chosen = []
    for index, part in enumerate(parts):
        remaining = len(parts) - index - 1
        candidate = "; ".join([*chosen, part])
        suffix = f"; +{remaining} more" if remaining else ""
        if len(candidate + suffix) > limit:
            if not chosen:
                return part[: limit - len(suffix)] + suffix
            suffix = f"; +{len(parts) - index} more"
            return ("; ".join(chosen)[: limit - len(suffix)] + suffix).lstrip("; ")
        chosen.append(part)
    return "; ".join(chosen)


def inspect_zones(config_dir: Path, *, timeout: float = 1.0, budget: float = 5.0) -> dict:
    """Compare main-file serials to authoritative localhost TCP SOA responses."""
    result = {
        "zone-serials": {},
        "zone-serials-ok": True,
        "zone-serials-summary": "",
        "zone-serials-error": "",
    }
    deadline = time.monotonic() + max(0.0, budget)
    discovery_error = ""
    try:
        with _directory(config_dir) as config:
            try:
                zones = os.open(
                    "zones", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=config
                )
            except FileNotFoundError:
                return result
            try:
                candidates = []
                with os.scandir(zones) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False) and _zone_name(entry.name):
                            candidates.append(entry.name)
                        if time.monotonic() >= deadline:
                            discovery_error = (
                                "zone discovery incomplete: inspection time budget exhausted"
                            )
                            logger.warning("SOA inspection: %s", discovery_error)
                            break
                candidates.sort()
                for filename in candidates:
                    apex = _zone_name(filename)
                    item = {"in-sync": False}
                    errors = []
                    if time.monotonic() >= deadline:
                        errors.append("inspection time budget exhausted")
                    else:
                        try:
                            item["configured-serial"] = _read_serial(
                                zones, filename, apex, deadline=deadline
                            )
                        except (
                            OSError,
                            ValueError,
                            UnicodeError,
                            dns.exception.DNSException,
                        ) as error:
                            errors.append(f"configured SOA: {error or type(error).__name__}")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or timeout <= 0:
                            errors.append("served SOA: inspection time budget exhausted")
                        else:
                            try:
                                item["served-serial"] = _served_serial(
                                    apex, min(timeout, remaining)
                                )
                            except (OSError, ValueError, dns.exception.DNSException) as error:
                                errors.append(f"served SOA: {error or type(error).__name__}")
                    if not errors:
                        if item["configured-serial"] == item["served-serial"]:
                            item["in-sync"] = True
                        else:
                            errors.append(
                                f"serial mismatch (configured {item['configured-serial']}, "
                                f"served {item['served-serial']})"
                            )
                    if errors:
                        item["error"] = "; ".join(errors)
                        logger.warning("SOA inspection %s: %s", filename, item["error"])
                    result["zone-serials"][filename] = item
            finally:
                os.close(zones)
    except (OSError, ValueError) as error:
        # A missing configuration directory is also a fresh, unconfigured installation.
        if isinstance(error, FileNotFoundError):
            return result
        message = f"zone discovery: {error}"
        logger.warning("SOA inspection: %s", message)
        result["zone-serials-ok"] = False
        result["zone-serials-error"] = message[:300]
        return result
    items = result["zone-serials"]
    result["zone-serials-ok"] = not discovery_error and all(
        item["in-sync"] for item in items.values()
    )
    result["zone-serials-summary"] = _compact(
        ([discovery_error] if discovery_error else [])
        + [
            f"{name} {item.get('configured-serial', '?')}/{item.get('served-serial', '?')}"
            + (" in sync" if item["in-sync"] else " error")
            for name, item in items.items()
        ],
        236,
    )
    result["zone-serials-error"] = _compact(
        ([discovery_error] if discovery_error else [])
        + [f"{name}: {item['error']}" for name, item in items.items() if "error" in item],
        300,
    )
    return result
