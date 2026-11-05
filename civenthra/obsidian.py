"""Export a new local Obsidian vault; never overwrite user notes or settings."""
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re

from . import config as C
from .evidence import verify


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*#^\[\]\x00-\x1f]', " ", str(value)).strip(" .")[:90] or "未命名"


def quoted(text):
    # Source content must not create spurious wiki links or execute embeds.
    text = str(text).replace("[", r"\[").replace("]", r"\]").replace("<", "&lt;").replace(">", "&gt;")
    return "\n".join("> " + line for line in text.splitlines())


def export_vault(store, destination=None, evidence_per_book=3):
    if not 1 <= evidence_per_book <= 10:
        raise ValueError("evidence_per_book must be 1..10")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = Path(destination or C.ROOT / "obsidian-vault" / ("文明穹顶52本知识库_" + stamp)).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("Destination must be new or empty; existing notes are never overwritten")
    if target == C.ROOT or target.is_relative_to(C.INDEX_DIR.resolve()):
        raise ValueError("Do not export into source code or retrieval indexes")
    with store.connection() as db:
        nodes = {r["id"]: dict(r) for r in db.execute("SELECT * FROM nodes")}
        books = [dict(r) for r in db.execute("SELECT * FROM books ORDER BY no")]
        edges = [dict(r) for r in db.execute("SELECT * FROM edges")]
        ranked = db.execute("SELECT p.id,p.book_no,COUNT(DISTINCT m.concept_id) AS score FROM passages p "
                            "LEFT JOIN mentions m ON p.id=m.passage_id "
                            "WHERE json_extract(p.locator,'$.locator_status')='located' "
                            "GROUP BY p.id ORDER BY score DESC,p.id").fetchall()
        selected, used = {}, Counter()
        for row in ranked:
            if used[row["book_no"]] < evidence_per_book:
                p = store.passage(row["id"], db)
                if verify(p):
                    selected[p["id"]] = p
                    used[p["no"]] += 1
        claims = [dict(r) for r in db.execute("SELECT * FROM claims ORDER BY id")]
        reviews = defaultdict(list)
        for row in db.execute("SELECT * FROM reviews ORDER BY at"):
            reviews[row["claim_id"]].append(dict(row))
        # Include every claim's evidence even if outside the display sample.
        for claim in claims:
            p = store.passage(claim["passage_id"], db)
            if p and verify(p):
                selected[p["id"]] = p
        mentions = [dict(r) for r in db.execute("SELECT DISTINCT passage_id,concept_id,alias FROM mentions")
                    if r["passage_id"] in selected]
    names = {}
    folders = {"book": "01 书籍", "pillar": "02 分类", "author": "03 作者", "concept": "04 概念"}
    for nid, node in nodes.items():
        kind = node["kind"]
        if kind in folders:
            suffix = nid.split(":", 1)[1]
            names[nid] = folders[kind] + "/" + safe_name(node["label"]) + " " + suffix
    for pid in selected:
        names[pid] = "05 证据/证据 " + pid.split(":", 1)[1]
    for claim in claims:
        names[claim["id"]] = "06 主张/" + claim["status"] + " " + claim["id"].split(":", 1)[1]

    def link(nid, label=None):
        if nid not in names:
            return "未导出"
        return "[[" + names[nid] + "|" + safe_name(label or nodes.get(nid, {}).get("label", nid)) + "]]"

    pages = {}
    def page(nid, title, kind, body, status="reference", **properties):
        values = dict(id=nid, type=kind, status=status, tags=["civenthra", "civenthra/" + kind], **properties)
        front = "---\n" + "\n".join(k + ": " + json.dumps(v, ensure_ascii=False) for k, v in values.items()) + "\n---\n\n"
        pages[names[nid] + ".md"] = front + "# " + safe_name(title) + "\n\n" + body + "\n"

    per_book, per_concept = defaultdict(set), defaultdict(set)
    for mention in mentions:
        per_book[selected[mention["passage_id"]]["no"]].add(mention["concept_id"])
        per_concept[mention["concept_id"]].add(mention["passage_id"])
    for book in books:
        bid = "book:%s" % book["no"]
        authors = [e["source"] for e in edges if e["target"] == bid and e["predicate"] == "catalog_author"]
        evidence = [pid for pid, p in selected.items() if p["no"] == book["no"]]
        body = "分类：%s\n\n作者：%s\n\n源工程阶段：`%s`。此阶段不证明译文准确，也不等于终版。\n\n" % (
            link("pillar:" + book["pillar"]), "、".join(link(a) for a in authors), book["stage"])
        body += "## 证据中出现的概念\n\n" + ("、".join(link(c) for c in sorted(per_book[book["no"]])) or "当前展示样本没有概念标注")
        body += "\n\n## 可核验的片段\n\n" + "\n".join("- " + link(pid, "证据片段") for pid in evidence)
        page(bid, book["title"], "book", body, "draft" if book["stage"] != "done" else "final",
             book_no=book["no"], pilot=bool(book["pilot"]), source_stage=book["stage"])
    for nid, node in nodes.items():
        if node["kind"] == "pillar":
            related = ["book:%s" % b["no"] for b in books if "pillar:" + b["pillar"] == nid]
            page(nid, node["label"], "pillar", "这是书目导航分类，不表示因果或思想影响。\n\n" +
                 "\n".join("- " + link(b) for b in related))
        elif node["kind"] == "author":
            related = [e["target"] for e in edges if e["source"] == nid and e["predicate"] == "catalog_author"]
            page(nid, node["label"], "author", "书目作者署名来自 canon.json，不自动解析复合署名。\n\n" +
                 "\n".join("- " + link(b) for b in related))
        elif node["kind"] == "concept":
            props = json.loads(node["properties"])
            body = props.get("sense", node["label"]) + "。这是编辑限定的检索词义，不是模型确认的科学结论。\n\n"
            body += "检索别名：" + "、".join(props["aliases"]) + "\n\n## 原文出现证据\n\n"
            body += "\n".join("- " + link(pid, selected[pid]["title"] + "中的片段") for pid in sorted(per_concept[nid])) or "当前样本未覆盖此概念。"
            page(nid, node["label"], "concept", body)
    for pid, p in selected.items():
        concepts = {m["concept_id"] for m in mentions if m["passage_id"] == pid}
        body = "书籍：" + link("book:%s" % p["no"]) + "\n\n"
        body += "仅确认文本出现：" + ("、".join(link(c) for c in sorted(concepts)) or "未标注") + "\n\n"
        body += "原文件：[打开本机文本](<%s>)\n\n" % Path(p["source_path"]).as_uri()
        body += "原文件 SHA256：`%s`\n\n" % p["source_sha256"]
        body += "UTF-8 解码后的字符区间：`[%s, %s)`；不是 PDF 页码。导出时校验通过，修改源文件后需重导出。\n\n" % (p["raw_start"], p["raw_end"])
        body += "## 索引中的清洗文本\n\n" + quoted(p["text"])
        body += "\n\n## 原文件对应片段\n\n" + quoted(p["raw_excerpt"])
        page(pid, "文献证据", "evidence", body, p["content_status"], book_no=p["no"], verified_at_export=True)
    for claim in claims:
        body = "状态：`%s`。候选不代表事实；所有状态都不会自动进入排序。\n\n" % claim["status"]
        body += "主题：%s\n\n谓词：`%s`\n\n候选解释：%s\n\n" % (
            link(claim["subject"]), claim["predicate"], quoted(claim["object"]))
        body += "## 引文与出处\n\n" + quoted(claim["quote"]) + "\n\n" + link(claim["passage_id"], "查看对应原文")
        if reviews[claim["id"]]:
            body += "\n\n## 校核记录\n\n" + "\n\n".join(
                "`%s` — %s — %s\n\n%s" % (r["decision"], safe_name(r["reviewer"]), r["at"], quoted(r["note"]))
                for r in reviews[claim["id"]])
        if claim["passage_id"] not in selected:
            body += "\n\n警告：当前原文件证据未通过核验，历史审核状态不代表当前有效。"
        body += "\n\n人工审核须用 graph review 命令写入 SQLite；改这份导出笔记不会自动改变主张状态。"
        page(claim["id"], "文献主张 " + claim["status"], "claim", body, claim["status"],
             evidence_verified_at_export=claim["passage_id"] in selected)
    status = store.status()
    home = ("# 文明穹顶52本知识库\n\n"
            "这是本机文献图谱的浏览快照。先从分类或书籍阅读，再沿概念链接查证原文。"
            "图谱连线表示导航、文本出现或待审核主张，不自动证明观点一致、因果关系或现代有效性。\n\n"
            "## 图谱入口\n\n" + "\n".join("- " + link(n) for n in names if n.startswith("pillar:")) +
            "\n\n## 覆盖与边界\n\n"
            "收录 %s 本书；底层索引 %s 个片段，%s 个可定位。本库每本展示最多 %s 个证据样本，"
            "另保留主张对应证据，不是全书或全量片段导出。\n\n" %
            (status["books"], status["passages"], status.get("located", 0), evidence_per_book) +
            "当前仅八本进行概念试点，其他书有书目和证据样本。源译文仍可能存在质量问题；"
            "历史医学、食品加工和工程内容不能直接作为现代操作建议。\n\n"
            "## 使用方法\n\n"
            "在 Obsidian 中选择打开本文件夹为仓库。打开左侧关系图谱或书籍的局部图谱。"
            "全局图谱可用 `-path:\"05 证据\" -path:\"06 主张\"` 查看清爽的书目概念图；"
            "校核时用局部图谱展开证据。原生图谱本身不显示关系谓词，应打开主张笔记查看。\n\n"
            "这是新生成的独立库，不改动已有笔记，不安装插件，不主动启用 Sync 或 Publish。"
            "若你手动开启云同步，原文摘录可能被上传。重新导出会生成新目录，保留此处的人工笔记。\n\n"
            "[Obsidian 内部链接说明](https://help.obsidian.md/links)\n")
    pages["00 开始阅读.md"] = home
    note_targets = {name[:-3] for name in pages}
    for content in pages.values():
        for reference in re.findall(r"\[\[([^|\]]+)\|[^\]]*\]\]", content):
            if reference not in note_targets:
                raise ValueError("Dangling generated wiki link: " + reference)
    # Build all content in memory first, then write only into this new folder.
    target.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
    report = {"vault": str(target), "home": str(target / "00 开始阅读.md"),
              "notes": len(pages), "books": len(books), "evidence_samples": len(selected),
              "claims": len(claims), "snapshot": str(store.path), "links_validated": True,
              "exported_at": datetime.now().isoformat()}
    (target / ".civenthra-export.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
