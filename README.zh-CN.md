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
