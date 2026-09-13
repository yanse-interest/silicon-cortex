#!/usr/bin/env python3
"""Build a privacy-safe H5 snapshot without mutating canonical Memory state."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dashboard_model import CaseRegistry, frontmatter_value
from dashboard_refresh_support import REGISTRY_PATH, WIKI_ROOT
from daily_deposition_receipt import source_deposition_state
from export_h5_snapshot import atomic_write, build_h5_snapshot
from recipe_library import build_recipe_collection


SOURCE_TYPES = {
    "chatgpt-daily": "chatgpt_daily_source_summary",
    "codex-daily": "codex_daily_source_summary",
}
VALID_STATUSES = {"access_incomplete", "no_tasks", "ready", "complete", "completed", "valid"}
PROHIBITED_KEYS = {
    "case_id", "event_id", "task_id", "thread_id", "turn_id", "branch_id",
    "capability_domain_id", "candidate_id", "value_id", "source_locator", "excerpt",
    "category", "line", "id", "session_id",
}
PROHIBITED_TEXT = (
    re.compile(r"/Users/"),
    re.compile(r"raw/conversations", re.I),
    re.compile(r"dashboard-token|authorization|bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"(?:case|event|thread|turn)-[0-9a-f]{8,}", re.I),
    re.compile(r"\b(?:case_id|event_id|task_id|thread_id|turn_id|candidate_id|value_id)\b", re.I),
    re.compile(r"\b(?:prompt|reasoning)\b", re.I),
)


class PublicSnapshotError(RuntimeError):
    pass


def _source_date(path: Path, text: str, family: str) -> str:
    match = re.fullmatch(rf"{re.escape(family)}-report-(\d{{4}}-\d{{2}}-\d{{2}})\.md", path.name)
    if not match:
        return ""
    date = frontmatter_value(text, "date")
    expected_type = SOURCE_TYPES[family]
    status = frontmatter_value(text, "status").lower()
    if date != match.group(1) or frontmatter_value(text, "type") != expected_type or status not in VALID_STATUSES:
        return ""
    return date


def _copy_valid_sources(vault: Path) -> dict[str, set[str]]:
    dates: dict[str, set[str]] = {family: set() for family in SOURCE_TYPES}
    canonical_root = WIKI_ROOT / "sources/conversations"
    for family in SOURCE_TYPES:
        for path in sorted((canonical_root / family).glob("*/*.md")):
            text = path.read_text(encoding="utf-8")
            date = _source_date(path, text, family)
            if not date:
                continue
            # Opening the H5 only rebuilds from canonical sources whose end-of-
            # daily extraction and deposition has already completed.  It never
            # performs extraction or deposition itself.
            if not source_deposition_state(path, WIKI_ROOT.parent)["ready"]:
                continue
            target = vault / "wiki/sources/conversations" / family / date[:4] / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            dates[family].add(date)

            # ChatGPT's only compatibility fallback is the exact, date/type
            # attested immutable raw report. Copy that one file into staging.
            if family == "chatgpt-daily":
                raw_rel = frontmatter_value(text, "raw_source").strip("\"'")
                expected = f"raw/conversations/chatgpt-daily/{date[:4]}/chatgpt-daily-report-{date}.md"
                raw = WIKI_ROOT.parent / raw_rel
                if raw_rel == expected and raw.is_file():
                    raw_text = raw.read_text(encoding="utf-8")
                    if frontmatter_value(raw_text, "type") == "chatgpt_daily_report" and frontmatter_value(raw_text, "date") == date:
                        raw_target = vault / expected
                        raw_target.parent.mkdir(parents=True, exist_ok=True)
                        raw_target.write_text(raw_text, encoding="utf-8")
    return dates


def validate_public_snapshot(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 3:
        raise PublicSnapshotError("unsupported public snapshot schema")
    if not str(payload.get("generated_at") or ""):
        raise PublicSnapshotError("missing generation timestamp")

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            leaked = PROHIBITED_KEYS.intersection(value)
            if leaked:
                raise PublicSnapshotError("public snapshot contains prohibited keys")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and any(pattern.search(value) for pattern in PROHIBITED_TEXT):
            raise PublicSnapshotError("public snapshot contains prohibited text")

    walk(payload)
    for domain in payload.get("capability_domains") or []:
        for item in domain.get("items") or []:
            if item.get("answer_status") == "source_grounded" and (
                not item.get("answer") or not item.get("evidence_refs")
            ):
                raise PublicSnapshotError("source-grounded answer lacks public evidence")


def generate_fresh_snapshot() -> dict[str, Any]:
    """Reconcile only staged validated sources; canonical files remain untouched."""
    with tempfile.TemporaryDirectory(prefix="dashboard-h5-open-") as temporary:
        vault = Path(temporary)
        wiki = vault / "wiki"
        wiki.mkdir(parents=True)
        shutil.copy2(REGISTRY_PATH, wiki / REGISTRY_PATH.name)
        dates = _copy_valid_sources(vault)
        common_dates = dates["chatgpt-daily"].intersection(dates["codex-daily"])
        store = CaseRegistry(
            wiki_root=wiki,
            registry_path=wiki / REGISTRY_PATH.name,
            reviews_root=WIKI_ROOT / "reviews",
            daily_root=wiki / "sources/conversations/chatgpt-daily",
            codex_daily_root=wiki / "sources/conversations/codex-daily",
            live_state_path=vault / "disabled-live-state.json",
        )
        payload = build_h5_snapshot(
            # _copy_valid_sources above has already checked every staged input;
            # recipe reconciliation reads the canonical completed-receipt set so
            # it can verify receipt bindings without copying private receipts.
            {**store.snapshot(), "recipes": build_recipe_collection(source_root=WIKI_ROOT / "sources/conversations", memory_root=WIKI_ROOT.parent)},
            synced_through=max(common_dates, default=""),
        )
        validate_public_snapshot(payload)
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = generate_fresh_snapshot()
        if args.output:
            atomic_write(args.output, payload)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2))
        return 0
    except Exception:
        # This command is consumed by the local gateway. Never serialize local
        # paths, parser details, or source content into its error channel.
        print(json.dumps({"ok": False, "error": "fresh_snapshot_unavailable"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
