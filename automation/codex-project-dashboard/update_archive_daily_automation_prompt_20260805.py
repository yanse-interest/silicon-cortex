#!/usr/bin/env python3
"""Update the daily automation prompt for detail-preserving, source-grounded answers."""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_AUTOMATION = Path("/Users/shiba/.codex/automations/archive-chatgpt-daily-report/automation.toml")
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"

OLD_DETAIL = (
    "然后用 <details><summary>技术记录（需要时展开）</summary> 收起技术记录，只保留领域、来源 URL、关键事实、"
    "明确决定、未解决、可复用洞察和证据边界。空字段写‘无’，不得用套话填充。正文不可读时，只写可确认的标题、URL、时间和不可读边界；不得编造。"
)
NEW_DETAIL = (
    "然后用 <details><summary>技术记录（需要时展开）</summary> 收起技术记录。对于 lab、project、engineering、research 工作会话，"
    "必须保留领域、来源 URL、原问题与上下文、回答要点、推理或诊断链、关键术语/条件/版本/适用边界、关键事实、明确决定、未解决、"
    "可复用洞察和证据边界；不得把可见回答压缩成只有主题、标签或一句结论。删除寒暄和重复，但保留以后检索、复习和回答考点所需的语义细节。"
    "如果只看到问题而看不到 assistant 回答，必须明确写回答不可见，不能补全。仍须脱敏客户、项目、样品、批次、内部链接、原始结果等可还原实验工作的标识。"
    "其他会话继续使用上述字段的最小必要集合。空字段写‘无’，不得用套话填充。正文不可读时，只写可确认的标题、URL、时间和不可读边界；不得编造。"
)

OLD_CANDIDATE = (
    "每项字段为 `topic`、可选的 `summary`、`question`、`answer`、非空数组 `instrument_types`、"
    "`knowledge_status: reusable`、`privacy_mode: non_reconstructable`、`confidence`、`evidence_boundary`、"
    "`related_projects`（仅允许 sequencing、CAAA），`knowledge_id` 可省略，由项目看板按内容生成稳定 ID。"
)
NEW_CANDIDATE = (
    "每项字段为 `topic`、可选的 `summary`、`question`、`answer`、非空数组 `instrument_types`、"
    "`knowledge_status: reusable`、`privacy_mode: non_reconstructable`、`confidence`、`evidence_boundary`、"
    "`related_projects`（仅允许 sequencing、CAAA）和非空 `evidence_refs`，`knowledge_id` 可省略，由项目看板按内容生成稳定 ID。"
    "每条 evidence ref 必须包含 `source_type: daily_report|chatgpt_conversation`、报告内 `session_id`（Sxx）、可选 `session_title`、"
    "`support_level: direct_answer|source_summary`，以及能实际支撑答案且不少于 20 个字符的 `excerpt`。题目、标题或关键词匹配不能作为答案证据。"
)

OLD_ACCEPT = "缺少题目、缺少实质答案或没有有效仪器类型时不得输出该候选，也不得保留只有问题没有答案的条目。"
NEW_ACCEPT = (
    "生成 answer 前先检索当前 raw 日报的细节技术记录；日报证据不足时，只有固定来源账号中的对应 ChatGPT 原会话仍可访问，才允许回查可见正文后回答。"
    "不得用模型常识替代缺失来源。缺少题目、缺少实质答案、没有有效仪器类型或没有有效 evidence_refs 时不得输出该候选；"
    "只有问题而无回答的内容应留在未解决知识缺口，不得生成看似完整的答案。"
)


def update_text(text: str) -> str:
    replacements = ((OLD_DETAIL, NEW_DETAIL), (OLD_CANDIDATE, NEW_CANDIDATE), (OLD_ACCEPT, NEW_ACCEPT))
    for old, new in replacements:
        if text.count(old) != 1:
            raise RuntimeError(f"expected prompt fragment exactly once: {old[:40]}")
        text = text.replace(old, new)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=DEFAULT_AUTOMATION)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    original = args.automation.read_text(encoding="utf-8")
    updated = update_text(original)
    if not args.dry_run:
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = args.backup_dir / f"archive-chatgpt-daily-report-automation-before-evidence-{datetime.now().strftime('%Y%m%dT%H%M%S')}.toml"
        shutil.copy2(args.automation, backup)
        temporary = args.automation.with_name(f".{args.automation.name}.{os.getpid()}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, args.automation)
    print("updated" if not args.dry_run else "dry-run-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
