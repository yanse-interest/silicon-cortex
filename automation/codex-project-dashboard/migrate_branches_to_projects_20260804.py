#!/usr/bin/env python3
"""Migrate the two abstract experimental branches into normal project records."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_log_compat import insert_memory_log_entry


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
REGISTRY = WIKI / "project-dashboard-case-registry.json"
PROJECT_IDS = {
    "branch-sequencing": "project-sequencing",
    "branch-caaa": "project-caaa",
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    existing_ids = {str(item.get("case_id") or "") for item in registry.get("cases", [])}
    branches = list(registry.get("branches") or [])
    timestamp = now_iso()

    for branch in branches:
        branch_id = str(branch.get("branch_id") or "")
        project_id = PROJECT_IDS.get(branch_id)
        if not project_id:
            raise RuntimeError(f"unmapped branch: {branch_id}")
        if project_id in existing_ids:
            continue
        related = [
            event for event in registry.get("events", [])
            if event.get("branch_id") == branch_id and event.get("state") == "branch_question"
        ]
        registry.setdefault("cases", []).append({
            "case_id": project_id,
            "title": str(branch.get("title") or ""),
            "line": "work",
            "category": "experimental_work",
            "status": "in_progress",
            "current_summary": str(branch.get("current_summary") or ""),
            "next_step": "\n".join(str(item.get("detail") or "") for item in related if item.get("detail")),
            "created_at": str(branch.get("created_at") or timestamp),
            "updated_at": timestamp,
            "evidence_mode": "compliance_abstracted",
            "project_type": "abstract_experimental",
            "privacy_mode": "compliance_abstracted",
            "time_granularity": "month",
            "capability_domain_ids": list(branch.get("capability_domain_ids") or []),
            "value_items": list(branch.get("value_items") or []),
            "routing_terms": [str(branch.get("title") or "").lower()],
            "legacy_branch_id": branch_id,
            "needs_review": False,
        })
        existing_ids.add(project_id)

    for event in registry.get("events", []):
        branch_id = str(event.get("branch_id") or "")
        project_id = PROJECT_IDS.get(branch_id)
        if not project_id:
            continue
        event["case_id"] = project_id
        event["state"] = "routed" if event.get("state") == "branch_progress" else "linked"
        event["route_origin"] = "schema_migration"
        event["routing_reason"] = "abstract_branch_became_project"
        event.pop("branch_id", None)

    for mapping in registry.get("project_mappings") or []:
        branch_id = str(mapping.get("branch_id") or "")
        if branch_id in PROJECT_IDS:
            mapping["case_id"] = PROJECT_IDS[branch_id]
            mapping.pop("branch_id", None)

    registry["branches"] = []
    registry["updated_at"] = timestamp
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    decisions = WIKI / "decisions.md"
    decision_text = decisions.read_text(encoding="utf-8")
    heading = "## [2026-08-04] 解序与 CAAA 改为实验工作项目"
    if heading not in decision_text:
        entry = (
            f"{heading}\n\n"
            "- 决策：H5 与项目看板不再把“解序”“CAAA”单列为长期需求分支，改为普通项目列表中的“实验工作”项目。\n"
            "- 合规边界不变：两个项目只保存自然月粒度、不可还原具体工作的抽象进展、相关问询和可复用价值；不记录客户、样品、批次、结构、原始数据、精确参数、结果、具体 case 数量或可还原时间线。\n"
            "- “仪器知识与应用方法论”继续作为横向能力域，不升级为同级项目；registry 的兼容字段不代表创建具体实验 case。\n\n"
        )
        decisions.write_text(decision_text.replace("# 决策\n\n", "# 决策\n\n" + entry, 1), encoding="utf-8")

    projects = WIKI / "projects.md"
    projects_text = projects.read_text(encoding="utf-8")
    project_note = (
        "- 2026-08-04 项目类型纠正：H5 中“解序”和“CAAA”由长期需求分支改为“实验工作”项目，"
        "与其他项目共用项目卡片、排序和详情结构；仅展示合规抽象的月级进展、相关问询和价值，具体实验 case 仍禁止保存。\n"
    )
    if project_note not in projects_text:
        anchor = "- 2026-08-03 Codex 进度桥接与跨来源项目看板："
        index = projects_text.find(anchor)
        if index < 0:
            raise RuntimeError("project dashboard anchor not found")
        projects.write_text(projects_text[:index] + project_note + projects_text[index:], encoding="utf-8")

    log_heading = "## [2026-08-04] update | 解序与 CAAA 从需求分支迁移为项目"
    entry = (
        f"{log_heading}\n\n"
        "- 按用户最新分类，将“解序”和“CAAA”从长期需求分支迁移为“实验工作”项目；原抽象进展、相关问询、价值条目和稳定关联全部保留。\n"
        "- H5 不再显示独立的“长期需求分支”区块，两个项目进入统一项目列表和近期排序。合规边界保持不变，不创建或保存可识别的具体实验 case。\n\n"
    )
    insert_memory_log_entry(VAULT, entry)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
