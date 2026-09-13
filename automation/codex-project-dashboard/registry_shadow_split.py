#!/usr/bin/env python3
"""Stage-1 shadow split for the canonical project Dashboard registry.

This tool is intentionally not connected to production readers or writers.  It
reads one registry, writes a complete generation beneath a fresh caller-owned
output directory, recomposes the registry, and fails closed unless parity and
all ownership/privacy invariants hold.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = "1.0"
TOOL_ID = "project-dashboard-registry-shadow-split"

TOP_MANUAL_FIELDS = {
    "branches",
    "capability_domains",
    "categories",
    "created_at",
    "migration_audit",
    "privacy_policy",
    "project_mappings",
    "routing_audit",
    "routing_rules",
    "source_policy",
}
TOP_REPLAY_FIELDS = {"updated_at", "version"}
TOP_COLLECTION_FIELDS = {"cases", "events", "value_candidates"}

CASE_MANUAL_FIELDS = {
    "case_id",
    "title",
    "line",
    "category",
    "status",
    "current_summary",
    "next_step",
    "routing_terms",
    "hidden",
    "needs_review",
    "field_authority",
    "privacy_mode",
    "evidence_mode",
    "project_type",
    "time_granularity",
    "related_capability_domain_ids",
    "legacy_branch_id",
    "origin",
    "created_at",
    "updated_at",
}

EVENT_MANUAL_FIELDS = {
    "additional_case_ids",
    "branch_id",
    "branch_ids",
    "capability_domain_id",
    "case_id",
    "ignored_at",
    "live_routing_reason",
    "privacy_mode",
    "project_key",
    "related_project_ids",
    "route_origin",
    "route_origin_when_manual",
    "route_undone_at",
    "routed_at",
    "routing_history",
    "routing_reason",
    "state",
    "suggested_case_ids",
    "thread_id",
    "turn_id",
}
EVENT_REPLAY_FIELDS = {
    "answer",
    "answer_origin",
    "answer_status",
    "coverage",
    "codex_cwd",
    "codex_project_id",
    "codex_project_identity_source",
    "created_at",
    "date",
    "detail",
    "event_id",
    "event_kind",
    "evidence_boundary",
    "evidence_refs",
    "evidence_type",
    "goal",
    "instrument_types",
    "knowledge_kind",
    "knowledge_status",
    "new_status",
    "next_step",
    "period",
    "previous_status",
    "project_metadata_present",
    "progress_node",
    "provenance_reviewed_at",
    "question",
    "retired_reason",
    "session_id",
    "source",
    "source_kind",
    "source_status",
    "status_changed",
    "summary",
    "superseded_at",
    "superseded_by",
    "supersession_reason",
    "task_id",
    "task_status",
    "title",
    "topic",
    "verification_refs",
}

VALUE_CANDIDATE_MANUAL_FIELDS = {
    "manual_evidence_overrides",
    "routing_conflict",
    "state",
    "target_id",
    "target_kind",
}
VALUE_CANDIDATE_REPLAY_FIELDS = {
    "candidate_id",
    "confidence",
    "coverage",
    "date",
    "detail",
    "domain",
    "evidence_boundary",
    "evidence_sources",
    "evidence_type",
    "sessions",
    "source",
    "source_kind",
    "source_status",
    "type",
    "value_id",
}

VALUE_ITEM_MANUAL_FIELDS = {"privacy_mode", "manual_edit_overlay", "manual_routing_target"}
VALUE_ITEM_REPLAY_FIELDS = {
    "answer",
    "answer_origin",
    "answer_status",
    "candidate_id",
    "created_at",
    "date",
    "detail",
    "evidence_boundary",
    "evidence_mode",
    "evidence_refs",
    "evidence_sources",
    "evidence_type",
    "instrument_types",
    "knowledge_status",
    "question",
    "source",
    "source_kind",
    "topic",
    "value_id",
}

# These may be retained in the private/manual projection but must never enter
# the replay/public projection, including when nested inside evidence refs.
PRIVATE_KEYS = {
    "auth_cookie",
    "conversation_id",
    "codex_cwd",
    "codex_project_id",
    "internal_url",
    "private_locator",
    "source_locator",
    "thread_id",
    "turn_id",
}


class ShadowSplitError(RuntimeError):
    """Fail-closed shadow split validation error."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def aggregate_path_hash(path: Path) -> str:
    """Hash one file/tree without placing an absolute path in the digest."""
    path = path.resolve()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    if not files and not path.exists():
        raise ShadowSplitError(f"guard path does not exist: {path}")
    digest = hashlib.sha256()
    for item in files:
        relative = item.name if path.is_file() else item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _require_dict_list(registry: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = registry.get(field)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ShadowSplitError(f"{field} must be a list of objects")
    return value


def _unique_ids(records: Iterable[dict[str, Any]], field: str, label: str) -> list[str]:
    ids: list[str] = []
    for index, record in enumerate(records):
        value = str(record.get(field) or "").strip()
        if not value:
            raise ShadowSplitError(f"missing stable ID: {label}[{index}].{field}")
        ids.append(value)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ShadowSplitError(f"duplicate stable IDs in {label}.{field}: {duplicates}")
    return ids


def _unknown_fields(record: dict[str, Any], allowed: set[str]) -> list[str]:
    return sorted(set(record) - allowed)


def _extract_private(value: Any, path: tuple[Any, ...] = ()) -> tuple[Any, list[dict[str, Any]]]:
    overlays: list[dict[str, Any]] = []
    if isinstance(value, dict):
        public: dict[str, Any] = {}
        for key, child in value.items():
            if key in PRIVATE_KEYS or key.endswith("_locator"):
                overlays.append({"path": list(path + (key,)), "value": copy.deepcopy(child)})
                continue
            clean, nested = _extract_private(child, path + (key,))
            public[key] = clean
            overlays.extend(nested)
        return public, overlays
    if isinstance(value, list):
        public_list: list[Any] = []
        for index, child in enumerate(value):
            clean, nested = _extract_private(child, path + (index,))
            public_list.append(clean)
            overlays.extend(nested)
        return public_list, overlays
    return copy.deepcopy(value), overlays


def _set_nested(target: Any, path: list[Any], value: Any) -> None:
    cursor = target
    for segment in path[:-1]:
        if isinstance(segment, int):
            if not isinstance(cursor, list) or segment >= len(cursor):
                raise ShadowSplitError(f"invalid private overlay list path: {path}")
            cursor = cursor[segment]
        else:
            if not isinstance(cursor, dict):
                raise ShadowSplitError(f"invalid private overlay object path: {path}")
            cursor = cursor.setdefault(segment, {})
    final = path[-1]
    if isinstance(final, int):
        if not isinstance(cursor, list) or final >= len(cursor):
            raise ShadowSplitError(f"invalid private overlay final path: {path}")
        cursor[final] = copy.deepcopy(value)
    else:
        if not isinstance(cursor, dict):
            raise ShadowSplitError(f"invalid private overlay final object path: {path}")
        cursor[final] = copy.deepcopy(value)


def _split_record(
    record: dict[str, Any],
    *,
    manual_fields: set[str],
    replay_fields: set[str],
    label: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    unknown = _unknown_fields(record, manual_fields | replay_fields)
    if unknown:
        return {}, {}, [], [f"{label}.{field}" for field in unknown]
    manual = {field: copy.deepcopy(record[field]) for field in record if field in manual_fields}
    replay_raw = {field: copy.deepcopy(record[field]) for field in record if field in replay_fields}
    replay, private = _extract_private(replay_raw)
    return manual, replay, private, []


def _privacy_violations(value: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in PRIVATE_KEYS or key.endswith("_locator"):
                issues.append(child_path)
            issues.extend(_privacy_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_privacy_violations(child, f"{path}[{index}]"))
    return issues


def split_registry(registry: dict[str, Any], source_sha256: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(registry, dict):
        raise ShadowSplitError("registry root must be an object")
    top_unknown = sorted(set(registry) - TOP_MANUAL_FIELDS - TOP_REPLAY_FIELDS - TOP_COLLECTION_FIELDS)
    if top_unknown:
        raise ShadowSplitError(f"unknown/unclassified top-level fields: {top_unknown}")

    cases = _require_dict_list(registry, "cases")
    events = _require_dict_list(registry, "events")
    candidates = _require_dict_list(registry, "value_candidates")
    case_ids = _unique_ids(cases, "case_id", "cases")
    event_ids = _unique_ids(events, "event_id", "events")
    candidate_ids = _unique_ids(candidates, "candidate_id", "value_candidates")
    _unique_ids(candidates, "value_id", "value_candidates")

    generation_id = "shadow-" + hashlib.sha256(
        f"{TOOL_ID}\x1f{SCHEMA_VERSION}\x1f{source_sha256}".encode("utf-8")
    ).hexdigest()[:24]
    common = {
        "artifact_schema_version": SCHEMA_VERSION,
        "generation_id": generation_id,
        "source_registry_sha256": source_sha256,
        "source_registry_version": registry.get("version"),
        "tool_id": TOOL_ID,
    }
    manual_state: dict[str, Any] = {
        **common,
        "artifact_type": "project_dashboard_manual_state_shadow",
        "classification": "canonical_state_candidate_temporary",
        "state": {
            "top_level": {field: copy.deepcopy(registry[field]) for field in registry if field in TOP_MANUAL_FIELDS},
            "cases": [],
            "manual_events": [],
            "event_overlays": [],
            "value_candidate_overlays": [],
            "case_value_routes": [],
        },
    }
    replay_state: dict[str, Any] = {
        **common,
        "artifact_type": "project_dashboard_replay_state_shadow",
        "classification": "derived_view_temporary",
        "state": {
            "top_level": {field: copy.deepcopy(registry[field]) for field in registry if field in TOP_REPLAY_FIELDS},
            "events": [],
            "value_candidates": [],
            "case_value_items": [],
        },
        "ordering": {
            "case_ids": case_ids,
            "event_ids": event_ids,
            "candidate_ids": candidate_ids,
        },
    }

    unclassified: list[str] = []
    private_count = 0
    replay_event_count = 0
    manual_event_count = 0
    value_item_ids: list[str] = []

    for case in cases:
        unknown = _unknown_fields(case, CASE_MANUAL_FIELDS | {"value_items"})
        unclassified.extend(f"cases[{case['case_id']}].{field}" for field in unknown)
        manual_case = {field: copy.deepcopy(case[field]) for field in case if field in CASE_MANUAL_FIELDS}
        manual_state["state"]["cases"].append(manual_case)
        values = case.get("value_items", [])
        if values is None:
            values = []
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ShadowSplitError(f"cases[{case['case_id']}].value_items must be a list of objects")
        for value in values:
            value_id = str(value.get("value_id") or "").strip()
            if not value_id:
                raise ShadowSplitError(f"missing stable ID: cases[{case['case_id']}].value_items.value_id")
            value_item_ids.append(value_id)
            manual, replay, private, unknown = _split_record(
                value,
                manual_fields=VALUE_ITEM_MANUAL_FIELDS,
                replay_fields=VALUE_ITEM_REPLAY_FIELDS,
                label=f"cases[{case['case_id']}].value_items[{value_id}]",
            )
            unclassified.extend(unknown)
            manual_state["state"]["case_value_routes"].append(
                {"case_id": case["case_id"], "value_id": value_id, "manual_fields": manual, "private_overlays": private}
            )
            replay_state["state"]["case_value_items"].append({"case_id": case["case_id"], "record": replay})
            private_count += len(private)
    duplicate_value_items = sorted({value for value in value_item_ids if value_item_ids.count(value) > 1})
    if duplicate_value_items:
        raise ShadowSplitError(f"duplicate stable IDs in case value items: {duplicate_value_items}")

    for event in events:
        event_id = str(event["event_id"])
        if event.get("source_kind") == "manual":
            manual_state["state"]["manual_events"].append(copy.deepcopy(event))
            manual_event_count += 1
            continue
        manual, replay, private, unknown = _split_record(
            event,
            manual_fields=EVENT_MANUAL_FIELDS,
            replay_fields=EVENT_REPLAY_FIELDS,
            label=f"events[{event_id}]",
        )
        unclassified.extend(unknown)
        manual_state["state"]["event_overlays"].append(
            {"event_id": event_id, "manual_fields": manual, "private_overlays": private}
        )
        replay_state["state"]["events"].append(replay)
        replay_event_count += 1
        private_count += len(private)

    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        manual, replay, private, unknown = _split_record(
            candidate,
            manual_fields=VALUE_CANDIDATE_MANUAL_FIELDS,
            replay_fields=VALUE_CANDIDATE_REPLAY_FIELDS,
            label=f"value_candidates[{candidate_id}]",
        )
        unclassified.extend(unknown)
        manual_state["state"]["value_candidate_overlays"].append(
            {"candidate_id": candidate_id, "manual_fields": manual, "private_overlays": private}
        )
        replay_state["state"]["value_candidates"].append(replay)
        private_count += len(private)

    if unclassified:
        raise ShadowSplitError(f"unknown/unclassified fields: {sorted(unclassified)}")
    privacy_violations = _privacy_violations(replay_state)
    if privacy_violations:
        raise ShadowSplitError(f"privacy-field leakage into replay projection: {privacy_violations}")

    coverage = {
        "classified_field_occurrences": sum(len(item) for item in cases)
        + sum(len(item) for item in events)
        + sum(len(item) for item in candidates)
        + sum(len(value) for case in cases for value in (case.get("value_items") or []))
        + len(registry),
        "private_nested_field_occurrences": private_count,
        "top_level_manual_fields": sorted(set(registry) & TOP_MANUAL_FIELDS),
        "top_level_replay_fields": sorted(set(registry) & TOP_REPLAY_FIELDS),
        "unknown_or_unclassified_fields": [],
    }
    counts = {
        "cases": len(cases),
        "events": len(events),
        "manual_events": manual_event_count,
        "replay_events": replay_event_count,
        "value_candidates": len(candidates),
        "case_value_items": len(value_item_ids),
        "capability_domains": len(registry.get("capability_domains") or []),
        "branches": len(registry.get("branches") or []),
    }
    return manual_state, replay_state, {"counts": counts, "field_ownership_coverage": coverage}


def _apply_private(record: dict[str, Any], overlays: list[dict[str, Any]]) -> None:
    for overlay in overlays:
        path = overlay.get("path")
        if not isinstance(path, list) or not path:
            raise ShadowSplitError("private overlay path must be a non-empty list")
        _set_nested(record, path, overlay.get("value"))


def recompose_registry(manual: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    for label, projection in (("manual", manual), ("replay", replay)):
        if projection.get("artifact_schema_version") != SCHEMA_VERSION:
            raise ShadowSplitError(f"unsupported {label} projection schema")
    identity_fields = ("generation_id", "source_registry_sha256", "source_registry_version", "tool_id")
    mismatched = [field for field in identity_fields if manual.get(field) != replay.get(field)]
    if mismatched:
        raise ShadowSplitError(f"projection generation mismatch: {mismatched}")

    manual_data = manual.get("state") or {}
    replay_data = replay.get("state") or {}
    registry = copy.deepcopy(manual_data.get("top_level") or {})
    registry.update(copy.deepcopy(replay_data.get("top_level") or {}))
    registry["cases"] = copy.deepcopy(manual_data.get("cases") or [])
    cases_by_id = {str(case.get("case_id") or ""): case for case in registry["cases"]}
    routes = {
        (str(route.get("case_id") or ""), str(route.get("value_id") or "")): route
        for route in manual_data.get("case_value_routes") or []
    }
    for item in replay_data.get("case_value_items") or []:
        case_id = str(item.get("case_id") or "")
        record = copy.deepcopy(item.get("record") or {})
        value_id = str(record.get("value_id") or "")
        route = routes.get((case_id, value_id))
        if case_id not in cases_by_id or route is None:
            raise ShadowSplitError(f"missing case value route: {case_id}/{value_id}")
        record.update(copy.deepcopy(route.get("manual_fields") or {}))
        _apply_private(record, route.get("private_overlays") or [])
        cases_by_id[case_id].setdefault("value_items", []).append(record)
    if len(routes) != len(replay_data.get("case_value_items") or []):
        raise ShadowSplitError("missing or duplicate replay case value item")

    events_by_id = {
        str(event.get("event_id") or ""): copy.deepcopy(event)
        for event in replay_data.get("events") or []
    }
    for overlay in manual_data.get("event_overlays") or []:
        event_id = str(overlay.get("event_id") or "")
        if event_id not in events_by_id:
            raise ShadowSplitError(f"event overlay without replay record: {event_id}")
        events_by_id[event_id].update(copy.deepcopy(overlay.get("manual_fields") or {}))
        _apply_private(events_by_id[event_id], overlay.get("private_overlays") or [])
    for event in manual_data.get("manual_events") or []:
        event_id = str(event.get("event_id") or "")
        if event_id in events_by_id:
            raise ShadowSplitError(f"manual/replay event collision: {event_id}")
        events_by_id[event_id] = copy.deepcopy(event)
    event_order = replay.get("ordering", {}).get("event_ids") or []
    if set(event_order) != set(events_by_id):
        raise ShadowSplitError("event ordering has missing or extra stable IDs")
    registry["events"] = [events_by_id[event_id] for event_id in event_order]

    candidates_by_id = {
        str(record.get("candidate_id") or ""): copy.deepcopy(record)
        for record in replay_data.get("value_candidates") or []
    }
    for overlay in manual_data.get("value_candidate_overlays") or []:
        candidate_id = str(overlay.get("candidate_id") or "")
        if candidate_id not in candidates_by_id:
            raise ShadowSplitError(f"candidate overlay without replay record: {candidate_id}")
        candidates_by_id[candidate_id].update(copy.deepcopy(overlay.get("manual_fields") or {}))
        _apply_private(candidates_by_id[candidate_id], overlay.get("private_overlays") or [])
    candidate_order = replay.get("ordering", {}).get("candidate_ids") or []
    if set(candidate_order) != set(candidates_by_id):
        raise ShadowSplitError("candidate ordering has missing or extra stable IDs")
    registry["value_candidates"] = [candidates_by_id[candidate_id] for candidate_id in candidate_order]

    case_order = replay.get("ordering", {}).get("case_ids") or []
    if set(case_order) != set(cases_by_id):
        raise ShadowSplitError("case ordering has missing or extra stable IDs")
    registry["cases"] = [cases_by_id[case_id] for case_id in case_order]
    return registry


def semantic_normalize(registry: dict[str, Any]) -> dict[str, Any]:
    """Normalize only explicitly generated provenance/timestamp fields."""
    normalized = copy.deepcopy(registry)
    normalized.pop("shadow_generation_id", None)
    normalized.pop("shadow_source_registry_sha256", None)
    # updated_at remains part of stage-1 parity because it is preserved exactly.
    return normalized


def mismatch_diagnostics(expected: Any, actual: Any, path: str = "$") -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if type(expected) is not type(actual):
        return [{"path": path, "kind": "type", "expected": type(expected).__name__, "actual": type(actual).__name__}]
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                diagnostics.append({"path": child, "kind": "unexpected"})
            elif key not in actual:
                diagnostics.append({"path": child, "kind": "missing"})
            else:
                diagnostics.extend(mismatch_diagnostics(expected[key], actual[key], child))
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            diagnostics.append({"path": path, "kind": "length", "expected": len(expected), "actual": len(actual)})
        for index, (left, right) in enumerate(zip(expected, actual)):
            diagnostics.extend(mismatch_diagnostics(left, right, f"{path}[{index}]"))
    elif expected != actual:
        diagnostics.append({"path": path, "kind": "value", "expected_sha256": sha256_value(expected), "actual_sha256": sha256_value(actual)})
    return diagnostics


def protected_state_hash(manual: dict[str, Any]) -> str:
    return sha256_value(manual.get("state") or {})


def build_receipt(
    *,
    source_path: Path,
    source_sha256: str,
    manual: dict[str, Any],
    replay: dict[str, Any],
    recomposed: dict[str, Any],
    source: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = mismatch_diagnostics(semantic_normalize(source), semantic_normalize(recomposed))
    recomposed_manual, _, _ = split_registry(recomposed, source_sha256)
    before_manual_hash = protected_state_hash(manual)
    after_manual_hash = protected_state_hash(recomposed_manual)
    receipt = {
        "artifact_type": "project_dashboard_registry_shadow_split_parity_receipt",
        "artifact_schema_version": SCHEMA_VERSION,
        "tool_id": TOOL_ID,
        "generation_id": manual["generation_id"],
        "source_registry": source_path.name,
        "source_registry_sha256": source_sha256,
        "source_registry_version": source.get("version"),
        "source_updated_at": source.get("updated_at"),
        "projection_schema_versions": {"manual": manual["artifact_schema_version"], "replay": replay["artifact_schema_version"]},
        "projection_sha256": {"manual": sha256_bytes(pretty_bytes(manual)), "replay": sha256_bytes(pretty_bytes(replay))},
        "recomposed_registry_sha256": sha256_bytes(pretty_bytes(recomposed)),
        "semantic_sha256": {"source": sha256_value(semantic_normalize(source)), "recomposed": sha256_value(semantic_normalize(recomposed))},
        "protected_manual_field_sha256": {"source_projection": before_manual_hash, "recomposed_projection": after_manual_hash},
        **inventory,
        "privacy": {"public_projection_violations": _privacy_violations(replay), "status": "passed"},
        "parity": {
            "full_semantic_parity": not diagnostics,
            "manual_field_hash_parity": before_manual_hash == after_manual_hash,
            "exact_mismatch_count": len(diagnostics),
            "exact_mismatch_diagnostics": diagnostics,
        },
        "source_mutation_guard": {"checked_before_commit": True, "status": "unchanged"},
        "stage": 1,
        "status": "passed" if not diagnostics and before_manual_hash == after_manual_hash else "failed",
    }
    if receipt["status"] != "passed":
        raise ShadowSplitError(f"shadow split parity failed: {receipt['parity']}")
    return receipt


def write_shadow_split(
    source_path: Path,
    output_dir: Path,
    *,
    expected_source_sha256: str = "",
    inject_failure: str = "",
    before_commit_hook: Callable[[], None] | None = None,
    guard_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ShadowSplitError(f"output directory must be fresh and absent: {output_dir}")
    if source_path == output_dir or source_path in output_dir.parents:
        raise ShadowSplitError("output directory may not contain or replace the source registry")
    guards = {"registry": source_path, **(guard_paths or {})}
    if any(not label or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in label) for label in guards):
        raise ShadowSplitError("guard labels must use lowercase letters, digits, underscore, or hyphen")
    guard_before = {label: aggregate_path_hash(path) for label, path in sorted(guards.items())}
    source_bytes = source_path.read_bytes()
    source_sha256 = sha256_bytes(source_bytes)
    if expected_source_sha256 and source_sha256 != expected_source_sha256:
        raise ShadowSplitError(
            f"source hash mismatch before split: expected {expected_source_sha256}, observed {source_sha256}"
        )
    source = json.loads(source_bytes)
    manual, replay, inventory = split_registry(source, source_sha256)
    recomposed = recompose_registry(manual, replay)
    receipt = build_receipt(
        source_path=source_path,
        source_sha256=source_sha256,
        manual=manual,
        replay=replay,
        recomposed=recomposed,
        source=source,
        inventory=inventory,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent))
    try:
        files = {
            "manual-state.json": manual,
            "replay-state.json": replay,
            "recomposed-registry.json": recomposed,
        }
        for name, payload in files.items():
            (staging / name).write_bytes(pretty_bytes(payload))
        if inject_failure == "before_commit":
            raise ShadowSplitError("injected failure before atomic directory commit")
        if before_commit_hook:
            before_commit_hook()
        observed_after = sha256_bytes(source_path.read_bytes())
        if observed_after != source_sha256:
            raise ShadowSplitError(
                f"source registry changed during split: before {source_sha256}, after {observed_after}"
            )
        guard_after = {label: aggregate_path_hash(path) for label, path in sorted(guards.items())}
        guard_mismatches = [label for label in guard_before if guard_before[label] != guard_after[label]]
        if guard_mismatches:
            raise ShadowSplitError(f"canonical input guard changed during split: {guard_mismatches}")
        receipt["canonical_input_guard"] = {
            "hashes_before": guard_before,
            "hashes_after": guard_after,
            "status": "unchanged",
        }
        (staging / "parity-receipt.json").write_bytes(pretty_bytes(receipt))
        if output_dir.exists():
            raise ShadowSplitError(f"output directory appeared before commit: {output_dir}")
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a stage-1 project registry shadow split in a fresh temporary directory.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", default="")
    parser.add_argument("--inject-failure", choices=("", "before_commit"), default="")
    parser.add_argument(
        "--guard-path",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Additional canonical input file/tree to hash before and after; repeatable.",
    )
    args = parser.parse_args()
    try:
        guards: dict[str, Path] = {}
        for raw_guard in args.guard_path:
            label, separator, raw_path = raw_guard.partition("=")
            if not separator or not label or not raw_path:
                raise ShadowSplitError(f"invalid --guard-path (expected LABEL=PATH): {raw_guard}")
            if label in guards or label == "registry":
                raise ShadowSplitError(f"duplicate/reserved guard label: {label}")
            guards[label] = Path(raw_path)
        receipt = write_shadow_split(
            args.source,
            args.output_dir,
            expected_source_sha256=args.expected_source_sha256,
            inject_failure=args.inject_failure,
            guard_paths=guards,
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
