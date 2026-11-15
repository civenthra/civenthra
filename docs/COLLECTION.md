# Public collection / 公开采集

This document describes the networked collection process. Collection is a
separate, explicit operation; the offline retrieval and answer services never
start it implicitly.

本文说明独立联网采集流程。采集必须由用户显式启动；离线检索和问答服务不会
暗中联网，也不会自动调用采集器。

## Boundary / 边界

The public collector supports official catalogs, public-domain works, open
licenses, and explicit permission. It does not use accounts, cookies, browser
profiles, paywalls, DRM circumvention, CAPTCHAs, restricted lending, dark
items, or hidden endpoints.

公开采集器只处理官方目录、公共领域作品、开放许可作品或明确授权作品。它不
使用账号、Cookie、浏览器配置，不绕过付费墙、DRM、验证码、借阅限制或隐藏接口。

The built-in registry currently documents:

| Source | Discovery method | Content rule |
| --- | --- | --- |
| Project Gutenberg | Official weekly CSV catalog | Use official bulk/item files only after territory and item review. |
| DOAB | OAI-PMH | Metadata points to books; the license in each record controls reuse. |
| OpenEdition Books | OAI-PMH | Metadata first; verify the publication's access and license separately. |
| NCBI Bookshelf | OAI-PMH | Full text exists only for a subset; review each rights notice. |
| Internet Archive | Advanced Search metadata | Reject dark, restricted, lending-only, print-disabled, encrypted, LCP, and DRM files. |
| English/Chinese Wikisource | Official Wikimedia latest MD5 file manifests | Discover dump filenames; review work status and contribution licensing; preserve attribution. |

`civenthra/public_sources.json` records the official homepage, catalog endpoint,
rights policy, automation policy, allowed hosts, and rate limit for each source.
A registry entry is not a blanket copyright decision.

The historical project used additional channels and a STEM selection profile.
See [the historical inventory](HISTORICAL_SOURCES.md) for their methods and actual
integration status. Historical documentation does not imply an executable adapter.

原工程使用过的其他渠道及 STEM 专项方法见[历史来源清单](HISTORICAL_SOURCES.md)。
清单逐项区分实际使用证据、发现方法和当前公共版适配状态。

## Live verification / 真实联网验收

Run `python scripts/live_collection_smoke.py` explicitly to check every registered
metadata source. This downloads bounded metadata, validates its format and SHA-256
receipts, and saves evidence to a unique system temporary directory. It exits with
a failure status if any source fails. Do not run live checks implicitly in CI.
See [the dated verification record](COLLECTION_LIVE_CHECK.md) for results and scope.

运行 `python scripts/live_collection_smoke.py` 可逐个验收全部已注册入口，检查真实返回
内容、格式和哈希回执；证据保存至独立临时目录。任一入口失败即返回失败状态。
此测试验证元数据入口；正文下载、转储解析和翻译另行验收。

## Workflow / 工作流

```text
official catalog
      |
      v
metadata staging -> item/territory rights review -> reviewed manifest
                                                   |
                                                   v
                                          quarantine download
                                                   |
                                                   v
                                  hash + receipt + identity/format QA
                                                   |
                                                   v
                                    human import approval -> books_library
```

1. Synchronize metadata, not book files.
2. Identify the exact edition and file.
3. Record the applicable territory, rights basis, evidence URL, reviewer, date,
   and access flags.
4. Download only URLs in an approved source host allowlist.
5. Keep the file in quarantine with a SHA-256 receipt.
6. Check identity, completeness, format, OCR quality, and malware risk.
7. Import it into the private local library only after human approval.

Interrupted item batches are restartable: if both the file and its receipt
already exist and the SHA-256, source, item, and URL still match, the collector
skips that item. A missing, mismatched, or modified receipt fails closed.

先同步元数据，再逐本确认版本、地域和许可。下载文件只进入隔离目录，并生成
SHA-256及来源回执；书名、作者、完整性、格式、OCR质量和恶意文件风险通过人工
复核后，才能进入私人本地图书库。

## Commands / 命令

```powershell
# Show the reviewed source registry.
python -m civenthra.collect list

# Download Project Gutenberg's official metadata snapshot.
python -m civenthra.collect sync-catalog project-gutenberg `
  --output E:/civenthra-staging/catalogs/pg_catalog.csv.gz `
  --max-bytes 33554432

# Harvest the first 500 DOAB OAI-PMH metadata records.
python -m civenthra.collect sync-catalog doab `
  --output E:/civenthra-staging/catalogs/doab.jsonl `
  --limit 500 --max-pages 10

# Download items that have a completed local review manifest.
python -m civenthra.collect fetch E:/private/reviewed-items.json `
  --staging-dir E:/civenthra-staging/quarantine
```

Use `--limit 0` only after estimating storage, page count, time, and source
policy. Catalog receipts explicitly say `metadata_only_not_content_clearance`.

## Reviewed manifest / 逐项复核清单

The manifest is private operational evidence and should not be committed. Every
access flag must be explicitly false; availability alone never proves rights.

```json
{
  "schema": 1,
  "territory": "US",
  "items": [
    {
      "source_id": "project-gutenberg",
      "item_id": "reviewed-item-id",
      "title": "Reviewed title",
      "download_url": "https://www.gutenberg.org/cache/epub/11/pg11.txt",
      "filename": "reviewed-item.txt",
      "access": {
        "restricted": false,
        "dark": false,
        "borrow_only": false,
        "drm": false
      },
      "rights": {
        "basis": "public-domain",
        "territories": ["US"],
        "evidence_url": "https://www.gutenberg.org/policy/license",
        "reviewed_by": "local-operator",
        "reviewed_on": "2026-11-15"
      }
    }
  ]
}
```

The accepted rights bases are `public-domain`, `open-license`, and
`explicit-permission`. A URL outside the source allowlist, future review date,
territory mismatch, executable filename, encrypted/LCP/DRM hint, or any true
restricted-access flag fails closed.

## Scaling toward 100,000 books / 十万本扩展

Do not send 100,000 files directly into the current single in-memory BM25
builder. Scale in three independent stages:

1. catalog and rights ledger;
2. content acquisition, hash deduplication, OCR, and quality quarantine;
3. incremental, subject/language-sharded indexing and removable content packs.

Recommended gates are 1,000, 10,000, then 100,000 reviewed items. Preserve raw
source files and immutable receipts, but keep them outside Git. Large bulk
downloads should use the source's documented mirror, dump, rsync, or harvest
mechanism rather than crawling human-facing pages.

当前BM25构建器会把全部分块载入内存，不应直接接收十万本。应依次完成书目与
权利台账、内容采集与去重/OCR隔离、按学科和语言增量分片索引；建议在1,000、
10,000和100,000本三个规模门槛分别验收。

## Adding a source / 新增来源

A contribution must provide an official homepage, machine-readable catalog,
rights policy, automation policy, host allowlist, conservative rate limit, and
mocked tests. A search result, mirror listing, or claim that content is “free”
is not enough. Sources requiring login, cookies, CAPTCHA bypass, borrowing,
payment, or access-control circumvention are out of scope.
