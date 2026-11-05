"""Bounded offline retrieval comparison, not an answer correctness certificate."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from . import config as C
from .evidence import verify, file_digest
from .graph import GraphStore, merge_passages
from .index import IndexBundle

# Expected book coverage is fixed before evaluation. Co-mentions do not prove
# that the books' positions agree. Questions inspect historical texts only.
CASES = [
    ("通风和卫生在护理中有什么联系", [937]),
    ("密封和罐藏如何用于食品保存", [711]),
    ("电学实验是怎样进行的", [708]),
    ("科学与假设怎样讨论实验", [291]),
    ("控制论的反馈如何参与控制", [260]),
    ("香农信息论如何理解熵和编码", [301]),
    ("政府论怎样讨论财产权与同意", [48]),
    ("车间管理怎样讨论劳动与标准化", [314]),
    ("控制论和通信的数学理论如何讨论通信", [260, 301]),
    ("电学实验和科学与假设如何理解实验", [708, 291]),
    ("火和热能不能变成动力", [321]),
    ("食物怎么保存才不会坏", [711, 1881]),
    ("怎么组织一群人干活", [314, 1070]),
    ("权力为什么能让人服从", [48]),
    ("通风 ventilation", [937]),
    ("科学方法 scientific method", [291]),
]


def evaluate(index=None, store=None):
    index = index or IndexBundle.load(C.INDEX_DIR)
    store = store or GraphStore.load()
    if store is None:
        raise RuntimeError("Build a matching graph snapshot first")
    results = []
    for question, expected in CASES:
        t0 = time.perf_counter()
        base = index.search(question, k=6, cap_per_book=2)
        bm_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        enriched = [store.enrich(p) for p in base]
        hybrid = merge_passages(enriched, store.search(question, 6), 6, 2)
        extra_ms = (time.perf_counter() - t0) * 1000
        base_nos, hybrid_nos = {p["no"] for p in base}, {p["no"] for p in hybrid}
        results.append({"question": question, "expected": expected,
                        "baseline_books": sorted(base_nos), "hybrid_books": sorted(hybrid_nos),
                        "baseline_coverage": len(base_nos.intersection(expected)) / len(expected),
                        "hybrid_coverage": len(hybrid_nos.intersection(expected)) / len(expected),
                        "baseline_ms": round(bm_ms, 2), "graph_extra_ms": round(extra_ms, 2),
                        "hybrid_passages": len(hybrid), "verified_passages": sum(verify(p) for p in hybrid),
                        "graph_additions": sum(bool(p.get("graph")) for p in hybrid)})
    def avg(key):
        return round(sum(r[key] for r in results) / len(results), 4)
    summary = {"questions": len(results), "baseline_expected_book_coverage": avg("baseline_coverage"),
               "hybrid_expected_book_coverage": avg("hybrid_coverage"),
               "improved": sum(r["hybrid_coverage"] > r["baseline_coverage"] for r in results),
               "regressed": sum(r["hybrid_coverage"] < r["baseline_coverage"] for r in results),
               "mean_baseline_ms": avg("baseline_ms"), "mean_graph_extra_ms": avg("graph_extra_ms"),
               "verified_passages": sum(r["verified_passages"] for r in results),
               "hybrid_passages": sum(r["hybrid_passages"] for r in results),
               "basis": "BM25 vs BM25 + scoped concept mentions; no LLM answer or dense benchmark"}
    return {"built_at": datetime.now().isoformat(), "index_sha256": file_digest(C.INDEX_DIR / "chunks.jsonl"),
            "snapshot": str(store.path), "summary": summary, "cases": results}


def main():
    parser = argparse.ArgumentParser(description="Local retrieval comparison, no API/model calls")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    result = evaluate()
    if args.save:
        folder = C.ROOT / "graph"
        folder.mkdir(exist_ok=True)
        path = folder / ("evaluation_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report"] = str(path)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
