#!/usr/bin/env python3
"""Deterministic, production-isolated historical replay evidence driver."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from dashboard_model import CaseRegistry, atomic_write_json
from dashboard_refresh_support import RECEIPT_REQUIRED_FROM, daily_source_gate
from export_h5_snapshot import build_h5_snapshot
from registry_migration_gate import DEFAULT_GATE_ROOT, ENFORCEMENT_VERSION, publish_gate_summary
from registry_shadow_compare import run_shadow_compare
from registry_shadow_split import aggregate_path_hash, sha256_bytes, split_registry


TZ = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1
TOOL_ID = "project-dashboard-registry-historical-replay"


class HistoricalReplayError(RuntimeError):
    """Fail-closed historical replay error."""


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_pretty_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _aggregate(paths: dict[str, Path]) -> dict[str, str]:
    return {label: aggregate_path_hash(path) for label, path in sorted(paths.items())}


def _apply_private(record: dict[str, Any], overlays: list[dict[str, Any]]) -> None:
    for overlay in overlays:
        path = overlay.get("path")
        if not isinstance(path, list) or not path:
            raise HistoricalReplayError("invalid private overlay path")
        cursor: Any = record
        for part in path[:-1]:
            if isinstance(part, int):
                if not isinstance(cursor, list) or part >= len(cursor):
                    raise HistoricalReplayError("private overlay list path is absent")
                cursor = cursor[part]
            else:
                if not isinstance(cursor, dict):
                    raise HistoricalReplayError("private overlay object path is absent")
                cursor = cursor.setdefault(str(part), {})
        final = path[-1]
        if isinstance(final, int):
            if not isinstance(cursor, list) or final >= len(cursor):
                raise HistoricalReplayError("private overlay list target is absent")
            cursor[final] = copy.deepcopy(overlay.get("value"))
        elif isinstance(cursor, dict):
            cursor[str(final)] = copy.deepcopy(overlay.get("value"))
        else:
            raise HistoricalReplayError("private overlay target is absent")


def _source_date(record: dict[str, Any]) -> str:
    return str(record.get("date") or record.get("period") or "")[:10]


def reconstruct_registry_to_cutoff(
    *,
    canonical_registry: dict[str, Any],
    canonical_registry_sha256: str,
    cutoff: str,
    scratch_registry_path: Path,
    wiki_root: Path,
    memory_root: Path,
) -> dict[str, Any]:
    """Rebuild source-owned state through ``cutoff`` and reapply stable manual overlays."""
    manual, _, _ = split_registry(canonical_registry, canonical_registry_sha256)
    manual_state = manual.get("state") or {}
    baseline = copy.deepcopy(canonical_registry)
    baseline["events"] = [
        copy.deepcopy(item)
        for item in manual_state.get("manual_events") or []
        if not _source_date(item) or _source_date(item) <= cutoff
    ]
    baseline["value_candidates"] = []
    for case in baseline.get("cases") or []:
        if isinstance(case, dict):
            case["value_items"] = []
    scratch_registry_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(scratch_registry_path, baseline)

    store = CaseRegistry(
        wiki_root=wiki_root,
        registry_path=scratch_registry_path,
        reviews_root=wiki_root / "reviews",
        daily_root=wiki_root / "sources/conversations/chatgpt-daily",
        codex_daily_root=wiki_root / "sources/conversations/codex-daily",
        live_state_path=scratch_registry_path.parent / "disabled-live-state.json",
        require_deposition_receipts=True,
        memory_root=memory_root,
        source_through_date=cutoff,
    )
    rebuilt = store.refresh()

    event_overlays = {
        str(item.get("event_id") or ""): item for item in manual_state.get("event_overlays") or []
    }
    for event in rebuilt.get("events") or []:
        overlay = event_overlays.get(str(event.get("event_id") or ""))
        if overlay:
            event.update(copy.deepcopy(overlay.get("manual_fields") or {}))
            _apply_private(event, overlay.get("private_overlays") or [])

    candidate_overlays = {
        str(item.get("candidate_id") or ""): item
        for item in manual_state.get("value_candidate_overlays") or []
    }
    for candidate in rebuilt.get("value_candidates") or []:
        overlay = candidate_overlays.get(str(candidate.get("candidate_id") or ""))
        if overlay:
            candidate.update(copy.deepcopy(overlay.get("manual_fields") or {}))
            _apply_private(candidate, overlay.get("private_overlays") or [])

    value_routes = {
        (str(item.get("case_id") or ""), str(item.get("value_id") or "")): item
        for item in manual_state.get("case_value_routes") or []
    }
    for case in rebuilt.get("cases") or []:
        case_id = str(case.get("case_id") or "")
        for value in case.get("value_items") or []:
            overlay = value_routes.get((case_id, str(value.get("value_id") or "")))
            if overlay:
                value.update(copy.deepcopy(overlay.get("manual_fields") or {}))
                _apply_private(value, overlay.get("private_overlays") or [])
        if not case.get("value_items"):
            case.pop("value_items", None)
    atomic_write_json(scratch_registry_path, rebuilt)
    return rebuilt


def _validated_bindings(gate: dict[str, Any], *, memory_root: Path) -> dict[str, Any]:
    if gate.get("ready") is not True or set(gate.get("inputs") or {}) != {"chatgpt", "codex"}:
        raise HistoricalReplayError("daily source/raw/receipt gate is not ready")
    result: dict[str, Any] = {}
    for family, details in sorted((gate.get("inputs") or {}).items()):
        required = ("source_path", "source_sha256", "raw_path", "raw_sha256", "receipt_path", "receipt_sha256")
        if any(not details.get(key) for key in required):
            raise HistoricalReplayError(f"{family} lacks immutable source/raw/completed-receipt binding")
        compact: dict[str, Any] = {}
        for path_key, hash_key in (("source_path", "source_sha256"), ("raw_path", "raw_sha256"), ("receipt_path", "receipt_sha256")):
            path = Path(str(details[path_key]))
            if not path.is_file() or sha256_bytes(path.read_bytes()) != details[hash_key]:
                raise HistoricalReplayError(f"{family} {path_key} hash mismatch")
            try:
                compact[path_key] = path.resolve().relative_to(memory_root.resolve()).as_posix()
            except ValueError:
                raise HistoricalReplayError(f"{family} binding escapes Memory root")
            compact[hash_key] = details[hash_key]
        compact["candidate_count"] = int(details.get("candidate_count") or 0)
        result[family] = compact
    generation = hashlib.sha256(
        json.dumps(gate.get("inputs") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if generation != gate.get("source_generation_sha256"):
        raise HistoricalReplayError("source generation hash mismatch")
    return result


def run_historical_replay(
    *,
    source_date: str,
    registry_path: Path,
    wiki_root: Path,
    memory_root: Path,
    source_root: Path,
    stage1_receipt_path: Path,
    h5_snapshot_path: Path,
    gate_root: Path = DEFAULT_GATE_ROOT,
    scratch_parent: Path = Path("/private/tmp"),
    shadow_compare_fn: Callable[..., dict[str, Any]] = run_shadow_compare,
) -> dict[str, Any]:
    parsed = date.fromisoformat(source_date)
    if parsed.isoformat() != source_date:
        raise HistoricalReplayError("source date must be canonical YYYY-MM-DD")
    gate = daily_source_gate(source_date, source_root=source_root)
    bindings = _validated_bindings(gate, memory_root=memory_root)
    canonical_guards = {
        "registry": registry_path,
        "h5_snapshot": h5_snapshot_path,
        "raw": memory_root / "raw",
        "sources": wiki_root / "sources",
        "daily_receipts": wiki_root / "review-cycles/daily-deposition",
        "stage1_receipt": stage1_receipt_path,
        "legacy_stage2_streak": wiki_root / "review-cycles/registry-shadow-compare/stage-2-streak.json",
    }
    guard_before = _aggregate(canonical_guards)
    canonical_bytes = registry_path.read_bytes()
    canonical_sha = sha256_bytes(canonical_bytes)
    canonical_registry = json.loads(canonical_bytes)
    scratch_root = Path(tempfile.mkdtemp(prefix=f"registry-historical-replay-{source_date}-", dir=scratch_parent))
    comparison: dict[str, Any] = {}
    reconstruction_sha = ""
    try:
        scratch_registry = scratch_root / "wiki/project-dashboard-case-registry.json"
        rebuilt = reconstruct_registry_to_cutoff(
            canonical_registry=canonical_registry,
            canonical_registry_sha256=canonical_sha,
            cutoff=source_date,
            scratch_registry_path=scratch_registry,
            wiki_root=wiki_root,
            memory_root=memory_root,
        )
        reconstruction_sha = sha256_bytes(scratch_registry.read_bytes())
        scratch_h5 = scratch_root / "dashboard-snapshot.json"
        scratch_h5.write_text("{}\n", encoding="utf-8")
        comparison = shadow_compare_fn(
            registry_path=scratch_registry,
            wiki_root=wiki_root,
            memory_root=memory_root,
            stage1_receipt_path=stage1_receipt_path,
            output_root=scratch_root / "compare-receipts",
            h5_snapshot_path=scratch_h5,
            h5_builder=build_h5_snapshot,
            synced_through=source_date,
            observation_id=f"historical-replay:{source_date}:{gate['source_generation_sha256'][:16]}",
            guard_paths={
                "raw": memory_root / "raw",
                "sources": wiki_root / "sources",
                "daily_receipts": wiki_root / "review-cycles/daily-deposition",
            },
        )
        compare_receipt = json.loads(Path(comparison["receipt_path"]).read_text(encoding="utf-8"))
        if compare_receipt.get("result") != "passed":
            raise HistoricalReplayError("isolated shadow comparison failed")
        replay_metrics = {
            "scratch_registry_sha256": reconstruction_sha,
            "scratch_registry_version": rebuilt.get("version"),
            "event_count": len(rebuilt.get("events") or []),
            "value_candidate_count": len(rebuilt.get("value_candidates") or []),
            "stage2_compare_receipt_sha256": sha256_bytes(Path(comparison["receipt_path"]).read_bytes()),
            "registry_parity": compare_receipt.get("registry_parity"),
            "dashboard_snapshot_parity": compare_receipt.get("dashboard_snapshot_parity"),
            "h5_snapshot_parity": compare_receipt.get("h5_snapshot_parity"),
            "privacy_checks": compare_receipt.get("privacy_checks"),
        }
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    guard_after = _aggregate(canonical_guards)
    unchanged = guard_before == guard_after
    checks = {
        "two_source_generation_ready": gate.get("ready") is True,
        "immutable_source_raw_completed_receipt_bindings": set(bindings) == {"chatgpt", "codex"},
        "scratch_source_cutoff_reconstruction": bool(reconstruction_sha),
        "registry_semantic_and_protected_parity": bool(replay_metrics["registry_parity"].get("protected_field_parity"))
            and replay_metrics["registry_parity"].get("semantic_sha256", {}).get("canonical")
            == replay_metrics["registry_parity"].get("semantic_sha256", {}).get("recomposed"),
        "dashboard_snapshot_parity": replay_metrics["dashboard_snapshot_parity"].get("semantic_sha256", {}).get("canonical_registry_snapshot")
            == replay_metrics["dashboard_snapshot_parity"].get("semantic_sha256", {}).get("recomposed_registry_snapshot"),
        "h5_snapshot_parity": replay_metrics["h5_snapshot_parity"].get("semantic_sha256", {}).get("canonical_registry_h5")
            == replay_metrics["h5_snapshot_parity"].get("semantic_sha256", {}).get("recomposed_registry_h5"),
        "privacy": replay_metrics["privacy_checks"].get("nested_private_locator_confinement") == "passed"
            and replay_metrics["privacy_checks"].get("h5_public_whitelist") == "passed",
        "canonical_inputs_unchanged": unchanged,
        "scratch_cleanup": not scratch_root.exists(),
        "legacy_stage2_streak_unchanged": guard_before["legacy_stage2_streak"] == guard_after["legacy_stage2_streak"],
    }
    if not all(checks.values()):
        raise HistoricalReplayError(f"historical replay checks failed: {[key for key, value in checks.items() if not value]}")
    basis = {
        "tool_id": TOOL_ID,
        "enforcement_version": ENFORCEMENT_VERSION,
        "source_date": source_date,
        "source_generation_sha256": gate["source_generation_sha256"],
        "canonical_registry_sha256": canonical_sha,
        "scratch_registry_sha256": reconstruction_sha,
    }
    replay_id = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    receipt_path = gate_root / "historical-replays" / f"registry-historical-replay-{source_date}-{replay_id}.json"
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "project_dashboard_registry_historical_replay_receipt",
        "tool_id": TOOL_ID,
        "replay_id": replay_id,
        "status": "passed",
        "evidence_class": "isolated_historical_calendar_date_replay",
        "acceptance_effect": "historical_replay_gate_only_never_live_streak",
        "source_date": source_date,
        "date_contract_classification": (
            "receipt_required_contract" if source_date >= RECEIPT_REQUIRED_FROM
            else "legacy_pre_gate_date_with_valid_completed_receipts"
        ),
        "source_generation_sha256": gate["source_generation_sha256"],
        "input_bindings": bindings,
        "canonical_registry_sha256_at_replay": canonical_sha,
        "reconstruction": replay_metrics,
        "canonical_input_guard": {
            "hashes_before": guard_before,
            "hashes_after": guard_after,
            "status": "unchanged",
        },
        "checks": {**checks, "all_required_checks_passed": True},
        "temporary_scratch_deleted": True,
        "evidence_limitations": [
            "This receipt proves one archived input date in isolation; it is not a production scheduler observation.",
            "Current manual overlays are applied only by matching stable IDs and do not reconstruct the historical timing of owner edits.",
            "The replay neither mutates nor increments the frozen legacy Stage-2 streak.",
        ],
        "stage3_authorized": False,
        "dual_write_enabled": False,
        "cutover_enabled": False,
        "completed_at": datetime.now(TZ).isoformat(),
    }
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        immutable = (
            "replay_id", "source_date", "date_contract_classification", "source_generation_sha256",
            "input_bindings", "canonical_registry_sha256_at_replay", "reconstruction", "checks",
        )
        if any(existing.get(key) != receipt.get(key) for key in immutable):
            raise HistoricalReplayError("existing historical replay receipt conflict")
    else:
        _atomic_write(receipt_path, receipt)
    return {"source_date": source_date, "status": "passed", "receipt_path": receipt_path.as_posix(), "receipt": receipt}


def run_series(
    *,
    dates: list[str],
    registry_path: Path,
    wiki_root: Path,
    memory_root: Path,
    source_root: Path,
    stage1_receipt_path: Path,
    h5_snapshot_path: Path,
    gate_root: Path = DEFAULT_GATE_ROOT,
) -> dict[str, Any]:
    if len(dates) != 7 or len(set(dates)) != 7:
        raise HistoricalReplayError("official series requires exactly seven distinct dates")
    parsed = sorted(date.fromisoformat(item) for item in dates)
    if any(right - left != timedelta(days=1) for left, right in zip(parsed, parsed[1:])):
        raise HistoricalReplayError("official series dates must be consecutive")
    results = [
        run_historical_replay(
            source_date=item.isoformat(),
            registry_path=registry_path,
            wiki_root=wiki_root,
            memory_root=memory_root,
            source_root=source_root,
            stage1_receipt_path=stage1_receipt_path,
            h5_snapshot_path=h5_snapshot_path,
            gate_root=gate_root,
        )
        for item in parsed
    ]
    summary = publish_gate_summary(
        gate_root=gate_root,
        legacy_streak_path=wiki_root / "review-cycles/registry-shadow-compare/stage-2-streak.json",
    )
    return {"status": "passed", "dates": [item.isoformat() for item in parsed], "results": results, "gate": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", nargs=7, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--wiki-root", type=Path, required=True)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--stage1-receipt", type=Path, required=True)
    parser.add_argument("--h5-snapshot", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, default=DEFAULT_GATE_ROOT)
    args = parser.parse_args()
    try:
        result = run_series(
            dates=args.dates,
            registry_path=args.registry,
            wiki_root=args.wiki_root,
            memory_root=args.memory_root,
            source_root=args.source_root,
            stage1_receipt_path=args.stage1_receipt,
            h5_snapshot_path=args.h5_snapshot,
            gate_root=args.gate_root,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
