# 参与贡献

简体中文 | [English](CONTRIBUTING.md)

感谢你帮助离线知识服务更多人。请保持提交小而清晰，并避免加入私有或
受限制材料。

## 提交前

1. 不得加入书籍、译文、模型权重、索引、声音、肖像、生成媒体、凭据、
   账号数据或机器专用绝对路径。
2. 行为改变应增加或更新测试。
3. 功能或安全边界变化时，同时更新中英文用户文档。
4. 每个计划公开的新文件都必须加入 `release-manifest.txt`。
5. 运行 `python scripts/open_source_audit.py`、`python scripts/validate_rights.py`
   和 `python -m pytest -q`。

翻译必须标明源语言和审核状态。机器翻译不能冒充已经人工审核或具有权威
效力的译本。包含私密信息的安全报告应使用私密安全通道，不得发布到公开
Issue。
