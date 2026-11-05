# -*- coding: utf-8 -*-
"""build_dense.py · 给已有索引补建向量库（一次性动作）。

这是整个项目里**唯一需要联网**的一步——只为下载嵌入模型。
模型下好、向量算完，之后拔掉网线照样跑，一辈子不用再联。

    python build_dense.py            # 全量
    python build_dense.py --limit 200  # 先试 200 块

未建向量库也能用（纯 BM25），只是检索精度差一档。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from civenthra import config as C                       # noqa: E402
from civenthra.embed import EmbedServer, build_dense    # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    idx = C.INDEX_DIR
    chunks = idx / "chunks.jsonl"
    if not chunks.exists():
        raise SystemExit("还没有索引。先跑：python -m civenthra.ingest")

    records = [json.loads(l) for l in chunks.read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.limit:
        records = records[:a.limit]
    print("待嵌入 %d 块" % len(records))

    srv = EmbedServer()
    print("启动嵌入服务 %s …" % srv.model.name)
    t0 = time.time()
    srv.start()
    print("就绪（%.1fs）" % (time.time() - t0))

    try:
        t1 = time.time()
        n = build_dense(records, idx, srv)
        print("\n向量库已生成：%s（%d 条，用时 %.0fs）" % (idx / "dense.bin", n, time.time() - t1))
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
