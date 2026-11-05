import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_rights.py"
SPEC = importlib.util.spec_from_file_location("validate_rights", SCRIPT)
rights = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rights)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def minimal_root(tmp_path):
    write_json(tmp_path / "canon" / "canon.json", {"books": [{"no": 1}]})
    write_json(tmp_path / "canon" / "rights-manifest.json", {
        "schema": 1,
        "release_policy": "deny_unless_explicitly_included",
        "catalog_defaults": {key: "excluded_unreviewed" for key in (
            "source_text", "translation", "audio", "video", "voice_or_likeness"
        )},
        "catalog_book_nos": [1],
        "overrides": [],
        "public_assets": [],
    })
    return tmp_path


def test_repository_rights_ledger_passes():
    assert rights.findings() == []


def test_missing_catalog_entry_fails_closed(tmp_path):
    root = minimal_root(tmp_path)
    ledger = json.loads((root / "canon" / "rights-manifest.json").read_text())
    ledger["catalog_book_nos"] = []
    write_json(root / "canon" / "rights-manifest.json", ledger)
    assert any(code == "rights.catalog" for code, _ in rights.findings(root, tracked=set()))


def test_content_approval_requires_evidence(tmp_path):
    root = minimal_root(tmp_path)
    ledger = json.loads((root / "canon" / "rights-manifest.json").read_text())
    ledger["public_assets"] = [{
        "asset_id": "demo-video", "asset_type": "video",
        "paths": ["demo.mp4"], "status": "approved",
    }]
    write_json(root / "canon" / "rights-manifest.json", ledger)
    issues = rights.findings(root, tracked={"demo.mp4"})
    assert any(code == "rights.approval" for code, _ in issues)


def test_unregistered_media_fails(tmp_path):
    root = minimal_root(tmp_path)
    issues = rights.findings(root, tracked={"surprise.wav"})
    assert ("rights.unregistered_media", "surprise.wav") in issues


def test_unregistered_ordinary_content_fails(tmp_path):
    root = minimal_root(tmp_path)
    issues = rights.findings(root, tracked={"notes/source.txt"})
    assert ("rights.unregistered_content", "notes/source.txt") in issues


def test_content_directories_are_deny_by_default(tmp_path):
    root = minimal_root(tmp_path)
    issues = rights.findings(root, tracked={"examples/unreviewed.md"})
    assert ("rights.unregistered_content", "examples/unreviewed.md") in issues
