#!/usr/bin/env python3
"""Require every supported exam point, including multiple points per session."""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path


DEFAULT_AUTOMATION = Path(
    "/Users/shiba/.codex/automations/archive-chatgpt-daily-report/automation.toml"
)
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"

OLD = (
    "凡能独立表述为考点的通用知识、概念区别、可复用结论、判断框架或适用边界都应形成仪器知识候选，即使没有推动项目。"
)
NEW = (
    "凡能独立表述为考点的通用知识、概念区别、可复用结论、判断框架或适用边界都应形成仪器知识候选，即使没有推动项目。"
    "必须遍历每个相关会话并提取其中全部彼此独立、由可见证据支持的考点；同一会话允许形成多道候选，不得只保留最概括或最像摘要的一道。"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=DEFAULT_AUTOMATION)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    original = args.automation.read_text(encoding="utf-8")
    if NEW in original and OLD not in original.replace(NEW, ""):
        print("unchanged")
        return 0
    if original.count(OLD) != 1:
        raise RuntimeError("expected exam-point prompt fragment exactly once")
    updated = original.replace(OLD, NEW)

    if args.dry_run:
        print("dry-run-ok")
        return 0

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = args.backup_dir / (
        f"archive-chatgpt-daily-report-automation-before-all-exam-points-{stamp}.toml"
    )
    shutil.copy2(args.automation, backup)
    temporary = args.automation.with_name(f".{args.automation.name}.{os.getpid()}.tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, args.automation)
    print(f"backup: {backup}")
    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
