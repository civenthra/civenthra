"""Generate a deterministic SPDX 2.3 JSON SBOM for the exact release manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                   encoding="utf-8").strip()


def git_status() -> list[str]:
    output = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT,
    )
    return [item.decode("utf-8", errors="replace") for item in output.split(b"\0") if item]


def ensure_clean_tree() -> None:
    dirty = git_status()
    if dirty:
        raise RuntimeError(
            "refusing to build a commit-namespaced SBOM from a dirty tree "
            "(%d changed or untracked paths)" % len(dirty)
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_paths() -> list[str]:
    return sorted(
        line.strip()
        for line in (ROOT / "release-manifest.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def document(*, require_clean: bool = True) -> dict:
    if require_clean:
        ensure_clean_tree()
    commit = git("rev-parse", "HEAD")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    committed_at = datetime.fromisoformat(git("show", "-s", "--format=%cI", "HEAD"))
    created = committed_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    namespace = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "civenthra:" + commit))
    files = []
    relationships = []
    for relative in release_paths():
        path = ROOT / relative
        spdx_id = "SPDXRef-File-" + hashlib.sha256(relative.encode()).hexdigest()[:16]
        files.append({
            "SPDXID": spdx_id,
            "fileName": "./" + relative,
            "checksums": [{"algorithm": "SHA256", "checksumValue": sha256(path)}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        })
        relationships.append({
            "spdxElementId": "SPDXRef-Package-Civenthra",
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": spdx_id,
        })
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "civenthra-local-" + version,
        "documentNamespace": namespace,
        "creationInfo": {"created": created, "creators": ["Tool: civenthra-build-sbom"]},
        "documentDescribes": ["SPDXRef-Package-Civenthra"],
        "packages": [{
            "SPDXID": "SPDXRef-Package-Civenthra",
            "name": "civenthra-local",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": True,
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "NOASSERTION",
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": "pkg:pypi/civenthra-local@" + version,
            }],
        }],
        "files": files,
        "relationships": relationships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document(), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("SBOM_WRITTEN=%s" % args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
