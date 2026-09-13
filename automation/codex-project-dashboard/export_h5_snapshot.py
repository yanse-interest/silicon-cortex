#!/usr/bin/env python3
"""Export the dashboard into a read-only, hosting-safe H5 snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from dashboard_refresh_support import (
    DEFAULT_CONFIG,
    REGISTRY_PATH,
    WIKI_ROOT,
    account_guard,
    daily_source_gate,
    load_config,
    previous_day_source_gate,
    snapshot,
)
from dashboard_contiguous_catchup import run_contiguous_catchup
from registry_shadow_compare import run_shadow_compare


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR.parent / "codex-project-dashboard-h5" / "public" / "dashboard-snapshot.json"
MEMORY_ROOT = WIKI_ROOT.parent
STAGE1_RECEIPT = (
    WIKI_ROOT
    / "review-cycles/registry-shadow-split/project-dashboard-registry-shadow-split-post-capability-fix-2026-08-28.json"
)
STAGE2_RECEIPT_ROOT = WIKI_ROOT / "review-cycles/registry-shadow-compare"
REFRESH_RECEIPT_ROOT = WIKI_ROOT / "review-cycles/dashboard-refresh"

PUBLIC_TEXT_REPLACEMENTS = (
    (re.compile(r"\bthread_id\s*\+\s*turn_id\b", re.I), "内部任务引用"),
    (re.compile(r"\b(?:case_id|event_id|task_id|thread_id|turn_id|candidate_id|value_id)\b", re.I), "内部引用"),
    (re.compile(r"\bprompt\b", re.I), "模板"),
    (re.compile(r"\breasoning\b", re.I), "分析过程"),
    (re.compile(r"\bcanonical\s+raw\b", re.I), "已验证来源"),
)


def clean_text(value: Any, limit: int = 5000) -> str:
    text = str(value or "").strip()
    for pattern, replacement in PUBLIC_TEXT_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def source_label(value: Any) -> str:
    raw = clean_text(value).lower()
    if "codex" in raw:
        return "Codex"
    if "chatgpt" in raw or "weekly-review" in raw:
        return "ChatGPT"
    if "compliance" in raw or "abstract" in raw:
        return "合规抽象"
    if "manual" in raw:
        return "人工"
    return "Memory"


def project_log(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": clean_text(item.get("last_date") or item.get("date") or item.get("period")),
        "first_date": clean_text(item.get("first_date") or item.get("date") or item.get("period")),
        "member_count": int(item.get("member_count") or 1),
        "title": clean_text(item.get("title"), 180),
        "detail": clean_text(item.get("detail"), 2000),
        "source": source_label(item.get("source")),
        "evidence_type": clean_text(item.get("evidence_type"), 80),
    }


def value_item(item: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = [
        {
            "source_type": clean_text(ref.get("source_type"), 40),
            "source_date": clean_text(ref.get("source_date") or item.get("date"), 32),
            "session_ref": clean_text(ref.get("session_id"), 24),
            "session_title": clean_text(ref.get("session_title"), 180),
            "support_level": clean_text(ref.get("support_level"), 40),
        }
        for ref in item.get("evidence_refs") or []
        if isinstance(ref, dict)
    ]
    answer_status = clean_text(item.get("answer_status"), 40)
    if item.get("question") and answer_status != "source_grounded":
        answer_status = "needs_source_review"
    return {
        "date": clean_text(item.get("date") or item.get("period")),
        "topic": clean_text(item.get("topic"), 80),
        "instrument_types": [clean_text(value, 80) for value in item.get("instrument_types") or [] if clean_text(value, 80)],
        "question": clean_text(item.get("question"), 500),
        "answer": clean_text(item.get("answer"), 4000) if answer_status != "needs_source_review" else "",
        "answer_status": answer_status,
        "answer_origin": clean_text(item.get("answer_origin"), 40),
        "evidence_refs": evidence_refs,
        "evidence_boundary": clean_text(item.get("evidence_boundary"), 1000),
        "detail": clean_text(item.get("detail"), 2000) if answer_status != "needs_source_review" else "",
        "source": source_label(item.get("source") or item.get("source_family")),
        "evidence_type": clean_text(item.get("evidence_type") or item.get("evidence_mode"), 80),
    }


def project_case(item: dict[str, Any]) -> dict[str, Any]:
    # Hook live-state is a routing signal, not a public activity feed. Only
    # formally landed daily/Memory/manual records enter the H5 payload.
    logs = [
        project_log(log)
        for log in item.get("evidence_units") or item.get("logs") or []
        if str(log.get("source_kind") or "") != "codex_live"
    ]
    suggested_items = [
        project_log(log)
        for log in item.get("suggested_evidence_units") or []
        if str(log.get("source_kind") or "") != "codex_live"
    ]
    values = [value_item(value) for value in item.get("value_items") or []]
    open_items = [clean_text(entry.get("text"), 1000) for entry in item.get("open_items") or []]
    related_items = [project_log(entry) for entry in item.get("related_events") or []]
    raw_latest_progress = item.get("latest_daily_progress") or {}
    latest_progress_items = [
        project_log(entry)
        for entry in raw_latest_progress.get("items") or []
        if isinstance(entry, dict)
        and entry.get("state") == "routed"
        and entry.get("source_kind") in {"daily", "codex_daily"}
    ]
    latest_daily_progress = {
        "date": clean_text(raw_latest_progress.get("date"), 32) if latest_progress_items else "",
        "items": latest_progress_items,
    }
    source_mix = [
        {
            "label": clean_text(source.get("label") or source.get("family"), 80),
            "progress_count": int(source.get("progress_count") or 0),
            "value_count": int(source.get("value_count") or 0),
            "latest_date": clean_text(source.get("latest_date"), 32),
        }
        for source in item.get("source_mix") or []
    ]
    updated_at = max(
        [
            clean_text(item.get("updated_at"), 64),
            *[log["date"] for log in logs],
            latest_daily_progress["date"],
        ],
        default="",
    )
    return {
        "title": clean_text(item.get("title"), 240),
        "category_label": clean_text(item.get("category_label"), 120),
        "status": clean_text(item.get("status"), 48),
        "status_label": clean_text(item.get("status_label"), 80),
        "current_summary": clean_text(item.get("current_summary"), 3000),
        "latest_daily_progress": latest_daily_progress,
        "next_step": clean_text(item.get("next_step"), 3000),
        # Registry metadata can lag behind automatically routed daily events.
        # Use the newest visible log date so H5 recency reflects actual content.
        "updated_at": updated_at,
        "evidence_mode": clean_text(item.get("evidence_mode"), 80),
        "log_count": len(logs),
        "raw_log_count": int(item.get("log_count") or len(logs)),
        "suggested_count": len(suggested_items),
        "value_count": int(item.get("value_count") or len(values)),
        "open_item_count": int(item.get("open_item_count") or len(open_items)),
        "related_count": int(item.get("related_count") or 0),
        "logs": logs,
        "suggested_items": suggested_items,
        "value_items": values,
        "open_items": open_items,
        "related_items": related_items,
        "source_mix": source_mix,
    }


def branch(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": clean_text(item.get("title"), 180),
        "status": clean_text(item.get("status"), 48),
        "current_summary": clean_text(item.get("current_summary"), 3000),
        "progress_count": int(item.get("progress_count") or 0),
        "related_count": int(item.get("related_count") or 0),
        "value_count": int(item.get("value_count") or 0),
        "progress_events": [project_log(entry) for entry in item.get("progress_events") or []],
        "related_questions": [project_log(entry) for entry in item.get("related_questions") or []],
        "value_items": [value_item(entry) for entry in item.get("value_items") or []],
    }


def capability_domain(item: dict[str, Any]) -> dict[str, Any]:
    values = []
    for entry in item.get("items") or []:
        value = value_item(entry)
        value["capability_tags"] = [
            clean_text(tag, 80)
            for tag in (entry.get("capability_tags") or entry.get("instrument_types") or [])
            if clean_text(tag, 80)
        ]
        values.append(value)
    return {
        "title": clean_text(item.get("title"), 180),
        "summary": clean_text(item.get("summary"), 3000),
        "tag_label": clean_text(item.get("tag_label"), 80),
        "item_count": int(item.get("item_count") or len(values)),
        "items": values,
    }


def recipe(item: dict[str, Any]) -> dict[str, Any]:
    ingredients = []
    for value in item.get("ingredients") or []:
        if not isinstance(value, dict):
            continue
        amount = value.get("amount")
        ingredients.append({
            "name": clean_text(value.get("name"), 180),
            "role": clean_text(value.get("role"), 16),
            "group": clean_text(value.get("group"), 80),
            "amount": amount if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None,
            "quantity_text": clean_text(value.get("quantity_text"), 80),
            "unit": clean_text(value.get("unit"), 32),
            "package_spec": clean_text(value.get("package_spec"), 120),
            "amount_status": clean_text(value.get("amount_status"), 32),
        })
    return {
        "title": clean_text(item.get("title"), 180), "variant": clean_text(item.get("variant"), 120),
        "ingredients": [value for value in ingredients if value["name"]],
        "steps": [clean_text(value, 800) for value in item.get("steps") or [] if clean_text(value, 800)],
        "method_status": clean_text(item.get("method_status"), 32),
        "status": clean_text(item.get("status"), 40),
        "source_dates": sorted({clean_text(ref.get("source_date"), 32) for ref in item.get("source_refs") or [] if clean_text(ref.get("source_date"), 32)}, reverse=True),
        "sources": [{
            "type": clean_text(ref.get("source_type"), 48),
            "name": clean_text(ref.get("source_name") or ref.get("session_title"), 180),
            "url": clean_text(ref.get("source_url"), 500),
            "row_id": int(ref["source_row_id"]) if isinstance(ref.get("source_row_id"), int) else None,
        } for ref in item.get("source_refs") or [] if isinstance(ref, dict)],
        "notes": [clean_text(note, 3000) for note in item.get("notes") or [] if clean_text(note, 3000)],
    }


def build_h5_snapshot(data: dict[str, Any], *, synced_through: str = "") -> dict[str, Any]:
    summary = data.get("summary") or {}
    work_groups = {
        clean_text(key, 80): [project_case(case) for case in cases]
        for key, cases in (data.get("work_groups") or {}).items()
    }
    life_cases = [project_case(item) for item in data.get("life_cases") or []]
    closed_cases = [project_case(item) for item in data.get("closed_cases") or []]
    refresh_date = clean_text(synced_through or data.get("daily_updated_through"), 32)
    recent_updates: list[dict[str, Any]] = []
    for cases in [*work_groups.values(), life_cases, closed_cases]:
        for case in cases:
            for log in case["logs"]:
                if log["date"] == refresh_date:
                    recent_updates.append({
                        "scope": case["title"],
                        **log,
                    })
    recent_updates.sort(key=lambda item: (item["date"], item["scope"], item["title"]), reverse=True)
    source_counts = {"ChatGPT": 0, "Codex": 0, "Memory": 0, "合规抽象": 0, "人工": 0}
    for item in recent_updates:
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1

    return {
        "schema_version": 3,
        "generated_at": clean_text(data.get("generated_at"), 64),
        # This label describes the completed two-source refresh gate, not the
        # latest routable event. A valid daily source can contain no dashboard
        # event (for example partial metadata-only ChatGPT evidence), so event
        # dates alone can legitimately lag behind the completed refresh date.
        "daily_updated_through": refresh_date,
        "daily_digest": {
            "date": refresh_date,
            "source_counts": source_counts,
            "items": recent_updates,
        },
        "summary": {
            "branch_count": int(summary.get("branch_count") or 0),
            "case_count": int(summary.get("case_count") or 0),
            "active_case_count": int(summary.get("active_case_count") or 0),
            "chatgpt_project_count": int(summary.get("chatgpt_project_count") or 0),
            "codex_project_count": int(summary.get("codex_project_count") or 0),
            "value_item_count": int(summary.get("value_item_count") or 0),
            "work_case_count": int(summary.get("work_case_count") or 0),
            "life_case_count": int(summary.get("life_case_count") or 0),
        },
        "branches": [branch(item) for item in data.get("branches") or []],
        "capability_domains": [capability_domain(item) for item in data.get("capability_domains") or []],
        "recipes": [recipe(item) for item in data.get("recipes") or []],
        "work_sections": [
            {
                "label": next(
                    (
                        clean_text(category.get("label"), 120)
                        for category in data.get("categories") or []
                        if clean_text(category.get("id"), 80) == key
                    ),
                    "工作项目",
                ),
                "projects": projects,
            }
            for key, projects in work_groups.items()
        ],
        "life_cases": life_cases,
        "closed_cases": closed_cases,
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_export(*, config_path: Path, output: Path, enforce_gates: bool = True) -> dict[str, Any]:
    config = load_config(config_path)
    route = account_guard(config.get("account_policy") or {}) if enforce_gates else None
    if enforce_gates:
        result = run_contiguous_catchup(
            output_path=output,
            registry_path=REGISTRY_PATH,
            wiki_root=WIKI_ROOT,
            memory_root=MEMORY_ROOT,
            source_root=WIKI_ROOT / "sources/conversations",
            stage1_receipt_path=STAGE1_RECEIPT,
            stage2_root=STAGE2_RECEIPT_ROOT,
            refresh_receipt_root=REFRESH_RECEIPT_ROOT,
            h5_builder=build_h5_snapshot,
            source_gate=lambda target: daily_source_gate(target),
            snapshot_fn=snapshot,
            shadow_compare_fn=run_shadow_compare,
            atomic_h5_writer=atomic_write,
        )
        result["route"] = route
        return result

    # Ungated export remains a local test/preview helper. Production always uses
    # the derived contiguous catch-up transaction above.
    source_gate = previous_day_source_gate()
    canonical_snapshot = snapshot()
    payload = build_h5_snapshot(
        canonical_snapshot,
        synced_through="",
    )
    atomic_write(output, payload)
    return {
        "ok": True,
        "skipped": False,
        "routing_status": "test_override",
        "route": route,
        "source_gate": source_gate,
        "output": str(output),
        "generated_at": payload["generated_at"],
        "case_count": payload["summary"]["case_count"],
        "value_item_count": payload["summary"]["value_item_count"],
        "shadow_compare": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a hosting-safe read-only dashboard snapshot.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-gates", action="store_true", help="Only for local tests and previews.")
    args = parser.parse_args()
    try:
        result = run_export(config_path=args.config, output=args.output, enforce_gates=not args.skip_gates)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "routing_status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
