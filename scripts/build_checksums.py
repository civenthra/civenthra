"""Write SHA-256 checksums for release artifacts."""
from __future__ import annotations

import argparse
import glob
import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def expand(patterns: list[str]) -> list[Path]:
    paths = []
    for pattern in patterns:
        matches = [Path(item) for item in glob.glob(pattern)]
        paths.extend(matches or [Path(pattern)])
    return sorted({path.resolve() for path in paths if path.is_file()}, key=lambda p: p.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("artifacts", nargs="+")
    args = parser.parse_args()
    artifacts = expand(args.artifacts)
    if not artifacts:
        parser.error("no release artifacts found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join("%s  %s\n" % (digest(path), path.name)
                                   for path in artifacts), encoding="utf-8")
    print("CHECKSUMS_WRITTEN=%s files=%d" % (args.output, len(artifacts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
