# Historical sources and methods / 历史采集源与方法

Reviewed / 核查日期：2026-11-15。

This inventory preserves the historical source landscape, including attempted,
disabled, manual and metadata-only routes. A source entry is not a claim that
the public collector can acquire complete books from it today. The machine-readable
inventory is [historical_sources.json](historical_sources.json); current runnable
adapters remain in [public_sources.json](../civenthra/public_sources.json).

The inventory contains **68 source/service records**, with language variants and
collection profiles nested within records. It deliberately includes attempted
and reference-only routes rather than silently dropping them.

本清单包含 **68 条来源服务记录**，语种、馆藏变体另列于记录内。
本清单公开原工程使用、配置或探测过的来源及其方法，包括失效通道、人工入口和
仅提供元数据的目录。登记、连通和完整著作采集是不同证据，不能相互替代。
历史程序中硬编码的工作目录、账号、Cookie、密钥、原始书籍及私人台账均不随清单发布。

## Counting and evidence / 数量与证据口径

The inspected historical `channels.py` registers **33 channel keys**. These are
not 33 independent websites: Wikisource occupies 14 language keys, Open Library
uses both D5 and D5x, and B9 includes more than one site. STEM is a subject-based
workflow with **two routes: Internet Archive and OpenAlex**, not a website named
“STEM”. Additional helpers and exploratory probes sit outside that registry.

历史通道表有 **33 个操作键**：维基文库 14 个语种分别占键；Open Library 的
D5/D5x 是同源不同语种质量策略；B9 包含多个网站。因此不能把通道键数当平台数。
STEM 是理工学科采集策略，曾走 IA 与 OpenAlex 两条路线。历史探针和单书修复工具
还有其他来源，必须一并登记。JSON 的 `sources` 以来源服务计数，`historical_keys`
保留操作键，`evidence_class` 区分注册、历史探测记录、辅助脚本和仅有探针代码。

Evidence paths use aliases `books/` and `kindling-box/` for historical projects.
They identify original code locations, not files promised in this public checkout.
Historical annotations and cached artifacts establish earlier attempts, not current
availability. HTTPS homepages identify services; some original probes used HTTP,
which must be reverified before an HTTPS-only public adapter is added.

证据路径是历史工程相对位置，不含本机绝对路径；也不表示这些私人历史脚本已经
整体复制入公开仓库。原代码中的成功注释需结合当时产物核查，不能据此宣称今天
仍可用。当前实测应另行记录来源、时间、请求规模、内容类型、完整性及失败原因。

## Registered historical channels / 历史注册通道

| Keys / 操作键 | Source / 来源 | Historical method and limit / 方法与限制 |
| --- | --- | --- |
| A-level | Gutenberg | Official catalog, exact edition matching, official TXT. / 官方目录、版本匹配和 TXT。 |
| A-level; IA-DLI | Internet Archive | Search → item metadata → public files. DLI/MBP are collections, not separate platforms. / DLI 为馆藏变体。 |
| B1 + 13 variants | Wikisource | en, de, fr, es, it, ru, la, nl, sv, pt, el, ar, fa, zh; search/parse and ordered subpages. / 14 语种，目录与子页按书序展开。 |
| B2 | ctext | Search API; historical body fallback was Chinese Wikisource. / 不能将回退成功记作 ctext 正文成功。 |
| B3 | Sacred Texts | Cached static old-site catalog and bounded traversal. / 必须剔除导航、商店和残篇。 |
| B4 | Perseus | Hopper search; disabled after upstream errors. / 历史上游错误，保留记录。 |
| B5 | GRETIL | Cached catalog and bounded same-host extraction. / HTTPS 端点另验。 |
| B6 | Gallica | SRU → ARK → pagination → per-page ALTO. / 旧 OCR、限流及逐页成本使其退出自动兜底。 |
| B7 | DTA | Metadata search → `download_txt`; Zeno only a separately probed fallback. / 避免误取查看器页。 |
| B8 | Runeberg | Catalog/author index → ordered `rel=next` traversal. / 不能只取第一页。 |
| B9 | DBNL; Liber Liber | Index/author pages and EPUB/PDF links; historical failures. Cervantes only named in notes. / 分别记录，不能混算成功。 |
| B10 | HathiTrust | Public full-view/manual authorized access; historic 403. / 不自动绕过访问限制。 |
| C1 | OAPEN | OAI metadata, REST and offered bitstreams. / 历史 REST 地址须复核。 |
| C2 | arXiv + ar5iv helper | Atom search, HTML/PDF papers. / 论文不等于整书。 |
| C3 | NASA NTRS | Citations/downloads API → technical report TXT/PDF. / 技术报告。 |
| C4 | RFC Editor | Exact RFC index/version → TXT. / 核对协议版本，不能拿文档替代同名书。 |
| D1 | OpenAlex | OA book metadata, cursors, DOI/edition dedup and publisher locations. / 独立凭证、预算及出版社访问复核。 |
| D2 | CORE | Works API → download URL. / 现行认证与配额须核。 |
| D3 | OpenAIRE | Metadata → webresources; removed from automatic sequence after empty landing-page yields. / 保留人工定位方法。 |
| D4 | Zenodo | Records API → public files. / 逐项核对版本和许可。 |
| D5; D5x | Open Library | Public fulltext catalog → IA metadata/files; non-English variant shares source. / 借阅项不冒充公开文件。 |

## STEM workflow / 理工科专项

The inspected seed file contains **803 subject tuples** across the original,
appended and third-batch sets. The IA route uses `iaharvest.py` with `--seeds stem`
or the third-batch seed set; the OA route uses `oabooks.py harvest --seeds stem`
and explicitly combines both sets. Initial comments saying 221 seeds describe an
earlier version, not the entire inspected file.

当前检查到的种子文件包含 **803 个主题元组**，不是早期注释所写的 221 个。
原始、追加、第三批种子属于不同变量，执行时应明确选择，避免重复或漏批。
IA 路线以主题及书名检索；OA 路线检索 OpenAlex 的 OA 专著并解析外部正文位置。

The complete public topic list is [stem_seeds.json](stem_seeds.json), extracted
with AST literal evaluation without importing the historical script: 560 tuples
in the extended `SEEDS_STEM`, 243 in `SEEDS_STEM_3`, **803 exact unique pairs**.
Only topic labels and English query phrases are included.

803 条种子已全部导出为公开 JSON：扩展后的主集合 560 条，第三批 243 条，
按“标签、检索词”精确去重后仍为 803 条。导出没有执行原脚本，也没有包含私人书单。

This standalone example needs only Python's standard library. Run it from the
public repository root; it builds a single three-record IA metadata request from
the published topic list, without unavailable private scripts:

以下独立示例只用 Python 标准库，从公开仓库根目录运行；默认打印请求地址。
需要联网实测时将 `RUN_REQUEST` 设为 `True`，一次只取三条元数据：

```python
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

RUN_REQUEST = False
seed_file = json.loads(Path("docs/stem_seeds.json").read_text(encoding="utf-8"))
label, phrase = seed_file["seeds"][0]
collections = ["cdl", "toronto", "universallibrary", "wellcomelibrary",
               "library_of_congress", "harvard", "cornell", "princeton",
               "smithsonian", "getty"]
collection_query = "(" + " OR ".join("collection:" + c for c in collections) + ")"
query = ("mediatype:texts AND " + collection_query
         + ' AND (subject:"' + phrase + '" OR title:("' + phrase + '"))'
         + ' AND format:"DjVuTXT" AND language:eng'
         + " AND -collection:inlibrary AND -collection:printdisabled")
url = "https://archive.org/advancedsearch.php?" + urlencode({
    "q": query, "rows": 3, "page": 1, "output": "json",
    "fl[]": "identifier,title,creator,language,licenseurl,collection",
})
print(label, url)
if RUN_REQUEST:
    request = Request(url, headers={"User-Agent": "Civenthra-STEM-example/1.0"})
    with urlopen(request, timeout=30) as response:
        data = json.load(response)
    print("catalog_matches", data["response"]["numFound"])
    print("sample_records", len(data["response"]["docs"]))
```

One query is not a content download or access review. Preserve metadata, inspect
each item's official metadata and exact file, then use the reviewed manifest
workflow in [COLLECTION.md](COLLECTION.md). Do not turn this example into an
unbounded parallel loop. OpenAlex uses a different API and requires current
operator quota/authentication handling before automated STEM harvesting.

2026-11-15 supplementary live probes / 当日补充实测：IA 的 CDL 馆藏
`physics` 查询返回有效 JSON，样本 3 条、目录匹配数 241；匿名 OpenAlex
OA 专著 `physics` 请求返回 **HTTP 429**。后者不能标为已通过；应在操作者配置
合法凭证并确认可用预算、遵守服务退避后再验收。七个公开目录适配器的实测结果
另见 [COLLECTION_LIVE_CHECK.md](COLLECTION_LIVE_CHECK.md)。

Historical IA collection profile / 历史 IA 馆藏组合：

```text
cdl, toronto, universallibrary, wellcomelibrary, library_of_congress,
harvard, cornell, princeton, smithsonian, getty
```

The dedicated STEM launcher fixed that ten-collection expression before importing
the harvester. A later general IA harvester default expanded to all collections;
these are different profiles. Store the selected collection expression with each
run rather than silently substituting one for the other.

STEM 专用入口固定上述十馆组合，通用 IA 采集器后续版本默认放宽为全站。
两者不是同一采集口径；应记录实际查询、种子版本和批次标签，不能用全站数量
替代十馆召回量。历史默认参数是 `pages=3`、`per_subject=250`、`jobs=2`，
它们说明原方法，不是今天的请求额度或吞吐保证。

Reproducible sequence / 可复现步骤：

1. Save the seed set, language, collection query and a unique wave tag. / 固定种子、语言、馆藏和唯一批次标签。
2. Harvest metadata with bounded pages and current rate limits. / 小批获取目录，遵守现行限速及配额。
3. Deduplicate works, editions, DOI/IA IDs and files. / 分别去重作品、版本及文件。
4. Record exact title, author, edition, public access and per-item rights. / 逐项复核身份、访问及使用依据。
5. Fetch into quarantine with hashes and receipts. / 下载到隔离区，保留哈希和来源回执。
6. Verify text identity, front/back matter, chapter order, OCR and completeness. / 校验正文身份、前后附文、篇序、OCR 及完整性。
7. Import after human approval; index incrementally. / 人工批准后导入，增量索引。

IA historical queries favored English and downloaded-text availability, while
OpenAlex used citation sorting. These are selection biases, not independent proof
of scholarly importance. Historical STEM filtering excluded pocketbooks, numerical
tables, proceedings, catalogs and generic short titles. Adapt these choices to the
actual education objective rather than silently presenting them as a complete field.

IA 英语限制、图书馆主题标引、下载排序、OpenAlex 引用排序及排除工具书等规则
都会造成偏差。生存教育可能恰恰需要手册，不能把历史“经典专著”筛选标准
直接当作“离线生活指导”标准。大规模运行前应估计请求量，遇到 429、验证码、
认证或配额限制时记录原因、退避或交由人工授权；不自动更换身份绕过限制。

## Sources beyond the channel registry / 通道表以外的来源

`doaboooks.py` implements DOAB/OAPEN OAI-PMH metadata harvesting with
`resumptionToken`. `oabooks.py` adds Unpaywall DOI lookup, reviewed publisher
landing-page resolution and Cambridge chapter-PDF assembly. The private renewal
helper reads Penn Libraries CCE records as evidence, not as fulltext.

通道表以外还包括 DOAB/OAPEN 的 OAI 收割工具，OpenAlex OA 专著批次工具，
Unpaywall/DOI 定位、Cambridge 开放章节拼接及 Penn 更新登记证据工具。
Hesperian 工具下载官方中文分章 PDF，提取并按章拼接；这不意味着内容经医学验证。

The dated 25-platform recon record includes Internet Archive, Gutenberg, OpenAlex,
DOAB, OAPEN, HathiTrust, Google Books, arXiv, Open Textbook Library, MIT OCW,
Standard Ebooks, Europeana, SciELO Books, NCBI Bookshelf, NASA NTRS, OSTI, ERIC,
BHL, LOC, Smithsonian, Getty, Wikibooks, Open Book Publishers, Ubiquity and Punctum.
Its 2026-10-25 results describe availability probes, not 25 successful book collectors.

2026-10-25 的 25 平台巡检表只证明进行过连通或目录探测，不能宣称全部已下载
完整著作。所有平台在 JSON 保留；失效或需注册的入口写明限制，避免选择性遗漏。

Chinese classical-text helpers inspect Kanripo, Shicihui, Gushiwen, Shidianguji,
Zdic classics, Guoxuedashi, Gujizhijia, Guoxuedaohang, Guoxue, Shicimingju,
Zhonghuadiancang and Gujiguan. The Shicihui/Kanripo helper describes ordered
chapter acquisition and edition cross-checking; other probe scripts alone do not
establish complete-book success. Do not publish the privately acquired texts.

中文古籍来源中，诗词汇与 Kanripo 有专用取文及版本交叉核验工具；另十个站点
有不同程度的探针。中文本直通方法应保留原文、顺序展开子页、清除导航而保留
必要署名与许可说明，避免把中文再当外文翻译。

The historical learning project also registers NASA reference pages, EIA,
University of Calgary chemistry textbook sections, NIST handbook sections, WHO
publication overviews, OpenStax and Noba modules. They are educational-page or
module snapshots, not whole-book collectors. Their public integration status is
tracked separately from the seven runnable catalog entries.

离线教育工程另外登记 NASA、EIA、卡尔加里大学教材章节、NIST、WHO、OpenStax
和 Noba。这些是专题网页或教育模块，不能按整本书数量统计，也不能据此宣称
医疗、设备操作或火星生存指导已经验证。

## Publication and verification / 公开与验收

This release publishes the inventory and methodology for historical routes.
`documented-not-integrated` explicitly means no runnable public adapter is supplied
for that source yet. Accounts, institutions and API keys are operator configuration;
never copy private credentials or cookie-dependent collectors into a release.
Verify a small metadata sample and an appropriate reviewed content sample before
promoting each new adapter, and retain failed cases instead of hiding them.

本次登记来源与方法，未实现的公开适配器明确标为 `documented-not-integrated`。
“原工程存在代码”“目录今天可取”“能获取指定整书”“十万册可规模运行”须分别
验收。新增适配器应有小规模联网证据，正文样本应核对身份和完整性，并保留失败
原因；不得用网页返回 200 或一份空回执冒充可用。
