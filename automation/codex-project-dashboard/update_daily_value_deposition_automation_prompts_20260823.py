#!/usr/bin/env python3
"""Require explicit candidate extraction and completed deposition in both daily jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import tomllib
from pathlib import Path


AUTOMATIONS = (
    Path("/Users/shiba/.codex/automations/archive-chatgpt-daily-report/automation.toml"),
    Path("/Users/shiba/.codex/automations/archive-codex-daily-report/automation.toml"),
)
BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"
MARKER = "DAILY_VALUE_DEPOSITION_POLICY_V1"
OLD_CHATGPT_DIRECT = (
    "运行 deposition_pipeline.py validate-source 和 daily。Structured Candidates 仍只允许当天自动晋升明确、完整、非高风险、无冲突的 decision、稳定 preference 和 project state；"
)
NEW_CHATGPT_DIRECT = (
    "Structured Candidates 的验证与 official daily deposition 必须统一由下文 DAILY_VALUE_DEPOSITION_POLICY_V1 的 finalizer 执行，不得直接把初稿数组传给 deposition_pipeline。"
    "候选仍只允许当天自动晋升明确、完整、非高风险、无冲突的 decision、稳定 preference 和 project state；"
)
OLD_CODEX_DIRECT = (
    "运行 `ingest_codex_daily_report.py`，第二次 ingest 必须 unchanged；创建 `codex_daily_source_summary`，包含恰好一个 Structured Candidates JSON 数组；"
    "运行 `deposition_pipeline.py validate-source` 和 `daily`。同日不同 raw 不覆盖。"
)
NEW_CODEX_DIRECT = (
    "运行 `ingest_codex_daily_report.py`，第二次 ingest 必须 unchanged；创建 `codex_daily_source_summary` 初稿，包含恰好一个 Structured Candidates JSON 数组；"
    "随后必须按下文 DAILY_VALUE_DEPOSITION_POLICY_V1 显式抽取候选并由 finalizer 统一执行验证与 official daily deposition，不得直接把初稿数组传给 deposition_pipeline。"
    "同日不同 raw 不覆盖。"
)
BLOCK = r"""

DAILY_VALUE_DEPOSITION_POLICY_V1（日报结束后的强制价值沉淀；不得省略）：
- immutable raw 与 source summary 初稿完成后，不能把已有 `Structured Candidates`（包括 `[]`）直接当作抽取结果。必须显式运行：
  `env PYTHONDONTWRITEBYTECODE=1 python3 /Users/shiba/Documents/codex/projects/codex-skills/chatgpt-daily-report/scripts/daily_value_deposition.py prepare --daily-source SOURCE_PATH --memory-root "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory" --output /private/tmp/FAMILY-daily-candidate-extraction-TARGET_DATE.json`
- `prepare` 产物只是 extraction work manifest，不是正式来源。逐一阅读并审查其中全部 `unit_ids` 对应的 formal raw/source `Sxx` 或 `Txx` 单元；把 `reviewed_units` 填为完整 unit 列表。不得只读跨会话/跨任务摘要，不得把标题、问题、preview、一般模型知识或旧空数组当作结论证据。
- 对每个由可见来源直接支持、可复用且相互独立的稳定结论，生成 Structured Candidate。candidate ID 不手填，由 finalizer 计算。每条除 deposition schema 字段外还必须包含 `privacy_mode: non_reconstructable|compliance_abstracted` 和非空 `evidence_refs`；每个 ref 包含 formal `session_id` 及至少 20 字、可在当前 immutable raw/source 中逐字命中的 `excerpt`。`sessions` 必须与 evidence refs 完全一致。
- 不要把普通完成事件本身当价值；但完成任务中形成并被验证的可复用 workflow/knowledge 必须抽取，不能仅因 source summary 初稿写了“无候选”而跳过。实验内容只允许合规抽象的方法边界/横向能力价值，不保存可还原标识、参数、原始结果或具体时间线。健康、财务、研究方法等高风险结论保持人工确认边界。
- 若所有 formal 单元逐一审查后确实没有安全、可复用、来源支持的候选，才写 `result: no_relevant_content` 与 `candidates: []`；有候选则写 `result: candidates`。空数组本身不证明抽取已执行。
- 完成 manifest 后必须运行：
  `env PYTHONDONTWRITEBYTECODE=1 python3 /Users/shiba/Documents/codex/projects/codex-skills/chatgpt-daily-report/scripts/daily_value_deposition.py finalize --daily-source SOURCE_PATH --extraction /private/tmp/FAMILY-daily-candidate-extraction-TARGET_DATE.json --memory-root "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory" --cognitive-root "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/wiki/cognitive-observatory"`
- `finalize` 会再次验证 stable ID/schema/证据逐字落点/隐私，原子更新 source summary 的 Structured Candidates，随后先运行 official daily deposition，最后才写 `wiki/review-cycles/daily-deposition/YYYY/` completion receipt。不要在 finalize 之外再运行一套不同的候选/deposition 流程。
- 成功必须有 `status: completed` receipt、`raw_files_modified: 0`、`actions_dispatched: 0`；失败或 receipt 缺失时本分支不得报告完成。删除临时 extraction manifest。07:20 Dashboard/H5 只消费有完成 receipt 的新日期；打开 H5 不负责生成日报、候选或沉淀。
"""


def update_text(text: str) -> str:
    parsed = tomllib.loads(text)
    if parsed.get("model") != "gpt-5.6-terra" or parsed.get("reasoning_effort") != "medium":
        raise RuntimeError("daily automation must remain fixed at gpt-5.6-terra medium")
    prompt = str(parsed.get("prompt") or "")
    prompt = prompt.replace(OLD_CHATGPT_DIRECT, NEW_CHATGPT_DIRECT)
    prompt = prompt.replace(OLD_CODEX_DIRECT, NEW_CODEX_DIRECT)
    if MARKER not in prompt:
        prompt += BLOCK
    prompt_line = "prompt = " + json.dumps(prompt, ensure_ascii=False)
    if len(re.findall(r"^prompt = .*?$", text, flags=re.MULTILINE)) != 1:
        raise RuntimeError("automation TOML must contain exactly one prompt line")
    updated = re.sub(r"^prompt = .*?$", lambda _: prompt_line, text, count=1, flags=re.MULTILINE)
    updated = re.sub(r"^updated_at = \d+$", f"updated_at = {int(time.time() * 1000)}", updated, count=1, flags=re.MULTILINE)
    reparsed = tomllib.loads(updated)
    if MARKER not in reparsed.get("prompt", ""):
        raise RuntimeError("rendered prompt lost deposition policy")
    return updated


def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    updates: list[tuple[Path, str, str]] = []
    for path in AUTOMATIONS:
        original = path.read_text(encoding="utf-8")
        updates.append((path, original, update_text(original)))
    print(json.dumps({
        "automations": [path.parent.name for path, _, _ in updates],
        "changed": [original != updated for _, original, updated in updates],
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "policy": MARKER,
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for path, original, updated in updates:
        if original == updated:
            continue
        shutil.copy2(path, BACKUP_DIR / f"{path.parent.name}-before-daily-value-deposition-{stamp}.toml")
        atomic_write(path, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
