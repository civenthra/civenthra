# -*- coding: utf-8 -*-
"""textutil · 文本清洗、切块、分词（纯标准库）。

分词是中文检索的关键。这里不引入 jieba，用「汉字单字 + 汉字二元组」：
  "文明火种" -> 文 明 火 种 文明 明火 火种
二元组已经能抓住绝大多数中文词，单字保证召回不丢，且完全不需要词典和模型。
英文按单词切。数字整体保留。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# 中日韩统一表意文字（含扩展 A、兼容）
_RE_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
# 切分器：CJK 串 / 拉丁词 / 数字
_RE_TOKEN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
    r"|[A-Za-z][A-Za-z'\-]*"
    r"|\d+(?:[.,]\d+)*"
)

# 机译流水线留下的引擎标记，形如 [mm] / [gq2] / 【…】整行
_RE_ENGINE_TAG = re.compile(r"^\s*\[[a-z0-9_]{1,12}\]\s*.*$", re.I)
_RE_PROMPT_LEAK = re.compile(
    r"^\s*(请翻译|逐段翻译|以下是|原文[:：]|译文[:：]|Translation[:：]|原文片段|输出格式)\b.*$", re.I
)

# ── 提示词回显（机译产物的真实污染源，实测约占 4% 的块）────────────────────
# 翻译模型的「作业说明」被一并写进了译文文件，例如：
#   「…保持学术注释语气…译文如下：」/「检查要求：- 直译优先 ✓ - 不增删…」
# 这类行必须逐行剔除，否则会作为「原文」被检索出来、再被引用进答案里。
_RE_PROMPT_LINE = re.compile(
    r"^\s*[-*•·>#\s]{0,6}\s*(?:"
    r"请(?:提供|翻译|将|把|输出|直接)|"
    r"译文如下|以下(?:为|是)(?:译文)?|逐[句段篇]翻译|翻译任务|任务说明|"
    r"重新翻译|修正译|改译|上译有误|另译|重译如下|"
    r"译文(?:严重|有误|质量|不佳|不通|如下|为)[^。\n]{0,20}|"
    r"本段译文|上述译文|此译文|"
    r"检查要求|格式要求|输出要求|注意事项?[:：]|要求[:：]|只需输出|只输出|"
    r"保持.{0,14}(?:语气|格式|风格)|不增删|不改写|不概括|不解释|不加译者注|"
    r"术语一致|无夹注|直译优先|学术书面语|保留.{0,10}(?:引号|讹误)|"
    r"【无法翻译】|\[无法翻译\]|"
    r"[✓✗✔✘]"
    r")"
)
# 仍带强烈残留标记的整块直接丢弃（重叠切块保证邻近块仍能覆盖该内容）
_RE_CHUNK_BAD = re.compile(
    r"(译文如下|逐句翻译|逐段翻译|检查要求|直译优先|学术书面语|"
    r"术语一致|无夹注英文|翻译任务|"
    r"重新翻译如下|译文严重破坏|本段译文|上述译文|"
    r"请(?:提供|翻译)[^。\n]{0,24}(?:原文|内容|文本)|"
    r"【无法翻译】|\[无法翻译\])"
)

# 附属页：序跋、目录、索引、附录、凡例。它们会「什么都沾一点」，在检索里天然占优，
# 但几乎从不正面回答一个具体问题。降权而非剔除——有些序言本身是有内容的。
_MATTER_HINT = re.compile(
    r"(序言|序$|译序|译者序|前言|引言|后记|跋|附录|目录|索引|凡例|编例|"
    r"出版说明|内容提要|术语表|年表|参考文献|致谢|版次|目录。页码"
    # 英文底本同样有附属页，且同样是「什么都沾一点、从不正面回答」
    r"|contents|index|preface|foreword|prologue|epilogue|appendix|addenda"
    r"|glossary|bibliography|notes|introduction|translator|acknowledg)",
    re.I)


def is_matter(title: str) -> bool:
    return bool(_MATTER_HINT.search(title or ""))


def looks_like_matter(text: str) -> bool:
    """文本形态是否真的像目录/索引（大量短行、编号、页码）。

    为什么需要这个：章标是不可信的。实测《护理笔记》的 108 个块章标全被切成
    「目录。页码」「索引」——但块里装的是**正文**（南丁格尔讲猩红热如何经
    共用卧室传播）。只看章标就降权，会把一条语义完全正确、排第一的命中砍掉。
    ⇒ 必须**章标像附属页 且 形态确实像清单**，才降权。
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return True
    if len(lines) < 5:
        return False
    short = sum(1 for l in lines if len(l) <= 24)
    numbered = sum(1 for l in lines if re.match(r"^[0-9ivxlcIVXLC]+[.、,，)）]?$", l))
    return (short / len(lines)) > 0.6 or (numbered / len(lines)) > 0.3

_RE_SPACES = re.compile(r"[ \t\u00a0]+")

# 明显不是正文的行（页眉页脚、页码、纯符号）
_RE_JUNK_LINE = re.compile(r"^[\s\-_=·—–*\d\W]{0,8}$")

# ── 字形映射缺失（扫描件 PDF 缺 ToUnicode 表）──────────────────────────────
# 症状：pdftotext 把无映射字形吐成乱码，正文里冒出「字母汤」，
# 如「找出潜дࣧЯ来加以解决」「是ٙѣˠ的健康」。实测 #9002《无医之地》336 字。
#
# ⚠️⚠️ **绝不要按码位范围清「异域文字」** —— 这是本文件里最贵的一课：
#   实测 #723《大众天文学》里，希腊字母是**天文学专名**（「白羊座α星」「天蝎座αυ星」
#   「β Arietis」共 294 个），天城文是**原文夹注**（「婆伽罗（भगोल）」共 1045 个）。
#   按 `\u0370-\u03ff` / `\u0900-\u0dff` 一刀切，会把一本天文学书的星名标记整本删光。
#
#   真乱码与真夹注的区别在**散乱度**，不在单字码位：
#     · #723 全程只用 2 种文字（希腊 + 天城文）→ 有体系的引用
#     · #9002 散在 25+ 种互不相关的文字（西里尔/阿拉伯/藏/亚美尼亚/马拉雅拉姆/
#       奥里亚/卡纳达/希伯来/古木基/泰卢固/孟加拉/泰米尔/叙利亚…）→ 字体映射崩了
#   所以：**私用区一律清**（U+E000–U+F8FF 按定义就不是任何语言的正文），
#   **异域文字只在「散乱度 ≥ FOREIGN_SCATTER_MIN」时才清**，判据由 ingest 按书算。
_PUA_BULLET = re.compile(r"[\uf020\uf076\uf07c\uf096\uf09f\uf0a1\uf0a7\uf0a8"
                         r"\uf0b0\uf0b7\uf0ba\uf0d7\uf0d8\uf0e0\uf0fc]")
_RE_PUA = re.compile(r"[\ue000-\uf8ff]")

# 除私用区外「可能是真夹注、也可能是乱码」的文字块。**只用于算「散乱度」**，
# 决定一本书要不要清 —— 所以这里刻意保守、只列已知会出现的块，不追求穷举。
FOREIGN_BLOCKS = (
    (0x0370, 0x03FF, "希腊"), (0x0400, 0x052F, "西里尔"), (0x0530, 0x058F, "亚美尼亚"),
    (0x0590, 0x05FF, "希伯来"), (0x0600, 0x06FF, "阿拉伯"), (0x0700, 0x074F, "叙利亚"),
    (0x0750, 0x077F, "阿拉伯补充"), (0x08A0, 0x08FF, "阿拉伯扩展"),
    (0x0900, 0x097F, "天城文"), (0x0980, 0x09FF, "孟加拉"), (0x0A00, 0x0A7F, "古木基"),
    (0x0A80, 0x0AFF, "古吉拉特"), (0x0B00, 0x0B7F, "奥里亚"), (0x0B80, 0x0BFF, "泰米尔"),
    (0x0C00, 0x0C7F, "泰卢固"), (0x0C80, 0x0CFF, "卡纳达"), (0x0D00, 0x0D7F, "马拉雅拉姆"),
    (0x0E00, 0x0E7F, "泰"), (0x0E80, 0x0EFF, "老挝"), (0x0F00, 0x0FFF, "藏"),
    (0x1000, 0x109F, "缅甸"), (0x1200, 0x137F, "埃塞俄比亚"), (0x1780, 0x17FF, "高棉"),
    (0x1800, 0x18AF, "蒙古"), (0x2E80, 0x2EFF, "CJK 部首"), (0xFB00, 0xFB4F, "拉丁连字"),
    (0xFB50, 0xFDFF, "阿/希表现形"), (0xFE30, 0xFE4F, "CJK 兼容形"),
)
_RE_FOREIGN = re.compile("[" + "".join(
    "\\u%04x-\\u%04x" % (lo, hi) for lo, hi, _ in FOREIGN_BLOCKS) + "]")

# 散乱度门槛：一本书里异域文字跨了这么多「互不相关的文字」⇒ 判为字体乱码
FOREIGN_SCATTER_MIN = 5

# ★ 真正动手清的时候，**不用上面那张表**（枚举永远补不完：实测残余还有 NKo、
#   乔治亚、韩文谚文、加拿大原住民音节…）。改成**白名单之外一律清**。
#   这张白名单只列「正常中英正文会出现的字符」，且**只在已被判定为乱码的书里生效** ——
#   被判定乱码的书里没有一个异域码位是真内容（#9002 是乡村保健手册，却混着 25 种文字），
#   所以这里可以放心做全清，零误伤；而没被判定乱码的书根本走不到这一步。
_RE_KEEP = re.compile(
    r"[\u0009\u000a\u000d\u0020-\u007e"                        # ASCII
    r"\u00a0-\u024f\u2c60-\u2c7f\u2d00-\u2d2f\u1e00-\u1eff"    # 拉丁（含变音 / 越南语）
    r"\u2000-\u206f\u2070-\u209f\u20a0-\u20cf"                 # 通用标点 / 上下标 / 货币
    r"\u2100-\u214f\u2150-\u218f"                              # 字母式符号 ℵ / 数字形式 Ⅳ
    r"\u2190-\u21ff\u2200-\u22ff\u2300-\u23ff"                 # 箭头 / 数学 ∈⊂∀ / 技术符号
    r"\u2460-\u24ff\u2500-\u257f\u25a0-\u25ff"                 # 带圈数字 / 制表 / 几何
    r"\u2600-\u26ff\u2700-\u27bf"                              # 杂项 ★ / 装饰 ✓
    r"\u3000-\u303f\uff00-\uffef"                              # CJK 标点 / 全角
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"                # 汉字
)
_RE_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def foreign_scatter(text: str) -> int:
    """这本书的异域文字跨了多少种互不相关的文字。≥5 基本可断定是字体映射崩了。"""
    seen = set()
    for ch in text:
        o = ord(ch)
        if 0xE000 <= o <= 0xF8FF:
            seen.add("PUA")
            continue
        for lo, hi, name in FOREIGN_BLOCKS:
            if lo <= o <= hi:
                seen.add(name)
                break
    return len(seen)


def strip_bad_glyphs(s: str, strip_foreign: bool = False) -> str:
    """私用区项目符号 → 「·」，其余私用区删除；strip_foreign 时再按白名单清残余异域码位。"""
    s = _PUA_BULLET.sub("·", s)
    s = _RE_PUA.sub("", s)
    if strip_foreign:
        s = "".join(ch for ch in s if _RE_KEEP.match(ch))
    return s


def clean_text(raw: str, strip_foreign: bool = False) -> str:
    """清掉机译产物里的标记行、格式噪声与字形乱码，保留正文。

    strip_foreign：该书是否被判为「字体映射乱码」（见 foreign_scatter()）。
    默认 False —— **宁可留一点乱码，也不能误删合法原文**。
    """
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if _RE_ENGINE_TAG.match(s) and len(s) < 120:
            continue
        if _RE_PROMPT_LEAK.match(s):
            continue
        if _RE_PROMPT_LINE.match(s):
            continue
        if _RE_JUNK_LINE.match(s):
            continue
        if strip_foreign:
            s2 = strip_bad_glyphs(s, True)
        elif _RE_PUA.search(s):
            s2 = strip_bad_glyphs(s, False)
        else:
            out.append(_RE_SPACES.sub(" ", s))
            continue
        if s2 == s:
            out.append(_RE_SPACES.sub(" ", s))
            continue
        s2 = s2.strip()
        # 清完只剩一两个汉字、原行又短 ⇒ 是图示/提示框的碎标签，没有检索价值
        if not s2 or (len(_RE_HAN.findall(s2)) < 4 and len(s) <= 14):
            continue
        out.append(_RE_SPACES.sub(" ", s2))
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_contaminated(chunk: str) -> bool:
    """整块是否仍带提示词回显残留。"""
    return bool(_RE_CHUNK_BAD.search(chunk))


def tokenize(text: str) -> List[str]:
    """中英混排分词：CJK 出单字+二元组，拉丁出小写词，数字整体。"""
    toks: List[str] = []
    for m in _RE_TOKEN.finditer(text):
        s = m.group(0)
        if _RE_CJK.match(s):
            n = len(s)
            toks.extend(s)                                  # 单字
            if n > 1:
                toks.extend(s[i:i + 2] for i in range(n - 1))  # 二元组
        else:
            toks.append(s.lower())
    return toks


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> List[str]:
    """按段落边界切块，尽量不切在半句话上；块间带 overlap 字符的上下文重叠。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return []

    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0

    for p in paras:
        # 单段就超长：硬切
        if len(p) > size * 2:
            flush()
            step = max(1, size - overlap)
            for i in range(0, len(p), step):
                piece = p[i:i + size]
                if len(piece.strip()) >= size // 4:
                    chunks.append(piece)
            continue
        if cur_len + len(p) > size and cur:
            flush()
            # 重叠：把上一块的尾巴接回来
            if overlap > 0 and chunks:
                tail = chunks[-1][-overlap:]
                cur = [tail]
                cur_len = len(tail)
        cur.append(p)
        cur_len += len(p) + 2
    flush()
    return [c for c in chunks if len(c.strip()) >= 80]


def chapter_title(text: str, fallback: str = "") -> str:
    """取正文首行的 # 标题或短行标题。"""
    for line in text.splitlines()[:6]:
        s = line.strip().lstrip("#").strip()
        if s and len(s) <= 60:
            return s
    return fallback


def snippet(text: str, width: int = 120) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    return s if len(s) <= width else s[:width] + "…"


def lang_of(text: str) -> str:
    cjk = sum(1 for ch in text[:4000] if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(text[:4000]) * 0.06 else "en"
