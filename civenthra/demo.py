# -*- coding: utf-8 -*-
"""demo · 演示脚本（也是宣传视频的分镜）。

三幕，用可核验的本地检索与进程防护展示本地优先路径：

  第一幕  盒子连着网：问「人类的本质是什么？」，得到一段标准的、谁都不得罪的回答。
  第二幕  启用当前 Python 进程的出站限制，并当场自检。
  第三幕  再问同一个问题：基于文明穹顶书目的原文，给出带引用的、有重量的回答。

跑法：
    python -m civenthra.demo
    python -m civenthra.demo --q "人为什么要服从权力" --k 5
"""
from __future__ import annotations

import argparse
import sys
import time

from . import airgap
from . import config as C
from .embed import EmbedServer
from .index import IndexBundle
from .llm import LlamaServer
from .rag import Civenthra, format_citations

W = 74


def rule(ch="─"):
    print("  " + ch * W)


def title(t):
    print()
    rule("═")
    print("  " + t)
    rule("═")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="civenthra.demo")
    ap.add_argument("--q", default="人类的本质是什么？", help="演示用的问题")
    ap.add_argument("--k", type=int, default=C.TOP_K)
    ap.add_argument("--model", default=None)
    ap.add_argument("--small", action="store_true")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--no-airgap", action="store_true")
    ap.add_argument("--no-dense", action="store_true")
    a = ap.parse_args(argv)

    q = a.q
    print()
    print("  🔥  文 明 穹 顶   ·   civenthra")
    print("  问题：%s" % q)

    # 先起服务（此时还没封锁网络——因为模型加载走的是本地磁盘，本就不需要网）
    server = LlamaServer(model=a.model or (C.MODEL_SMALL if a.small else C.MODEL_MAIN),
                         ngl=(0 if a.cpu else C.NGL))
    sys.stdout.write("\n  加载模型 %s … " % server.model.name)
    sys.stdout.flush()
    t0 = time.time()
    server.start()
    print("就绪 (%.1fs)" % (time.time() - t0))

    es = None
    if not a.no_dense:
        try:
            es = EmbedServer()
            es.start()
        except Exception:
            es = None

    kb = Civenthra(server=server, top_k=a.k)
    if es:
        kb.index = IndexBundle.load(C.INDEX_DIR, dense=True, embed_server=es)
    print("  文明穹顶书目：%d 本 · %d 块文献" % (len(kb.index.books()), kb.index.n_chunks))

    try:
        # ── 第一幕 ─────────────────────────────────────────────────────────
        title("第一幕 · 盒子连着网：它只是一个普通的 AI 助手")
        print("  （没有任何文明穹顶文献，模型只能凭自己记住的东西说话）\n")
        t = time.time()
        r1 = kb.ask(q, mode="cloud")
        print("  " + r1["answer"].replace("\n", "\n  "))
        print("\n  ⏱ %.1fs　·　出处：无" % (time.time() - t))

        # ── 第二幕 ─────────────────────────────────────────────────────────
        title("第二幕 · 启用进程出站限制")
        print()
        if a.no_airgap:
            print("  ⚠ 本次以 --no-airgap 运行，跳过进程出站限制演示")
        else:
            airgap.engage()
            print(airgap.banner())
            print("  正在检查进程内网络钩子（不会访问真实外网）…")
            ok = airgap.verify(verbose=True)
            print()
            print("  → %s" % ("进程内钩子生效；整机隔离仍需防火墙、隔离网络或物理断网。"
                              if ok else "⚠ 检查失败：钩子未完整生效"))
        print()
        print("  现在再问一遍同一个问题。")

        # ── 第三幕 ─────────────────────────────────────────────────────────
        title("第三幕 · 文明穹顶模式：只在你拥有的文本里说话")
        print("  检索中 …")
        t = time.time()
        r2 = kb.ask(q, mode="civenthra")
        el = time.time() - t
        print()
        print("  " + r2["answer"].replace("\n", "\n  "))
        print()
        rule()
        print("  引用出处：")
        print(format_citations(r2["passages"]))
        rule()
        print("\n  ⏱ %.1fs　·　检索 %d 段 ·　来自 %d 本书"
              % (el, len(r2["passages"]),
                 len({p["no"] for p in r2["passages"]})))

        # ── 收尾 ───────────────────────────────────────────────────────────
        print()
        print("  " + "·" * W)
        print("  两段回答的差别，不在于哪个模型更强，而在于：")
        print("  前者靠「它训练时记住了什么」，后者靠「你手里真正有什么」。")
        print("  " + "·" * W)
        print()
    finally:
        server.stop()
        if es:
            es.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
