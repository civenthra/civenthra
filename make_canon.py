# -*- coding: utf-8 -*-
"""make_canon.py · 把 canon.json 渲染成人读的文明穹顶书目.md（保持单一真相源）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CANON = json.loads((ROOT / "canon" / "canon.json").read_text(encoding="utf-8"))


def on_disk(no: int) -> bool:
    sys.path.insert(0, str(ROOT))
    from civenthra import config as C
    for pat in ("%03d_*" % no, "%d_*" % no):
        if list(C.BOOKS_LIBRARY.glob(pat)):
            return True
    return False


def main():
    L = []
    A = L.append
    A("# 🔥 文明穹顶书目")
    A("")
    A("> %s" % CANON["subtitle"])
    A("")
    A(CANON["note"])
    A("")
    A("---")
    A("")

    pillars = {p["id"]: p for p in CANON["pillars"]}
    for pid, p in pillars.items():
        books = [b for b in CANON["books"] if b["pillar"] == pid]
        A("## %s" % p["name"])
        A("")
        A("*%s*" % p["desc"])
        A("")
        A("| # | 书名 | 作者 | 为什么带上它 | 底本 |")
        A("|---|---|---|---|---|")
        for i, b in enumerate(books, 1):
            mark = "✅ 已入库" if on_disk(b["no"]) else "⬜ 待补"
            A("| %d | **%s** | %s | %s | %s |"
              % (i, b["title"], b["author"], b["role"], mark))
        A("")

    A("---")
    A("")
    A("## 待补（书目里想要，库里还没有）")
    A("")
    missing = CANON.get("wanted_on_disk_missing", [])
    if not missing:
        A("**无。** 全部 %d 本都有底本。" % len(CANON["books"]))
    else:
        A("| 书名 | 作者 | 作用 | 说明 |")
        A("|---|---|---|---|")
        for b in missing:
            A("| **%s** | %s | %s | %s |" % (b["title"], b["author"], b["role"], b["why"]))
    A("")

    resolved = CANON.get("resolved", [])
    if resolved:
        A("### 补书留痕（原「待补」已补齐）")
        A("")
        A("| 书名 | 书号 | 怎么补的 | 日期 |")
        A("|---|---|---|---|")
        for b in resolved:
            A("| **%s** | #%d | %s | %s |"
              % (b["title"], b["no"], b["how"], b.get("at", "")))
        A("")
    A("---")
    A("")
    A("## 选书判据")
    A("")
    A("只有一条：")
    A("")
    A("> 如果明天所有的搜索引擎、维基百科和云端模型都消失，")
    A("> 一个普通人靠这本书能不能**把某件事重新做出来**，")
    A("> 或者**把某个概念重新想明白**。")
    A("")
    A("「读过会变聪明」不是理由。「很重要」不是理由。**能照做**才是理由。")
    A("")
    A("## 如果是你，你会带哪 %d 本？" % len(CANON["books"]))
    A("")
    A("这份书单是有争议的——它必须是。欢迎用 `canon/canon.json` 的格式提交你自己的版本。")
    A("")

    out = ROOT / "canon" / "文明穹顶书目.md"
    out.write_text("\n".join(L), encoding="utf-8")

    n = sum(1 for b in CANON["books"] if on_disk(b["no"]))
    print("已写出 %s" % out)
    print("  %d 本，其中已入库 %d 本，待补 %d 本"
          % (len(CANON["books"]), n, len(CANON["books"]) - n))


if __name__ == "__main__":
    main()
