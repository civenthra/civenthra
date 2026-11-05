# -*- coding: utf-8 -*-
"""ingest · 把文明穹顶书目变成索引。

它做四件事：
  1. 按 canon.json 的书号，在底本库里找到那本书的目录；
  2. 优先取中译文（mt/ch*.txt），没有中译就取原始底本（seg/ch*.txt），并标注语种；
  3. 清掉机译流水线留下的标记行，按段落切块；
  4. 建 BM25 倒排索引，落盘到 index/。

用法：
    python -m civenthra.ingest                 # 按 canon.json 建全量索引
    python -m civenthra.ingest --limit 8       # 只先建 8 本，快速试跑
    python -m civenthra.ingest --nos 937,456   # 指定书号
    python -m civenthra.ingest --report        # 只看会收录什么，不建索引
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as C
from .index import BM25, write_index_manifest
from .textutil import (FOREIGN_SCATTER_MIN, chapter_title, chunk_text, clean_text,
                       foreign_scatter, is_contaminated, lang_of, tokenize)

_RE_CH = re.compile(r"ch(\d+)\.txt$", re.I)


def load_canon() -> dict:
    return json.loads(C.CANON_FILE.read_text(encoding="utf-8"))


def load_safety_reviews() -> dict:
    try:
        payload = json.loads(C.SAFETY_REVIEW_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if payload.get("schema") != 1 or not isinstance(payload.get("reviews"), list):
        raise RuntimeError("安全审核台账格式无效")
    reviews = {}
    for item in payload["reviews"]:
        if not isinstance(item, dict) or not isinstance(item.get("book_no"), int):
            raise RuntimeError("安全审核台账条目无效")
        if item["book_no"] in reviews:
            raise RuntimeError("安全审核台账存在重复书号")
        reviews[item["book_no"]] = item
    return reviews


def find_book_dir(no: int) -> Optional[Path]:
    if not C.BOOKS_LIBRARY.exists():
        return None
    for pat in ("%03d_*" % int(no), "%d_*" % int(no)):
        hits = sorted(C.BOOKS_LIBRARY.glob(pat))
        if hits:
            return hits[0]
    return None


def _chapters(d: Path, sub: str) -> List[Path]:
    p = d / sub
    if not p.is_dir():
        return []
    fs = [f for f in p.glob("ch*.txt") if _RE_CH.search(f.name)]
    fs.sort(key=lambda f: int(_RE_CH.search(f.name).group(1)))
    return fs


def _lang_of(files: List[Path]) -> str:
    """按内容判语种，不按「放在哪个子目录」判。

    子目录只是流水线的约定（mt=机译稿、seg=切好的原文），但**人工译本同样走 mt/**
    （见 #9002《草根良醫》），且历史上出现过中文底本落在 seg/ 的情况。
    语种只影响报告里的「中译 / 原文(英)」标注与引用时的语气，按内容判最稳。
    """
    head = ""
    for f in files[:2]:
        try:
            head += f.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            pass
        if len(head) >= 4000:
            break
    return lang_of(head) if head else "en"


def read_book(no: int) -> Optional[Tuple[Path, str, List[Path], str]]:
    """返回 (目录, 用哪个子目录, 章节文件, 语种)。优先中译，其次原文。"""
    d = find_book_dir(no)
    if d is None:
        return None
    mt = _chapters(d, "mt")
    if mt:
        return d, "mt", mt, _lang_of(mt)
    seg = _chapters(d, "seg")
    if seg:
        return d, "seg", seg, _lang_of(seg)
    return None


def build(limit: Optional[int] = None, only_nos: Optional[List[int]] = None,
          report_only: bool = False, quiet: bool = False) -> dict:
    canon = load_canon()
    safety_reviews = load_safety_reviews()
    pillar_name = {p["id"]: p["name"] for p in canon["pillars"]}
    books = canon["books"]
    if only_nos:
        books = [b for b in books if b["no"] in only_nos]
    if limit:
        books = books[:limit]

    records: List[dict] = []
    used: List[dict] = []
    missing: List[int] = []
    dropped = 0
    t0 = time.time()

    for b in books:
        no = b["no"]
        got = read_book(no)
        if not got:
            missing.append(no)
            continue
        d, sub, files, lang = got
        n_before = len(records)
        raws = [(f, f.read_text(encoding="utf-8", errors="ignore")) for f in files]
        # 整本先判一次「是不是字体映射乱码」——判据是**散乱度**，不是单字码位。
        # 乱码的签名是跨了 5+ 种互不相关的文字（#9002 跨 25 种）；
        # 真夹注只会有 1~2 种（#723 用希腊字母写星名、天城文写梵文原词）。
        # 见 textutil.foreign_scatter() 的注释，那里记着这条判据的由来。
        strip_foreign = foreign_scatter("".join(t for _, t in raws)) >= FOREIGN_SCATTER_MIN
        for f, raw in raws:
            text = clean_text(raw, strip_foreign=strip_foreign)
            if len(text) < 200:
                continue
            ch_title = chapter_title(text, f.stem)
            body = re.sub(r"^\s*#.*$", "", text, count=1, flags=re.M)
            for piece in chunk_text(body, C.CHUNK_CHARS, C.CHUNK_OVERLAP):
                if is_contaminated(piece):
                    dropped += 1
                    continue
                records.append({
                    "i": len(records),
                    "no": no,
                    "title": b["title"],
                    "author": b.get("author", ""),
                    "pillar": b["pillar"],
                    "pillar_name": pillar_name.get(b["pillar"], ""),
                    "chapter": ch_title,
                    "lang": lang,
                    "text": piece,
                    **({"safety_review": safety_reviews[no]}
                       if no in safety_reviews else {}),
                })
        n = len(records) - n_before
        used.append({"no": no, "title": b["title"], "source": sub, "lang": lang,
                     "chunks": n, "dir": d.name, "glyph_strip": strip_foreign})

    chars = sum(len(r["text"]) for r in records)
    summary = {"books_in_canon": len(books), "books_indexed": len(used),
               "books_missing": missing, "chunks": len(records), "chars": chars,
               "dropped": dropped}

    if report_only:
        print("== 收录预览（未建索引）==")
        for u in used:
            tag = "中译" if u["lang"] == "zh" else "原文(英)"
            print("  #%-5s %-30s %-8s %4d 块  %s" % (u["no"], u["title"][:28], tag, u["chunks"], u["dir"]))
        if missing:
            print("  ⚠ 底本库中找不到：%s" % missing)
        print("\n合计 %d 本 / %d 块 / %.1f 万字符" % (len(used), len(records), chars / 10000))
        return summary

    if not records:
        raise SystemExit("没有可索引的正文。请检查 config.py 的 books_library 是否正确。")

    print("正在分词 …（%d 块）" % len(records))
    t1 = time.time()
    docs_tokens = [tokenize(r["text"]) for r in records]
    print("  分词完成 %.1fs" % (time.time() - t1))

    print("正在构建 BM25 倒排 …")
    t2 = time.time()
    bm = BM25().build(docs_tokens)
    print("  词表 %d 项，耗时 %.1fs" % (len(bm.postings), time.time() - t2))

    C.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    bm.save(C.INDEX_DIR / "bm25.json.gz")
    with open(C.INDEX_DIR / "chunks.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "canon": canon.get("name"),
        "books": used,
        "chunks": len(records),
        "chars": chars,
        "dropped_contaminated": dropped,
        "vocab": len(bm.postings),
        "chunk_chars": C.CHUNK_CHARS,
        "elapsed_s": round(time.time() - t0, 1),
    }
    (C.INDEX_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index_manifest(C.INDEX_DIR)

    print("\n索引已生成：%s" % C.INDEX_DIR)
    print("  %d 本 / %d 块 / %.1f 万字符 / 词表 %d / 用时 %.1fs"
          % (len(used), len(records), chars / 10000, len(bm.postings), time.time() - t0))
    if dropped:
        print("  已剔除 %d 块提示词回显污染" % dropped)
    if missing:
        print("  ⚠ 缺底本：%s" % missing)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="civenthra.ingest", description="建立文明穹顶索引")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 本")
    ap.add_argument("--nos", type=str, default=None, help="指定书号，逗号分隔")
    ap.add_argument("--report", action="store_true", help="只预览，不建索引")
    a = ap.parse_args(argv)
    nos = [int(x) for x in a.nos.split(",")] if a.nos else None
    build(limit=a.limit, only_nos=nos, report_only=a.report)


if __name__ == "__main__":
    main()
