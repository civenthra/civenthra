# -*- coding: utf-8 -*-
"""扫融合权重 w_vec（向量一路的 RRF 权重），看是否存在一个稳健区间。

    python tests/exp_weight.py

只读实验，用来给 `IndexBundle.search` 的 w_vec 默认值定档。
判据不是「某一条用例过了」，而是**整组用例的通过数随 w_vec 的变化是否平坦**——
若只有一个孤立点能过，那是过拟合，不能当默认值。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from civenthra import config as C          # noqa: E402
from civenthra.index import IndexBundle    # noqa: E402
from test_retrieval import CASES          # noqa: E402

W_VEC = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5, 3.0]


def main():
    from civenthra.embed import EmbedServer
    es = EmbedServer()
    es.start()
    ix = IndexBundle.load(C.INDEX_DIR, dense=True, embed_server=es)
    print("索引：%d 块 / %d 本\n" % (ix.n_chunks, len(ix.books())))

    rows = {}
    for w in W_VEC:
        passed, detail = 0, []
        for q, expect in CASES:
            hits = ix.search(q, k=4, cap_per_book=2, w_vec=w)
            titles = [h["title"] for h in hits]
            ok = any(any(e in t for t in titles) for e in expect)
            passed += ok
            detail.append("%s:%s" % (q[:4], "O" if ok else "X"))
        rows[w] = (passed, detail)

    print("%-6s %-6s  %s" % ("w_vec", "通过", "逐题"))
    for w in W_VEC:
        p, d = rows[w]
        print("%-6.1f %-6s  %s" % (w, "%d/%d" % (p, len(CASES)), " ".join(d)))

    print("\n各题在哪个 w_vec 下通过：")
    for i, (q, _) in enumerate(CASES):
        wins = [w for w in W_VEC if rows[w][1][i].endswith("O")]
        print("  %-24s %s" % (q, wins if wins else "** 无 **"))

    es.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
