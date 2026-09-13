---
name: codex-memory-maintainer
description: Maintain the user's shared Obsidian Codex Memory vault, including its nested Cognitive Observatory knowledge layer. Use when Codex needs to organize or lint `/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory`, update its wiki indexes, check Obsidian links, inspect raw/wiki layering, record reusable memory, or improve the cross-account memory workflow without storing secrets.
---

# Codex Memory Maintainer

Use this skill for the shared Obsidian memory vault and structural maintenance of
the nested `wiki/cognitive-observatory/` knowledge layer. Use
`chatgpt-daily-report` for Observatory ingestion, promotion, Review Cycles, and
generated index maintenance.

## Required Read Path

Before answering or editing memory, read:

1. `<vault>/_index.md`
2. `<vault>/wiki/index.md`
3. `<vault>/AGENTS.memory.md`
4. Only the linked wiki pages relevant to the task

Default vault path:

```text
/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory
```

## Workflow

1. Run the read-only audit before structural maintenance:

```bash
python3 codex-memory-maintainer/scripts/audit_codex_memory.py --vault "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory"
```

2. Inspect broken links, legacy external links, unindexed pages, missing required files, oversized pages, empty notes, date-heading issues, log-order issues, `wiki/source-of-truth-manifest.json` ownership/coverage errors, and any `wiki/cold-asset-inventory.json` action-count, path, fingerprint, protected-hash, or active-canonical-reference drift.
3. Edit only `wiki/`, `_index.md`, `inbox.md`, and maintenance logs unless the user explicitly provides source material for `raw/`.
4. Keep `raw/` immutable. Do not rewrite raw captures except to fix a confirmed capture mistake.
5. Write only stable, reusable information: durable preferences, project state, decisions, workflows, corrections, and source-backed synthesis.
6. Write maintenance receipts only through `scripts/maintain_memory.py insert --vault <vault> --entry-file <file>`. The helper inserts one `## [YYYY-MM-DD] type | title` entry idempotently into the canonical reverse-chronological monthly shard under `wiki/logs/YYYY/YYYY-MM.md`, then refreshes `wiki/log.md` and navigation as a rollback-protected transaction. Never edit the generated compatibility log directly.
7. Update an existing governed catalog project or decision section only through `scripts/maintain_memory.py update-catalog-section --vault <vault> --catalog-id <stable-id> --section-file <file>`. The section file must contain the complete H2 section with its legacy heading unchanged. This locked atomic writer updates the curated page body, the page's section-hash metadata, and `wiki/catalog/catalog.json` together. Never edit those surfaces separately. The one-time `migrate_project_catalog.py` and `migrate_decision_catalog.py` tools remain migration-only and are not ordinary update paths.
8. Run `scripts/maintain_memory.py generate --vault <vault>` after adding, moving, or renaming `wiki/` or `wiki/sources/` pages. Generated blocks and pages carry explicit provenance; keep human judgment outside generated blocks.
9. When a structural change touches `wiki/cognitive-observatory/`, also run the
   `chatgpt-daily-report` Observatory audit against that nested root.

## Cold Asset Governance

- Treat `wiki/cold-asset-inventory.json` as a canonical governance receipt; the assets it records remain non-canonical recovery material.
- Do not rerun `converge_cold_assets.py` for routine maintenance and do not infer permission for additional moves or deletions from an existing inventory.
- The official audit must verify summary action counts, current/original path state, tree fingerprints, protected before/after equality, and references from canonical or curated active files to either the original or archived locator of a moved asset.
- A drift or reference finding is fail-closed: report and reconcile it explicitly. Never silence it by rewriting the receipt, moving the asset again, or deleting evidence.
- Restore only from an exact inventory mapping after verifying its stored fingerprint.

The audit is read-only by default. To refresh the paired machine/human health
views from its single deterministic data model, run:

```bash
python3 codex-memory-maintainer/scripts/audit_codex_memory.py --vault "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory" --write-health --fail-on-errors
```

This atomically replaces `wiki/memory-system-health.json` and
`wiki/memory-system-health.md`. A locally unreachable H5 endpoint is recorded as
`unavailable` and does not by itself fail vault integrity.

## Date Heading Rules

- Dated maintained wiki sections use bracketed ISO dates: `## [YYYY-MM-DD] type | Title` or `## [YYYY-MM-DD] Title`.
- Do not use bare date headings such as `## 2026-06-25 ...`.
- Keep dated sections reverse-chronological unless a page explicitly documents a different order.

## Layer Rules

- `raw/`: original source material. Preserve as evidence.
- `wiki/sources/`: compact summaries of raw source material.
- `wiki/`: maintained knowledge layer. Prefer concise pages and strong indexes.
- `wiki/cognitive-observatory/`: curated Observation, Framework, cognitive
  Decision, and Review layers; preserve its deterministic promotion rules.
- `inbox.md`: temporary capture only. Classify or leave untouched when evidence is insufficient.

## Safety

- Never store passwords, private keys, API keys, auth cookies, one-time codes, or secret values.
- Store only secret names, purposes, and storage pointers when necessary.
- The user's latest explicit instruction overrides memory. If memory is stale, update the relevant page and log the correction.
- Treat health, finance, scientific, and other high-risk content conservatively: record dated observations and evidence boundaries, not unsupported conclusions.
