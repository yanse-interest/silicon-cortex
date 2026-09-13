#!/usr/bin/env python3
"""Mark legacy quiz answers unverified and ground the answers with confirmed sources."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard_model import atomic_write_json


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
DEFAULT_REGISTRY = VAULT / "wiki/project-dashboard-case-registry.json"
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"

GROUNDED = {
    "TOF 和 MRT 的基本原理有什么区别？": {
        "answer_origin": "chatgpt_conversation",
        "evidence_refs": [{
            "source_type": "chatgpt_conversation",
            "source_date": "2026-06-29",
            "session_id": "S02",
            "session_title": "Waters MRT解析",
            "support_level": "direct_answer",
            "excerpt": "原会话说明 MRT 仍属于 TOF，通过两个静电镜之间的多次反射延长飞行路径，从而提高时间展开与分辨能力。",
            "source_locator": "https://chatgpt.com/c/6a4207f1-edf8-83ea-9f42-959b1041c1d3",
        }],
    },
    "SIM 和 MRM 为什么具有不同的选择性？": {
        "answer_origin": "daily_report",
        "evidence_refs": [{
            "source_type": "daily_report",
            "source_date": "2026-07-20",
            "session_id": "S03",
            "session_title": "SIM与MRM区别",
            "support_level": "source_summary",
            "excerpt": "日报技术记录明确区分：SIM 监测指定一级 m/z，MRM 监测特定母离子到子离子的 transition，并指出后者特异性更强。",
            "source_locator": "raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-07-20.md",
        }],
    },
}


def migrate(registry: dict, timestamp: str) -> dict[str, int]:
    grounded = 0
    pending = 0
    for event in registry.get("events") or []:
        if event.get("state") != "capability_item" or event.get("capability_domain_id") != "instrument_methodology":
            continue
        question = str(event.get("question") or "")
        provenance = GROUNDED.get(question)
        if provenance:
            event.update({
                "answer_status": "source_grounded",
                "answer_origin": provenance["answer_origin"],
                "evidence_refs": provenance["evidence_refs"],
                "provenance_reviewed_at": timestamp,
            })
            grounded += 1
        else:
            event.update({
                "answer_status": "needs_source_review",
                "answer_origin": "legacy_migration_generated",
                "evidence_refs": [],
                "provenance_reviewed_at": timestamp,
            })
            pending += 1

    audit = registry.setdefault("migration_audit", [])
    if not any(item.get("kind") == "knowledge_answer_provenance" for item in audit):
        audit.append({
            "at": timestamp,
            "kind": "knowledge_answer_provenance",
            "authority": "user_confirmed",
            "summary": "考点回答改为证据优先；无日报/ChatGPT 会话证据的历史生成答案降级为待回查。",
        })
    registry["updated_at"] = timestamp
    return {"grounded": grounded, "pending": pending}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    counts = migrate(registry, timestamp)
    if not args.dry_run:
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = args.backup_dir / f"project-dashboard-case-registry-before-answer-provenance-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        shutil.copy2(args.registry, backup)
        atomic_write_json(args.registry, registry)
    print(json.dumps({"ok": True, "dry_run": args.dry_run, **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
