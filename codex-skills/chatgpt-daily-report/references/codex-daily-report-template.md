# Codex Daily Summary and Evidence Template

Use this template for the previous calendar day's Codex task deposition. It is a
durable summary-and-evidence record, not a transcript archive. Read the exact
tasks in memory while compiling it, then retain only the minimum reusable result
and verification references.

```markdown
---
type: codex_daily_report
date: YYYY-MM-DD
timezone: Asia/Shanghai
coverage_start: YYYY-MM-DDT00:00:00+08:00
coverage_end: YYYY-MM-DDT23:59:59+08:00
generated_at: YYYY-MM-DDTHH:MM:SS+08:00
source: codex
coverage: complete
status: ready
task_count: 0
---

# Codex 每日任务沉淀 — YYYY-MM-DD

## 覆盖范围与限制

- 可见任务范围：...
- 缺失或不可读取任务：无
- 脱敏：无

## 今日进度摘要

- 完成：...
- 进行中：...
- 阻塞或需人工介入：...

## 任务摘要与证据

### T01 — Codex task title

**目标：** 用户希望达成的结果。

**结果：** 实际完成或确认的结果；没有可靠结果时写 `没有可靠结果`。

**状态：** completed | in_progress | blocked | cancelled | unknown

**工作类型：** general | experimental

**证据模式：** summary_evidence | compliance_abstracted

**来源线程 ID：** controller JSON 中该任务所属的 exact `thread_id`；实验工作草稿仍提供给 ingester 核对，归档前会改为 `已脱敏`。

**Codex 项目 ID：** 由 ingest 根据 controller JSON 注入；报告草稿可省略。

**工作目录：** 由 ingest 根据 controller JSON 注入；报告草稿可省略。

**项目身份来源：** 由 ingest 根据 controller JSON 注入；报告草稿可省略。

**文件证据：** vault 或 workspace 内可定位的文件路径；没有时写 `无`。

**Commit 证据：** commit hash；未提交或不适用时写 `无`。

**测试证据：** 已执行的测试及结果；未执行或不适用时写 `无`。

**下一步：** 具体后续动作；没有时写 `无`。

**证据边界：** 实际核验了什么、没有核验什么，以及推断边界。

## 跨任务综合

### 更新的项目或案例

- ...

### 决策与偏好变化

- ...

### 待办

- [ ] item — owner — due date or `unspecified`

### 可复用知识候选

- ...

### 矛盾与低置信项

- ...

## 任务索引

- T01 — Codex task title — status — one-line outcome
```

## Allowed states and evidence rules

- `generated_at` is mandatory and must be the report's actual generation time in
  ISO 8601 format with the `Asia/Shanghai` UTC offset. It is independent of the
  covered `date`; never omit it, copy the coverage boundary into it, or leave a
  placeholder value. A report without a parseable `generated_at` is invalid and
  must be regenerated as one complete artifact before ingestion.
- `coverage`: `complete`, `partial`, or `unknown`.
- `status`: `ready`, `access_incomplete`, `no_tasks`, or
  `no_relevant_content`.
- Use `no_tasks` only when complete access confirms no Codex tasks existed.
- `task_count` must equal the number of `Txx` entries.
- Every task must include goal, result, status, file/commit/test evidence, next
  step, and evidence boundary. `无` is valid evidence when the item genuinely
  does not exist or is not applicable.
- When the controller JSON is available, every general task must cite exactly
  one `来源线程 ID`. Archive with `ingest_codex_daily_report.py
  --evidence-projection ...`; the ingester replaces project ID, cwd and identity
  provenance from that sidecar before validation and immutable archival. Task
  title, goal and result text never determine parent-project membership.
- Legacy reports without project-context fields remain valid. New reports made
  from a controller projection must not omit the exact source thread ID.
- Store summaries and evidence references only. Never store full prompts,
  transcripts, command output, tool responses, approval payloads, credentials,
  cookies, or tokens.
- For experimental work, use `工作类型: experimental` and
  `证据模式: compliance_abstracted`. Omit project/client/sample identifiers, raw
  data, exact parameters, concrete experimental results, and timelines. Preserve
  only the source-backed abstract progress, judgment frame, reusable method idea,
  open question, and evidence boundary. The ingester replaces project ID and cwd
  with `已脱敏`, including the source thread ID, while retaining only the
  `compliance_abstracted` provenance marker.

## Compiled source summary

Compile the validated raw report into:

`wiki/sources/conversations/codex-daily/YYYY/codex-daily-report-YYYY-MM-DD.md`

Use `type: codex_daily_source_summary`, retain the raw source link and coverage
metadata and each task's injected project-context fields, and add exactly one
`## Structured Candidates` JSON array following
[deposition-schema.md](deposition-schema.md). Candidate evidence references use
the corresponding `Txx` task IDs. Stable candidate IDs are shared with ChatGPT
daily sources, so the same normalized claim converges in the same ledger entry.
