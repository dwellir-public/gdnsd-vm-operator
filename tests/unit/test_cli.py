"""Check pull identity, conflict metadata and Juju action result handling."""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gdnsd_cli


def test_pull_preserves_target_and_digest(tmp_path, monkeypatch):
    content = b"zone content\n"
    monkeypatch.setattr(
        gdnsd_cli,
        "run_action",
        lambda *a: {
            "content-b64": base64.b64encode(content).decode(),
            "sha256": "a" * 64,
            "exists": True,
        },
    )
    target = tmp_path / "x.zone"
    gdnsd_cli.main(
        "pull",
        [
            "--model",
            "ctl:model",
            "--unit",
            "gdnsd-vm/0",
            "--zone",
            "x.example.com",
            "--file",
            str(target),
        ],
    )
    assert target.read_bytes() == content
    meta = json.loads(Path(str(target) + ".orig.json").read_text())
    assert meta["model"] == "ctl:model"
    assert meta["unit"] == "gdnsd-vm/0"
    assert "units" not in meta
    assert meta["sha256"] == "a" * 64
    assert meta["zone"] == "x.example.com"


def test_push_refuses_a_different_target(tmp_path, monkeypatch):
    file = tmp_path / "x.zone"
    file.write_text("edited")
    Path(str(file) + ".orig.json").write_text(
        json.dumps(
            {
                "model": "other:model",
                "unit": "gdnsd-vm/0",
                "scope": "zone",
                "zone": "x.example.com",
                "sha256": "a" * 64,
            }
        )
    )
    calls = []
    monkeypatch.setattr(gdnsd_cli, "run_action", lambda *a: calls.append(a))
    with pytest.raises(ValueError, match="target"):
        gdnsd_cli.main(
            "push",
            [
                "--model",
                "ctl:model",
                "--unit",
                "gdnsd-vm/0",
                "--zone",
                "x.example.com",
                "--file",
                str(file),
            ],
        )
    assert not calls


def test_extract_failed_action_is_not_success():
    with pytest.raises(RuntimeError, match="rejected"):
        gdnsd_cli.action_results({"gdnsd-vm/0": {"status": "failed", "message": "rejected"}})


def test_extract_completed_action():
    assert gdnsd_cli.action_results(
        {"gdnsd-vm/0": {"status": "completed", "results": {"sha256": "abc"}}}
    ) == {"sha256": "abc"}


def test_model_must_be_controller_qualified(tmp_path):
    with pytest.raises(ValueError, match="controller"):
        gdnsd_cli.main(
            "pull",
            [
                "--model",
                "model",
                "--unit",
                "gdnsd-vm/0",
                "--zone",
                "x.example.com",
                "--file",
                str(tmp_path / "x"),
            ],
        )


def multi_args(target):
    """Select two explicit units in one controller-qualified model."""
    return [
        "--model",
        "ctl:model",
        "--unit",
        "gdnsd-vm/0",
        "--unit",
        "gdnsd-vm/1",
        "--zone",
        "x.example.com",
        "--file",
        str(target),
    ]


def prepare_multi_pull(tmp_path, monkeypatch):
    """Create the conflict metadata through the real pull command."""
    target = tmp_path / "multi.zone"
    monkeypatch.setattr(
        gdnsd_cli,
        "run_action",
        lambda *args: {"content-b64": "b3JpZ2luYWw=", "sha256": "a" * 64},
    )
    gdnsd_cli.main("pull", multi_args(target))
    target.write_text("edited")
    return target


def test_multi_push_validates_every_unit_before_applying(tmp_path, monkeypatch):
    target = prepare_multi_pull(tmp_path, monkeypatch)
    calls = []

    def action(model, unit, name, params):
        calls.append((unit, name, params["expected-sha256"]))
        return {"sha256": "b" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    gdnsd_cli.main("push", multi_args(target))
    assert calls == [
        ("gdnsd-vm/0", "validate-config", "a" * 64),
        ("gdnsd-vm/1", "validate-config", "a" * 64),
        ("gdnsd-vm/0", "apply-config", "a" * 64),
        ("gdnsd-vm/1", "apply-config", "a" * 64),
    ]


def test_multi_push_validation_failure_applies_nothing(tmp_path, monkeypatch):
    target = prepare_multi_pull(tmp_path, monkeypatch)
    calls = []

    def action(model, unit, name, params):
        calls.append((unit, name))
        if unit == "gdnsd-vm/1":
            raise RuntimeError("configuration rejected")
        return {"sha256": "b" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    with pytest.raises(RuntimeError, match="configuration rejected"):
        gdnsd_cli.main("push", multi_args(target))
    assert all(name == "validate-config" for _, name in calls)


def test_multi_push_reports_partial_application_and_keeps_digests(tmp_path, monkeypatch):
    target = prepare_multi_pull(tmp_path, monkeypatch)

    def action(model, unit, name, params):
        if name == "apply-config" and unit == "gdnsd-vm/1":
            raise RuntimeError("connection lost")
        return {"sha256": "b" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    with pytest.raises(RuntimeError) as failure:
        gdnsd_cli.main("push", multi_args(target))
    message = str(failure.value)
    assert "failed target: gdnsd-vm/1" in message
    assert "already applied: gdnsd-vm/0" in message
    assert "connection lost" in message
    metadata = json.loads(Path(str(target) + ".orig.json").read_text())
    assert metadata["digests"] == {"gdnsd-vm/0": "b" * 64, "gdnsd-vm/1": "a" * 64}


def test_multi_pull_rejects_different_source_contents_without_writing(tmp_path, monkeypatch):
    target = tmp_path / "multi.zone"
    target.write_text("keep local edits")

    def action(model, unit, name, params):
        return {"content-b64": base64.b64encode(unit.encode()).decode(), "sha256": "a" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    with pytest.raises(ValueError, match="pull individually"):
        gdnsd_cli.main("pull", multi_args(target))
    assert target.read_text() == "keep local edits"
    assert not Path(str(target) + ".orig.json").exists()


def test_multi_validate_only_preserves_metadata(tmp_path, monkeypatch):
    target = prepare_multi_pull(tmp_path, monkeypatch)
    metadata = Path(str(target) + ".orig.json")
    before = metadata.read_bytes()
    calls = []

    def action(model, unit, name, params):
        calls.append((unit, name))
        return {"sha256": "b" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    gdnsd_cli.main("push", multi_args(target) + ["--validate-only"])
    assert calls == [("gdnsd-vm/0", "validate-config"), ("gdnsd-vm/1", "validate-config")]
    assert metadata.read_bytes() == before


def test_single_push_keeps_original_metadata_format(tmp_path, monkeypatch):
    target = tmp_path / "single.zone"
    args = multi_args(target)
    del args[4:6]
    monkeypatch.setattr(
        gdnsd_cli,
        "run_action",
        lambda *args: {"content-b64": "eA==", "sha256": "a" * 64},
    )
    gdnsd_cli.main("pull", args)
    calls = []

    def action(model, unit, name, params):
        calls.append((unit, name))
        return {"sha256": "b" * 64}

    monkeypatch.setattr(gdnsd_cli, "run_action", action)
    gdnsd_cli.main("push", args)
    assert calls == [("gdnsd-vm/0", "apply-config")]
    metadata = json.loads(Path(str(target) + ".orig.json").read_text())
    assert metadata["unit"] == "gdnsd-vm/0"
    assert metadata["sha256"] == "b" * 64
    assert "units" not in metadata


def test_action_results_normalizes_juju_string_booleans():
    result = gdnsd_cli.action_results(
        {
            "u/0": {
                "status": "completed",
                "results": {
                    "exists": "False",
                    "changed": "False",
                    "validated": "True",
                    "active": "True",
                },
            }
        }
    )
    assert result == {"exists": False, "changed": False, "validated": True, "active": True}


def test_parameter_file_is_visible_outside_snap_private_tmp(monkeypatch):
    import subprocess

    seen = []
    monkeypatch.setattr(gdnsd_cli.shutil, "which", lambda _: "/snap/bin/juju")

    def command(argv, **kwargs):
        parameter_file = Path(argv[argv.index("--params") + 1])
        assert parameter_file.is_file()
        assert parameter_file.is_relative_to(Path.home())
        seen.append(parameter_file)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"u/0": {"status": "completed", "results": {}}}), ""
        )

    monkeypatch.setattr(gdnsd_cli.subprocess, "run", command)
    gdnsd_cli.run_action("ctl:model", "u/0", "get-config", {})
    assert not seen[0].exists()
