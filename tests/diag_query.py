# -*- coding: utf-8 -*-
"""诊断一条查询的精排全景：每一分的来路（fusion / 覆盖度 / 短语 / 邻近度 / 附属页降权）。

    python tests/diag_query.py                 # 默认诊断「怎么防止瘟疫在城里蔓延」
    python tests/diag_query.py "你的问题"       # 指定问题
    python tests/diag_query.py "..." --no-dense

只读，不改索引。用来回答「为什么这本书没进前四名」这类问题：
是召回就没捞到（in_bm25=False 且向量排名靠后），还是召回捞到了、精排把它压下去了。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from civenthra import config as C          # noqa: E402
from civenthra.index import IndexBundle    # noqa: E402

DEFAULT_Q = "怎么防止瘟疫在城里蔓延"


def _opt(name: str, default: float) -> float:
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return float(sys.argv[i + 1])
    return default


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    query = args[0] if args else DEFAULT_Q
    use_dense = "--no-dense" not in sys.argv
    w_bm = _opt("--wbm", 1.0)
    w_vec = _opt("--wvec", 1.0)

    es = None
    ix = IndexBundle.load(C.INDEX_DIR, dense=False)
    if use_dense:
        try:
            from civenthra.embed import EmbedServer
            es = EmbedServer()
            es.start()
            ix = IndexBundle.load(C.INDEX_DIR, dense=True, embed_server=es)
        except Exception as e:
            print("向量不可用，退回 BM25：%s" % e)
            use_dense = False

    keys = ix.bm25.key_terms(query)
    print("查询：%s" % query)
    print("锐利词（按稀有度）：%s" % " · ".join(keys[:12]))
    print("模式：%s\n" % ("BM25 + 向量直扫" if use_dense else "纯 BM25"))

    if w_bm != 1.0 or w_vec != 1.0:
        print("融合权重：w_bm=%.2f  w_vec=%.2f" % (w_bm, w_vec))
    trace: list = []
    hits = ix.search(query, k=8, cap_per_book=2, w_bm=w_bm, w_vec=w_vec, _trace=trace)

    print("%-4s %-22s %-14s %8s %5s %5s %5s %9s %s"
          % ("#", "书名", "章", "score", "cov", "phr", "prox", "fuse", "来源"))
    for t in trace[:25]:
        src = ("BM25+vec" if t["in_bm25"] else "vec")
        print("%-4d %-22s %-14s %8.2f %5.2f %5.2f %5.2f %9.5f %s%s"
              % (t["pos"], t["title"][:22], (t["chapter"] or "-")[:14],
                 t["score"], t["cov"], t["phrase"], t["prox"],
                 t["fuse"], src, "  [matter×0.6]" if t["matter"] else ""))

    gate = trace[0]["score"] * 0.35 if trace else 0.0
    print("\n相对门槛 = %.2f（第一名 × 0.35）" % gate)
    print("\n最终入选 %d 条：" % len(hits))
    for i, h in enumerate(hits, 1):
        print("  [%d] %s · %s  (score %.2f, cov %.2f)"
              % (i, h["title"], h.get("chapter", "-"), h["score"], h["coverage"]))

    if "--full" in sys.argv:
        print("\n" + "=" * 70)
        for i, h in enumerate(hits, 1):
            print("\n── [%d] %s · %s ──" % (i, h["title"], h.get("chapter", "-")))
            print(re.sub(r"\s+", " ", h["text"])[:600])

    if es:
        es.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
