"""Inventory local generated media without modifying or publishing it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone


MEDIA = {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".flac", ".png", ".jpg", ".jpeg", ".webp"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(root: Path, *, hash_files: bool = False) -> dict:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in MEDIA:
            continue
        size = path.stat().st_size
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": size,
                     "status": "empty" if size == 0 else "present",
                     "sha256": digest(path) if hash_files and size else None})
    return {"generated_utc": datetime.now(timezone.utc).isoformat(), "root": str(root),
            "counts": {"total": len(rows), "present": sum(r["status"] == "present" for r in rows),
                       "empty": sum(r["status"] == "empty" for r in rows)}, "files": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--hash", action="store_true")
    args = parser.parse_args()
    report = inventory(args.root.resolve(), hash_files=args.hash)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 1 if report["counts"]["empty"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
