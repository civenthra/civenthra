# -*- coding: utf-8 -*-
"""cli · 命令行对话入口。

    python -m civenthra.cli                      交互提问（默认文明穹顶模式）
    python -m civenthra.cli -q "人类的本质是什么"   单次提问
    python -m civenthra.cli --compare -q "..."    对照：裸模型 vs 文明穹顶模式
    python -m civenthra.cli --cloud -q "..."      模拟「联网助手」的回答
    python -m civenthra.cli --passages            同时打印召回的原文段落
    python -m civenthra.cli --small --cpu         树莓派档：1.7B 模型，纯 CPU
    python -m civenthra.cli --no-airgap           关闭进程出站限制（仅用于调试）

注意：默认启用的是当前 Python 进程的纵深防御，不等同整机防火墙或物理断网。
"""
from __future__ import annotations

import argparse
import sys
import time

from . import airgap
from . import config as C
from .llm import LlamaServer
from .rag import Civenthra, format_citations

APP = "文明穹顶"

HEAD = """\
  ┌────────────────────────────────────────────────────┐
  │  🔥  文 明 穹 顶  ·  civenthra                     │
  │  一个只在你拥有文本里说话的离线问答终端            │
  └────────────────────────────────────────────────────┘"""


def _print_answer(res: dict, show_passages: bool):
    english = res.get("language") == "en"
    ans = res["answer"]
    print()
    print("  " + "─" * 60)
    for line in ans.splitlines():
        print("  " + line if line.strip() else "")
    print("  " + "─" * 60)
    if res["citations"]:
        print("\n  Sources:" if english else "\n  依据：")
        print(format_citations(res["passages"], language=res.get("language", "zh-CN")))
    else:
        print("\n  (No local sources were retrieved; this answer comes from model memory.)"
              if english else "\n  （本轮未检索文献，回答来自模型自身参数记忆）")
    if show_passages:
        print("\n  ── 召回原文 ──")
        for i, p in enumerate(res["passages"], 1):
            print("\n  【%d】《%s》· %s" % (i, p["title"], p.get("chapter", "")))
            print("      " + p["text"][:700].replace("\n", "\n      "))
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="civenthra.cli", add_help=True,
                                 description="文明穹顶 · 离线问答")
    ap.add_argument("-q", "--question", default=None, help="直接提问（不给则进入交互模式）")
    ap.add_argument("--compare", action="store_true", help="同一问题对比：裸模型 vs 文明穹顶")
    ap.add_argument("--cloud", action="store_true", help="只走「联网助手」模式")
    ap.add_argument("--k", type=int, default=C.TOP_K, help="送入模型的段落数")
    ap.add_argument("--cap", type=int, default=C.PER_BOOK_CAP, help="同一本书最多几段")
    ap.add_argument("--passages", action="store_true", help="打印召回原文全文")
    ap.add_argument("--model", default=None, help="指定 GGUF 模型路径")
    ap.add_argument("--small", action="store_true", help="用 1.7B 小模型（树莓派档）")
    ap.add_argument("--cpu", action="store_true", help="纯 CPU 推理（-ngl 0）")
    ap.add_argument("--no-airgap", action="store_true", help="不启用进程出站限制（调试用）")
    ap.add_argument("--no-dense", action="store_true", help="只用 BM25，不走向量重排")
    ap.add_argument("--lang", choices=("auto", "en", "zh-CN"), default="auto",
                    help="answer language: auto, en, or zh-CN")
    a = ap.parse_args(argv)

    # ── 进程出站限制 + 自检 ────────────────────────────────────────────────
    if not a.no_airgap:
        airgap.engage()
        print(airgap.banner())
        print("  正在检查进程内网络钩子 …")
        ok = airgap.verify(verbose=True)
        print("  → %s\n" % ("通过：进程内钩子生效" if ok else "⚠ 检查失败：钩子未完整生效"))
    else:
        print("\n  ⚠ 调试模式：未启用进程出站限制\n")

    model = a.model or (C.MODEL_SMALL if a.small else C.MODEL_MAIN)
    ngl = 0 if a.cpu else C.NGL
    server = LlamaServer(model=model, ngl=ngl)
    print("  加载模型 %s …" % server.model.name)
    t0 = time.time()
    server.start()
    print("  就绪（%.1fs）" % (time.time() - t0))

    print("  载入索引 …")
    t1 = time.time()
    from .embed import EmbedServer
    es = None
    use_dense = not a.no_dense
    if use_dense:
        try:
            es = EmbedServer()
            es.start()
        except Exception as e:
            print("  （向量服务不可用，退化为 BM25：%s）" % str(e)[:70])
            es = None
    kb = Civenthra(server=server, top_k=a.k, cap_per_book=a.cap)
    if use_dense:
        from .index import IndexBundle
        kb.index = IndexBundle.load(C.INDEX_DIR, dense=True, embed_server=es)
        if kb.index.dense is None:
            print("  （未找到向量库，退化为 BM25。用 build_dense.py 生成）")
    print("  %d 块 / %d 本  就绪（%.2fs）\n"
          % (kb.index.n_chunks, len(kb.index.books()), time.time() - t1))

    def ask(q, mode):
        t = time.time()
        r = kb.ask(q, mode=mode, language=a.lang)
        r["elapsed"] = time.time() - t
        return r

    def run_one(q):
        if a.compare:
            print("\n═══ ① 裸模型（没有文明穹顶知识库）═══")
            r1 = ask(q, "cloud")
            _print_answer(r1, False)
            print("  用时 %.1fs" % r1["elapsed"])
            print("\n═══ ② 文明穹顶模式（离线 + 引用原文）═══")
            r2 = ask(q, "civenthra")
            _print_answer(r2, a.passages)
            print("  用时 %.1fs" % r2["elapsed"])
        else:
            mode = "cloud" if a.cloud else "civenthra"
            r = ask(q, mode)
            _print_answer(r, a.passages)
            print("  用时 %.1fs" % r["elapsed"])

    try:
        if a.question:
            run_one(a.question)
        else:
            print("  直接输入问题即可。命令：/q 退出  /p 切换显示原文  /cloud 切换模式\n")
            show = a.passages
            mode = "cloud" if a.cloud else "civenthra"
            while True:
                try:
                    q = input("  问> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not q:
                    continue
                if q in ("/q", "/quit", "/exit"):
                    break
                if q == "/p":
                    show = not show
                    print("  显示原文：%s" % ("开" if show else "关"))
                    continue
                if q == "/cloud":
                    mode = "cloud" if mode == "civenthra" else "civenthra"
                    print("  模式：%s" % mode)
                    continue
                a.passages = show
                if a.compare:
                    run_one(q)
                else:
                    r = ask(q, mode)
                    _print_answer(r, show)
                    print("  用时 %.1fs" % r["elapsed"])
    finally:
        server.stop()
        if es:
            es.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
