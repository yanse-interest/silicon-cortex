# ChatGPT Daily Report Template

Use this template for the scheduled ChatGPT output. Summarize every session in the covered calendar day. Do not reconstruct unavailable chats from memory.

```markdown
---
type: chatgpt_daily_report
date: YYYY-MM-DD
timezone: Asia/Shanghai
coverage_start: YYYY-MM-DDT00:00:00+08:00
coverage_end: YYYY-MM-DDT23:59:59+08:00
generated_at: YYYY-MM-DDTHH:MM:SS+08:00
source: chatgpt
coverage: complete
status: ready
session_count: 0
---

# ChatGPT 每日会话归档 — YYYY-MM-DD

## 覆盖范围与限制

- 可见范围：...
- 缺失范围或不可访问会话：无
- 脱敏：无

## 今天用人话说

- 今天主要和 ChatGPT 聊了什么：...
- 哪些事情变清楚了或被决定了：...
- 还有哪些需要行动或继续确认：...

## 会话摘要

### S01 — Original session title

**我们聊了什么：** 用 1–3 句日常语言还原这段对话：我问了什么、为什么问、ChatGPT 给了什么回答或方向。读者不展开技术记录也应能看懂。

**结论：** 用一句话写结果；如果可见证据没有可靠结论，写 `没有可靠结论`。

**下一步：** 用一句话写具体下一步；如果没有，写 `无`。

<details>
<summary>技术记录（需要时展开）</summary>

- 领域：lab | project | engineering | research | health | travel | personal | other
- 来源 URL / 原会话 ID：原 ChatGPT 会话可访问时必填，保存其 `/c/<UUID>` URL；确实不可访问时写 `unavailable`
- 原问题与上下文：保留理解这次提问所必需的背景、对象层级和用户追问；工作会话不得只写主题标签
- 回答要点：按逻辑顺序保留 ChatGPT 实际给出的关键解释、区别、方法或判断，不只保留一句结论
- 推理或诊断链：保留“依据什么 → 如何区分 → 得出什么”的关键链条；没有时写 `无`
- 关键术语、条件与边界：记录影响答案成立的仪器/软件版本、对象类型、前提、例外与适用范围；未知项明确写未知
- 关键事实：只写理解或复用结果所需的事实
- 明确决定：只写明确决定；否则写 `无`
- 未解决：未回答问题或缺失证据；否则写 `无`
- 可复用洞察：值得带到未来的稳定知识；否则写 `无`
- 证据边界：实际可见内容，以及无法核验的内容

</details>

## 跨会话综合

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

## 会话索引

- S01 — Original session title — domain — one-line outcome
```

## Allowed states

- `generated_at` is mandatory and must be the report's actual generation time in
  ISO 8601 format with the `Asia/Shanghai` UTC offset. It is independent of the
  covered `date`; never omit it, copy the coverage boundary into it, or leave a
  placeholder value. A report without a parseable `generated_at` is invalid and
  must be regenerated as one complete artifact before ingestion.
- For a legacy report that was already emitted without `generated_at`, a
  desktop-direct ingestion adapter may supply only this missing field from the
  exact `completedAt` timestamp of the containing completed assistant turn,
  converted to `Asia/Shanghai`. Add
  `generated_at_source: desktop_thread_completed_at` to preserve provenance.
  Do not change any session fact, coverage field, status, count, or body text.
  If the completed-turn timestamp is unavailable or ambiguous, reject the
  report instead of estimating a time.

- `coverage`: `complete`, `partial`, or `unknown`.
- `status`: `ready`, `access_incomplete`, `no_sessions`, or `no_relevant_content`.
- Use `access_incomplete` whenever ChatGPT cannot verify that it saw every session in the requested time range.
- Use `no_sessions` only when complete access confirms there were no sessions.
- `session_count` must equal the number of `Sxx` entries.
- A `session_count: 0` report with `status: access_incomplete` is an access-boundary placeholder, not a formal daily archive. Record a failure or `user_handoff` state and retry/backfill when real session evidence is available.

## Content rules

- Include all session domains; do not filter to lab work.
- Preserve original session titles and label cross-session inference explicitly.
- Preserve the original ChatGPT conversation URL/ID whenever the conversation is
  accessible. Treat the report-local `Sxx` as a daily locator, not a replacement
  for the globally stable conversation ID. When access is unavailable, record
  `unavailable` explicitly instead of omitting the field or inventing an ID.
- Write for the user first, not for a database. Each session must begin with the Chinese plain-language three-part summary (`我们聊了什么` / `结论` / `下一步`) before any metadata or technical detail.
- Reconstruct the conversation arc, not a bag of labels: question/situation -> answer or current understanding -> next step.
- Use the user's own wording where it improves recognition, but explain specialist terms briefly instead of stacking jargon.
- Keep `我们聊了什么` to 1–3 short sentences, `结论` to one sentence, and `下一步` to one sentence.
- Put exhaustive context, evidence limits, and reusable technical details inside the collapsed `技术记录（需要时展开）`; omit low-value repetition.
- For `lab`, `project`, `engineering`, and `research` sessions, optimize the
  collapsed record for later retrieval, not maximum compression. Preserve the
  original question context, substantive answer points, reasoning/diagnostic
  chain, key distinctions, conditions, exceptions, versions, and applicability
  boundaries whenever they are visible and material.
- Preserve enough detail to extract every distinct reusable exam point supported
  by a relevant work session, including multiple points from one session. Do not
  retain only the broadest or most summary-like point.
- A work-session record is insufficient when it contains only the title, topic,
  user's question, or a one-line conclusion while omitting visible answer logic.
  Do not claim that an answer came from a session when only the question was
  accessible; record that limitation explicitly.
- Summarize faithfully rather than copying the full transcript. Remove filler
  and repetition, but keep semantic details needed to answer likely follow-up
  questions or support an `Instrument Knowledge Candidate`.
- Do not preserve customer, project, sample, batch, internal-link, raw-result, or
  other reconstructable experimental identifiers. Preserve reusable technical
  detail and explicitly mark what was redacted or unavailable.
- Do not force every technical field to contain prose. Use `无` when there was no decision, unresolved issue, reusable insight, or next step.
- Summarize rather than reproduce full transcripts.
- Never include credentials, tokens, cookies, one-time codes, private keys, or unnecessary personal identifiers.
- If a session contains sensitive material, record only the minimum reusable conclusion and add `redacted` to the limitations section.

## Plain-language example

```markdown
### S04 — Q-TOF多肽碎裂电压分析

**我们聊了什么：** 我用 Waters G3 Q-TOF 做一条 40 个氨基酸以上、带硫醚环和特殊修饰的长肽解序，碰撞能量已经加到 70 eV，母离子还是没有完全碎。我主要问了仪器还能不能继续加能量、High CE ramp 是不是类似电压梯度，以及 MSe 下怎样才算碎得足够。由于当时看不到完整回复和谱图，还不能确定仪器上限或最终方法。

**结论：** 70 eV 对这条复杂长肽不够，但不能只看母离子消失比例，还要看序列覆盖和关键结构是否被打开。

**下一步：** 按不同 CE/ramp 记录母离子残留、序列覆盖和环区/修饰区碎片，必要时再比较 targeted MS/MS。

<details>
<summary>技术记录（需要时展开）</summary>

- 领域：药物分析 / 质谱 / Waters Q-TOF / 多肽解序
- 原问题与上下文：用户想判断高能通道继续加碰撞能量是否有意义，并区分 High CE ramp、MSe 与 targeted MS/MS 在复杂长肽碎裂中的作用。
- 回答要点：不能只用母离子是否消失判断碎裂是否充分；应同时看序列覆盖、关键结构区域是否打开、碎片归属确定性和过度碎裂风险。MSe 的高能碎片需要结合共洗脱与算法归属，targeted MS/MS 的前体—碎片关系通常更直接。
- 推理或诊断链：先确认采集模式与前体选择方式 → 再看母离子残留和碎片覆盖 → 检查关键结构区域是否有可解释碎片 → 最后比较继续加能量与改用 targeted MS/MS 的收益。
- 关键术语、条件与边界：具体 CE 上限依赖型号、采集模式与方法界面；完整谱图和官方文档不可见，不能给出设备上限或最终方法结论。
- 关键事实：Waters G3 Q-TOF；MSe；>40 aa；硫醚小环；特殊氨基酸或脂肪长链；70 eV 仍碎裂不足
- 明确决定：无
- 未解决：仪器/方法 CE 上限；电荷态和母离子 m/z；谱图覆盖；完整 assistant 回复不可见
- 可复用洞察：复杂长肽不能用单一“母离子碎了多少”判定方法成功
- 证据边界：只能确认可见的用户输入，未核对原始谱图、方法界面或官方文档

</details>
```

## Ingestion acceptance

This scheduled output remains immutable raw evidence. During ingestion, compile a
separate
`wiki/sources/conversations/chatgpt-daily/YYYY/chatgpt-daily-report-YYYY-MM-DD.md`
source summary with
`type: chatgpt_daily_source_summary` and the `## Structured Candidates` contract
in [deposition-schema.md](deposition-schema.md). Do not add promotion state to or
rewrite this raw report.
