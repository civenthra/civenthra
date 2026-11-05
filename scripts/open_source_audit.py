"""Fail closed when a GitHub release candidate contains private or unsafe files."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RELEASE_MANIFEST = "release-manifest.txt"
MAX_PUBLIC_BYTES = 5 * 1024 * 1024
TEXT_SUFFIXES = {".bat", ".cff", ".cjs", ".cmd", ".css", ".html", ".ini", ".js", ".json",
                 ".lock", ".md", ".ps1", ".py", ".toml", ".txt", ".yaml", ".yml"}
TEXT_NAMES = {".gitattributes", ".gitignore", "CODEOWNERS", "LICENSE"}
DETECTOR_FILES = {"scripts/open_source_audit.py", "scripts/audit_archive.py"}
FORBIDDEN_SUFFIXES = {".db", ".epub", ".flac", ".gguf", ".key", ".m4a", ".mobi",
                      ".mov", ".mp3", ".mp4", ".onnx", ".pdf", ".pem", ".pkl", ".pt",
                      ".pth", ".safetensors", ".sqlite", ".wav"}
FORBIDDEN_PARTS = {".omx", ".venv", ".venv-avatar", ".venv-lora", "browser-profile",
                   "corpus", "data", "index", "models", "obsidian-vault", "out", "outputs"}
FORBIDDEN_NAMES = {"config.local.json", "publish_receipt.json", "publication_state.json"}
REQUIRED_FILES = [
    "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md", "CODE_OF_CONDUCT.md",
    "GOVERNANCE.md", "SUPPORT.md", "CHANGELOG.md", "ROADMAP.md",
    "MAINTAINERS.md", "CITATION.cff", "config.example.json", "pyproject.toml",
    "canon/rights-manifest.json", RELEASE_MANIFEST,
]


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


PATTERNS = [
    ("secret.private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("secret.aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret.openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("secret.github", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("secret.google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("privacy.user-path", re.compile(
        r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\\/\s'\"]+|/(?:home|Users)/[^/\s'\"]+)"
    )),
    ("privacy.platform-account", re.compile(r"(?i)(creator\.xiaohongshu\.com|account_identifier|Lionstar|2617567512)")),
    ("privacy.ssh-key-path", re.compile(r"(?i)\.ssh[\\/][^\s'\"]+")),
]


def scan_payload(relative: str, text: str) -> str:
    """Mask only the detector's PATTERNS assignment, never the rest of its source."""
    if relative not in DETECTOR_FILES:
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "PATTERNS"
                        for target in node.targets)):
            for index in range(node.lineno - 1, (node.end_lineno or node.lineno)):
                lines[index] = "\n" if lines[index].endswith(("\n", "\r")) else ""
    return "".join(lines)


def candidates(staged: bool = False) -> list[str]:
    if staged:
        command = ["git", "-c", "core.quotepath=false", "diff", "--cached",
                   "--name-only", "--diff-filter=ACMR"]
    else:
        command = ["git", "-c", "core.quotepath=false", "ls-files", "--cached",
                   "--others", "--exclude-standard"]
    output = subprocess.check_output(command, cwd=ROOT, text=True, encoding="utf-8")
    return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})


def tracked_paths() -> list[str]:
    output = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})


def manifest_findings(paths: list[str]) -> list[Finding]:
    manifest_path = ROOT / RELEASE_MANIFEST
    if not manifest_path.is_file():
        return [Finding("release.missing", RELEASE_MANIFEST, "required release file")]
    declared = {
        line.strip().replace("\\", "/")
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    tracked = set(paths)
    findings = [
        Finding("release.unreviewed", path, "tracked file is absent from release manifest")
        for path in sorted(tracked - declared)
    ]
    findings.extend(
        Finding("release.stale", path, "release manifest entry is not tracked")
        for path in sorted(declared - tracked)
    )
    return findings


def audit(paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in paths:
        pure = PurePosixPath(relative)
        lower_parts = {part.lower() for part in pure.parts}
        name = pure.name.lower()
        path = ROOT / Path(*pure.parts)
        if lower_parts & FORBIDDEN_PARTS:
            findings.append(Finding("path.private", relative, "private/generated directory"))
        if name in FORBIDDEN_NAMES or "cookie" in name or "credential" in name:
            findings.append(Finding("path.credential", relative, "credential or platform receipt name"))
        if pure.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(Finding("path.binary", relative, "book, model, key or generated-media format"))
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size == 0:
            findings.append(Finding("file.empty", relative, "zero-byte public file"))
        if size > MAX_PUBLIC_BYTES:
            findings.append(Finding("file.large", relative, f"{size} bytes exceeds 5 MiB"))
        is_text = pure.suffix.lower() in TEXT_SUFFIXES or pure.name in TEXT_NAMES
        if not is_text:
            findings.append(Finding(
                "file.unreviewed-format", relative,
                "public files must use an allowlisted UTF-8 text format",
            ))
            continue
        if size > MAX_PUBLIC_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("file.encoding", relative, "public text is not UTF-8"))
            continue
        text = scan_payload(relative, text)
        for code, pattern in PATTERNS:
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(Finding(code, relative, f"match at line {line}"))
    for name in REQUIRED_FILES:
        if not (ROOT / name).is_file():
            findings.append(Finding("release.missing", name, "required release file"))
    return sorted(set(findings), key=lambda item: (item.path, item.code, item.detail))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="audit only the staged release candidate")
    args = parser.parse_args()
    paths = candidates(args.staged)
    findings = audit(paths)
    findings.extend(manifest_findings(tracked_paths()))
    findings = sorted(set(findings), key=lambda item: (item.path, item.code, item.detail))
    if findings:
        print(f"OPEN_SOURCE_AUDIT=FAIL files={len(paths)} findings={len(findings)}")
        for item in findings:
            print(f"{item.code}\t{item.path}\t{item.detail}")
        return 1
    print(f"OPEN_SOURCE_AUDIT=PASS files={len(paths)} findings=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
