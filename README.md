# Silicon Cortex

Silicon Cortex 是一套本地优先的个人知识沉淀系统。它把 ChatGPT 与 Codex 日报整理为不可变证据、结构化候选、项目状态和周/月复盘，并生成 Obsidian 知识层与只读 Dashboard。

## 目录

- `codex-skills/chatgpt-daily-report/`：日报、周报、Review Cycle 与价值沉淀流水线。
- `codex-skills/codex-memory-maintainer/`：Obsidian Memory 的索引、审计和受治理写入工具。
- `automation/codex-project-dashboard/`：项目 registry、证据门禁、快照生成和本地管理服务。
- `automation/codex-project-dashboard-h5/`：移动端只读 H5 与本地只读网关。

## 数据边界

仓库只保存系统实现、测试和必要说明。个人 Memory 内容、日报原文、项目状态文件、访问令牌、运行日志、构建产物和迁移备份不进入版本库。

## 开发验证

各组件保留自己的测试与运行说明。路径相关配置需要按本机目录调整；任何生产刷新都应继续遵守 receipt、privacy、registry parity 与 fail-closed 门禁。
