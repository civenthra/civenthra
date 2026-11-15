"""Opt-in live checks of every registered metadata source; outputs stay outside Git."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from civenthra.sources import load_registry


def check(source: dict, directory: Path) -> dict:
    destination = directory / (source["id"] + ".metadata")
    command = [sys.executable, "-m", "civenthra.collect", "sync-catalog", source["id"],
               "--output", str(destination), "--limit", "3", "--max-pages", "1",
               "--max-bytes", "33554432", "--timeout", "35"]
    started = time.monotonic()
    result = {"source_id": source["id"], "catalog_url": source["catalog_url"],
              "status": "failed", "scope": "metadata_only"}
    try:
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", timeout=95)
        if run.returncode:
            raise ValueError(run.stderr.strip())
        body = destination.read_bytes()
        receipt = json.loads(Path(str(destination) + ".receipt.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(body).hexdigest()
        if receipt.get("source_id") != source["id"] or receipt.get("sha256") != digest:
            raise ValueError("metadata receipt source or SHA-256 mismatch")
        if source["adapter"] == "oai-pmh":
            records = [json.loads(line) for line in body.decode("utf-8").splitlines()]
            if not any(not row["deleted"] and row["metadata"] for row in records):
                raise ValueError("no active metadata records")
            if source.get("oai_set") and not all(
                    source["oai_set"] in row["set_specs"] for row in records):
                raise ValueError("harvest contains records outside the configured set")
            result["records"] = len(records)
        elif source["catalog_format"] == "csv.gz":
            reader = csv.DictReader(io.StringIO(gzip.decompress(body).decode("utf-8-sig")))
            if not {"Text#", "Title", "Authors"}.issubset(reader.fieldnames or []):
                raise ValueError("unexpected Gutenberg catalog columns")
            result["records"] = sum(1 for _ in reader)
            if not result["records"]:
                raise ValueError("empty catalog")
        elif source["catalog_format"] == "md5-manifest":
            lines = body.decode("utf-8").splitlines()
            if not lines or not all(re.fullmatch(r"[0-9a-f]{32}\s+\S+", line) for line in lines):
                raise ValueError("invalid Wikimedia checksum manifest")
            if not any("pages-articles" in line for line in lines):
                raise ValueError("manifest contains no article dumps")
            result["file_entries"] = len(lines)
        else:
            document = json.loads(body)
            records = document["response"]["docs"]
            if not records or not all(row.get("identifier") for row in records):
                raise ValueError("empty or invalid Archive search results")
            result["records"] = len(records)
        result.update(status="passed", bytes=len(body), sha256=digest)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        help="new evidence directory (defaults to a unique temporary directory)")
    args = parser.parse_args()
    directory = args.output_dir or Path(tempfile.mkdtemp(prefix="civenthra-live-"))
    if args.output_dir:
        directory.mkdir(parents=True, exist_ok=False)
    results = []
    # Sequential requests also respect NCBI's no-concurrency requirement.
    for source in load_registry()["sources"]:
        result = check(source, directory)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        time.sleep(float(source["rate_limit_seconds"]))
    (directory / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Evidence directory: " + str(directory))
    return 0 if all(row["status"] == "passed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
