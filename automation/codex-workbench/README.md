# Codex Workbench M1–M3

`workbench.py` is an independent, local-only core for the authoritative JSON
state block in a project's `PROJECT_MAP.md`. It does not call the old
Dashboard, ports 8791/8792, LAN/Sites, account router, or any task executor.

The same Python core runs on macOS and Windows. Use
[README-MACOS.md](README-MACOS.md) or [README-WINDOWS.md](README-WINDOWS.md)
for platform setup. The workbench uses each person's own project files; this
repository does not include private registries, project maps, Codex sessions,
or quota caches.

The private `--data-dir` holds only bindings, per-project locks and the most
recent valid backup. It must not be committed or served. A map remains the
only business source of truth; the generated view is disposable.

Typical local flow:

```sh
python3 workbench.py --data-dir .local preview-init ... # use init --preview
python3 workbench.py --data-dir .local init --root /absolute/project --project example-project --title Example --goal '...' --outcome first-outcome --outcome-title 'First outcome'
python3 workbench.py --data-dir .local register --root /absolute/project --project example-project
python3 workbench.py --data-dir .local read --project example-project --outcome first-outcome
```

An `update` request must include the raw-file SHA-256 and revision returned by
`read`. Under a per-project `flock`, the writer rereads both values, validates
the full prospective map and semantic relationships, saves the prior valid map
to a private recovery file, fsyncs a same-directory temporary file, atomically
replaces the map, and fsyncs its directory. A mismatched version returns a
non-zero `revision_conflict`; it never overwrites.

Run the focused M1 suite with:

```sh
python3 -m unittest discover -s tests -v
```

## M2 private H5 preview

`web.py` is a separate, read-only local HTTP projection. It binds only
`127.0.0.1` (or explicit `::1`), never starts the old 8791/8792 services,
does not create Sites/LAN output, and has no map-write or task-execution route.
Browsing, searching, details, and ordinary quota GETs make zero model calls.

Register an existing valid map, or deliberately retain a second real project
with no map as an error card. The latter does **not** create a map or infer any
business status:

```sh
python3 workbench.py --data-dir .local register --root /absolute/project --project existing-project
python3 workbench.py --data-dir .local register --root /absolute/other-project --project other-project --preview-missing-map
python3 web.py --data-dir .local --port 0
```

The server prints its loopback URL. Optional `--quota-cache` accepts the
Account Router's existing state cache and reads only its `quotaDisplay` member.
It does not read `auth.json`, tokens, cookies, or router configuration. An
optional `--quota-refresh-script /absolute/routerctl.mjs` only runs when the
user presses **显式刷新额度**; otherwise refresh is clearly unavailable and
ordinary GETs remain cache-only. The configured router command, not this
workbench, owns any credential handling.

`--activity-file` accepts short-lived observations with `expires_at` and the
redacted Codex Progress Bridge `live-state.json` schema. Bridge rows are joined
only through an outcome's explicit `conversations[].thread_id`; cwd, prompts,
outputs, and changed-file data are never projected. Real running/Stop states
are rendered as non-authoritative TTL observations and cannot change an
outcome's map status. Missing terminal events expire instead of being guessed
as a crash. Task deep links are intentionally not guessed; the supported H5
fallback is a copyable resumption note.

`--recommended-model` and `--actual-model` are display labels only. They make
the distinction visible; model changes take effect only when a caller explicitly
passes `model` and `thinking` to `create_thread` or `send_message`.

M2 adds no third-party runtime dependency. Its targeted suite exercises two
registered roots, missing-map handling, search/detail, quota cache versus
explicit refresh, stopped-state isolation, loopback Host/Origin checks, and
responsive CSS breakpoints.

## M3 fault acceptance candidate

M3 keeps the product local and read-only over HTTP, adds fail-closed quota
freshness, stable status filtering/sorting, keyboard detail access, strict
method/query/security-header handling, and an explicit CAS-protected recovery
command. The final combined suite uses only isolated temporary projects:

```sh
cd /path/to/silicon-cortex
python3 -m unittest discover -s automation/codex-workbench/tests -v
```

The most recent valid map backup can be restored only after inspecting the
current file and supplying its exact SHA-256. Recovery preserves the rejected
bytes in the private data directory:

```sh
python3 workbench.py --data-dir .local restore-backup \
  --project PROJECT_ID --expected-sha256 CURRENT_FILE_SHA256
```

Do not point this at a file that has changed since inspection, and stop the
local preview before recovery. The command rejects a stale hash, invalid
backup, missing map, and mismatched project ID.

M3 is a **completed local fault-acceptance candidate**, not a production switch.
Cross-slot shared-file continuation, passive real dual-account quota cache, and
app-native navigation to the local task have been observed. The user explicitly
clicked **显式刷新额度** in the separate loopback preview; both slots refreshed
without changing the current slot. The UI now labels independent quota buckets
(`codex` versus `additional:gpt-reserve`) and their window durations instead of
showing two ambiguous `primary` values. The preview now reads explicitly linked,
redacted real Codex running/Stop observations with TTL. H5-to-Desktop navigation
uses the documented copy fallback because no supported browser jump contract
was verified; an eventless hard crash expires to no observation and is never
invented as a crash state. Do not publish it to LAN or Sites, route it through
ports 8791/8792, or restore paused report automation.

## M5 项目成员管理

页面的“管理项目”只管理私有 registry，项目 `PROJECT_MAP.md` 仍是唯一业务状态权威。

- **加入：** 输入稳定项目 ID、绝对非符号链接根和相对地图路径，先执行预览；只有再次点击确认加入才写入。有效地图登记为 ready；缺地图可登记为 `missing` 错误卡，不会初始化或推断业务状态。
- **暂时归档：** 默认项目列表隐藏该成员；管理面可查看并恢复。它不修改项目地图或成果状态。
- **移出：** 必须输入项目 ID 二次确认；只删除 registry 绑定。项目目录、`PROJECT_MAP.md`、业务数据和私有备份都不会删除，之后可用同一 ID 重新加入。

所有成员变更都需要同源 POST、启动期管理令牌、严格 JSON 大小/类型校验以及 registry revision CAS。普通 GET、浏览、搜索、排序和额度 GET 仍是零模型调用。

## M5 任务面板

左栏项目卡是单选的本地视图状态；默认优先展示 Silicon Cortex 工作台（若未登记，再选有活跃任务的项目）。中央首屏保留项目标题、业务状态、当前任务的“做到这里”和“下一步”，以及简化后的其他成果列表。最近完成的一项可直接看见，更早成果折叠；验收条件、依赖、证据和运行观察放进按需打开的详情抽屉。业务状态固定为：待开始、进行中、暂停、待验收、已完成、已取消；运行活动、额度和模型配置不改变业务状态。界面浅色/深色随系统外观自动切换，不保存独立主题偏好。

面板内容完全来自已登记的 `PROJECT_MAP.md`，读取和刷新不调用 AI 模型。首次打开会加载完整内容；页面可见时每 10 秒仅检查本地 revision，revision 变化后才重新读取完整项目内容；窗口重新获得焦点或从后台恢复时也会检查，页面隐藏时停止有效轮询。顶部筛选菜单中的“刷新项目内容”会立即重读项目，额度刷新仍只由用户显式触发。

点击成果可查看证据，并复制紧凑续做包。它不会猜测旧会话、deep link、未记录的负责人或截止日期；缺少字段会显示为“待核对”。筛选和搜索仍只作用于读模型，且不会写入地图。
