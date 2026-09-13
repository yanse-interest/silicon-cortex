# ChatGPT + Codex 项目进度与价值沉淀

以 ChatGPT 日报洞察和 Codex 日报执行结果为输入、以项目为聚合单位、以合规抽象为边界的个人看板。Codex Hooks 只负责后台采集与路由，不展示 Codex 实时任务。

实验工作不保存具体 case。“解序”和“CAAA”作为合规抽象的实验工作项目展示；“仪器知识与应用方法论”是唯一横向能力域，肽图归入解序。派生看板只保存自然月粒度的抽象进展、知识缺口、能力方法和可复用价值。

禁止进入派生看板的实验工作信息包括：客户、项目、样品、批次、序列/结构、原始数据、精确参数、内部链接、具体 case 数量/状态以及可组合还原项目的时间线。疑似实验工作日报会被改写为“待合规抽象”占位，且不能自动建立 Case。

系统不再把“最新月度 case manifest”当作实时主库：首次启动时仅用最新 manifest
迁移历史项目和 timeline，之后由
`wiki/project-dashboard-case-registry.json` v4 保存项目、能力域、合规抽象 event、价值沉淀和 event routing。解序/CAAA 虽复用 registry 的项目记录结构，但不代表具体实验 case。月度 manifest 的 lessons 会按 candidate stable ID 或项目内语义文本跨月去重累计，多来源合并为同一价值的证据列表；最新 manifest 只负责当前开放项。
每日 source summary 中的逐 session / task 记录会幂等摄取为 event：

- ChatGPT 兼容 legacy `Session Inventory` / `已报告事实` 与当前 `跨会话摘要` 的单条 `Sxx` 记录；当 compiled source 只有聚合摘要时，只能沿 frontmatter 中精确、同日、同类型且位于 canonical `raw/conversations/chatgpt-daily/` 的 `raw_source` 回读正式 `会话摘要`。不得用会话标题、preview、模型知识或分组编号补写事件。
- Codex 兼容 legacy `任务摘要` 与当前 `Cross-task Summary` 的单条 `Txx — title — status — actual result`；无 `Txx` 的聚合叙述不生成任务事件。两类事件 ID 均由来源族、精确日期与 S/T ID 确定，重复行不会产生重复事件。
- 项目价值不从普通完成事件或进度推断。两份日报在 raw/source 完成后必须先运行显式 candidate extraction：逐一审查全部 S/T 单元，每条候选携带可在 formal raw/source 逐字命中的 evidence refs；无相关价值时也必须以 `no_relevant_content` 留下完成结果。随后先运行 official daily deposition，再写 completion receipt。只有 source summary 正式 `Structured Candidates` 中显式、证据完整、低风险、无冲突、stable ID 正确且类型/目标为 `knowledge` 或 `workflow` 的可复用结论，才可在其全部 S/T 引用唯一归入同一项目后写入 value；否则保持待路由或拒绝，不自动晋升。

- 疑似实验工作内容只进入不可见的合规审查状态，具体文本不进入派生 registry。
- 实验内容按知识点或工作片段分类，不把整场会话整体归入一个项目：通用仪器原理、采集模式、软件处理层、硬件边界和跨平台方法进入能力域；以序列确认为目标的结构表示、肽图、碎片证据和覆盖边界进入解序；水解、衍生化、构型判定和诊断证据进入 CAAA。
- 混合会话可以同时关联能力域与项目，但能力知识只保存一次，不计作项目进展；只有目标相关的方法推进、证据变化或判断收敛才进入解序或 CAAA。
- 每份新 ChatGPT 日报 source summary 还必须输出一个机器可读的 `Instrument Knowledge Candidates` JSON 数组。每条必须同时包含明确的 `question`、可独立复习的展开 `answer`、非空规范化 `instrument_types`，以及至少一条 `evidence_refs` 日报/ChatGPT 会话证据。`topic` 仅作展示标签，必须是约 4–8 个中文字符（或同等简短的既定技术术语）的证据忠实主题词，如“采集模式”“软件边界”“LockMass配置”；完整判断及边界放入 `summary` 和 `answer`，不得写成长结论句。证据必须标注 session、支持层级和足以核验回答的摘要；只有题目或主题相似不能支撑答案。旧迁移生成且没有来源证据的答案不会在 H5 中作为答案展示，而是明确降级为“待回查日报或原会话”。
- `instrument_types` 只属于“仪器知识与应用方法论”能力域。能力域各自拥有独立的标签维度与筛选状态；未来新能力域应定义自己的 `tag_label` 和标签集合，不复用全局标签。
- 其他非保密的具体问题或推进信号优先按证据路径映射项目；只有一个可靠关键词匹配时自动归入，否则进入“待归入项目”。
- Codex 任务保留目标、实际结果、状态、下一步、验证证据和证据边界，不保存提示词、推理过程或原始工具输出。
- 概念解释、区别、介绍等普通问答进入“日报知识流水”，不会自动建立 Case。
- 同一 stable ID 的源摘要修订会更新源字段；普通修订不覆盖人工路由、项目状态和编辑内容，但隐私风险修订始终清除旧路由并进入合规审查。自动路由会按新证据重新计算。

项目展示分类包括：

1. 实验工作（仅合规抽象项目）
2. 产业与职业认知
3. AI 与自动化前瞻

“研发实践与问题解决”已由解序/CAAA 两个实验工作项目取代；“仪器知识与应用方法论”只在横向能力域中展示。两个旧枚举仅作为历史数据读取兼容，不出现在界面或新建选项中。

实验工作项目卡片与其他项目一致，并额外保留相关问询和合规抽象价值；横向能力域只保存一次知识，并可关联多个项目。其他项目卡片显示最近两条进展，详情页显示完整记录、来源覆盖、验证证据和边界。每张项目卡片都提供“更新进度 / 状态”入口；可只改进行中、待确认、暂缓、已完成或已归档，也可同时手动输入最新进度。人工更新会追加为一条记录，当前进度不会被日报摘要吞没，历史也不会丢失。当前版本支持人工新建、
编辑、追加进展、把任意日报 event（包括知识流水）归入已有项目、从日报创建项目
和忽略 event。归入时可选择“仅归入 Log”或“归入并设为当前进度”；详情中的
既有 log 可移动到其他 Case，并可撤销最近一次人工归入。

## Run

```bash
python3 /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard/dashboard_server.py --host 0.0.0.0 --port 8791
```

正常使用由 macOS LaunchAgent `com.shiba.codex-project-dashboard` 常驻运行。
它只保持网页服务可访问，不主动抓取 ChatGPT/Codex 会话；页面在打开、手动刷新、保存或每 5 分钟的低频刷新时读取已落库的 ChatGPT/Codex 日报 source summary。

默认地址：

```text
http://127.0.0.1:8791/
```

这是 Mac 本机的管理入口。直接从这台 Mac 打开时无需输入访问令牌；服务只根据连接来源是否为本机回环地址判断，不信任网页传入的代理头。Mac 需要保持开机，LaunchAgent 会自动维持服务。

手机和 Mac 在同一 Wi-Fi 时，正式使用只读 H5：

```text
http://mbp.local:8792/
```

H5 由 LaunchAgent `com.shiba.codex-project-dashboard-h5` 常驻运行。页面立即显示最近一次验证通过的内置快照，并在首次打开时通过同源只读 `/api/fresh-snapshot` 重新聚合已经落库且具有匹配 daily deposition completion receipt 的 canonical registry、ChatGPT/Codex source summary 与允许状态；它不访问 8791 管理 API、不访问 ChatGPT、不切换或绑定账号，也不执行候选抽取或日报沉淀。生成失败或超时会继续显示内置快照并明确标记回退，绝不以失败输出覆盖好快照。

8792 网关只提供 GET/HEAD，写方法统一返回 405；应用后端仅绑定 127.0.0.1:8793。动态请求具有短缓存、并发单航班和有界超时，响应使用 `no-store`。客户端负载不包含写接口、访问令牌、本机路径、canonical conversation URL、prompt、推理、内部稳定 ID 或原始日志。页面分别展示生成时间与日报更新至日期；后者不等于底层证据完整。`mbp.local` 是当前 Mac 的 Bonjour 名称，优先于会随 DHCP 变化的数字 IP。

若手机无法解析 Bonjour 名称，可临时使用 Mac 当前局域网 IP；手机必须与 Mac 处于同一非访客 Wi-Fi，并关闭会隔离本地网络的 VPN/代理。当前地址可用 `ipconfig getifaddr en0` 查询。

## Daily H5 Refresh

LaunchAgent `com.shiba.codex-project-dashboard-h5-refresh` 每天 07:20（Asia/Shanghai）运行一次，位于 07:05 前一日日报自动化及其候选抽取/沉淀之后。这仍是无需打开页面的确定性每日回退。它先确认桌面当前账号是已登录的 `account-1` 或 `account-2`，且 router 保持 `paused=true`、`autoSwitchWhenSafe=false`、`automationStatus=disabled`，再从上一份已验证 H5 的 `daily_updated_through + 1` 唯一推导起点、以 Asia/Shanghai 的昨日唯一推导终点，逐日验证 ChatGPT 与 Codex 两份 canonical compiled source、immutable raw 与匹配的 completed deposition receipt/hash。不存在可传入的 production replay 日期，也不能跳过中间缺口。

每个合格日期都独立执行正常 registry refresh、Stage-2 isolated compare、privacy/parity checks 与 H5 原子替换，并写入 `wiki/review-cycles/dashboard-refresh/` 的 schema-v2 compact provenance receipt；其中绑定准确 source generation、ChatGPT/Codex raw/source/completed-receipt hashes、registry/H5 before/after hashes、Stage-2 receipt、实际 commit timestamp、enforcement version 与 commit-chain hash。Stage-4 canonical manual state 是唯一人工权威；replay/compatibility 可能因本地 preview 或正常 server refresh 暂时领先 H5 checkpoint。此时 catch-up 只有在 split generation/artifact classification/compatibility hash 全部一致，且从 H5 下一日到 replay 最新日的每一天都有两来源有效 completed deposition receipt 时，才使用隔离的只读 source-through 历史投影逐日推进 H5；manual/replay/compatibility 三个 canonical 文件必须保持 byte-identical。任何日期缺口、无效/未收据的超前证据、compatibility drift、manual metadata corruption 或目标窗外未来证据都在首个 H5 写入前 fail closed。固定 `/private/tmp` transaction journal 会在下一次运行先回滚未完成的单日事务；因此第二日失败时第一日 checkpoint 仍有效，而失败日不会留下半提交 registry/H5/Stage state。任一日期门禁失败时立即停止并保留此前完整 checkpoint；不会 switch/login/bind/fallback/auth repair。ChatGPT 日报内容来源仍固定为经过可见 attestation 的 account-1，绝不以 account-2 历史回退；执行账号与内容来源账号相互独立。打开 H5 的同源刷新不运行这些账号或来源门禁，只读取已经完成沉淀并落库的现有资料。

07:20 与独立的 07:05 ChatGPT/Codex 沉淀任务之间允许一个严格受限的完成窗口：只有进程同时具备 exact `XPC_SERVICE_NAME=com.shiba.codex-project-dashboard-h5-refresh`、launchd parent PID 1 与 exact runner 时，且 catch-up 停在 `missing_sources` 或 `incomplete_deposition`，才会每次最多等待 60 秒并重新调用同一个 gated export。每次重试仍由 canonical catch-up 从最新已提交 H5 checkpoint 重新推导剩余日期；不会传入日期、回放或跳过缺口。等待最迟在当日 08:10 或启动后 50 分钟（以较早者为准）结束。`invalid_sources`、结构/事务错误、账号 `user_handoff` 与全部手动 CLI 均 fail fast；成功后 build 与 H5 restart 仍只执行一次。

手动刷新：

```bash
python3 /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard/dashboard_h5_refresh.py
```

手动 CLI、测试、preview、registry-only rebuild、手动 catch-up 或任意 caller-supplied observation ID 都不能成为 migration live gate evidence。只有安装 provenance enforcement 之后，由 launchd 在真实 07:20 时间窗启动、来源日期为前一自然日、且完整 production commit chain 通过内部复核的 refresh 才能记录一条 live receipt；不要为了推进门槛手动 kick LaunchAgent。

若只修改了本地 registry 的分类或展示文案、没有摄取新日报，可使用
`--registry-only` 重建 H5。它保留上次完成门禁的“已更新至”日期，不读取账号、
不摄取来源，也不推进每日同步状态。

## Daily Private Sites Publish

Codex automation `publish-project-dashboard-sites` 每天 07:30（Asia/Shanghai）运行。它在相同账号和双日报门禁通过后检查快照隐私与问答证据、运行 H5 测试，只提交脱敏快照，并把新版本发布到既有 owner-only Sites 项目。浏览器刷新不会访问本机 Memory 或触发构建；任何门禁、测试、隐私检查或私有访问检查失败都会保留上一线上版本，不会改成 shared/public。

部署连接器返回 `succeeded` 只表示版本已发布，不等于当前网络已能使用。发布后（包括无需新版本的 idempotent run）必须使用 Sites 返回的短期私有 bearer 对 production URL 的 `dashboard-snapshot.json` 做认证回读，并同时匹配目标 `daily_updated_through` 与本次 `generated_at`。只有回读成功才报告 `delivery_status: usable` / `routing_status: normal_complete`。

若当前网络在应用前被 Cloudflare 403 拦截，流程返回 `delivery_status: deployed_but_edge_blocked` / `routing_status: user_handoff`，记录非敏感的 `cf-ray` 与 colo，且不重复部署、不改变 owner-only allowlist。此时同 Wi-Fi 本地 H5 `http://mbp.local:8792/` 继续作为可靠入口。确定性检查器为 `dashboard_sites_delivery_check.py`；它只从进程环境读取短期 bearer，不把 token 写入文件或输出。

本地交付也不能只检查磁盘文件。`app/page.tsx` 在 server bundle 中静态导入快照，因此 `npm run build` 覆盖 `dist/` 后，已运行的 Vinext 进程仍可能保留旧的模块内数据。任何人工或自动刷新都必须在构建后 `launchctl kickstart -k gui/$(id -u)/com.shiba.codex-project-dashboard-h5`，再运行 `dashboard_local_delivery_check.py`；检查器同时回读 127 与 `mbp.local` 的根 HTML 和静态 JSON，要求 JSON 与生成快照完全一致、SSR HTML 含本次日期/生成时间/全部 topic，且两类响应均为 `no-store`。只有实际服务回读通过后，才能把本地 H5 作为可用回退。

## Registry Stage-1 Shadow Split

`registry_shadow_split.py` 是隔离的迁移验收工具，不在 Dashboard/H5 production 调用链中。它只接受一个 canonical registry 和一个尚不存在的 caller-specified output directory，确定性生成 manual/non-rebuildable projection、replayable/derived projection、recomposed registry 与 parity receipt，并在目录级原子提交前重新验证 source SHA-256。

工具对 unknown/unclassified field、稳定 ID 缺失或重复、protected manual hash 不一致、split→merge 语义不一致、private locator 泄漏到 replay/public projection、source 运行中变化全部 fail closed。临时输出不得复制到 Memory canonical split 路径，不得接入 reader/writer；验证后删除，仅由 Memory 维护流程保存 compact passed receipt。当前生产权威和读写路径仍是 `wiki/project-dashboard-case-registry.json`。

```bash
python3 registry_shadow_split.py \
  --source "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/wiki/project-dashboard-case-registry.json" \
  --output-dir /private/tmp/<fresh-shadow-generation> \
  --expected-source-sha256 <observed-sha256>
```

## Registry Stage-2 Accelerated Acceptance Gate

`registry_shadow_compare.py` 由既有 gated `export_h5_snapshot.py` refresh 路径调用。production Dashboard/H5 payload 仍只由 `wiki/project-dashboard-case-registry.json` 生成；comparator 创建临时 Stage-1 projections，独立渲染 canonical 与 recomposed registries，比较 registry/manual、capability-domain、Dashboard、H5、privacy 与 canonical-input hashes，然后删除全部临时 projection。compare 只发布 compact evidence，不再接受 eligibility boolean，也不再改写原有 `stage-2-streak.json`。

V1.0 最终 Stage-2 门槛是两类证据的合取：

1. `registry_historical_replay.py` 对七个不同且连续的历史自然日做 scratch source-cutoff reconstruction，逐日绑定 immutable ChatGPT+Codex raw/source/completed receipt，验证 split/recompose、Dashboard/H5 parity、privacy、input immutability 与 scratch cleanup。历史 receipts 写入 `wiki/review-cycles/registry-migration-gate/historical-replays/`，永远不修改 production registry/H5 或 frozen legacy 3/7 streak，也不能替代真实定时运行。
2. `registry_migration_gate.py` 在 refresh 已提交后内部核验 launchd service identity、parent PID、exact runner、project/installed plist equality、07:20 时间窗、前一自然日、source generation、refresh/Stage-2 receipts、registry/H5 commit hashes 与 commit chain。`stage2-accelerated-provenance-v2` 只通过 `/bin/ps -o lstart= -p <current-os-pid>` 读取当前进程的 macOS start time：07:20–07:39 窄窗口和 expected previous source date 均绑定这个不可由 caller/CLI/env 提供的 OS 时间；`observed_at`/`committed_at` 另作审计，因此 genuine 07:20 进程在 bounded source wait 后于 07:39 以后提交仍保持资格。process-start 查询失败或解析失败、窗口外启动、older catchup、manual/direct runner 均 fail closed。只有 post-enforcement genuine scheduled run 才写 `live-refreshes/` receipt。

当前 2026-08-22～2026-08-28 七日 replay 已通过；08-22 明确分类为 `legacy_pre_gate_date_with_valid_completed_receipts`，08-23～08-28 为 `receipt_required_contract`。canonical gate summary 仍是 `not_ready`，只差一次 enforcement 安装后的真实 07:20 refresh。即使 summary 变为 ready，也只允许停下请求单独 Stage 3 批准；不会创建 split stores、启用 dual-write 或 cutover。

## Tests

```bash
cd /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard
python3 -m unittest -v test_dashboard_model.py test_dashboard_http.py test_dashboard_h5_refresh.py test_export_h5_snapshot.py test_dashboard_refresh_support.py test_dashboard_sites_delivery_check.py test_dashboard_local_delivery_check.py test_registry_shadow_split.py test_registry_shadow_compare.py test_dashboard_contiguous_catchup.py test_registry_migration_gate.py test_registry_historical_replay.py

cd /Users/shiba/Documents/codex/projects/automation/codex-project-dashboard-h5
python3 -m unittest -v tests/test_local_gateway.py
npm test
```
