"""Audit every blob reachable from HEAD, including files later deleted."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

from open_source_audit import (
    FORBIDDEN_NAMES, FORBIDDEN_PARTS, FORBIDDEN_SUFFIXES, MAX_PUBLIC_BYTES,
    PATTERNS, RELEASE_MANIFEST, ROOT, TEXT_SUFFIXES,
)

DETECTOR_FILES = {
    "scripts/open_source_audit.py", "scripts/git_history_audit.py",
    "scripts/audit_archive.py",
}


def run(*args: str, text: bool = False):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=text,
                                   encoding="utf-8" if text else None)


def declared_paths() -> set[str]:
    return {
        line.strip().replace("\\", "/")
        for line in (ROOT / RELEASE_MANIFEST).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def findings() -> list[tuple[str, str, str]]:
    declared = declared_paths()
    rows = run("-c", "core.quotepath=false", "rev-list", "--objects", "HEAD", text=True)
    seen = set()
    issues = []
    for row in rows.splitlines():
        if " " not in row:
            continue
        oid, path = row.split(" ", 1)
        path = path.replace("\\", "/")
        key = (oid, path)
        if key in seen:
            continue
        seen.add(key)
        pure = PurePosixPath(path)
        lower_parts = {part.lower() for part in pure.parts}
        if (lower_parts & FORBIDDEN_PARTS or pure.name.lower() in FORBIDDEN_NAMES
                or pure.suffix.lower() in FORBIDDEN_SUFFIXES):
            issues.append(("history.private-path", path, oid[:12]))
        try:
            kind = run("cat-file", "-t", oid, text=True).strip()
            size = int(run("cat-file", "-s", oid, text=True).strip())
        except (subprocess.CalledProcessError, ValueError):
            issues.append(("history.unreadable", path, oid[:12]))
            continue
        if kind != "blob":
            continue
        if path not in declared:
            issues.append(("history.removed-path", path, "reachable from HEAD but absent from release manifest"))
        if size > MAX_PUBLIC_BYTES:
            issues.append(("history.large", path, "%d bytes" % size))
        if path in DETECTOR_FILES or pure.suffix.lower() not in TEXT_SUFFIXES or size > MAX_PUBLIC_BYTES:
            continue
        data = run("cat-file", "blob", oid)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(("history.encoding", path, oid[:12]))
            continue
        for code, pattern in PATTERNS:
            if pattern.search(text):
                issues.append(("history." + code, path, oid[:12]))
    return sorted(set(issues))


def main() -> int:
    issues = findings()
    if issues:
        print("HISTORY_AUDIT=FAIL findings=%d" % len(issues))
        for code, path, detail in issues:
            print("%s\t%s\t%s" % (code, path, detail))
        return 1
    print("HISTORY_AUDIT=PASS findings=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
