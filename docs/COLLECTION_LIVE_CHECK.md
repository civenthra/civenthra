# Live collection verification / 采集入口真实联网验收

Verified on 2026-11-15 from the maintainer's local environment using
`python scripts/live_collection_smoke.py`. The command runs the actual collection
CLI, validates non-empty structured output, and compares every local metadata
file with its SHA-256 receipt. All seven entries passed after the fixes below.

2026年11月15日，本地通过真实 CLI 逐项请求并验收，全部 7 个入口通过。
原始输出与回执留在 Git 之外；下表为不含私有路径的结果摘要。

| Source / 入口 | Validated output / 验收内容 | Bytes | Result |
| --- | --- | ---: | --- |
| Project Gutenberg | Gzip CSV, 79,769 catalog rows; required title/author columns | 5,602,458 | PASS |
| DOAB | 3 active OAI-PMH metadata records | 8,172 | PASS |
| OpenEdition Books | 3 active records, every header belongs to `books` set | 4,248 | PASS |
| NCBI Bookshelf | 3 active `nbk_meta` records | 5,160 | PASS |
| Internet Archive | 50 records with identifiers from first search page | 12,306 | PASS |
| English Wikisource | 85 valid checksum entries, including article dumps | 7,482 | PASS |
| Chinese Wikisource | 42 valid checksum entries, including article dumps | 3,231 | PASS |

## Corrections / 修复

- DOAB: use `/oai/request`, not the base `/oai/` path. The base returned HTTP 400.
  [Official metadata documentation](https://doabooks.org/en/librarians/doab-metadata).
- NCBI: use `nbk_meta`; `oai_dc` returned `cannotDisseminateFormat`.
  [Official Books-OAI formats](https://www.ncbi.nlm.nih.gov/books/about/oai/).
- Wikisource: `/latest/dumpstatus.json` returned HTTP 404. Use the official
  `latest/*-latest-md5sums.txt` file manifest instead. A checksum manifest lists
  dump files; it does not contain book text or certify each work's rights.
  [English listing](https://dumps.wikimedia.org/enwikisource/latest/),
  [Chinese listing](https://dumps.wikimedia.org/zhwikisource/latest/).
- OpenEdition: restrict the initial OAI request to `set=books`; the unfiltered
  endpoint also serves journals, blogs and events. Continuation requests use only
  `verb` and `resumptionToken` as required by OAI-PMH.
  [Official repository documentation](https://oai-openedition.readthedocs.io/en/latest/).
- OAI harvest receipts now include output byte count and SHA-256, matching the
  integrity evidence already produced for snapshots.

## Scope / 范围

These results establish metadata discovery and integrity for these seven entries
at the time of the test. They do not establish full-catalog paging, all historical
channels, book extraction from Wikimedia dumps, OCR, translation, or 100,000-book
throughput. The Archive query is a first-page sample, not a public-only rights
filter. Item review is still required before downloading any result.

验收结论限定于上述七个入口的元数据发现及文件完整性。Internet Archive 的搜索
结果必须逐项复核；其全部搜索命中数量不等于可下载、可复用书籍数量。
历史渠道的使用记录和公共版接入状态另见 [HISTORICAL_SOURCES.md](HISTORICAL_SOURCES.md)。

A separate Gutenberg single-item test downloaded 174,311 bytes for ebook 11 into
quarantine, matched the file SHA-256 to its receipt, and verified a repeat call
returned `already_verified` without modifying the file. Its SHA-256 was
`01b38ea4c710a84bc18d0bd41271a5a1a92b94e97b2812f4dece97d4a694725e`.
That download is private test evidence and is not distributed with the source code.

## Supplemental STEM probes / STEM 补充探测

On the same date, Internet Archive Advanced Search with
`mediatype:texts AND collection:cdl AND title:physics` returned three identifiers
for a three-record request (241 search matches at the time). This verifies the
IA STEM discovery route only; it does not clear any result for download.

An unauthenticated OpenAlex works request with `search=physics`,
`filter=type:book,is_oa:true`, and `per-page=3` returned HTTP 429. The OA STEM
route therefore remains rate-limited in this environment and is not included
in the seven passing adapter results. It was not retried aggressively.

The standalone example published in `HISTORICAL_SOURCES.md` was also executed
with its first public mathematics seed, the ten-collection profile, and the
documented text/language filters: valid JSON, 2,395 matches, and three returned
metadata records. This verifies the published example itself, not all 803 seeds.

同日 IA STEM 书目检索返回有效记录；OpenAlex OA STEM 检索返回 HTTP 429。
后者属于历史方法的当前受限状态，不记为通过，也不通过切换账号或代理规避限流。
