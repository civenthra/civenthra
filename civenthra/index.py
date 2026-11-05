# -*- coding: utf-8 -*-
"""index · BM25 倒排索引（纯标准库，无 numpy、无 faiss、无 torch）。

为什么不用向量检索？
  1. 文明穹顶的前提是「在一台不知道还能买到什么零件的机器上跑起来」。少一个依赖
     就少一个装不上的可能。
  2. 中文的汉字二元组分词 + BM25，在「引用原文」这个任务上表现相当好——我们要的是
     找到包含答案的那一段话，而不是判断两句话语义像不像。
  3. 索引是纯文本 + 数组，可读、可审计、可手工修。这在「文明存档」语境里是优点。

倒排表用 array('i') 存 (doc_id, tf) 交替序列，50 本书量级下索引只有几十 MB。
"""
from __future__ import annotations

import array
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .textutil import is_matter, looks_like_matter, tokenize

SCHEMA = 2
INDEX_FILENAME = "bm25.json.gz"
LEGACY_UNSAFE_FILENAME = "bm25.pkl"
INDEX_MANIFEST_FILENAME = "index-manifest.json"

#: 跨语种「保底席位」的门槛：向量全库直扫名次 < 此值的段落保证进候选。
#: 取 3 是刻意从严的——全库 8500+ 块里的前三名，几乎不可能是噪声。
CROSS_KEEP_TOP = 3

def _is_han(text: str, sample: int = 400) -> bool:
    """这段文字是不是中文写成的。只用来判「查询与段落是否同一语种」。

    判据取汉字占非空白字符的比例 > 0.15 —— 中文段落里夹几个英文术语
    （「三段论 syllogism」）不该被判成外文。
    """
    s = "".join(text[:sample].split())
    if not s:
        return False
    han = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    return han / len(s) > 0.15


def _proximity(text: str, terms: List[str], max_hits: int = 40) -> float:
    """两个最稀有查询词在文中靠得多近。1 = 紧邻，0 = 找不到/相距很远。

    这是把「信息是什么」和一段泛泛而谈区分开的关键：前者会在某处紧挨着出现
    「信息」与「是什」，后者只是碰巧都出现过。

    ⚠️ 曾经的写法是硬取 `terms[:2]`，只要其中任何一个在这段文字里找不到就返回 0。
    实测下来这等于把邻近度**整个废掉**：中文二元组分词会产出「疫在」「在城」这种
    跨词边界的伪词，它们在正文里天然不存在，于是所有块的 prox 都是 0，
    精排只剩覆盖度一个信号。改为**跳过找不到的词、取实际出现的前两个**。
    """
    if len(terms) < 2:
        return 0.0
    lists = []
    for t in terms:                      # terms 已按稀有度升序：最锐利的先试
        if len(lists) >= 2:
            break
        pos, start = [], text.find(t)
        while start >= 0 and len(pos) < max_hits:
            pos.append(start)
            start = text.find(t, start + 1)
        if pos:
            lists.append(pos)
    if len(lists) < 2:
        return 0.0
    a, b = lists
    best, i, j = 10 ** 9, 0, 0
    while i < len(a) and j < len(b):
        d = abs(a[i] - b[j])
        if d < best:
            best = d
        if a[i] < b[j]:
            i += 1
        else:
            j += 1
    return 1.0 / (1.0 + best / 150.0)


class BM25:
    # 二元组最低「凝聚度」：df(ab) 低于构成它的两个单字的较小 df 的这个比例，
    # 就判定它只是「偶尔挨在一起」，不是一个词。
    COHESION = 0.25

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.N = 0
        self.avgdl = 0.0
        self.dlen = array.array("i")
        self.postings: Dict[str, array.array] = {}
        self.df: Dict[str, int] = {}

    # ── 构建 ────────────────────────────────────────────────────────────────
    def build(self, docs_tokens: Sequence[Sequence[str]]):
        self.N = len(docs_tokens)
        self.dlen = array.array("i", [len(d) for d in docs_tokens])
        self.avgdl = (sum(self.dlen) / self.N) if self.N else 0.0

        acc: Dict[str, List[int]] = {}
        for did, toks in enumerate(docs_tokens):
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                acc.setdefault(t, []).extend((did, c))

        self.postings = {t: array.array("i", v) for t, v in acc.items()}
        self.df = {t: len(v) // 2 for t, v in self.postings.items()}
        return self

    # ── 检索 ────────────────────────────────────────────────────────────────
    def search(self, query: str, k: int = 6, cap_per_book: Optional[int] = None,
               book_of: Optional[Sequence] = None) -> List[Tuple[int, float]]:
        q = tokenize(query)
        if not q or not self.N:
            return []
        seen_q = set()
        scores: Dict[int, float] = {}
        for t in q:
            if t in seen_q:
                continue
            seen_q.add(t)
            post = self.postings.get(t)
            if not post:
                continue
            n = self.df[t]
            idf = math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))
            for i in range(0, len(post), 2):
                did = post[i]
                tf = post[i + 1]
                dl = self.dlen[did] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[did] = scores.get(did, 0.0) + idf * (tf * (self.k1 + 1)) / denom

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        if cap_per_book is None or book_of is None:
            return ranked[:k]

        # 同一本书最多占 cap_per_book 个名额：保证引用来自不同来源
        out: List[Tuple[int, float]] = []
        used: Dict[object, int] = {}
        for did, sc in ranked:
            bk = book_of[did]
            if used.get(bk, 0) >= cap_per_book:
                continue
            used[bk] = used.get(bk, 0) + 1
            out.append((did, sc))
            if len(out) >= k:
                break
        return out

    def is_coincidental(self, t: str) -> bool:
        """二元组 ab 是不是「偶然共现」而非一个真词。

        中文二元组分词的最大副作用：跨词边界会切出 `疫在`（"瘟疫在"）、`在城`
        （"在城里"）这类伪词。它们 df 极低 ⇒ IDF 极高 ⇒ 在 key_terms 里排最前，
        把真正的实词挤下去，而且因为正文里根本不存在，还会把邻近度信号打成恒 0。

        判据：真词的两个字在语料里几乎只在彼此身边出现 ⇒ df(ab) 与单字 df 同量级；
        伪词只是两个常见字偶尔挨上 ⇒ df(ab) 远低于单字的较小值。
        """
        if len(t) != 2 or any(not ("\u4e00" <= c <= "\u9fff") for c in t):
            return False
        a, b = t[0], t[1]
        da, db = self.df.get(a, 0), self.df.get(b, 0)
        if not da or not db:
            return False
        return self.df.get(t, 0) < self.COHESION * min(da, db)

    def key_terms(self, query: str) -> List[str]:
        """按 IDF 挑出查询里的「有信息量」的词（取 IDF 高于中位数者），稀有的排前面。

        中文二元组分词会产生大量虚词性二元组（"物怎""么保"），它们在语料里遍地都是、
        IDF 低、只会带来噪声。这一步把它们摘掉，只留真正能指向答案的词；
        再剔除跨词边界的偶然共现（见 is_coincidental），最后按稀有度排序，
        供覆盖度/短语/邻近度重排优先使用最锐利的那个信号。
        """
        q = [t for t in dict.fromkeys(tokenize(query)) if t in self.postings]
        if not q:
            return []
        idf = {t: math.log(1.0 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
               for t in q}
        vals = sorted(idf.values())
        med = vals[len(vals) // 2]
        keys = [t for t in q if idf[t] >= med and not self.is_coincidental(t)]
        if not keys:                              # 全被判没了就退回原样，别把自己饿死
            keys = [t for t in q if idf[t] >= med]
        keys = self._dedup_keys(keys)
        keys.sort(key=lambda t: self.df[t])      # df 小 = 稀有 = 锐利
        return keys

    @staticmethod
    def _dedup_keys(keys: List[str]) -> List[str]:
        """去掉构成关系上的冗余：「瘟疫」在，就不必再单列「瘟」「疫」。

        实测问题：查询「怎么防止瘟疫在城里蔓延」的 keys 是
        `蔓延 · 瘟疫 · 瘟 · 蔓 · 疫 · 怎么`——六个词里有一半是同一个概念的不同粒度。
        于是《物性论》第六卷（卢克莱修描写雅典瘟疫）命中「瘟疫/瘟/疫/蔓延」就拿到
        0.83 的覆盖度，而每个概念其实只被覆盖了一次。覆盖度是**语义覆盖**的代理，
        不该被分词粒度灌水。
        """
        big = {t for t in keys if len(t) == 2}
        covered = set()
        for t in big:
            covered.update(t)
        return [t for t in keys if not (len(t) == 1 and t in covered)]

    # ── 存取 ────────────────────────────────────────────────────────────────
    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
            json.dump({
                "schema": SCHEMA, "k1": self.k1, "b": self.b,
                "N": self.N, "avgdl": self.avgdl,
                "dlen": list(self.dlen),
                "postings": {term: list(values) for term, values in self.postings.items()},
                "df": self.df,
            }, f, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def load(cls, path: Path) -> "BM25":
        path = Path(path)
        if path.suffix == ".pkl":
            raise RuntimeError(
                "拒绝读取旧版 pickle 索引；pickle 可执行任意代码。请重新运行 ingest，"
                "生成 bm25.json.gz。"
            )
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("BM25 索引损坏或格式无效，请重新运行 ingest") from exc
        try:
            if not isinstance(d, dict) or d.get("schema") != SCHEMA:
                raise ValueError("schema")
            n = d["N"]
            dlen = d["dlen"]
            postings = d["postings"]
            df = d["df"]
            if not isinstance(n, int) or n < 0 or not isinstance(dlen, list):
                raise ValueError("document counts")
            if len(dlen) != n or any(not isinstance(v, int) or v < 0 for v in dlen):
                raise ValueError("document lengths")
            if not isinstance(postings, dict) or not isinstance(df, dict):
                raise ValueError("postings")
            checked_postings = {}
            for term, values in postings.items():
                if not isinstance(term, str) or not isinstance(values, list) or len(values) % 2:
                    raise ValueError("posting shape")
                for i in range(0, len(values), 2):
                    did, tf = values[i], values[i + 1]
                    if (not isinstance(did, int) or not 0 <= did < n
                            or not isinstance(tf, int) or tf <= 0):
                        raise ValueError("posting values")
                if df.get(term) != len(values) // 2:
                    raise ValueError("document frequency")
                checked_postings[term] = array.array("i", values)
            if set(df) != set(postings):
                raise ValueError("term sets")
            o = cls(k1=float(d["k1"]), b=float(d["b"]))
            o.N, o.avgdl = n, float(d["avgdl"])
            o.dlen = array.array("i", dlen)
            o.postings = checked_postings
            o.df = {term: int(value) for term, value in df.items()}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("BM25 索引结构无效，请重新运行 ingest") from exc
        return o


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_index_manifest(index_dir: Path) -> None:
    index_dir = Path(index_dir)
    files = (INDEX_FILENAME, "chunks.jsonl", "meta.json")
    payload = {
        "schema": 1,
        "algorithm": "sha256",
        "files": {name: file_sha256(index_dir / name) for name in files},
    }
    (index_dir / INDEX_MANIFEST_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def verify_index_manifest(index_dir: Path) -> None:
    index_dir = Path(index_dir)
    path = index_dir / INDEX_MANIFEST_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (payload.get("schema") != 1 or payload.get("algorithm") != "sha256"
                or not isinstance(payload.get("files"), dict)):
            raise ValueError("manifest shape")
        required = {INDEX_FILENAME, "chunks.jsonl", "meta.json"}
        if set(payload["files"]) != required:
            raise ValueError("manifest file set")
        for name in required:
            expected = payload["files"][name]
            if (not isinstance(expected, str) or len(expected) != 64
                    or file_sha256(index_dir / name) != expected):
                raise ValueError("digest mismatch")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            "索引完整性校验失败；请重新运行 ingest，且不要使用来源不明的索引包"
        ) from exc


# ── 索引包（BM25 + 元数据），检索层的统一入口 ────────────────────────────────

class IndexBundle:
    def __init__(self, bm25: BM25, records: List[dict], meta: dict,
                 dense=None, embed_server=None):
        self.bm25 = bm25
        self.records = records
        self.meta = meta
        self.dense = dense
        self.embed_server = embed_server

    @classmethod
    def load(cls, index_dir: Path, dense: bool = False,
             embed_server=None) -> "IndexBundle":
        index_dir = Path(index_dir)
        safe_path = index_dir / INDEX_FILENAME
        legacy_path = index_dir / LEGACY_UNSAFE_FILENAME
        if not safe_path.exists() and legacy_path.exists():
            raise RuntimeError(
                "检测到旧版 bm25.pkl。为避免不安全反序列化，请重新运行 ingest。"
            )
        verify_index_manifest(index_dir)
        bm = BM25.load(safe_path)
        records = []
        with open(index_dir / "chunks.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        d = None
        if dense:
            from .embed import DenseIndex
            d = DenseIndex.load(index_dir)
        return cls(bm, records, meta, dense=d, embed_server=embed_server)

    def search(self, query: str, k: int = 6, cap_per_book: int = 2,
               recall: int = 300, dense: bool = None, dense_top: int = 200,
               w_bm: float = 1.0, w_vec: float = 1.0,
               _trace: list = None) -> List[dict]:
        """三段式检索：召回 → 融合 → 精排。

        1. **召回（两路并行）**
           · BM25 取前 300 块——词面命中，快、可靠；
           · 向量**整库直扫**（实测 8165×1024 只要 0.33s，比预想快一个数量级），
             取前 200 块，负责把「没有任何共同词、但意思相关」的段落捞出来。
           两路是互补的：BM25 擅长精确术语，向量擅长语义。

           ⚠️ 一开始我只让向量给 BM25 的结果重排，结果「怎么防止瘟疫在城里蔓延」
           依然召不回《护理笔记》（南丁格尔整本书讲的就是卫生防疫）——因为两段式重排
           只能重排**已经召回到的**东西。改成整库直扫后，那条题的第一名就是《护理笔记》。

        2. **融合**：RRF（倒数排名融合）。不需要两路分数可比，最省心。
           ⚠️ 但两路**不该等权**。「怎么防止瘟疫在城里蔓延」这条题上，BM25 把《物性论》
           第六卷（卢克莱修描写雅典瘟疫惨状、"疗救的努力全都徒劳无功"）顶到第一——
           只因为它主题词"瘟疫/蔓延"高频且相邻；而真正给出防疫办法的《护理笔记》
           在向量那一路排第 0，等权融合后被摊平。**文明穹顶收的是自然语言问句，不是
           关键词检索**，向量对「意图」的判断比词面对「主题」的判断更可信，故 w_vec > w_bm。

        3. **精排**：覆盖度 / 短语命中 / 词语邻近度，对序跋目录降权，再用相对门槛砍尾巴。
        """
        use_dense = self.dense is not None if dense is None else dense

        ranks: Dict[int, float] = {}
        dense_rank: Dict[int, int] = {}      # did → 向量全库直扫里的名次（保底席位用）
        bm = self.bm25.search(query, k=recall, cap_per_book=None, book_of=None)
        for i, (did, _) in enumerate(bm):
            ranks[did] = ranks.get(did, 0.0) + w_bm / (60 + i)
        if not ranks:
            return []

        dense_used = False
        if use_dense and self.dense is not None and self.embed_server is not None:
            try:
                qv = self.embed_server.embed_query(query)
                sc = self.dense.scores(qv, range(self.dense.n))
                order = sorted(range(len(sc)), key=lambda i: -sc[i])[:dense_top]
                for r, i in enumerate(order):
                    ranks[i] = ranks.get(i, 0.0) + w_vec / (60 + r)
                    dense_rank[i] = r
                dense_used = True
            except Exception:
                dense_used = False       # 向量服务不可用就静默退回纯 BM25

        # 取融合后的前 40 块做精排
        pooled = sorted(ranks.items(), key=lambda x: -x[1])[:40]

        keys = self.bm25.key_terms(query)
        nkeys = len(keys) or 1
        phrases = self._phrase_probes(query)
        nphr = len(phrases) or 1
        q_han = _is_han(query)

        # ★ 词面精排与「跨语种命中」的关系（2026-10-04 补书时踩出来的坑）
        # 中文查询 + 英文段落时，三个词面信号（覆盖度/短语/邻近度）**恒为 0**
        # —— 不是「不相关」，是「这把尺子量不了它」。若直接乘下去，等于给每一段
        # 外文原文打 0.55 折；而同一批候选里的中文段落还能白拿邻近度/短语加成，
        # 实测平均能吃到 ×1.4。结果：向量那一路明明把《几何原本》排到第 1 名
        # （中文问「三角形内角和是多少」），精排后还是被挤出前四。
        # 做法：跨语种段落不用固定 1.0，而用**同语种候选的平均因子**做中性校准
        # —— 对它的词面维度我们没有任何信息，那就别猜，给它一个中位数待遇。
        raw = []
        same_facs = []
        for did, fuse in pooled:
            rec = self.records[did]
            text = rec["text"]
            toks = set(tokenize(text))
            cov = (sum(1 for t in keys if t in toks) / nkeys) if keys else 0.0
            ph = (sum(1 for p in phrases if p in text) / nphr) if phrases else 0.0
            prox = _proximity(text, keys)
            fac = (0.55 + 0.45 * cov) * (1.0 + 0.45 * ph) * (1.0 + 0.40 * prox)
            cross = _is_han(text) != q_han
            if cross:
                fac = None                       # 待定：稍后用同语种均值补上
            else:
                same_facs.append(fac)
            raw.append((did, fuse, cov, ph, prox, cross, fac))
        neutral = (sum(same_facs) / len(same_facs)) if same_facs else 1.0

        reranked = []
        for did, fuse, cov, ph, prox, cross, fac in raw:
            text = self.records[did]["text"]
            score = fuse * 1000.0 * (neutral if cross else fac)
            if (is_matter(self.records[did].get("chapter", "")
                          or self.records[did].get("title", ""))
                    and looks_like_matter(text)):
                score *= 0.60          # 确属序跋目录这类附属页：降权，别让它当主角
            reranked.append((did, score,
                             1.0 if cross else cov, ph, prox))

        reranked.sort(key=lambda x: -x[1])
        gate = reranked[0][1] * 0.35   # 相对门槛

        if _trace is not None:         # 诊断出口：精排全景（不参与检索决策）
            for pos, (did, score, cov, ph, prox) in enumerate(reranked):
                rec = self.records[did]
                _trace.append({
                    "pos": pos, "did": did, "no": rec["no"], "title": rec["title"],
                    "chapter": rec.get("chapter", ""), "score": round(score, 3),
                    "cov": round(cov, 2), "phrase": round(ph, 2), "prox": round(prox, 2),
                    "fuse": round(ranks.get(did, 0.0), 6),
                    "in_bm25": did in {d for d, _ in bm},
                    "matter": bool(is_matter(rec.get("chapter", "") or rec.get("title", ""))
                                   and looks_like_matter(rec["text"])),
                    "text": rec["text"][:80],
                })

        out: List[dict] = []
        used: Dict[object, int] = {}
        for did, score, cov, ph, prox in reranked:
            if score < gate:
                continue
            rec = self.records[did]
            if used.get(rec["no"], 0) >= cap_per_book:
                continue
            used[rec["no"]] = used.get(rec["no"], 0) + 1
            r = dict(rec)
            r["score"] = round(float(score), 3)
            r["coverage"] = round(float(cov), 2)
            r["proximity"] = round(float(prox), 2)
            r["phrase"] = bool(ph)
            r["dense"] = dense_used
            r.pop("i", None)
            out.append(r)
            if len(out) >= k:
                break

        # ★ 外文原文的保底席位
        # BM25 是词面的：中文问题**不可能**命中一段英文原文。所以外文段落天生
        # 拿不到融合里的那一路票，证据量结构性地只有并列命中者的一半 ——
        # 实测「三角形内角和是多少」时《几何原本》的向量名次是**全库第 1**，
        # 精排后却排在第 6，四个名额一个都进不去；问「三段论是什么」时
        # 《工具论》向量第 2，同样进不去。这不是排序参数问题，是「外文书从
        # 中文入口基本不可达」。
        # 规则：向量全库直扫排进前 CROSS_KEEP_TOP 的跨语种段落，保证进候选。
        # 门槛取得极严（全库 8546 块里的前 3 名），不会挤掉真正的好答案。
        if dense_used and q_han and out and not any(not _is_han(h["text"]) for h in out):
            best = next((t for t in reranked
                         if _is_han(self.records[t[0]]["text"]) != q_han
                         and dense_rank.get(t[0], 1 << 30) < CROSS_KEEP_TOP), None)
            if best is not None:
                did, score, cov, ph, prox = best
                rec = self.records[did]
                r = dict(rec)
                r["score"] = round(float(score), 3)
                r["coverage"] = round(float(cov), 2)
                r["proximity"] = round(float(prox), 2)
                r["phrase"] = bool(ph)
                r["dense"] = True
                r["promoted"] = "cross_lingual"   # 供诊断与 UI 标注
                r.pop("i", None)
                if len(out) >= k:
                    out[-1] = r
                else:
                    out.append(r)
        return out

    def _phrase_probes(self, query: str) -> List[str]:
        """词级短语探针：查询里「确实是词」的二元组。

        原实现是「连续汉字的 4 字滑窗」，对「怎么防止瘟疫在城里蔓延」产出
        `怎么防止 / 么防止瘟 / 防止瘟疫 / 防瘟疫在 …`——全是跨词伪词，正文里一个都
        不会出现，于是短语信号恒为假，白占一个乘法因子。

        改用分词后剔除偶然共现，得到真正的词：`怎么 / 防止 / 瘟疫 / 城里 / 蔓延`。
        价值在于**「防止」这类意图词由此第一次参与精排**：它 IDF 低、进不了 key_terms，
        但正是它区分了「描写瘟疫」（《物性论》第六卷）和「防治瘟疫」（《护理笔记》）。
        命中比例而非布尔值——命中 3 个短语的段落比命中 1 个的更像个答案。
        """
        out: List[str] = []
        for t in dict.fromkeys(tokenize(query)):
            if (len(t) == 2 and "\u4e00" <= t[0] <= "\u9fff"
                    and not self.bm25.is_coincidental(t)):
                out.append(t)
        return out

    @property
    def n_chunks(self) -> int:
        return len(self.records)

    def books(self) -> List[dict]:
        seen, out = set(), []
        for r in self.records:
            if r["no"] in seen:
                continue
            seen.add(r["no"])
            out.append({"no": r["no"], "title": r["title"], "author": r.get("author", ""),
                        "pillar": r.get("pillar", ""), "lang": r.get("lang", "")})
        return out
