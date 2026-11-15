"""Reject unsafe or private content in a built wheel before attestation."""
from __future__ import annotations

import argparse
import glob
import re
import zipfile
from pathlib import Path, PurePosixPath

MAX_MEMBER_BYTES = 5 * 1024 * 1024
FORBIDDEN_SUFFIXES = {".db", ".epub", ".gguf", ".key", ".mp3", ".mp4", ".pdf", ".pem",
                      ".pkl", ".pt", ".pth", ".safetensors", ".sqlite", ".wav", ".pyc"}
PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\\/\s'\"]+|/(?:home|Users)/[^/\s'\"]+)"),
]
PACKAGE_TEXT_SUFFIXES = {".json", ".py"}
PACKAGE_TEXT_NAMES = {"LICENSE"}


def member_kind(pure: PurePosixPath) -> str | None:
    if not pure.parts:
        return None
    if pure.parts[0] == "civenthra":
        if pure.suffix.lower() in PACKAGE_TEXT_SUFFIXES or pure.name in PACKAGE_TEXT_NAMES:
            return "package"
        return None
    if re.fullmatch(r"civenthra_local-[A-Za-z0-9_.+!-]+\.dist-info", pure.parts[0]):
        return "metadata"
    return None


def findings(path: Path) -> list[str]:
    issues = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            pure = PurePosixPath(member.filename)
            if pure.is_absolute() or ".." in pure.parts:
                issues.append("unsafe path: %s" % member.filename)
                continue
            if pure.suffix.lower() in FORBIDDEN_SUFFIXES:
                issues.append("forbidden format: %s" % member.filename)
            kind = member_kind(pure)
            if kind is None:
                issues.append("unexpected root: %s" % member.filename)
                continue
            if member.file_size > MAX_MEMBER_BYTES:
                issues.append("oversized member: %s" % member.filename)
            if member.is_dir() or member.file_size > MAX_MEMBER_BYTES:
                continue
            data = archive.read(member)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                issues.append("non-UTF8 member: %s" % member.filename)
                continue
            if any(pattern.search(text) for pattern in PATTERNS):
                issues.append("sensitive pattern: %s" % member.filename)
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel")
    args = parser.parse_args()
    matches = [Path(item) for item in glob.glob(args.wheel)]
    if len(matches) != 1:
        parser.error("wheel pattern must match exactly one file")
    wheel = matches[0]
    issues = findings(wheel)
    if issues:
        print("ARCHIVE_AUDIT=FAIL findings=%d" % len(issues))
        for issue in issues:
            print(issue)
        return 1
    print("ARCHIVE_AUDIT=PASS wheel=%s" % wheel.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
