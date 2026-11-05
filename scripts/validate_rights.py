"""Fail-closed checks for public content, translation, media and likeness rights."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT_TYPES = {"book_text", "translation", "audio", "video", "image", "voice", "likeness",
                 "synthetic_text"}
MEDIA_SUFFIXES = {".pdf", ".epub", ".mobi", ".mp3", ".wav", ".mp4", ".mov",
                  ".png", ".jpg", ".jpeg", ".webp", ".safetensors", ".gguf"}
RIGHTS_MANAGED_PREFIXES = ("assets/", "canon/", "examples/")
RIGHTS_MANAGED_SUFFIXES = MEDIA_SUFFIXES | {".csv", ".srt", ".tsv", ".txt", ".vtt"}
EXCLUDED = "excluded_unreviewed"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def tracked_paths(root: Path = ROOT):
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root, stderr=subprocess.STDOUT
    )
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def findings(root: Path = ROOT, tracked=None):
    tracked = tracked if tracked is not None else tracked_paths(root)
    issues = []
    try:
        canon = read_json(root / "canon" / "canon.json")
        ledger = read_json(root / "canon" / "rights-manifest.json")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [("rights.invalid", str(exc))]

    if ledger.get("schema") != 1 or ledger.get("release_policy") != "deny_unless_explicitly_included":
        issues.append(("rights.policy", "rights ledger must use schema 1 and deny-by-default"))

    expected = [book.get("no") for book in canon.get("books", [])]
    observed = ledger.get("catalog_book_nos")
    if not isinstance(observed, list) or len(observed) != len(set(observed)):
        issues.append(("rights.catalog", "catalog_book_nos must be a unique list"))
    elif set(expected) != set(observed):
        issues.append(("rights.catalog", "rights ledger must cover every canon book number"))

    defaults = ledger.get("catalog_defaults", {})
    for component in ("source_text", "translation", "audio", "video", "voice_or_likeness"):
        if defaults.get(component) != EXCLUDED:
            issues.append(("rights.default", "%s must default to %s" % (component, EXCLUDED)))

    covered_paths = set()
    for asset in ledger.get("public_assets", []):
        status = asset.get("status")
        asset_type = asset.get("asset_type")
        paths = asset.get("paths")
        if not isinstance(paths, list) or not paths:
            issues.append(("rights.asset", "%s has no paths" % asset.get("asset_id", "unknown")))
            continue
        for path in paths:
            covered_paths.add(path)
            if path not in tracked:
                issues.append(("rights.asset", "%s is not tracked" % path))
        if asset_type in CONTENT_TYPES:
            required = ("status", "license_or_permission", "evidence_uri", "territories",
                        "reviewed_by", "reviewed_at")
            if status != "approved" or any(not asset.get(key) for key in required[1:]):
                issues.append(("rights.approval", "%s lacks complete approval evidence" % asset.get("asset_id")))
        elif status != "included_noncontent" or not asset.get("basis"):
            issues.append(("rights.noncontent", "%s has an invalid non-content decision" % asset.get("asset_id")))

    for override in ledger.get("overrides", []):
        if override.get("status") == "approved":
            required = ("component", "license_or_permission", "evidence_uri", "territories",
                        "reviewed_by", "reviewed_at")
            if any(not override.get(key) for key in required):
                issues.append(("rights.override", "approved override lacks evidence"))
        elif override.get("status") != EXCLUDED:
            issues.append(("rights.override", "override must be approved or excluded_unreviewed"))

    for path in tracked:
        if Path(path).suffix.lower() in MEDIA_SUFFIXES and path not in covered_paths:
            issues.append(("rights.unregistered_media", path))
        is_managed = path.startswith(RIGHTS_MANAGED_PREFIXES) or Path(path).suffix.lower() in RIGHTS_MANAGED_SUFFIXES
        if is_managed and path not in covered_paths:
            issues.append(("rights.unregistered_content", path))
    return issues


def main() -> int:
    issues = findings()
    if issues:
        print("RIGHTS_AUDIT=FAIL findings=%d" % len(issues))
        for code, detail in issues:
            print("%s\t%s" % (code, detail))
        return 1
    ledger = read_json(ROOT / "canon" / "rights-manifest.json")
    print("RIGHTS_AUDIT=PASS catalog=%d public_assets=%d" % (
        len(ledger["catalog_book_nos"]), len(ledger["public_assets"])
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
