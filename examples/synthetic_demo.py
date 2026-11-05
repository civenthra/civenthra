"""Run a bilingual retrieval path using only project-authored synthetic text."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civenthra.index import BM25, IndexBundle, write_index_manifest
from civenthra.textutil import tokenize

CORPUS = ROOT / "examples" / "synthetic_corpus.json"


def build(folder: Path) -> IndexBundle:
    records = json.loads(CORPUS.read_text(encoding="utf-8"))
    for index, row in enumerate(records):
        row.update({"i": index, "pillar": "synthetic", "pillar_name": "Synthetic"})
    BM25().build([tokenize(row["text"]) for row in records]).save(folder / "bm25.json.gz")
    (folder / "chunks.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    (folder / "meta.json").write_text(
        json.dumps({"books": [10001, 10002], "chunks": len(records)}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_index_manifest(folder)
    return IndexBundle.load(folder)


def run(language: str) -> dict:
    query = ("公共资源决定如何记录证据和复核日期？" if language == "zh-CN"
             else "How does a habitat record evidence and review dates for shared decisions?")
    with tempfile.TemporaryDirectory(prefix="civenthra-synthetic-") as temp:
        hits = build(Path(temp)).search(query, k=2, cap_per_book=1)
    return {
        "demo": "synthetic-bilingual-retrieval",
        "language": language,
        "query": query,
        "sources": [{"rank": index + 1, "title": row["title"], "chapter": row["chapter"],
                     "score": row["score"]} for index, row in enumerate(hits)],
        "contains_private_or_book_text": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=("en", "zh-CN"), default="en")
    args = parser.parse_args()
    result = run(args.lang)
    if not result["sources"]:
        raise SystemExit("synthetic retrieval returned no sources")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
