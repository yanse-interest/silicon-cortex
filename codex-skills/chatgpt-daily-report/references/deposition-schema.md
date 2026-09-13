# Deposition Schema

Use this contract for the compiled daily source summary, Review Cycle publication,
automatic promotion, action confirmation, and monthly calibration. The raw daily
report remains immutable and does not contain this machine-maintained state.

## Daily source summary

Use frontmatter:

```yaml
---
type: chatgpt_daily_source_summary
date: YYYY-MM-DD
coverage: complete | partial | unknown
status: ready | access_incomplete | no_sessions | no_relevant_content
raw_source: raw/conversations/chatgpt-daily/YYYY/chatgpt-daily-report-YYYY-MM-DD.md
---
```

For a Codex daily source summary, use the parallel source family:

```yaml
---
type: codex_daily_source_summary
date: YYYY-MM-DD
coverage: complete | partial | unknown
status: ready | access_incomplete | no_tasks | no_relevant_content
raw_source: raw/conversations/codex-daily/YYYY/codex-daily-report-YYYY-MM-DD.md
---
```

Store it at
`wiki/sources/conversations/codex-daily/YYYY/codex-daily-report-YYYY-MM-DD.md`.
Codex raw evidence is a bounded summary-and-evidence report, never a full
transcript or tool-output archive. Both daily source types use the same candidate
contract, stable candidate ID function, promotion ledger, and Review Cycle.

Add one `## Structured Candidates` section containing exactly one fenced JSON
array. Use `[]` when there are no candidates. Each object must contain:

```json
{
  "candidate_id": "cand-<16 hex chars>",
  "type": "decision | preference | project_state | knowledge | workflow | skill | observation | framework | cognitive_shift | action",
  "domain": "normalized topic domain",
  "normalized_claim": "one stable, atomic conclusion",
  "source_date": "YYYY-MM-DD",
  "sessions": ["S01"],
  "coverage": "complete | partial | unknown",
  "evidence_boundary": "what the cited evidence does and does not prove",
  "evidence_complete": true,
  "assertion": "explicit | inferred",
  "confidence": 0.0,
  "risk": "low | medium | high",
  "suggested_target": "project | preference | decision | knowledge | workflow | skill | cognitive_observation | cognitive_framework | action",
  "status": "candidate | promoted | conflict | manual_confirmation",
  "conflicts_with": []
}
```

Daily automations must not treat a pre-existing array as the extraction step.
After the raw and source-summary draft exist, run
`daily_value_deposition.py prepare`, review every formal `Sxx`/`Txx` unit, and
complete the manifest with all `reviewed_units` plus either `candidates` or
`no_relevant_content`. Each candidate additionally carries
`privacy_mode: non_reconstructable|compliance_abstracted` and one or more
`evidence_refs`; every reference contains a formal `session_id`, support level,
and a verbatim excerpt of at least 20 characters from the current immutable raw
or compiled source. `sessions` must exactly match those references. The
finalizer computes the stable ID, updates the candidate block, runs daily
deposition, and only then writes the daily completion receipt under
`wiki/review-cycles/daily-deposition/YYYY/`.

`[]` is valid only when the completed manifest says `no_relevant_content` and
shows that all formal units were reviewed. A missing or mismatched receipt means
the source is not ready for the scheduled/opening Dashboard refresh.

Use `Sxx` references for ChatGPT sessions and `Txx` references for Codex tasks.
For `codex_daily_source_summary`, all candidate references must be `Txx` IDs from
the corresponding immutable Codex daily report.

Compute the ID from the normalized `type`, `domain`, and `normalized_claim`:

```bash
python3 scripts/deposition_pipeline.py candidate-id \
  --type observation --domain "AI workflow" --claim "Normalized conclusion"
```

Do not hand-invent a different ID. Rewording that does not change the conclusion
must keep the normalized claim and ID stable. A materially different conclusion
gets a new ID and may declare `conflicts_with` or `supersedes`.

Optional type-specific fields:

- Explicit decision: `adoption_evidence` containing the user's adoption words.
- High-risk observation: dated `uncertainty`; never encode a causal conclusion,
  method decision, or action recommendation as an auto-promotable observation.
- Framework: `linked_observation_ids`, `observation_cycle_ids`, and distinct
  `application_cases`.
- Cognitive shift: `old_judgment`, `new_judgment`, and `change_evidence`.
- Skill candidate: integer `validation_successes`; two or more creates a Skill
  candidate but never edits Skill code.
- Action: non-empty `action`, plus `owner` and `due`; omit the latter two only
  when they are genuinely unknown, in which case the engine stores `unspecified`.

## Routing rule

Route candidates by durable value before promotion:

- Send only high-level cognitive abstractions to Cognitive Observatory:
  `observation`, `cognitive_shift`, and `framework`.
- Use `suggested_target: cognitive_observation` only for `observation` or
  `cognitive_shift` candidates.
- Use `suggested_target: cognitive_framework` only for `framework` candidates.
- Keep ordinary facts, preferences, project state, decisions, workflows, actions,
  and skill candidates in Codex Memory targets unless the source explicitly frames
  them as a reusable cognitive pattern or judgment model.
- Do not encode a simple task, status update, preference, tool bug, or one-off
  idea as a Cognitive Observatory candidate just because it appeared in a Weekly
  Review. Choose `project`, `preference`, `knowledge`, `workflow`, `action`, or
  `skill` instead.
- When uncertain, route to a Memory target and keep the candidate in
  `continue_observing`; do not promote to Observatory by default.

The pipeline validates obvious route mismatches and rejects candidates that send
simple content to a cognitive target.

`coverage: partial` is valid positive evidence. It must never be used to claim
that no other event occurred or to calculate complete-day frequency.

After writing and validating the compiled source summary, record its evidence and
apply same-day Memory rules with:

```bash
python3 scripts/deposition_pipeline.py daily \
  --daily-source /path/to/wiki/sources/conversations/chatgpt-daily/YYYY/chatgpt-daily-report-YYYY-MM-DD.md \
  --memory-root /path/to/Codex\ Memory \
  --cognitive-root /path/to/Codex\ Memory/wiki/cognitive-observatory
```

This daily command may auto-promote only explicit, complete, non-high-risk
decisions, preferences, and project state. Observatory thresholds remain gated on
a published Review Cycle.

## Review Cycle

Weekly Review frontmatter must use `status: draft` or `status: published`.
Published reviews also require an ISO `published_at`. Only a published review may
be archived as the cycle's single canonical raw file, write
`review-cycle-<id>.json`, count toward thresholds, or close the cycle. Drafts
may be used for an in-memory preview but must not be saved in the canonical raw
or weekly source-summary paths.

Run a draft preview or publish a cycle with:

```bash
python3 scripts/deposition_pipeline.py review \
  --weekly-review /path/to/weekly-review.md \
  --daily-source /path/to/wiki/sources/conversations/chatgpt-daily/YYYY/chatgpt-daily-report-YYYY-MM-DD.md \
  --memory-root /path/to/Codex\ Memory \
  --cognitive-root /path/to/Codex\ Memory/wiki/cognitive-observatory
```

The result groups stable IDs into new promotions, continued observation,
conflicts, Skill candidates, manual confirmation, and pending actions. A changed
published source cannot silently overwrite an already closed cycle.

## Actions

All actions start as `pending`. Confirm selected IDs with `confirm-actions`. This
only marks them `confirmed` and returns a `dispatch_ready` payload. It does not
send anything. Hermes/Feishu dispatch must consume only confirmed entries and
record a later `dispatched` state through the existing user-approved route.

An internal verification or maintenance action that has already been executed
must be marked `completed` with `complete-actions`, a completion date, and
source-backed completion evidence. `completed` is terminal, does not imply
external dispatch, and is excluded from monthly unfinished actions.

## Promotion and reversal

Auto-generated formal pages include `promotion: auto`, candidate ID, evidence
count, first/last dates, confidence, and source links. Promotion paths are stable
and ledger-backed. Re-running the same evidence is idempotent.

Use `revoke` to preserve the page, change it to `status: revoked`, and append a
dated reason. Never delete the historical page. A replaced decision should use
`supersede` with a known, explicitly adopted successor. The engine preserves the
old page as `status: superseded`, records `superseded_by`, and never erases it.

## Monthly review

Run `monthly --month YYYY-MM` against the common `wiki/sources` ancestor and the
Review Cycle directory. It discovers both ChatGPT and Codex daily source filename
families, includes cycles closed in that natural month, adds daily reports not
covered by any cycle, deduplicates by candidate ID, and writes
`wiki/reviews/monthly-review-YYYY-MM.md` without deleting history.

Before `monthly`, create `wiki/reviews/monthly-case-manifest-YYYY-MM.json` with
`version: 1`, the target `month`, and a `cases` array. Each case contains:

- stable `case_id`, computed from `line` (`work` or `life`) and stable `title`;
- `status`: `planned`, `completed`, `in_progress`, `blocked`, `on_hold`,
  `unknown`, or `archived`;
- `evidence_mode`: `direct_source`, `bounded_partial`, or
  `compliance_abstracted`; the last mode is reserved for non-experimental work
  cases where confidentiality prevents concrete disclosure;
- `summary`, `current_progress`, and `progress_basis` (`reported` or `inferred`);
- `timeline_scope` and `evidence_boundary`;
- non-empty `timeline` entries with date, event, evidence type, and a vault-relative
  source under `wiki/sources/`, `wiki/claims/`, or the allowed core Memory pages
  `wiki/projects`, `wiki/decisions`, and `wiki/preferences`;
- source-linked `lessons` and `open_items`;
- optional `prior_links` to an earlier monthly manifest, with relation
  `continuation`, `related`, or `predecessor`.

A `continuation` must reuse the exact prior `case_id`. Read earlier monthly
manifests and reviews before finalizing the current manifest, carry forward the
full visible timeline for continuing cases, and preserve explicit evidence gaps.
Unfinished cases remain visible even when they have no promotable candidate.
Validate with `validate-cases`; cases are Memory review material and are not
automatically promoted into Cognitive Observatory.

Experimental work must not enter this case manifest. It has no `case_id`, case
count, case status, or reconstructable timeline; retain only month-level abstract
progress, knowledge gaps, reusable method boundaries, and value under the
maintained demand branches/capability domains, with suspected concrete content
reduced to a manual-review placeholder.

For non-experimental work cases, `compliance_abstracted` does not require
confidential names or implementation details. Preserve source-backed conclusions
without inventing withheld facts. Missing compliant detail alone must not set a
case to `blocked` or `on_hold`; use explicit progress evidence, otherwise
`unknown`.

For work cases, compute inactivity by calendar month from the latest timeline
event. At three months without an update, set `status: archived`. Archived cases
retain their history and cross-month links but do not count as unfinished. A
later update reuses the same stable `case_id` and resumes an evidence-backed
active status.
