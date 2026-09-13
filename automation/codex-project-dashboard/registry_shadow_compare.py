#!/usr/bin/env python3
"""Stage-2 production-isolated dual-read comparison for the Dashboard registry.

The compatibility registry remains the only production authority.  This module
creates a complete Stage-1 generation in a temporary directory, renders both
the canonical and recomposed registries through the existing Dashboard model,
compares the resulting Dashboard/H5 projections, publishes a compact receipt,
and removes every temporary projection before returning.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from dashboard_model import CaseRegistry
from registry_shadow_split import (
    ShadowSplitError,
    aggregate_path_hash,
    canonical_bytes,
    mismatch_diagnostics,
    protected_state_hash,
    recompose_registry,
    semantic_normalize,
    sha256_bytes,
    sha256_value,
    split_registry,
    write_shadow_split,
)


ARTIFACT_SCHEMA_VERSION = "1.0"
TOOL_ID = "project-dashboard-registry-shadow-compare"
BASELINE_POLICY_VERSION = "stage2-dual-read-post-capability-fix-v1"
TZ = ZoneInfo("Asia/Shanghai")

VOLATILE_DASHBOARD_FIELDS = ("generated_at",)
VOLATILE_H5_FIELDS = ("generated_at",)
PROHIBITED_PUBLIC_KEYS = {
    "case_id", "event_id", "task_id", "thread_id", "turn_id", "branch_id",
    "capability_domain_id", "candidate_id", "value_id", "source_locator",
    "private_locator", "excerpt", "category", "line", "id", "session_id",
}
PROHIBITED_PUBLIC_TEXT = (
    re.compile(r"/Users/"),
    re.compile(r"(?:https?://)?(?:www\.)?chatgpt\.com/c/", re.I),
    re.compile(r"raw/conversations", re.I),
    re.compile(r"dashboard-token|authorization|bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"(?:case|event|thread|turn)-[0-9a-f]{8,}", re.I),
    re.compile(r"\b(?:case_id|event_id|task_id|thread_id|turn_id|candidate_id|value_id)\b", re.I),
    re.compile(r"\b(?:prompt|reasoning)\b", re.I),
)


class ShadowCompareError(RuntimeError):
    """Fail-closed Stage-2 comparison error with compact diagnostics."""

    def __init__(self, message: str, *, receipt_path: Path | None = None) -> None:
        super().__init__(message)
        self.receipt_path = receipt_path


def _now_iso() -> str:
    return datetime.now(TZ).isoformat()


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
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


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _aggregate_hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {label: aggregate_path_hash(path) for label, path in sorted(paths.items())}


def _hash_hashes(hashes: dict[str, str]) -> str:
    return sha256_value(hashes)


def _strip_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    normalized = copy.deepcopy(value)
    for field in fields:
        normalized.pop(field, None)
    return normalized


def _public_privacy_violations(value: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in PROHIBITED_PUBLIC_KEYS or key.endswith("_locator"):
                issues.append(child_path)
            issues.extend(_public_privacy_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_public_privacy_violations(child, f"{path}[{index}]"))
    elif isinstance(value, str) and any(pattern.search(value) for pattern in PROHIBITED_PUBLIC_TEXT):
        issues.append(path)
    return issues


def capability_projection(registry: dict[str, Any]) -> dict[str, Any]:
    """Return the exact capability-domain review surface used for parity hashes."""
    domains = [
        {
            "capability_domain_id": item.get("capability_domain_id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "tag_label": item.get("tag_label"),
        }
        for item in registry.get("capability_domains") or []
        if isinstance(item, dict)
    ]
    domain_ids = [str(item.get("capability_domain_id") or "") for item in domains]
    if any(not item for item in domain_ids) or len(domain_ids) != len(set(domain_ids)):
        raise ShadowSplitError("capability domain IDs must be non-empty and unique")

    active = [
        item for item in registry.get("events") or []
        if isinstance(item, dict) and item.get("state") == "capability_item"
    ]
    event_ids = [str(item.get("event_id") or "") for item in active]
    if any(not item for item in event_ids) or len(event_ids) != len(set(event_ids)):
        raise ShadowSplitError("active capability item IDs must be non-empty and unique")
    identities = [
        (str(item.get("source") or ""), str(item.get("question") or ""))
        for item in active
    ]
    duplicate_identities = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicate_identities:
        raise ShadowSplitError("duplicate active capability source/question identities")
    unknown_domains = sorted({
        str(item.get("capability_domain_id") or "") for item in active
        if str(item.get("capability_domain_id") or "") not in set(domain_ids)
    })
    if unknown_domains:
        raise ShadowSplitError("active capability items reference unknown domains")

    items: list[dict[str, Any]] = []
    for domain_id in domain_ids:
        records = [item for item in active if str(item.get("capability_domain_id") or "") == domain_id]
        records.sort(
            key=lambda item: (
                str(item.get("period") or item.get("date") or ""),
                str(item.get("created_at") or ""),
            ),
            reverse=True,
        )
        for item in records:
            items.append({
                "capability_domain_id": domain_id,
                "event_id": item.get("event_id"),
                "topic": item.get("topic"),
                "question": item.get("question"),
                "answer": item.get("answer"),
                "answer_status": item.get("answer_status"),
                "answer_origin": item.get("answer_origin"),
                "knowledge_status": item.get("knowledge_status"),
                "evidence_refs": item.get("evidence_refs") or [],
                "evidence_boundary": item.get("evidence_boundary"),
                "instrument_types": item.get("instrument_types") or [],
                "related_project_ids": item.get("related_project_ids") or [],
                "branch_ids": item.get("branch_ids") or [],
                "branch_id": item.get("branch_id"),
                "case_id": item.get("case_id"),
                "period": item.get("period"),
                "date": item.get("date"),
                "created_at": item.get("created_at"),
            })
    case_relationships = [
        {
            "case_id": case.get("case_id"),
            "related_capability_domain_ids": case.get("related_capability_domain_ids") or [],
        }
        for case in registry.get("cases") or []
        if isinstance(case, dict) and case.get("related_capability_domain_ids")
    ]
    branch_relationships = [
        {
            "branch_id": branch.get("branch_id"),
            "related_capability_domain_ids": branch.get("related_capability_domain_ids") or [],
        }
        for branch in registry.get("branches") or []
        if isinstance(branch, dict) and branch.get("related_capability_domain_ids")
    ]
    return {
        "domains": domains,
        "items": items,
        "case_relationships": case_relationships,
        "branch_relationships": branch_relationships,
        "counts": {
            "domains": len(domains),
            "active_items": len(items),
            "source_grounded_items": sum(1 for item in items if item.get("answer_status") == "source_grounded"),
            "needs_source_review_items": sum(1 for item in items if item.get("answer_status") == "needs_source_review"),
            "case_relationships": len(case_relationships),
            "branch_relationships": len(branch_relationships),
            "unique_item_ids": len(set(event_ids)),
            "unique_source_question_pairs": len(set(identities)),
        },
    }


def _shadow_snapshot(
    registry: dict[str, Any],
    *,
    registry_path: Path,
    wiki_root: Path,
    reviews_root: Path,
    daily_root: Path,
    codex_daily_root: Path,
    memory_root: Path,
    live_state_path: Path,
    source_through_date: str = "",
) -> dict[str, Any]:
    registry_path.write_bytes(_pretty_bytes(registry))
    return CaseRegistry(
        wiki_root=wiki_root,
        registry_path=registry_path,
        reviews_root=reviews_root,
        daily_root=daily_root,
        codex_daily_root=codex_daily_root,
        live_state_path=live_state_path,
        require_deposition_receipts=True,
        memory_root=memory_root,
        source_through_date=source_through_date,
    ).snapshot()


def _publish_receipt_only(
    *,
    output_root: Path,
    receipt: dict[str, Any],
    inject_failure: str = "",
) -> Path:
    """Publish immutable compare evidence without touching legacy Stage-2 streak state.

    Live migration eligibility is decided only after a committed production
    refresh by ``registry_migration_gate.py``.  Keeping comparison publication
    independent prevents a caller-controlled boolean or observation ID from
    manufacturing acceptance evidence.
    """
    output_root.mkdir(parents=True, exist_ok=True)
    receipt_path = output_root / f"project-dashboard-registry-shadow-compare-{receipt['generation_id']}.json"
    created_receipt = False
    try:
        if receipt_path.exists():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            immutable_fields = (
                "generation_id", "source_registry", "stage_1_anchor_receipt",
                "stage_1_runtime_generation", "capability_parity",
                "registry_parity", "dashboard_snapshot_parity",
                "h5_snapshot_parity", "result", "evidence_class",
            )
            if any(existing.get(field) != receipt.get(field) for field in immutable_fields):
                raise ShadowCompareError("existing Stage-2 receipt conflicts with this generation")
        else:
            _atomic_write(receipt_path, receipt)
            created_receipt = True
        if inject_failure == "after_receipt":
            raise ShadowCompareError("injected failure after receipt publication")
    except Exception:
        if created_receipt:
            receipt_path.unlink(missing_ok=True)
        raise
    return receipt_path


def run_shadow_compare(
    *,
    registry_path: Path,
    wiki_root: Path,
    memory_root: Path,
    stage1_receipt_path: Path,
    output_root: Path,
    h5_snapshot_path: Path,
    h5_builder: Callable[..., dict[str, Any]],
    synced_through: str,
    observation_id: str,
    guard_paths: dict[str, Path],
    inject_failure: str = "",
    recomposed_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one isolated comparison and publish non-sensitive compare evidence."""
    registry_path = registry_path.resolve()
    memory_root = memory_root.resolve()
    stage1_receipt_path = stage1_receipt_path.resolve()
    output_root = output_root.resolve()
    run_timestamp = _now_iso()
    source_bytes = registry_path.read_bytes()
    source_sha256 = sha256_bytes(source_bytes)
    source_registry = json.loads(source_bytes)
    generation_id = "stage2-" + hashlib.sha256(
        f"{TOOL_ID}\x1f{BASELINE_POLICY_VERSION}\x1f{observation_id}\x1f{source_sha256}".encode("utf-8")
    ).hexdigest()[:24]
    stage1_archive = json.loads(stage1_receipt_path.read_text(encoding="utf-8"))
    if (
        stage1_archive.get("status") != "passed"
        or stage1_archive.get("stage") != 1
        or stage1_archive.get("artifact_type") != "project_dashboard_registry_shadow_split_parity_receipt"
        or stage1_archive.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION
        or stage1_archive.get("tool_id") != "project-dashboard-registry-shadow-split"
        or not all(value == "passed" for value in (stage1_archive.get("stage_1_exit_criteria") or {}).values())
    ):
        raise ShadowCompareError("Stage-1 anchor receipt is not a valid passed baseline attestation")

    all_guards = {"registry": registry_path, "h5_snapshot": h5_snapshot_path, "stage1_receipt": stage1_receipt_path, **guard_paths}
    guard_before = _aggregate_hashes(all_guards)
    diagnostics: dict[str, Any] = {}
    result = "failed"
    manual_hashes: dict[str, str] = {}
    registry_hashes: dict[str, str] = {}
    dashboard_hashes: dict[str, str] = {}
    h5_hashes: dict[str, str] = {}
    capability_counts: dict[str, Any] = {}
    capability_hashes: dict[str, str] = {}
    privacy: dict[str, Any] = {"nested_private_locator_confinement": "failed", "h5_public_whitelist": "failed"}
    stage1_generation_id = ""
    stage1_runtime_receipt_sha256 = ""

    temporary_root = Path(tempfile.mkdtemp(prefix="dashboard-stage2-", dir="/private/tmp"))
    try:
        stage1_output = temporary_root / "stage1-generation"
        stage1_runtime = write_shadow_split(
            registry_path,
            stage1_output,
            expected_source_sha256=source_sha256,
            guard_paths=guard_paths,
        )
        stage1_generation_id = str(stage1_runtime.get("generation_id") or "")
        stage1_runtime_receipt_sha256 = sha256_bytes((stage1_output / "parity-receipt.json").read_bytes())
        if stage1_runtime.get("status") != "passed" or stage1_runtime.get("source_registry_sha256") != source_sha256:
            raise ShadowSplitError("runtime Stage-1 generation did not validate the current registry")
        manual = json.loads((stage1_output / "manual-state.json").read_text(encoding="utf-8"))
        replay = json.loads((stage1_output / "replay-state.json").read_text(encoding="utf-8"))
        recomposed = recompose_registry(manual, replay)
        if recomposed_mutator:
            recomposed_mutator(recomposed)

        source_capability = capability_projection(source_registry)
        recomposed_capability = capability_projection(recomposed)
        capability_diagnostics = mismatch_diagnostics(source_capability, recomposed_capability)
        capability_counts = source_capability["counts"]
        capability_hashes = {
            "source": sha256_value(source_capability),
            "recomposed": sha256_value(recomposed_capability),
        }
        if capability_diagnostics:
            diagnostics["capability"] = capability_diagnostics[:25]

        recomposed_manual, _, _ = split_registry(recomposed, source_sha256)
        manual_hashes = {
            "source": protected_state_hash(manual),
            "recomposed": protected_state_hash(recomposed_manual),
        }
        registry_diagnostics = mismatch_diagnostics(semantic_normalize(source_registry), semantic_normalize(recomposed))
        registry_hashes = {
            "source_semantic": sha256_value(semantic_normalize(source_registry)),
            "recomposed_semantic": sha256_value(semantic_normalize(recomposed)),
            "source_manual": manual_hashes["source"],
            "recomposed_manual": manual_hashes["recomposed"],
        }
        if registry_diagnostics:
            diagnostics["registry"] = registry_diagnostics[:25]

        missing_live = temporary_root / "disabled-live-state.json"
        source_snapshot = _shadow_snapshot(
            source_registry,
            registry_path=temporary_root / "canonical-registry.json",
            wiki_root=wiki_root,
            reviews_root=wiki_root / "reviews",
            daily_root=wiki_root / "sources/conversations/chatgpt-daily",
            codex_daily_root=wiki_root / "sources/conversations/codex-daily",
            memory_root=memory_root,
            live_state_path=missing_live,
            source_through_date=synced_through,
        )
        recomposed_snapshot = _shadow_snapshot(
            recomposed,
            registry_path=temporary_root / "recomposed-registry.json",
            wiki_root=wiki_root,
            reviews_root=wiki_root / "reviews",
            daily_root=wiki_root / "sources/conversations/chatgpt-daily",
            codex_daily_root=wiki_root / "sources/conversations/codex-daily",
            memory_root=memory_root,
            live_state_path=missing_live,
            source_through_date=synced_through,
        )
        source_snapshot_semantic = _strip_fields(source_snapshot, VOLATILE_DASHBOARD_FIELDS)
        recomposed_snapshot_semantic = _strip_fields(recomposed_snapshot, VOLATILE_DASHBOARD_FIELDS)
        dashboard_diagnostics = mismatch_diagnostics(source_snapshot_semantic, recomposed_snapshot_semantic)
        dashboard_hashes = {
            "canonical_registry_snapshot": sha256_value(source_snapshot_semantic),
            "recomposed_registry_snapshot": sha256_value(recomposed_snapshot_semantic),
        }
        if dashboard_diagnostics:
            diagnostics["dashboard_snapshot"] = dashboard_diagnostics[:25]

        source_h5 = h5_builder(source_snapshot, synced_through=synced_through)
        recomposed_h5 = h5_builder(recomposed_snapshot, synced_through=synced_through)
        source_h5_semantic = _strip_fields(source_h5, VOLATILE_H5_FIELDS)
        recomposed_h5_semantic = _strip_fields(recomposed_h5, VOLATILE_H5_FIELDS)
        h5_diagnostics = mismatch_diagnostics(source_h5_semantic, recomposed_h5_semantic)
        h5_hashes = {
            "canonical_registry_h5": sha256_value(source_h5_semantic),
            "recomposed_registry_h5": sha256_value(recomposed_h5_semantic),
        }
        if h5_diagnostics:
            diagnostics["h5_snapshot"] = h5_diagnostics[:25]
        privacy_violations = sorted(set(_public_privacy_violations(source_h5) + _public_privacy_violations(recomposed_h5)))
        if privacy_violations:
            diagnostics["privacy"] = [{"path": item, "kind": "public_whitelist_violation"} for item in privacy_violations[:25]]
        privacy = {
            "nested_private_locator_confinement": "passed" if stage1_runtime.get("privacy", {}).get("status") == "passed" else "failed",
            "private_nested_field_occurrences": stage1_runtime.get("field_ownership_coverage", {}).get("private_nested_field_occurrences", 0),
            "h5_public_whitelist": "passed" if not privacy_violations else "failed",
            "public_violation_count": len(privacy_violations),
        }
        if inject_failure == "compare_mismatch":
            diagnostics["injected"] = [{"path": "$", "kind": "injected_compare_mismatch"}]

        parity_ok = (
            not diagnostics
            and manual_hashes.get("source") == manual_hashes.get("recomposed")
            and registry_hashes.get("source_semantic") == registry_hashes.get("recomposed_semantic")
            and dashboard_hashes.get("canonical_registry_snapshot") == dashboard_hashes.get("recomposed_registry_snapshot")
            and h5_hashes.get("canonical_registry_h5") == h5_hashes.get("recomposed_registry_h5")
            and capability_hashes.get("source") == capability_hashes.get("recomposed")
            and all(value == "passed" for key, value in privacy.items() if key in {"nested_private_locator_confinement", "h5_public_whitelist"})
        )
        result = "passed" if parity_ok else "failed"
    except Exception as exc:
        diagnostics.setdefault("execution", []).append({
            "path": "$", "kind": type(exc).__name__, "message_sha256": sha256_bytes(str(exc).encode("utf-8")),
        })
        result = "failed"
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    guard_after = _aggregate_hashes(all_guards)
    changed_guards = sorted(label for label in guard_before if guard_before[label] != guard_after[label])
    if changed_guards:
        diagnostics["canonical_mutation"] = [{"path": label, "kind": "hash_changed"} for label in changed_guards]
        result = "failed"
    if result != "passed":
        # A later failed rerun of an otherwise idempotent successful
        # observation publishes separate immutable diagnostics rather than
        # colliding with the passed receipt for that input.
        generation_id = "stage2-failed-" + hashlib.sha256(
            canonical_bytes({
                "baseline_generation_id": generation_id,
                "diagnostics": diagnostics,
                "result": result,
            })
        ).hexdigest()[:24]
    receipt = {
        "artifact_type": "project_dashboard_registry_shadow_compare_receipt",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "tool_id": TOOL_ID,
        "stage": 2,
        "generation_id": generation_id,
        "run_timestamp": run_timestamp,
        "observation_id": observation_id,
        "evidence_class": "isolated_compare_only",
        "acceptance_effect": "none_until_committed_scheduled_refresh_provenance_is_verified",
        "baseline_policy_version": BASELINE_POLICY_VERSION,
        "source_registry": {
            "path": _relative_or_name(registry_path, memory_root),
            "sha256": source_sha256,
            "version": source_registry.get("version"),
        },
        "stage_1_anchor_receipt": {
            "path": _relative_or_name(stage1_receipt_path, memory_root),
            "sha256": sha256_bytes(stage1_receipt_path.read_bytes()),
            "generation_id": stage1_archive.get("generation_id"),
        },
        "stage_1_runtime_generation": {
            "generation_id": stage1_generation_id,
            "source_registry_sha256": source_sha256,
            "receipt_sha256": stage1_runtime_receipt_sha256,
            "temporary_projection_deleted": not temporary_root.exists(),
        },
        "capability_parity": {
            "counts": capability_counts,
            "semantic_sha256": capability_hashes,
            "dedupe_unique": not bool(diagnostics.get("capability")),
            "ordering_semantics": "domain registry order; active items by period/date then created_at descending",
        },
        "registry_parity": {
            "semantic_sha256": {"canonical": registry_hashes.get("source_semantic", ""), "recomposed": registry_hashes.get("recomposed_semantic", "")},
            "manual_sha256": {"canonical": manual_hashes.get("source", ""), "recomposed": manual_hashes.get("recomposed", "")},
            "protected_field_parity": manual_hashes.get("source") == manual_hashes.get("recomposed") and bool(manual_hashes),
        },
        "dashboard_snapshot_parity": {
            "semantic_sha256": dashboard_hashes,
            "stripped_volatile_fields": list(VOLATILE_DASHBOARD_FIELDS),
        },
        "h5_snapshot_parity": {
            "semantic_sha256": h5_hashes,
            "stripped_volatile_fields": list(VOLATILE_H5_FIELDS),
        },
        "privacy_checks": privacy,
        "canonical_input_guard": {
            "hashes_before": guard_before,
            "hashes_after": guard_after,
            "aggregate_before_sha256": _hash_hashes(guard_before),
            "aggregate_after_sha256": _hash_hashes(guard_after),
            "status": "unchanged" if not changed_guards else "changed",
        },
        "diagnostics": diagnostics,
        "temporary_projections_deleted": not temporary_root.exists(),
        "production_authority": "canonical_compatibility_registry_only",
        "dual_write_enabled": False,
        "cutover_enabled": False,
        "result": result,
    }
    receipt_path = _publish_receipt_only(
        output_root=output_root,
        receipt=receipt,
        inject_failure=inject_failure if inject_failure == "after_receipt" else "",
    )
    if result != "passed":
        raise ShadowCompareError("Stage-2 shadow comparison failed", receipt_path=receipt_path)
    return {
        "ok": True,
        "result": result,
        "generation_id": generation_id,
        "receipt_path": str(receipt_path),
        "streak_action": "legacy_streak_frozen_no_change",
        "source_registry_sha256": source_sha256,
        "temporary_projections_deleted": not temporary_root.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--wiki-root", type=Path, required=True)
    parser.add_argument("--memory-root", type=Path, required=True)
    parser.add_argument("--stage1-receipt", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--h5-snapshot", type=Path, required=True)
    parser.add_argument("--synced-through", default="")
    parser.add_argument("--observation-id", required=True)
    args = parser.parse_args()
    from export_h5_snapshot import build_h5_snapshot

    try:
        result = run_shadow_compare(
            registry_path=args.registry,
            wiki_root=args.wiki_root,
            memory_root=args.memory_root,
            stage1_receipt_path=args.stage1_receipt,
            output_root=args.output_root,
            h5_snapshot_path=args.h5_snapshot,
            h5_builder=build_h5_snapshot,
            synced_through=args.synced_through,
            observation_id=args.observation_id,
            guard_paths={},
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        receipt_path = getattr(exc, "receipt_path", None)
        print(json.dumps({
            "ok": False,
            "error": "stage2_shadow_compare_failed",
            "receipt_path": str(receipt_path) if receipt_path else "",
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
