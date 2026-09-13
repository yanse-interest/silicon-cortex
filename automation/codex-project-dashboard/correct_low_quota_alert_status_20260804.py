#!/usr/bin/env python3
"""Mark the already-delivered low-quota alert as complete across Memory views."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_log_compat import insert_memory_log_entry


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
CASE_ID = "case-e652ced6fd788ff2"
STALE_OPEN = "实现用户可见的低额度主动提醒，指出低额度账号、可接力账号和安全切换入口。"
RENEWAL_OPEN = "官方更新后按 Bundle ID、Team ID、签名、双向 smoke 和回到原账号做续批，不自动开启 auto。"
CURRENT = (
    "核心路由器已交付到 v0.7.x 并保持 account-1、auto off；低额度主动提醒已在 0.5.0/0.5.4 "
    "交付，可指出低额度账号、可接力账号与剩余额度，并提供用户触发的安全切换入口；系统不自动切换账号。"
)


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"expected text missing: {path}: {old[:80]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    decisions = WIKI / "decisions.md"
    old_decision = (
        "- 2026-07-12 补充决策：下一版必须把 `effective_remaining <= 2%` 实现为用户可见的主动提醒，而不只是内部路由阈值；"
        "提醒需指出低额度账号、可接力账号及其剩余额度，并提供安全切换入口。该能力是下一版发布阻断项，未实现时不得把产品描述为已完成自动路由闭环。"
    )
    corrected_decision = (
        old_decision
        + "\n- 2026-08-04 取代说明：上述主动提醒已在 Router `0.5.0` / 菜单 App `0.5.4` 交付，"
          "不再是开发开放项。当前行为固定为“主动提醒 + 用户触发安全切换入口”；`auto off` 保持不变，系统不得自动切换账号。"
    )
    replace_once(decisions, old_decision, corrected_decision)

    manifest_path = WIKI / "reviews" / "monthly-case-manifest-2026-07.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_case = next(case for case in manifest["cases"] if case.get("case_id") == CASE_ID)
    manifest_case["current_progress"] = CURRENT
    manifest_case["open_items"] = [
        item for item in manifest_case.get("open_items") or []
        if str(item.get("text") or "").strip() != STALE_OPEN
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    registry_path = WIKI / "project-dashboard-case-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_case = next(case for case in registry["cases"] if case.get("case_id") == CASE_ID)
    registry_case["current_summary"] = CURRENT
    registry_case["next_step"] = RENEWAL_OPEN
    registry_case["updated_at"] = now_iso()
    registry["updated_at"] = registry_case["updated_at"]
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review_path = WIKI / "reviews" / "monthly-review-2026-07.md"
    review_text = review_path.read_text(encoding="utf-8")
    stale_progress = (
        "- 当前进度：核心路由器已多次交付到 v0.7.x，日报来源 policy、唯一执行面和版本治理已进入 Memory；"
        "仍保留低额度提醒、官方更新续批、退出确认和后续生产批准边界。（已报告）"
    )
    review_text = review_text.replace(stale_progress, f"- 当前进度：{CURRENT}（已报告）", 1)
    review_text = review_text.replace(f"- {STALE_OPEN} [[wiki/decisions|来源]]\n", "", 1)
    review_path.write_text(review_text, encoding="utf-8")

    title = "## [2026-08-04] update | Router 低额度提醒状态纠正为已交付"
    entry = (
        f"{title}\n\n"
        "- 用户确认低额度主动提醒、低额度账号/可接力账号提示和安全切换入口在既有版本中已经交付，不再作为开发开放项。\n"
        "- 当前产品边界保持为主动提醒与用户手动触发安全切换；`auto off` 不变，系统不自动切换账号。"
        "7 月 manifest、月度 review、项目 registry 与旧决策取代说明已同步纠正。\n\n"
    )
    insert_memory_log_entry(VAULT, entry)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
