#!/usr/bin/env python3
"""Require original ChatGPT conversation IDs in the 07:05 archive prompt."""

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

OLD_DETAIL = "必须保留领域、来源 URL、原问题与上下文、回答要点"
NEW_DETAIL = (
    "必须保留领域、来源 URL / 原会话 ID（原 ChatGPT 会话可访问时必须保存 canonical `/c/<UUID>` URL；"
    "确实不可访问时明确写 `unavailable`，不得省略或猜测）、原问题与上下文、回答要点"
)

OLD_REF = (
    "报告内 `session_id`（Sxx）、可选 `session_title`、"
    "`support_level: direct_answer|source_summary`"
)
NEW_REF = (
    "报告内 `session_id`（Sxx）、可选 `session_title`、"
    "`support_level: direct_answer|source_summary`，以及原会话可访问时的私有 `source_locator`（canonical `/c/<UUID>` URL）"
)


def update_text(text: str) -> str:
    for old, new in ((OLD_DETAIL, NEW_DETAIL), (OLD_REF, NEW_REF)):
        if text.count(old) != 1:
            raise RuntimeError(f"expected prompt fragment exactly once: {old}")
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
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = args.backup_dir / (
            f"archive-chatgpt-daily-report-automation-before-conversation-id-{stamp}.toml"
        )
        shutil.copy2(args.automation, backup)
        temporary = args.automation.with_name(f".{args.automation.name}.{os.getpid()}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, args.automation)
        print(f"backup: {backup}")
    print("updated" if not args.dry_run else "dry-run-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
