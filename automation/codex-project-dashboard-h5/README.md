# ChatGPT + Codex 项目看板 H5

同一 Wi-Fi 内使用的移动端只读看板。页面先立即显示最近一次验证通过的内置快照，首次打开再向同源只读接口 `/api/fresh-snapshot` 请求当前快照。该接口只重建已经落库的数据：canonical registry、日期/类型校验通过的 ChatGPT/Codex source summary，以及允许的已落库状态；不访问 ChatGPT、不执行日报沉淀，也不连接 8791 管理端。

新快照只有在生成与 schema/隐私/证据检查全部成功后才替换页面内状态；超时或失败时继续显示内置快照，并明确标记回退。页面分别显示 `generated_at` 与 `daily_updated_through`，后者只表示双日报门禁已覆盖到该日，不声称全部底层证据完整。

ChatGPT 内容来源固定 account-1，Codex 来源独立；07:20 确定性每日构建可由桌面当前已登录的 account-1 或 account-2 执行，但不会切号、登录、绑定、回退或修复认证。客户端负载不包含本机路径、原始日志、canonical conversation URL、prompt、推理、内部稳定 ID、写接口或访问令牌，也不依赖外部数据表或数据库。

```bash
cd /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard
python3 dashboard_h5_open_snapshot.py --output ../codex-project-dashboard-h5/public/dashboard-snapshot.json

cd /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard-h5
npm test
npm run start -- --host 0.0.0.0 --port 8792
```

正常使用由 `com.shiba.codex-project-dashboard-h5` 常驻运行：只读网关监听 8792，应用后端仅监听本机回环 8793。网关对动态快照执行有界超时、短时缓存和并发单航班；所有响应均 `no-store`，所有写方法均返回 405。每日 07:20 的
`com.shiba.codex-project-dashboard-h5-refresh` 在双日报门禁通过后重新导出快照、构建页面并重启服务；失败时保留上一版。

每日 07:30 的 `publish-project-dashboard-sites` 自动化会在相同门禁、快照隐私/证据检查和 `npm test` 通过后，把新快照发布到既有 owner-only Sites 项目。任一步失败均保留上一线上版本，不改变站点访问范围。

能力域答案采用证据优先规则：只有携带日报或可访问 ChatGPT 会话引用的答案才展示；历史迁移生成但无法回溯来源的文本显示为“证据不足，待回查”。
