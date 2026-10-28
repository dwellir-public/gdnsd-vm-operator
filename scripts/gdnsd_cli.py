"""Pull and push a gdnsd file through authenticated Juju actions."""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def action_results(response: dict) -> dict:
    """Extract a completed Juju action, rejecting failed or pending results."""
    candidates = []

    def visit(value):
        if isinstance(value, dict):
            if "status" in value and ("results" in value or "message" in value):
                candidates.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response)
    if len(candidates) != 1:
        raise RuntimeError("expected one completed Juju action result")
    task = candidates[0]
    if task["status"] != "completed":
        raise RuntimeError(task.get("message", f"action status: {task['status']}"))
    result = task.get("results", {})
    for key in ("exists", "changed", "validated", "active"):
        if key in result and isinstance(result[key], str):
            if result[key].lower() not in {"true", "false"}:
                raise RuntimeError(f"unexpected boolean value for {key}")
            result[key] = result[key].lower() == "true"
    return result


def run_action(model: str, unit: str, name: str, params: dict) -> dict:
    """Use a temporary parameter file to avoid shell quoting and argv limits."""
    # Snap-installed Juju cannot see the host's private /tmp namespace.
    params_dir = None
    if "/snap/" in (shutil.which("juju") or ""):
        params_dir = Path.home() / "snap" / "juju" / "common"
        params_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", dir=params_dir) as stream:
        json.dump(params, stream)
        stream.flush()
        result = subprocess.run(
            [
                "juju",
                "run",
                "--model",
                model,
                unit,
                name,
                "--params",
                stream.name,
                "--format",
                "json",
                "--wait",
                "5m",
            ],
            text=True,
            capture_output=True,
            timeout=330,
        )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return action_results(json.loads(result.stdout))


def push_units(
    model: str,
    units: list[str],
    params: dict,
    original: dict,
    metadata: Path,
    validate_only: bool,
) -> dict:
    """Validate every target before applying sequentially, recording partial progress."""
    requests = {unit: params | {"expected-sha256": original["digests"][unit]} for unit in units}
    results = {}
    for unit in units:
        results[unit] = run_action(model, unit, "validate-config", requests[unit])
    if validate_only:
        return results
    applied = []
    for unit in units:
        try:
            results[unit] = run_action(model, unit, "apply-config", requests[unit])
        except Exception as exc:
            completed = ", ".join(applied) or "none"
            raise RuntimeError(
                f"failed target: {unit}; already applied: {completed}; {exc}. "
                "No distributed rollback was performed; inspect the failed target before retrying."
            ) from exc
        applied.append(unit)
        original["digests"][unit] = results[unit]["sha256"]
        metadata.write_text(json.dumps(original, indent=2) + "\n")
    return results


def main(operation: str, argv: list[str] | None = None) -> int:
    """Read or update explicit units and one configuration target."""
    parser = argparse.ArgumentParser(
        description=f"{operation.capitalize()} one gdnsd configuration file"
    )
    parser.add_argument(
        "--model", required=True, help="Controller-qualified model, CONTROLLER:MODEL"
    )
    parser.add_argument(
        "--unit",
        required=True,
        action="append",
        help="Unit, for example gdnsd-vm/0; repeat to select multiple units",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--zone", help="Exact lowercase zone name, without a trailing dot")
    scope.add_argument(
        "--global-config", action="store_true", help="Select the global plugin/server configuration"
    )
    parser.add_argument(
        "--file", required=True, type=Path, help="Local editable configuration file"
    )
    if operation == "push":
        parser.add_argument(
            "--validate-only", action="store_true", help="Validate without changing live files"
        )
    args = parser.parse_args(argv)
    if ":" not in args.model or not all(args.model.split(":", 1)):
        raise ValueError("--model must include the controller, CONTROLLER:MODEL")
    units = list(dict.fromkeys(args.unit))
    if any("/" not in unit for unit in units):
        raise ValueError("--unit must identify a unit, APPLICATION/NUMBER")
    identity = {
        "model": args.model,
        **({"unit": units[0]} if len(units) == 1 else {"units": sorted(units)}),
        "scope": "global" if args.global_config else "zone",
        "zone": args.zone or "",
    }
    params = {"scope": identity["scope"], "zone": identity["zone"]}
    metadata = Path(str(args.file) + ".orig.json")
    if operation == "pull":
        results = {unit: run_action(args.model, unit, "get-config", params) for unit in units}
        contents = [
            base64.b64decode(result["content-b64"], validate=True) for result in results.values()
        ]
        if any(content != contents[0] for content in contents[1:]):
            raise ValueError("source contents differ; pull individually using separate local files")
        args.file.write_bytes(contents[0])
        digests = {unit: result["sha256"] for unit, result in results.items()}
        tokens = {"sha256": digests[units[0]]} if len(units) == 1 else {"digests": digests}
        metadata.write_text(json.dumps(identity | tokens, indent=2) + "\n")
        print(
            f"Pulled {identity['scope']} {identity['zone']} from {args.model}: {', '.join(units)}"
        )
    elif operation == "push":
        if not metadata.is_file():
            raise ValueError("pull this target first to create its .orig.json conflict metadata")
        original = json.loads(metadata.read_text())
        if any(original.get(key) != value for key, value in identity.items()):
            raise ValueError(
                "local metadata belongs to a different target; pull the requested target first"
            )
        params["content-b64"] = base64.b64encode(args.file.read_bytes()).decode()
        if len(units) == 1:
            params["expected-sha256"] = original["sha256"]
            action = "validate-config" if args.validate_only else "apply-config"
            result = run_action(args.model, units[0], action, params)
            if not args.validate_only:
                metadata.write_text(
                    json.dumps(identity | {"sha256": result["sha256"]}, indent=2) + "\n"
                )
        else:
            result = push_units(args.model, units, params, original, metadata, args.validate_only)
        print(json.dumps(result, sort_keys=True))
    else:
        raise ValueError("operation must be pull or push")
    return 0
