# 文明穹顶（Civenthra）

简体中文 | [English](README.md)

[![开源审计](https://github.com/civenthra/civenthra/actions/workflows/open-source-audit.yml/badge.svg)](https://github.com/civenthra/civenthra/actions/workflows/open-source-audit.yml)
[![CodeQL](https://github.com/civenthra/civenthra/actions/workflows/codeql.yml/badge.svg)](https://github.com/civenthra/civenthra/actions/workflows/codeql.yml)
[![许可证](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

文明穹顶是一个本地优先、可追溯引用的离线知识原型。它把用户依法持有或
获准使用的资料留在本机，通过检索增强生成回答问题，并显示支撑回答的
来源片段。

本仓库公开程序、测试、合成示例和安全边界，不包含书籍正文、译文、模型
权重、索引、声音、肖像、账号数据或生成媒体。

## 开始采集书目与图书

从本页即可开始采集。需要 Python 3.11 或更新版本；只采集目录时，无需配置
模型、llama.cpp 或离线问答服务。当前通过命令行操作。

### 1. 下载并安装

Windows 打开 PowerShell，依次运行：

```powershell
git clone https://github.com/civenthra/civenthra.git
cd civenthra
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps .
```

如果没有 Git，点击本页 **Code → Download ZIP**，解压后在包含 `pyproject.toml`
的文件夹打开 PowerShell，再运行上面最后两条命令。直接调用虚拟环境中的 Python，
无需执行环境激活脚本。macOS/Linux 使用 `python3 -m venv .venv` 创建环境，
并将后续命令中的 `.\.venv\Scripts\python.exe` 替换为 `.venv/bin/python`。

### 2. 查看来源并采集一小批书目

在项目文件夹内运行：

```powershell
# 列出当前可执行的 7 个元数据入口。
.\.venv\Scripts\python.exe -m civenthra.collect list

# 获取 3 条 DOAB 元数据，并保存 SHA-256 回执。
.\.venv\Scripts\python.exe -m civenthra.collect sync-catalog doab --output ./staging/catalogs/doab-sample.jsonl --limit 3 --max-pages 1

# 下载 Gutenberg 的完整官方压缩目录。
.\.venv\Scripts\python.exe -m civenthra.collect sync-catalog project-gutenberg --output ./staging/catalogs/pg_catalog.csv.gz --max-bytes 33554432
```

这些输出是目录或元数据，尚不是书籍正文；每份输出附有 `.receipt.json` 回执。
如果目标文件已存在，请换用新文件名，目录同步不会覆盖历史输出。

| 采集入口 | 当前可自动获取的内容 |
| --- | --- |
| Project Gutenberg | 完整官方目录快照 |
| DOAB | 图书／章节元数据及链接 |
| OpenEdition Books | `books` 集合元数据，含章节 |
| NCBI Bookshelf | `nbk_meta` 元数据；只有部分条目提供全文 |
| Internet Archive | 搜索第一页元数据快照；每条仍需复核访问条件 |
| 英文 Wikisource | 官方转储文件的校验和清单 |
| 中文 Wikisource | 官方转储文件的校验和清单 |

### 3. 下载已复核的图书文件

先确认具体版本及可公开访问的文件，按照[完整采集指南](docs/COLLECTION.md)中
“逐项复核清单”的示例，在本地创建 `reviewed-items.local.json`。填写来源及条目
标识、下载地址、文件名、访问标志、适用地域和权利依据；下载主机必须在对应来源
白名单内。完成后运行：

```powershell
.\.venv\Scripts\python.exe -m civenthra.collect fetch ./reviewed-items.local.json --staging-dir ./quarantine
```

下载成功后，文件进入 `quarantine/<source_id>/<item_id>/`，并附 SHA-256 回执。
核对书名、作者、版本、完整性及文件质量后，再导入自己的书库。目录入口目前不会
自动解析全部出版社正文链接、将维基转储拆成逐本图书，或一键下载整个书库。

### 来源范围、STEM 与容量

- [完整操作指南及复核清单示例](docs/COLLECTION.md)
- [7 个入口的真实联网验收记录](docs/COLLECTION_LIVE_CHECK.md)
- [68 条历史来源服务记录与方法](docs/HISTORICAL_SOURCES.md)：
  逐项区分历史注册、探测和当前公共版可执行支持。
- [全部 803 条 STEM 种子](docs/stem_seeds.json)：这是检索主题，不是 803 本书。
- [来源容量与学科分布附件](https://github.com/civenthra/civenthra/releases/tag/v0.2.0)：
  提供注明日期的 Word、PDF、Markdown 和 JSON；不同平台统计口径不同且存在重叠，
  尚未建立跨源去重图书总量。

## 当前边界

- 这是研究和教育原型，不是航天、医疗、心理、救援或设备操作认证系统。
- 引用可追溯不等于答案正确；重要结论仍需人工核验原文和适用条件。
- `civenthra.airgap` 是进程内出站连接限制与自检，不是操作系统防火墙、
  隔离网络或物理断网。
- 使用者应自行确认书库和模型的来源、许可、适用地域与用途。
- 医疗、食品、饮水、电气、结构和生存操作问题默认拒答；只有检索片段带有
  仍在有效期内的专业审核记录时才进入模型。历史资料不会被冒充为现代指令。

## 架构

```text
ingest -> index -> retrieve -> local model -> cited answer
```

- `civenthra/ingest.py`：清洗、切块并建立本地索引。
- `civenthra/index.py`：BM25 检索及可选的本地向量重排。
- `civenthra/rag.py`：让本地模型基于检索证据回答。
- `civenthra/cli.py`：命令行界面。
- `civenthra/serve.py`：仅绑定回环地址的本地网页服务。
- `civenthra/airgap.py`：进程内网络限制与验证。
- `civenthra/scene_backend.py`：可选本地场景生成适配器。

## 语言支持

回答提示现支持英文和简体中文。`auto` 根据问题自动选择语言，也可以显式
指定 `en` 或 `zh-CN`。来源片段保留原文，译文必须标明，不能静默替换原文。

```powershell
python -m civenthra.cli --lang en -q "What does the local evidence say?"
python -m civenthra.cli --lang zh-CN -q "本地资料有什么依据？"
```

网页 API 同样接受 `"language": "auto"`、`"en"` 或 `"zh-CN"`。现有浏览器
界面文字仍以中文为主；界面翻译与已经完成的双语回答链路分开验收。

## 本地配置

```powershell
Copy-Item config.example.json config.local.json
Copy-Item authorizations.example.json authorizations.local.json
python -m pip install --no-deps .
python -m civenthra.ingest
python -m civenthra.cli --lang auto
```

语音是与核心包隔离的可选能力。安装 `.[voice]` 或 `.[voice-edge]` 前应单独
审查依赖和联网影响。

本地配置、语料、索引、模型和输出均不进入 Git。不得提交密钥、Cookie、
平台账号标识、参考声音或肖像。

## H3、语音与数字人边界

MiniMax H3 和 WanGP 是另行安装、受独立许可约束的可选场景生成后端，
不是本项目的开源组成部分。本仓库不分发其代码、权重或安装器；H3 默认
关闭，只有本地授权记录同时包含适用地域、用途范围、许可证摘要、证据索引、
复核人和有效期，且状态为已批准时才运行。示例文件默认为待审批，不能直接启用。

声音克隆默认拒绝运行。持有参考音频不等于获得授权，必须另行记录声音
主体的明确许可，并满足上游模型条款。本地授权会绑定参考音频的 SHA-256，
记录到期日，并强制 XTTS 的非商业约束；更换音频后旧授权立即失效。公开仓库
不包含参考音频、肖像、数字人模型或平台凭据。

`canon/rights-manifest.json` 对每本目录书、译文、音频、视频、音色和肖像采用
默认不公开策略；只有补齐授权证据才可加入公开资产。该边界由
`scripts/validate_rights.py` 强制检查。

## 无版权合成演示

可复现黄金路径只使用本项目原创的虚构文本：

```powershell
python examples/synthetic_demo.py --lang en
python examples/synthetic_demo.py --lang zh-CN
```

## 发布验证

```powershell
python scripts/open_source_audit.py
python scripts/validate_rights.py
python scripts/git_history_audit.py
python -m pytest -q
python scripts/build_sbom.py --output out/civenthra.spdx.json
```

`release-manifest.txt` 是公开文件精确白名单。新增跟踪文件或已从当前树删除但
仍留在历史中的文件都会触发发布门禁。零字节或损坏媒体不能作为演示证据。完整说明见
`docs/OPEN_SOURCE_READINESS.md`。

## 社区与方向

- 架构：`docs/ARCHITECTURE.md`
- 公开采集：[首页快速上手](#开始采集书目与图书)及[完整操作指南](docs/COLLECTION.md)
- 路线图：`ROADMAP.md`
- 变更记录：`CHANGELOG.md`
- 参与贡献：`CONTRIBUTING.zh-CN.md`
- 治理与维护者：`GOVERNANCE.md`、`MAINTAINERS.md`
- 安全与支持：`SECURITY.zh-CN.md`、`SUPPORT.md`

## 许可证、安全与贡献

本项目源代码采用 Apache-2.0，见 `LICENSE`。该许可证不会自动覆盖第三方
软件、模型、资料或生成物。贡献前请阅读 `THIRD_PARTY_NOTICES.md`、
`SECURITY.md` 和 `CONTRIBUTING.md`。

## 项目方向

长期方向是面向长期离线环境的知识、教育、生活指导和日志系统，并探索
火星长期驻留情景。方向声明不代表当前版本已经达到任务就绪、专业认证
或安全关键系统标准。
