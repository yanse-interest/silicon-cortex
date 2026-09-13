#!/usr/bin/env python3
"""Correct the 2026-08-03 cross-project daily routing and record the durable rule."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_log_compat import insert_memory_log_entry


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parents[2]
VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
REGISTRY = VAULT / "wiki" / "project-dashboard-case-registry.json"
ROUTER_CASE = "case-e652ced6fd788ff2"
DEPOSITION_CASE = "case-db88755f08e823fe"


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry.setdefault("source_policy", {})["allow_multi_project_routing"] = True
    cases = {str(case.get("case_id")): case for case in registry.get("cases", [])}

    router = cases[ROUTER_CASE]
    router["routing_terms"] = [
        "账号路由", "账号切换", "自动化治理", "固定账号", "account-1",
        "auto-off", "no-switch", "自动切换", "切号", "门禁",
    ]
    router["current_summary"] = (
        "核心路由器已交付到 v0.7.x 并保持 account-1、auto off；"
        "本地 H5 的 07:30 刷新也已纳入 fixed account-1/auto-off/no-switch 与双日报门禁，"
        "失败保留上一版且不切号、不回退账号 2。"
    )

    deposition = cases[DEPOSITION_CASE]
    deposition["current_summary"] = (
        "ChatGPT 与 Codex 日报已按独立 raw/source 线路归档并进入统一沉淀；"
        "2026-08-03 两份日报均已补齐为 partial/access_incomplete，项目看板通过双 source 门禁后"
        "生成脱敏 H5 快照，并保留 Review Cycle、stable-ID 去重与证据边界。"
    )
    timestamp = now_iso()
    router["updated_at"] = timestamp
    deposition["updated_at"] = timestamp
    registry["updated_at"] = timestamp
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sys.path.insert(0, str(PROJECT_ROOT))
    from dashboard_model import CaseRegistry

    refreshed = CaseRegistry(wiki_root=VAULT / "wiki", registry_path=REGISTRY).refresh()
    target = next(
        event for event in refreshed["events"]
        if event.get("event_id") == "codex-daily:2026-08-03:T01"
    )
    routed = {str(target.get("case_id")), *[str(value) for value in target.get("additional_case_ids") or []]}
    if routed != {ROUTER_CASE, DEPOSITION_CASE}:
        raise RuntimeError(f"unexpected T01 routing: {sorted(routed)}")

    entry = """## [2026-08-04] update | 日报任务修正为知识沉淀与账号路由双项目\n\n- 用户确认 2026-08-03 的日报/H5 工作同时推进“ChatGPT 日报、Review Cycle 与 Cognitive Observatory 沉淀系统”和“Codex 账号路由器与自动化治理”；此前项目路由只落到沉淀项目，导致 H5 视觉上近似无变化。\n- 修正 Codex daily 的跨项目路由：候选匹配纳入目标与下一步，受控开启一个任务关联多个项目；移除 Router 过宽的 `codex` 关键词，改用固定账号、account-1、auto-off/no-switch、切号与门禁等明确术语。2026-08-03 T01 现同时归入上述两个项目。\n- H5 新增“本次日报带来的变化”，按真实最新事件排序，明确展示 ChatGPT/Codex 各自产生的项目增量；原始日报、secret、内部稳定 ID 和写接口仍不进入手机快照。\n\n"""
    insert_memory_log_entry(VAULT, entry)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
