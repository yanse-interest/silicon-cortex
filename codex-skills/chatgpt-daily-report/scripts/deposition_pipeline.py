#!/usr/bin/env python3
"""Deterministic daily-candidate, Review Cycle, promotion, and monthly-review engine.

The engine never edits raw reports. It reads structured candidate records from
compiled daily source summaries and writes only derived review state or formal
promotion pages. All writes are stable-ID based and recorded in a reversible
ledger.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = SCRIPT_DIR / "compiled_source_contract.py"
CONTRACT_SPEC = importlib.util.spec_from_file_location("pipeline_compiled_source_contract", CONTRACT_PATH)
CONTRACT = importlib.util.module_from_spec(CONTRACT_SPEC)
assert CONTRACT_SPEC.loader
CONTRACT_SPEC.loader.exec_module(CONTRACT)


CANDIDATE_TYPES = {
    "decision",
    "preference",
    "project_state",
    "knowledge",
    "workflow",
    "skill",
    "observation",
    "framework",
    "cognitive_shift",
    "action",
}
TARGETS = {
    "project",
    "preference",
    "decision",
    "knowledge",
    "workflow",
    "skill",
    "cognitive_observation",
    "cognitive_framework",
    "action",
}
ASSERTIONS = {"explicit", "inferred"}
RISKS = {"low", "medium", "high"}
SOURCE_STATUSES = {"candidate", "promoted", "conflict", "manual_confirmation"}
REVIEW_STATUSES = {"draft", "published"}
CASE_LINES = {"work", "life"}
CASE_STATUSES = {
    "planned",
    "completed",
    "in_progress",
    "blocked",
    "on_hold",
    "unknown",
    "archived",
}
CASE_EVIDENCE_TYPES = {"reported", "inferred"}
CASE_RELATIONS = {"continuation", "related", "predecessor"}
CASE_EVIDENCE_MODES = {"direct_source", "bounded_partial", "compliance_abstracted"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
STRUCTURED_HEADING = "## Structured Candidates"
UNSPECIFIED = "unspecified"
DAILY_SOURCE_TYPES = {
    "chatgpt_daily_source_summary": "chatgpt",
    "codex_daily_source_summary": "codex",
    "weekly_review_source_summary": "weekly_review",
}
COGNITIVE_ROUTE_TYPES = {
    "cognitive_observation": {"observation", "cognitive_shift"},
    "cognitive_framework": {"framework"},
}
DIRECT_ROUTE_TYPES = {
    "project": {"project_state"},
    "preference": {"preference"},
    "decision": {"decision"},
    "workflow": {"workflow"},
    "skill": {"skill"},
    "action": {"action"},
}
DEFAULT_WRITE_LOCK = Path("/private/tmp/codex-memory-deposition.lock")


class PipelineError(ValueError):
    """Raised for an invalid or unsafe deposition input."""


@contextmanager
def exclusive_deposition_write_lock() -> Iterable[None]:
    """Serialize cross-process ledger/cycle mutations while collectors stay parallel."""
    path = Path(os.environ.get("CODEX_MEMORY_DEPOSITION_LOCK", DEFAULT_WRITE_LOCK.as_posix()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    try:
        return CONTRACT.parse_frontmatter(text)
    except CONTRACT.SourceContractError as exc:
        raise PipelineError(str(exc)) from exc


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[\s\-_—–]+", " ", value)
    value = re.sub(r"[^\w\u4e00-\u9fff ]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def stable_candidate_id(candidate_type: str, domain: str, normalized_claim: str) -> str:
    return CONTRACT.stable_candidate_id(candidate_type, domain, normalized_claim)


def stable_case_id(line: str, title: str) -> str:
    basis = "|".join([normalize_text(line), normalize_text(title)])
    return "case-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def route_candidate_target(candidate_type: str, target: str) -> tuple[bool, str]:
    if target in COGNITIVE_ROUTE_TYPES:
        allowed = COGNITIVE_ROUTE_TYPES[target]
        if candidate_type in allowed:
            return True, "cognitive route"
        return (
            False,
            f"{target} accepts only {', '.join(sorted(allowed))}; "
            f"simple {candidate_type} candidates must stay in Memory",
        )
    if target in DIRECT_ROUTE_TYPES and candidate_type not in DIRECT_ROUTE_TYPES[target]:
        allowed = ", ".join(sorted(DIRECT_ROUTE_TYPES[target]))
        return False, f"{target} target accepts only {allowed}"
    if target == "knowledge" and candidate_type in {"observation", "framework", "cognitive_shift"}:
        return False, "cognitive candidate types must use a cognitive target"
    return True, "memory route"


def extract_structured_candidates(body: str, *, allow_missing: bool = False) -> list[dict[str, Any]]:
    try:
        return CONTRACT.extract_structured_candidates(body, required=not allow_missing)
    except CONTRACT.SourceContractError as exc:
        raise PipelineError(str(exc)) from exc


def require_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"candidate {record.get('candidate_id', '<unknown>')} needs non-empty {key}")
    return value.strip()


def string_list(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PipelineError(f"candidate {record.get('candidate_id', '<unknown>')} {key} must be strings")
    return [item.strip() for item in value]


def validate_candidate(
    record: dict[str, Any], *, source_date: str, source_coverage: str, source_path: str
) -> dict[str, Any]:
    candidate_type = require_string(record, "type")
    domain = require_string(record, "domain")
    claim = require_string(record, "normalized_claim")
    assertion = require_string(record, "assertion")
    risk = require_string(record, "risk")
    target = require_string(record, "suggested_target")
    status = require_string(record, "status")
    boundary = require_string(record, "evidence_boundary")
    candidate_id = require_string(record, "candidate_id")

    if candidate_type not in CANDIDATE_TYPES:
        raise PipelineError(f"invalid candidate type: {candidate_type}")
    if assertion not in ASSERTIONS:
        raise PipelineError(f"invalid assertion: {assertion}")
    if risk not in RISKS:
        raise PipelineError(f"invalid risk: {risk}")
    if target not in TARGETS:
        raise PipelineError(f"invalid suggested_target: {target}")
    if status not in SOURCE_STATUSES:
        raise PipelineError(f"invalid candidate status: {status}")
    route_ok, route_reason = route_candidate_target(candidate_type, target)
    if not route_ok:
        raise PipelineError(f"invalid candidate route: {route_reason}")
    expected = stable_candidate_id(candidate_type, domain, claim)
    if candidate_id != expected:
        raise PipelineError(f"candidate_id {candidate_id} is unstable; expected {expected}")

    record_date = require_string(record, "source_date")
    try:
        date.fromisoformat(record_date)
    except ValueError as exc:
        raise PipelineError("candidate source_date must be YYYY-MM-DD") from exc
    if record_date != source_date:
        raise PipelineError(f"candidate source_date {record_date} does not match source {source_date}")

    sessions = string_list(record, "sessions")
    if not sessions:
        raise PipelineError(f"candidate {candidate_id} needs at least one source session")
    if any(not re.fullmatch(r"[ST]\d{2,}", session) for session in sessions):
        raise PipelineError(f"candidate {candidate_id} has invalid session/task IDs")

    confidence = record.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise PipelineError(f"candidate {candidate_id} confidence must be numeric")
    confidence = float(confidence)
    if not 0 <= confidence <= 1:
        raise PipelineError(f"candidate {candidate_id} confidence must be in [0, 1]")
    evidence_complete = record.get("evidence_complete")
    if not isinstance(evidence_complete, bool):
        raise PipelineError(f"candidate {candidate_id} evidence_complete must be boolean")

    if candidate_type == "decision" and assertion == "explicit":
        require_string(record, "adoption_evidence")
    if candidate_type == "action":
        require_string(record, "action")
    if risk == "high" and candidate_type in {"observation", "cognitive_shift"}:
        require_string(record, "uncertainty")

    normalized = dict(record)
    normalized.update(
        {
            "candidate_id": candidate_id,
            "type": candidate_type,
            "domain": domain,
            "normalized_claim": claim,
            "assertion": assertion,
            "risk": risk,
            "suggested_target": target,
            "status": status,
            "evidence_boundary": boundary,
            "source_date": record_date,
            "sessions": sorted(set(sessions)),
            "coverage": record.get("coverage", source_coverage),
            "confidence": confidence,
            "evidence_complete": evidence_complete,
            "source_path": source_path,
            "conflicts_with": sorted(set(string_list(record, "conflicts_with"))),
            "application_cases": sorted(set(string_list(record, "application_cases"))),
            "observation_cycle_ids": sorted(set(string_list(record, "observation_cycle_ids"))),
            "linked_observation_ids": sorted(set(string_list(record, "linked_observation_ids"))),
            "validation_successes": int(record.get("validation_successes", 0)),
            "owner": str(record.get("owner") or UNSPECIFIED),
            "due": str(record.get("due") or UNSPECIFIED),
        }
    )
    if normalized["coverage"] not in {"complete", "partial", "unknown"}:
        raise PipelineError(f"candidate {candidate_id} has invalid coverage")
    if normalized["validation_successes"] < 0:
        raise PipelineError("validation_successes cannot be negative")
    return normalized


def portable_source_reference(path: Path, memory_root: Path | None) -> str:
    if memory_root is None:
        return path.as_posix()
    try:
        relative = path.resolve().relative_to(memory_root.resolve()).as_posix()
    except ValueError:
        return path.name
    return (
        "obsidian://open?vault="
        + quote(memory_root.name, safe="")
        + "&file="
        + quote(relative, safe="/")
    )


def load_daily_source(
    path: Path, *, allow_legacy: bool = False, memory_root: Path | None = None
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    source_type = frontmatter.get("type")
    if source_type not in DAILY_SOURCE_TYPES:
        if not allow_legacy:
            raise PipelineError(
                f"{path} type must be chatgpt_daily_source_summary, "
                "codex_daily_source_summary, or weekly_review_source_summary"
            )
    parsed_contract = None
    if source_type in {"chatgpt_daily_source_summary", "codex_daily_source_summary"}:
        try:
            parsed_contract = CONTRACT.load_compiled_source(
                path,
                legacy_compatible=allow_legacy,
                require_structured=not allow_legacy,
                require_instrument=False,
            )
        except CONTRACT.SourceContractError as exc:
            raise PipelineError(str(exc)) from exc
        frontmatter = parsed_contract.frontmatter
        body = parsed_contract.body
        source_date = parsed_contract.source_date
        coverage = parsed_contract.coverage
        records = parsed_contract.structured_candidates
        has_structured_candidates = not any(
            issue.get("code") == "missing_structured_candidates_section"
            for issue in parsed_contract.validation_errors
        )
    else:
        source_date = frontmatter.get("date", "")
        try:
            date.fromisoformat(source_date)
        except ValueError as exc:
            raise PipelineError(f"{path} date must be YYYY-MM-DD") from exc
        coverage = frontmatter.get("coverage", "unknown")
        if coverage not in {"complete", "partial", "unknown"}:
            raise PipelineError(f"{path} has invalid coverage")
        has_structured_candidates = STRUCTURED_HEADING in body
        records = extract_structured_candidates(body, allow_missing=allow_legacy)
    source_reference = portable_source_reference(path, memory_root)
    normalized = [
        validate_candidate(
            record,
            source_date=source_date,
            source_coverage=coverage,
            source_path=source_reference,
        )
        for record in records
    ]
    source_kind = DAILY_SOURCE_TYPES.get(source_type, "legacy")
    if source_kind == "codex":
        invalid_refs = sorted(
            {
                session
                for record in normalized
                for session in record["sessions"]
                if not re.fullmatch(r"T\d{2,}", session)
            }
        )
        if invalid_refs:
            raise PipelineError(
                f"{path} Codex candidates must cite Txx task IDs, not: "
                + ", ".join(invalid_refs)
            )
    return {
        "path": source_reference,
        "filesystem_path": path.as_posix(),
        "date": source_date,
        "coverage": coverage,
        "records": normalized,
        "source_kind": source_kind,
        "legacy": source_type not in DAILY_SOURCE_TYPES or not has_structured_candidates,
    }


def empty_aggregate(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "candidate_id",
        "type",
        "domain",
        "normalized_claim",
        "suggested_target",
        "risk",
    ]
    aggregate = {key: record[key] for key in keys}
    aggregate.update(
        {
            "assertions": [],
            "confidence": 0.0,
            "evidence": [],
            "conflicts_with": [],
            "application_cases": [],
            "observation_cycle_ids": [],
            "linked_observation_ids": [],
            "published_cycles": [],
            "validation_successes": 0,
            "adoption_evidence": [],
            "uncertainties": [],
            "shift_evidence": [],
            "action": record.get("action", ""),
            "owner": record.get("owner", UNSPECIFIED),
            "due": record.get("due", UNSPECIFIED),
            "state": "candidate",
            "promotion_path": "",
        }
    )
    return aggregate


def evidence_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (item["source_date"], item["session"], item["source_path"])


def add_record(aggregate: dict[str, Any], record: dict[str, Any]) -> None:
    if aggregate["type"] != record["type"] or normalize_text(aggregate["normalized_claim"]) != normalize_text(
        record["normalized_claim"]
    ):
        raise PipelineError(f"candidate ID collision: {record['candidate_id']}")
    aggregate["assertions"] = sorted(set(aggregate["assertions"] + [record["assertion"]]))
    aggregate["confidence"] = max(float(aggregate["confidence"]), float(record["confidence"]))
    if RISK_ORDER[record["risk"]] > RISK_ORDER[aggregate["risk"]]:
        aggregate["risk"] = record["risk"]
    for session in record["sessions"]:
        item = {
            "source_date": record["source_date"],
            "session": session,
            "source_path": record["source_path"],
            "coverage": record["coverage"],
            "evidence_boundary": record["evidence_boundary"],
            "evidence_complete": record["evidence_complete"],
        }
        if evidence_key(item) not in {evidence_key(existing) for existing in aggregate["evidence"]}:
            aggregate["evidence"].append(item)
    aggregate["evidence"].sort(key=evidence_key)
    for key in ["conflicts_with", "application_cases", "observation_cycle_ids", "linked_observation_ids"]:
        aggregate[key] = sorted(set(aggregate[key] + record.get(key, [])))
    aggregate["validation_successes"] = max(
        int(aggregate["validation_successes"]), int(record.get("validation_successes", 0))
    )
    for key, target in [
        ("adoption_evidence", "adoption_evidence"),
        ("uncertainty", "uncertainties"),
    ]:
        value = record.get(key)
        if value and value not in aggregate[target]:
            aggregate[target].append(value)
            aggregate[target].sort()
    shift = {
        key: record.get(key, "")
        for key in ["old_judgment", "new_judgment", "change_evidence"]
    }
    if any(shift.values()) and shift not in aggregate["shift_evidence"]:
        aggregate["shift_evidence"].append(shift)


def aggregate_records(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        candidate_id = record["candidate_id"]
        aggregate = result.setdefault(candidate_id, empty_aggregate(record))
        add_record(aggregate, record)
    return result


def merge_aggregate(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(existing))
    if merged["type"] != incoming["type"] or normalize_text(merged["normalized_claim"]) != normalize_text(
        incoming["normalized_claim"]
    ):
        raise PipelineError(f"candidate ID collision: {incoming['candidate_id']}")
    merged["assertions"] = sorted(set(merged.get("assertions", []) + incoming.get("assertions", [])))
    merged["confidence"] = max(float(merged.get("confidence", 0)), float(incoming.get("confidence", 0)))
    if RISK_ORDER[incoming["risk"]] > RISK_ORDER[merged["risk"]]:
        merged["risk"] = incoming["risk"]
    existing_keys = {evidence_key(item) for item in merged.get("evidence", [])}
    for item in incoming.get("evidence", []):
        if evidence_key(item) not in existing_keys:
            merged.setdefault("evidence", []).append(item)
            existing_keys.add(evidence_key(item))
    merged["evidence"].sort(key=evidence_key)
    for key in [
        "conflicts_with",
        "application_cases",
        "observation_cycle_ids",
        "linked_observation_ids",
        "published_cycles",
        "adoption_evidence",
        "uncertainties",
    ]:
        merged[key] = sorted(set(merged.get(key, []) + incoming.get(key, [])))
    merged["validation_successes"] = max(
        int(merged.get("validation_successes", 0)), int(incoming.get("validation_successes", 0))
    )
    for shift in incoming.get("shift_evidence", []):
        if shift not in merged.setdefault("shift_evidence", []):
            merged["shift_evidence"].append(shift)
    if incoming.get("action"):
        merged["action"] = incoming["action"]
        merged["owner"] = incoming.get("owner", UNSPECIFIED)
        merged["due"] = incoming.get("due", UNSPECIFIED)
    return merged


def distinct_dates(aggregate: dict[str, Any]) -> set[str]:
    return {item["source_date"] for item in aggregate["evidence"]}


def distinct_sessions(aggregate: dict[str, Any]) -> set[tuple[str, str]]:
    return {(item["source_date"], item["session"]) for item in aggregate["evidence"]}


def assess_candidate(
    aggregate: dict[str, Any], candidate_lookup: dict[str, dict[str, Any]] | None = None
) -> tuple[str, str]:
    candidate_type = aggregate["type"]
    target = aggregate["suggested_target"]
    explicit_complete = "explicit" in aggregate["assertions"] and any(
        item["evidence_complete"] for item in aggregate["evidence"]
    )

    if aggregate.get("conflicts_with"):
        return "conflict", "declared conflict blocks promotion"
    if candidate_type == "action" or target == "action":
        return "pending_confirmation", "external dispatch requires user confirmation"
    if candidate_type == "skill" or target == "skill":
        if int(aggregate.get("validation_successes", 0)) >= 2:
            return "skill_candidate", "workflow has two verified successful runs"
        return "continue_observing", "skill needs two verified successful runs"

    if aggregate["risk"] == "high" and candidate_type not in {"observation", "cognitive_shift"}:
        return "manual_confirmation", "high-risk conclusions, decisions, and actions cannot auto-promote"

    if candidate_type in {"decision", "preference", "project_state"}:
        if not explicit_complete:
            return "manual_confirmation", "requires explicit, complete positive evidence"
        if candidate_type == "decision" and not aggregate.get("adoption_evidence"):
            return "manual_confirmation", "decision requires explicit adoption evidence"
        return "promote_memory", "explicit non-high-risk durable state"

    if candidate_type == "cognitive_shift":
        complete_shift = any(all(shift.get(key) for key in shift) for shift in aggregate["shift_evidence"])
        if not complete_shift:
            return "manual_confirmation", "cognitive shift needs old judgment, new judgment, and change evidence"
        candidate_type = "observation"

    if candidate_type == "observation":
        if aggregate["risk"] == "high" and not aggregate.get("uncertainties"):
            return "manual_confirmation", "high-risk observation needs dated uncertainty"
        if (
            len(distinct_sessions(aggregate)) >= 3
            and len(distinct_dates(aggregate)) >= 2
            and len(aggregate.get("published_cycles", [])) >= 1
        ):
            return "promote_observation", "three sessions, two dates, and one published Review Cycle"
        return "continue_observing", "observation threshold not met"

    if candidate_type == "framework":
        lookup = candidate_lookup or {}
        linked = [
            lookup[candidate_id]
            for candidate_id in aggregate.get("linked_observation_ids", [])
            if candidate_id in lookup
        ]
        verified_observation = any(
            item.get("type") in {"observation", "cognitive_shift"}
            and len(set(item.get("published_cycles", []))) >= 2
            and len(distinct_sessions(item)) >= 3
            and len(distinct_dates(item)) >= 2
            for item in linked
        )
        if verified_observation and len(set(aggregate.get("application_cases", []))) >= 2:
            return "promote_framework", "two published cycles and two application cases"
        return "continue_observing", "linked Observation or framework threshold not met"

    return "continue_observing", "no automatic rule for this candidate type"


def atomic_write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    existed = path.exists()
    if path.exists() and path.read_bytes() == encoded:
        return "unchanged"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return "updated" if existed else "created"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    return atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PipelineError(f"{path} must contain a JSON object")
    return value


def yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def promotion_destination(
    aggregate: dict[str, Any], state: str, memory_root: Path, cognitive_root: Path
) -> Path:
    candidate_id = aggregate["candidate_id"]
    if state == "promote_observation":
        return cognitive_root / "02_observations" / f"{candidate_id}.md"
    if state == "promote_framework":
        return cognitive_root / "03_frameworks" / f"{candidate_id}.md"
    target = aggregate["suggested_target"]
    return memory_root / "wiki" / "auto-promotions" / target / f"{candidate_id}.md"


def render_promotion_page(aggregate: dict[str, Any], state: str) -> str:
    evidence = sorted(aggregate["evidence"], key=evidence_key)
    dates = sorted(distinct_dates(aggregate))
    page_type = {
        "promote_observation": "observation",
        "promote_framework": "framework",
    }.get(state, aggregate["type"])
    lines = [
        "---",
        f"type: {page_type}",
        "status: active",
        "promotion: auto",
        f"candidate_id: {yaml_string(aggregate['candidate_id'])}",
        f"origin_type: {yaml_string(aggregate['type'])}",
        f"domain: {yaml_string(aggregate['domain'])}",
        f"evidence_count: {len(evidence)}",
        f"first_seen: {dates[0]}",
        f"last_seen: {dates[-1]}",
        f"confidence: {float(aggregate['confidence']):.3f}",
        "source_links:",
    ]
    for source_path in sorted({item["source_path"] for item in evidence}):
        lines.append(f"  - {yaml_string(source_path)}")
    lines.extend(
        [
            "---",
            "",
            f"# {aggregate['normalized_claim']}",
            "",
            "## Normalized conclusion",
            "",
            aggregate["normalized_claim"],
            "",
            "## Evidence",
            "",
        ]
    )
    for item in evidence:
        completeness = "complete statement" if item["evidence_complete"] else "bounded fragment"
        lines.append(
            f"- {item['source_date']} {item['session']} — {completeness}; "
            f"coverage `{item['coverage']}`; source `{item['source_path']}`; boundary: {item['evidence_boundary']}"
        )
    if aggregate.get("uncertainties"):
        lines.extend(["", "## Uncertainty", ""])
        lines.extend(f"- {item}" for item in aggregate["uncertainties"])
    lines.extend(
        [
            "",
            "## Audit",
            "",
            "- Generated only from structured candidate records.",
            "- Revoke by changing ledger state through `deposition_pipeline.py revoke`; never delete history.",
            "",
        ]
    )
    return "\n".join(lines)


def review_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(paths: Iterable[Path], memory_root: Path) -> dict[str, str]:
    return {
        portable_source_reference(path, memory_root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths), key=lambda item: item.as_posix())
    }


def validate_review(path: Path) -> dict[str, str]:
    frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    if frontmatter.get("type") != "weekly_review":
        raise PipelineError("Weekly Review type must be weekly_review")
    status = frontmatter.get("status", "")
    if status not in REVIEW_STATUSES:
        raise PipelineError("Weekly Review status must be draft or published")
    cycle = frontmatter.get("review_cycle", "").strip()
    if not cycle:
        raise PipelineError("Weekly Review needs review_cycle")
    if status == "published":
        published_at = frontmatter.get("published_at", "")
        try:
            datetime.fromisoformat(published_at)
        except ValueError as exc:
            raise PipelineError("published Weekly Review needs ISO published_at") from exc
    return frontmatter


def weekly_input_gate(
    review: dict[str, str],
    loaded_sources: list[dict[str, Any]],
    daily_sources: list[Path],
    memory_root: Path,
) -> dict[str, Any]:
    """Describe bounded weekly inputs; missing days do not block publication."""
    cycle = str(review.get("review_cycle") or "")
    match = re.fullmatch(r"(\d{4})-W(\d{2})", cycle)
    if not match:
        return {
            "enforced": False,
            "ready": True,
            "reason": "non_canonical_legacy_cycle",
        }
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise PipelineError(f"invalid ISO review_cycle: {cycle}") from exc
    dates = [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]
    expected = [f"{source_date}:{family}" for source_date in dates for family in ("chatgpt", "codex")]
    observed: dict[str, list[Path]] = defaultdict(list)
    receipt_failures: list[dict[str, str]] = []
    for source, path in zip(loaded_sources, daily_sources, strict=True):
        key = f"{source.get('date')}:{source.get('source_kind')}"
        observed[key].append(path)
        state = CONTRACT.source_deposition_state(path, memory_root)
        if not state.get("ready"):
            receipt_failures.append({
                "key": key,
                "reason": str(state.get("reason") or "not_ready"),
            })
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    duplicates = sorted(key for key, paths in observed.items() if len(paths) != 1)
    coverage_start = str(review.get("coverage_start") or "")
    coverage_end = str(review.get("coverage_end") or "")
    expected_start = f"{dates[0]}T00:00:00+08:00"
    expected_end = f"{dates[-1]}T23:59:59+08:00"
    coverage_matches = coverage_start == expected_start and coverage_end == expected_end
    ready = not unexpected and not duplicates and coverage_matches
    return {
        "enforced": True,
        "ready": ready,
        "complete": not missing and not receipt_failures,
        "evidence_source_count": len(daily_sources) - len(receipt_failures),
        "review_cycle": cycle,
        "expected_dates": dates,
        "expected_source_count": 14,
        "observed_source_count": len(daily_sources),
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
        "receipt_failures": receipt_failures,
        "coverage_matches": coverage_matches,
        "expected_coverage_start": expected_start,
        "expected_coverage_end": expected_end,
    }


def load_ledger(path: Path) -> dict[str, Any]:
    ledger = load_json(path, {"version": 1, "candidates": {}, "reversals": []})
    if ledger.get("version") != 1 or not isinstance(ledger.get("candidates"), dict):
        raise PipelineError("invalid promotion ledger")
    return ledger


def _process_daily_source_unlocked(
    daily_source: Path,
    memory_root: Path,
    cognitive_root: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Record daily evidence and apply only same-day Memory promotion rules."""
    source = load_daily_source(daily_source, memory_root=memory_root)
    incoming_by_id = aggregate_records(source["records"])
    ledger_path = memory_root / "wiki" / "review-cycles" / "promotion-ledger.json"
    ledger = load_ledger(ledger_path)
    results: list[dict[str, Any]] = []
    writes: list[dict[str, str]] = []
    for candidate_id, incoming in sorted(incoming_by_id.items()):
        existing = ledger["candidates"].get(candidate_id)
        aggregate = merge_aggregate(existing, incoming) if existing else incoming
        state, reason = assess_candidate(aggregate)
        if existing and existing.get("state") == "revoked":
            state, reason = "manual_confirmation", "revoked candidates cannot be restored automatically"
        if existing and existing.get("state") == "completed" and aggregate.get("type") == "action":
            state, reason = "completed", "action completion is terminal"
        # Daily execution may update Memory for explicit durable state, but it
        # cannot satisfy Review Cycle thresholds for Observatory promotion.
        if state in {"promote_observation", "promote_framework"}:
            state, reason = "continue_observing", "Observatory promotion requires published Review Cycle processing"
        display_state = state
        if state == "promote_memory" and existing and existing.get("promotion_path"):
            display_state = "already_promoted"
            reason = "stable promotion already exists; evidence metadata refreshed idempotently"
        aggregate["state"] = "promoted" if state == "promote_memory" else state
        result = {"candidate_id": candidate_id, "state": display_state, "reason": reason}
        if state == "promote_memory":
            aggregate["promotion_state"] = state
            destination = promotion_destination(
                aggregate, state, memory_root, cognitive_root
            )
            aggregate["promotion_path"] = destination.as_posix()
            result["promotion_path"] = destination.as_posix()
            if not dry_run:
                writes.append(
                    {
                        "path": destination.as_posix(),
                        "result": atomic_write_text(
                            destination, render_promotion_page(aggregate, state)
                        ),
                    }
                )
        ledger["candidates"][candidate_id] = aggregate
        results.append(result)
    if not dry_run:
        writes.append(
            {
                "path": ledger_path.as_posix(),
                "result": atomic_write_json(ledger_path, ledger),
            }
        )
    return {
        "ok": True,
        "date": source["date"],
        "coverage": source["coverage"],
        "candidate_results": results,
        "writes": writes,
        "dry_run": dry_run,
        "raw_files_modified": 0,
        "actions_dispatched": 0,
    }


def process_daily_source(
    daily_source: Path,
    memory_root: Path,
    cognitive_root: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return _process_daily_source_unlocked(
            daily_source, memory_root, cognitive_root, dry_run=True
        )
    with exclusive_deposition_write_lock():
        return _process_daily_source_unlocked(
            daily_source, memory_root, cognitive_root, dry_run=False
        )


def _process_review_unlocked(
    weekly_review: Path,
    daily_sources: list[Path],
    memory_root: Path,
    cognitive_root: Path,
    *,
    dry_run: bool,
    repair_confirmed_capture_mistake: bool = False,
    expected_prior_weekly_hash: str = "",
) -> dict[str, Any]:
    review = validate_review(weekly_review)
    loaded = [load_daily_source(path, memory_root=memory_root) for path in daily_sources]
    input_gate = weekly_input_gate(review, loaded, daily_sources, memory_root)
    # A captured or compiled source is readable, but only deposition-complete
    # sources may contribute candidates to a published Review Cycle.
    verified = [
        source for source, path in zip(loaded, daily_sources, strict=True)
        if (
            not input_gate.get("enforced")
            or source.get("source_kind") == "weekly_review"
            or str(source.get("date") or "") < CONTRACT.RECEIPT_REQUIRED_FROM
            or CONTRACT.source_deposition_state(path, memory_root).get("ready")
        )
    ]
    aggregates = aggregate_records(record for source in verified for record in source["records"])
    cycle = review["review_cycle"]

    preview_results = []
    if review["status"] == "draft":
        for aggregate in aggregates.values():
            state, reason = assess_candidate(aggregate)
            preview_results.append(
                {"candidate_id": aggregate["candidate_id"], "state": state, "reason": reason}
            )
        return {
            "ok": True,
            "review_cycle": cycle,
            "review_status": "draft",
            "closed": False,
            "writes": [],
            "candidate_results": sorted(preview_results, key=lambda item: item["candidate_id"]),
            "input_gate": input_gate,
            "actions_dispatched": 0,
        }

    if not input_gate.get("ready"):
        raise PipelineError(
            "published Weekly Review input gate is not ready: "
            + json.dumps(input_gate, ensure_ascii=False, sort_keys=True)
        )
    if input_gate.get("enforced"):
        published_at = datetime.fromisoformat(review["published_at"])
        period_end = date.fromisoformat(input_gate["expected_dates"][-1])
        if published_at.date() <= period_end:
            raise PipelineError("published Weekly Review cannot close before its ISO week has ended")

    ledger_path = memory_root / "wiki" / "review-cycles" / "promotion-ledger.json"
    cycle_path = memory_root / "wiki" / "review-cycles" / f"review-cycle-{normalize_text(cycle).replace(' ', '-')}.json"
    ledger = load_ledger(ledger_path)
    existing_cycle = load_json(cycle_path, {}) if cycle_path.exists() else {}
    digest = review_hash(weekly_review)
    if existing_cycle and existing_cycle.get("review_sha256") != digest:
        if not repair_confirmed_capture_mistake:
            raise PipelineError(f"closed Review Cycle {cycle} changed; create a superseding cycle instead")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_prior_weekly_hash):
            raise PipelineError("confirmed capture repair requires --expected-prior-weekly-hash")
        if existing_cycle.get("status") != "closed":
            raise PipelineError("confirmed capture repair requires an existing closed Review Cycle")
        if existing_cycle.get("review_sha256") != expected_prior_weekly_hash:
            raise PipelineError("confirmed capture repair prior weekly hash mismatch")
    elif repair_confirmed_capture_mistake:
        raise PipelineError("confirmed capture repair requires changed content for an existing closed Review Cycle")
    input_hashes = source_hashes(daily_sources, memory_root)
    if existing_cycle and existing_cycle.get("daily_source_hashes") != input_hashes:
        raise PipelineError(
            f"closed Review Cycle {cycle} daily evidence changed; create a superseding cycle instead"
        )

    results: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    writes: list[dict[str, str]] = []
    working_candidates: dict[str, dict[str, Any]] = {}
    for candidate_id, incoming in sorted(aggregates.items()):
        incoming["published_cycles"] = sorted(set(incoming.get("published_cycles", []) + [cycle]))
        existing = ledger["candidates"].get(candidate_id)
        aggregate = merge_aggregate(existing, incoming) if existing else incoming
        working_candidates[candidate_id] = aggregate
    candidate_lookup = {**ledger["candidates"], **working_candidates}

    for candidate_id, aggregate in sorted(working_candidates.items()):
        existing = ledger["candidates"].get(candidate_id)
        state, reason = assess_candidate(aggregate, candidate_lookup)
        promotion_states = {"promote_memory", "promote_observation", "promote_framework"}
        if existing and existing.get("state") == "revoked":
            state, reason = "manual_confirmation", "revoked candidates cannot be restored automatically"
        if existing and existing.get("state") == "completed" and aggregate.get("type") == "action":
            state, reason = "completed", "action completion is terminal"
        display_state = state
        if state in promotion_states and existing and existing.get("promotion_path"):
            display_state = "already_promoted"
            reason = "stable promotion already exists; evidence metadata refreshed idempotently"
        aggregate["state"] = "promoted" if state in promotion_states else state
        if state in promotion_states:
            aggregate["promotion_state"] = state
        result = {"candidate_id": candidate_id, "state": display_state, "reason": reason}
        if state in promotion_states:
            destination = promotion_destination(aggregate, state, memory_root, cognitive_root)
            aggregate["promotion_path"] = destination.as_posix()
            result["promotion_path"] = destination.as_posix()
            if not dry_run:
                write_state = atomic_write_text(destination, render_promotion_page(aggregate, state))
                writes.append({"path": destination.as_posix(), "result": write_state})
        if state in {"pending_confirmation", "completed"}:
            previous = next(
                (
                    action
                    for action in existing_cycle.get("actions", [])
                    if action.get("candidate_id") == candidate_id
                ),
                None,
            )
            action_payload = {
                "candidate_id": candidate_id,
                "action": aggregate.get("action") or aggregate["normalized_claim"],
                "owner": aggregate.get("owner") or UNSPECIFIED,
                "due": aggregate.get("due") or UNSPECIFIED,
                "status": previous.get("status", "pending") if previous else "pending",
            }
            if previous:
                for key in ["completed_at", "completion_evidence"]:
                    if previous.get(key):
                        action_payload[key] = previous[key]
            actions.append(action_payload)
        ledger["candidates"][candidate_id] = aggregate
        results.append(result)

    cycle_payload = {
        "version": 1,
        "review_cycle": cycle,
        "status": "closed",
        "published_at": review["published_at"],
        "review_path": weekly_review.as_posix(),
        "review_sha256": digest,
        "daily_source_paths": sorted(source["path"] for source in loaded),
        "daily_source_hashes": input_hashes,
        "source_dates": sorted(source["date"] for source in loaded),
        "candidate_results": results,
        "actions": actions,
        "input_gate": input_gate,
        "actions_dispatched": 0,
    }
    if existing_cycle.get("capture_corrections"):
        cycle_payload["capture_corrections"] = list(existing_cycle["capture_corrections"])
    if repair_confirmed_capture_mistake:
        cycle_payload["capture_corrections"] = list(existing_cycle.get("capture_corrections", [])) + [
            {
                "kind": "confirmed_capture_mistake",
                "previous_review_sha256": expected_prior_weekly_hash,
                "corrected_review_sha256": digest,
                "corrected_at": review["published_at"],
                "reason": "explicitly authorized correction of an invalid captured weekly synthesis",
            }
        ]
    if not dry_run:
        writes.append({"path": ledger_path.as_posix(), "result": atomic_write_json(ledger_path, ledger)})
        writes.append({"path": cycle_path.as_posix(), "result": atomic_write_json(cycle_path, cycle_payload)})
    return {
        "ok": True,
        "review_cycle": cycle,
        "review_status": "published",
        "closed": not dry_run,
        "dry_run": dry_run,
        "writes": writes,
        "candidate_results": results,
        "actions": actions,
        "input_gate": input_gate,
        "actions_dispatched": 0,
    }


def process_review(
    weekly_review: Path,
    daily_sources: list[Path],
    memory_root: Path,
    cognitive_root: Path,
    *,
    dry_run: bool,
    repair_confirmed_capture_mistake: bool = False,
    expected_prior_weekly_hash: str = "",
) -> dict[str, Any]:
    if dry_run:
        return _process_review_unlocked(
            weekly_review, daily_sources, memory_root, cognitive_root, dry_run=True,
            repair_confirmed_capture_mistake=repair_confirmed_capture_mistake,
            expected_prior_weekly_hash=expected_prior_weekly_hash,
        )
    with exclusive_deposition_write_lock():
        return _process_review_unlocked(
            weekly_review,
            daily_sources,
            memory_root,
            cognitive_root,
            dry_run=False,
            repair_confirmed_capture_mistake=repair_confirmed_capture_mistake,
            expected_prior_weekly_hash=expected_prior_weekly_hash,
        )


def _confirm_actions_unlocked(cycle_path: Path, candidate_ids: list[str]) -> dict[str, Any]:
    cycle = load_json(cycle_path, {})
    if cycle.get("status") != "closed":
        raise PipelineError("actions can be confirmed only in a closed Review Cycle")
    requested = set(candidate_ids)
    found: set[str] = set()
    for action in cycle.get("actions", []):
        if action.get("candidate_id") in requested:
            action["status"] = "confirmed"
            found.add(action["candidate_id"])
    missing = requested - found
    if missing:
        raise PipelineError("unknown action candidate IDs: " + ", ".join(sorted(missing)))
    result = atomic_write_json(cycle_path, cycle)
    return {
        "ok": True,
        "cycle": cycle.get("review_cycle"),
        "confirmed": sorted(found),
        "dispatch_ready": [
            action for action in cycle.get("actions", []) if action.get("status") == "confirmed"
        ],
        "actions_dispatched": int(cycle.get("actions_dispatched", 0)),
        "write": result,
    }


def confirm_actions(cycle_path: Path, candidate_ids: list[str]) -> dict[str, Any]:
    with exclusive_deposition_write_lock():
        return _confirm_actions_unlocked(cycle_path, candidate_ids)


def _complete_actions_unlocked(
    cycle_path: Path,
    ledger_path: Path,
    candidate_ids: list[str],
    completed_at: str,
    evidence: str,
) -> dict[str, Any]:
    try:
        date.fromisoformat(completed_at)
    except ValueError as exc:
        raise PipelineError("completed-at must be YYYY-MM-DD") from exc
    cycle = load_json(cycle_path, {})
    ledger = load_ledger(ledger_path)
    if cycle.get("status") != "closed":
        raise PipelineError("actions can be completed only in a closed Review Cycle")
    requested = set(candidate_ids)
    found: set[str] = set()
    for action in cycle.get("actions", []):
        candidate_id = action.get("candidate_id")
        if candidate_id not in requested:
            continue
        candidate = ledger["candidates"].get(candidate_id)
        if not candidate or candidate.get("type") != "action":
            raise PipelineError(f"unknown action candidate: {candidate_id}")
        action["status"] = "completed"
        action["completed_at"] = completed_at
        action["completion_evidence"] = evidence
        candidate["state"] = "completed"
        candidate["completed_at"] = completed_at
        candidate["completion_evidence"] = evidence
        found.add(candidate_id)
    missing = requested - found
    if missing:
        raise PipelineError("unknown action candidate IDs: " + ", ".join(sorted(missing)))
    for result in cycle.get("candidate_results", []):
        if result.get("candidate_id") in found:
            result["state"] = "completed"
            result["reason"] = "source-backed completion recorded"
    cycle_write = atomic_write_json(cycle_path, cycle)
    ledger_write = atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "cycle": cycle.get("review_cycle"),
        "completed": sorted(found),
        "completed_at": completed_at,
        "cycle_write": cycle_write,
        "ledger_write": ledger_write,
        "actions_dispatched": int(cycle.get("actions_dispatched", 0)),
    }


def complete_actions(
    cycle_path: Path,
    ledger_path: Path,
    candidate_ids: list[str],
    completed_at: str,
    evidence: str,
) -> dict[str, Any]:
    with exclusive_deposition_write_lock():
        return _complete_actions_unlocked(
            cycle_path, ledger_path, candidate_ids, completed_at, evidence
        )


def month_bounds(month: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(month + "-01")
    except ValueError as exc:
        raise PipelineError("month must be YYYY-MM") from exc
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start, end


def in_month(day: str, start: date, end: date) -> bool:
    parsed = date.fromisoformat(day)
    return start <= parsed < end


def case_string(value: dict[str, Any], key: str, case_id: str = "<unknown>") -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise PipelineError(f"case {case_id} needs non-empty {key}")
    return item.strip()


def validate_case_source(source: str, memory_root: Path, case_id: str) -> str:
    if source.startswith("/") or ".." in Path(source).parts:
        raise PipelineError(f"case {case_id} source must be a vault-relative Memory path")
    normalized = source[:-3] if source.endswith(".md") else source
    allowed_core_sources = {"wiki/projects", "wiki/decisions", "wiki/preferences"}
    if not (
        normalized.startswith("wiki/sources/")
        or normalized.startswith("wiki/claims/")
        or normalized in allowed_core_sources
    ):
        raise PipelineError(
            f"case {case_id} source must be under wiki/sources or an allowed core Memory page"
        )
    path = memory_root / (normalized + ".md")
    if not path.exists():
        raise PipelineError(f"case {case_id} source does not exist: {normalized}")
    return normalized


def validate_case_evidence_item(
    item: Any, memory_root: Path, case_id: str, *, require_date: bool
) -> dict[str, str]:
    if not isinstance(item, dict):
        raise PipelineError(f"case {case_id} evidence items must be objects")
    text = case_string(item, "event" if require_date else "text", case_id)
    evidence_type = case_string(item, "evidence_type", case_id)
    if evidence_type not in CASE_EVIDENCE_TYPES:
        raise PipelineError(f"case {case_id} has invalid evidence_type: {evidence_type}")
    source = validate_case_source(case_string(item, "source", case_id), memory_root, case_id)
    result = {
        "evidence_type": evidence_type,
        "source": source,
        "event" if require_date else "text": text,
    }
    if require_date:
        event_date = case_string(item, "date", case_id)
        try:
            date.fromisoformat(event_date)
        except ValueError as exc:
            raise PipelineError(f"case {case_id} timeline date must be YYYY-MM-DD") from exc
        result["date"] = event_date
    return result


def load_case_manifest(path: Path, month: str, memory_root: Path) -> list[dict[str, Any]]:
    payload = load_json(path, {})
    if payload.get("version") != 1:
        raise PipelineError("monthly case manifest version must be 1")
    if payload.get("month") != month:
        raise PipelineError(f"monthly case manifest month must be {month}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise PipelineError("monthly case manifest cases must be an array")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise PipelineError("monthly case manifest entries must be objects")
        case_id = case_string(raw, "case_id")
        title = case_string(raw, "title", case_id)
        line = case_string(raw, "line", case_id)
        status = case_string(raw, "status", case_id)
        if line not in CASE_LINES:
            raise PipelineError(f"case {case_id} has invalid line: {line}")
        if status not in CASE_STATUSES:
            raise PipelineError(f"case {case_id} has invalid status: {status}")
        evidence_mode = case_string(raw, "evidence_mode", case_id)
        if evidence_mode not in CASE_EVIDENCE_MODES:
            raise PipelineError(f"case {case_id} has invalid evidence_mode: {evidence_mode}")
        if evidence_mode == "compliance_abstracted" and line != "work":
            raise PipelineError(
                f"case {case_id} compliance_abstracted mode is reserved for the work line"
            )
        expected = stable_case_id(line, title)
        if case_id != expected:
            raise PipelineError(f"case_id {case_id} is unstable; expected {expected}")
        if case_id in seen:
            raise PipelineError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        progress_basis = case_string(raw, "progress_basis", case_id)
        if progress_basis not in CASE_EVIDENCE_TYPES:
            raise PipelineError(f"case {case_id} has invalid progress_basis: {progress_basis}")
        timeline_raw = raw.get("timeline")
        if not isinstance(timeline_raw, list) or not timeline_raw:
            raise PipelineError(f"case {case_id} needs a non-empty timeline")
        lessons_raw = raw.get("lessons", [])
        open_items_raw = raw.get("open_items", [])
        prior_links_raw = raw.get("prior_links", [])
        if not all(
            isinstance(value, list)
            for value in [lessons_raw, open_items_raw, prior_links_raw]
        ):
            raise PipelineError(
                f"case {case_id} lessons, open_items, and prior_links must be arrays"
            )
        timeline = [
            validate_case_evidence_item(item, memory_root, case_id, require_date=True)
            for item in timeline_raw
        ]
        target_year, target_month = (int(value) for value in month.split("-"))
        last_event = max(date.fromisoformat(item["date"]) for item in timeline)
        inactivity_months = (
            target_year * 12 + target_month - (last_event.year * 12 + last_event.month)
        )
        if line == "work" and inactivity_months >= 3 and status != "archived":
            raise PipelineError(
                f"case {case_id} has no work-line update for three months and must be archived"
            )
        lessons = [
            validate_case_evidence_item(item, memory_root, case_id, require_date=False)
            for item in lessons_raw
        ]
        open_items: list[dict[str, str]] = []
        for item in open_items_raw:
            if not isinstance(item, dict):
                raise PipelineError(f"case {case_id} open_items must be objects")
            open_items.append(
                {
                    "text": case_string(item, "text", case_id),
                    "source": validate_case_source(
                        case_string(item, "source", case_id), memory_root, case_id
                    ),
                }
            )
        prior_links: list[dict[str, str]] = []
        for item in prior_links_raw:
            if not isinstance(item, dict):
                raise PipelineError(f"case {case_id} prior_links must be objects")
            prior_month = case_string(item, "month", case_id)
            try:
                prior_start = date.fromisoformat(prior_month + "-01")
                current_start = date.fromisoformat(month + "-01")
            except ValueError as exc:
                raise PipelineError(f"case {case_id} prior link month must be YYYY-MM") from exc
            if prior_start >= current_start:
                raise PipelineError(f"case {case_id} prior link must target an earlier month")
            prior_case_id = case_string(item, "case_id", case_id)
            relation = case_string(item, "relation", case_id)
            if relation not in CASE_RELATIONS:
                raise PipelineError(f"case {case_id} has invalid prior relation: {relation}")
            if relation == "continuation" and prior_case_id != case_id:
                raise PipelineError(
                    f"case {case_id} continuation must reuse the same stable case_id"
                )
            prior_manifest = (
                memory_root / "wiki" / "reviews" / f"monthly-case-manifest-{prior_month}.json"
            )
            if not prior_manifest.exists():
                raise PipelineError(
                    f"case {case_id} prior manifest does not exist: {prior_month}"
                )
            prior_payload = load_json(prior_manifest, {})
            prior_case = next(
                (
                    value
                    for value in prior_payload.get("cases", [])
                    if isinstance(value, dict) and value.get("case_id") == prior_case_id
                ),
                None,
            )
            if not prior_case:
                raise PipelineError(
                    f"case {case_id} prior case does not exist: {prior_case_id}"
                )
            if relation == "continuation":
                current_events = {
                    (event["date"], event["event"], event["source"], event["evidence_type"])
                    for event in timeline
                }
                prior_events = {
                    (
                        value.get("date"),
                        value.get("event"),
                        str(value.get("source", "")).removesuffix(".md"),
                        value.get("evidence_type"),
                    )
                    for value in prior_case.get("timeline", [])
                    if isinstance(value, dict)
                }
                if not prior_events.issubset(current_events):
                    raise PipelineError(
                        f"case {case_id} continuation must carry the prior timeline forward"
                    )
            prior_links.append(
                {
                    "month": prior_month,
                    "case_id": prior_case_id,
                    "relation": relation,
                    "note": case_string(item, "note", case_id),
                }
            )
        cases.append(
            {
                "case_id": case_id,
                "title": title,
                "line": line,
                "status": status,
                "evidence_mode": evidence_mode,
                "inactivity_months": inactivity_months,
                "summary": case_string(raw, "summary", case_id),
                "current_progress": case_string(raw, "current_progress", case_id),
                "progress_basis": progress_basis,
                "timeline_scope": case_string(raw, "timeline_scope", case_id),
                "evidence_boundary": case_string(raw, "evidence_boundary", case_id),
                "timeline": sorted(
                    timeline, key=lambda item: (item["date"], item["source"], item["event"])
                ),
                "lessons": sorted(lessons, key=lambda item: (item["source"], item["text"])),
                "open_items": sorted(open_items, key=lambda item: (item["source"], item["text"])),
                "prior_links": sorted(
                    prior_links,
                    key=lambda item: (item["month"], item["relation"], item["case_id"]),
                ),
            }
        )
    return cases


def case_source_link(source: str) -> str:
    return f"[[{source}|来源]]"


def render_case_sections(cases: list[dict[str, Any]]) -> list[str]:
    labels = {"work": "工作线", "life": "生活线"}
    status_labels = {
        "planned": "已规划",
        "completed": "已完成",
        "in_progress": "进行中",
        "blocked": "受阻",
        "on_hold": "暂缓",
        "unknown": "状态待确认",
        "archived": "已归档",
    }
    evidence_mode_labels = {
        "direct_source": "直接来源",
        "bounded_partial": "有边界的部分证据",
        "compliance_abstracted": "合规抽象",
    }
    evidence_type_labels = {"reported": "已报告", "inferred": "有边界推断"}
    relation_labels = {
        "continuation": "延续",
        "related": "相关",
        "predecessor": "前序",
    }
    lines: list[str] = []
    for line in ["work", "life"]:
        selected = [case for case in cases if case["line"] == line]
        unfinished = sum(case["status"] not in {"completed", "archived"} for case in selected)
        lines.extend(
            [
                f"## {labels[line]}",
                "",
                f"- 沉淀案例：{len(selected)} 个",
                f"- 未完成案例：{unfinished} 个",
                "",
            ]
        )
        if line == "work":
            lines.extend(
                [
                    "- 工作案例允许使用合规抽象模式：不要求披露项目名、客户、样品、批次、原始数据或精确参数，可只沉淀认知结论、判断框架、方法思路和阶段进度。",
                    "- 合规抽象不等于事实已外部验证；不补写具体数据、实验结果、因果或安全结论。",
                    "",
                ]
            )
        if not selected:
            lines.extend(["- 本月无可验证案例。", ""])
            continue
        for case in selected:
            lines.extend(
                [
                    f"### {case['title']}",
                    "",
                    f"- 案例编号：`{case['case_id']}`",
                    f"- 状态：{status_labels[case['status']]}",
                    f"- 证据模式：{evidence_mode_labels[case['evidence_mode']]}",
                    f"- 距最后更新：{case['inactivity_months']} 个自然月",
                    f"- 案例概要：{case['summary']}",
                    f"- 当前进度：{case['current_progress']}（{evidence_type_labels[case['progress_basis']]}）",
                    f"- 时间线范围：{case['timeline_scope']}",
                    f"- 证据边界：{case['evidence_boundary']}",
                    "",
                    "#### 跨月关联",
                    "",
                ]
            )
            if case["prior_links"]:
                for link in case["prior_links"]:
                    lines.append(
                        f"- {relation_labels[link['relation']]} → "
                        f"[[monthly-review-{link['month']}|{link['month']} 月度复盘]] "
                        f"`{link['case_id']}`：{link['note']}"
                    )
            else:
                lines.append("- 无更早月度案例可关联。")
            lines.extend(
                [
                    "",
                    "#### 时间线",
                    "",
                ]
            )
            for event in case["timeline"]:
                lines.append(
                    f"- {event['date']} [{evidence_type_labels[event['evidence_type']]}] {event['event']} "
                    f"{case_source_link(event['source'])}"
                )
            lines.extend(["", "#### 经验沉淀", ""])
            if case["lessons"]:
                for lesson in case["lessons"]:
                    lines.append(
                        f"- [{evidence_type_labels[lesson['evidence_type']]}] {lesson['text']} "
                        f"{case_source_link(lesson['source'])}"
                    )
            else:
                lines.append("- 尚无可验证的经验结论。")
            lines.extend(["", "#### 未完成项", ""])
            if case["open_items"]:
                for item in case["open_items"]:
                    lines.append(f"- {item['text']} {case_source_link(item['source'])}")
            else:
                lines.append("- 无。")
            lines.append("")
    return lines


def render_monthly_review(
    month: str,
    cycles: list[dict[str, Any]],
    daily_sources: list[dict[str, Any]],
    uncovered: list[str],
    ledger: dict[str, Any],
    migration_issues: list[dict[str, str]],
    cases: list[dict[str, Any]],
) -> str:
    candidate_ids = sorted(
        {
            result["candidate_id"]
            for cycle in cycles
            for result in cycle.get("candidate_results", [])
        }
        | {
            record["candidate_id"]
            for source in daily_sources
            for record in source.get("records", [])
        }
    )
    candidates = [ledger.get("candidates", {}).get(candidate_id, {}) for candidate_id in candidate_ids]
    candidates = [item for item in candidates if item]
    candidate_state_labels = {
        "completed": "已完成",
        "pending_confirmation": "待确认",
        "continue_observing": "继续观察",
        "promoted": "已晋升",
        "conflict": "存在冲突",
        "superseded": "已被取代",
        "revoked": "已撤销",
        "manual_confirmation": "需人工确认",
        "skill_candidate": "技能候选",
    }
    sections: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        display_claim = candidate.get("display_claim_zh") or candidate["normalized_claim"]
        label = f"`{candidate['candidate_id']}` — {display_claim}"
        state = candidate.get("state", "continue_observing")
        if state in {"conflict", "superseded"} or candidate["type"] == "decision" and candidate.get("superseded_by"):
            sections["Decision conflicts"].append(label)
        elif state == "skill_candidate":
            sections["Skill candidates"].append(label)
        elif candidate["type"] == "framework":
            sections["Effective or failed frameworks"].append(
                label + f" — {candidate_state_labels.get(state, state)}"
            )
        elif candidate["type"] == "cognitive_shift":
            sections["Cognitive changes"].append(
                label + f" — {candidate_state_labels.get(state, state)}"
            )
        elif candidate["type"] == "project_state":
            sections["Project drift"].append(
                label + f" — {candidate_state_labels.get(state, state)}"
            )
        else:
            sections["Persistent patterns"].append(
                label + f" — {candidate_state_labels.get(state, state)}"
            )
    unfinished = [
        action
        for cycle in cycles
        for action in cycle.get("actions", [])
        if action.get("status") not in {"completed", "dispatched"}
    ]
    lines = [
        "---",
        "type: monthly_review",
        f"month: {month}",
        "status: published",
        "generated_by: deposition_pipeline",
        f"review_cycle_count: {len(cycles)}",
        f"candidate_count: {len(candidate_ids)}",
        "---",
        "",
        f"# 月度沉淀复盘 — {month}",
        "",
        "## 覆盖范围",
        "",
        f"- 已关闭的复盘周期：{', '.join(cycle['review_cycle'] for cycle in cycles) or '无'}",
        f"- 纳入的日报：{len(daily_sources)} 份",
        f"- 未被复盘周期覆盖的日报：{'、'.join(uncovered) or '无'}",
        "- 候选 ID 已去重；部分覆盖只计为有边界的正向证据。",
        "",
        "## 本月沉淀概览",
        "",
        f"- 高维抽象与跨案例候选：{len(candidate_ids)} 个",
        f"- 工作案例：{sum(case['line'] == 'work' for case in cases)} 个",
        f"- 生活案例：{sum(case['line'] == 'life' for case in cases)} 个",
        f"- 未完成案例：{sum(case['status'] not in {'completed', 'archived'} for case in cases)} 个",
        f"- 已归档工作案例：{sum(case['line'] == 'work' and case['status'] == 'archived' for case in cases)} 个",
        f"- 与历史月报关联的案例：{sum(bool(case['prior_links']) for case in cases)} 个",
        f"- 合规抽象工作案例：{sum(case['evidence_mode'] == 'compliance_abstracted' for case in cases)} 个",
        "",
    ]
    if migration_issues:
        lines.extend(["## 来源迁移或验证问题", ""])
        for issue in migration_issues:
            lines.append(
                f"- {issue.get('date', '日期未知')} — `{issue['path']}` — {issue['reason']}"
            )
        lines.append("")
    headings = {
        "Persistent patterns": "持续模式",
        "Cognitive changes": "认知变化",
        "Effective or failed frameworks": "有效或失败的框架",
        "Decision conflicts": "决策冲突",
        "Project drift": "项目偏移",
        "Skill candidates": "技能候选项",
    }
    lines.extend(["## 高维抽象与跨案例模式", ""])
    for heading, display_heading in headings.items():
        lines.extend([f"### {display_heading}", ""])
        lines.extend(f"- {item}" for item in sorted(sections.get(heading, [])))
        if not sections.get(heading):
            lines.append("- 未记录。")
        lines.append("")
    lines.extend(render_case_sections(cases))
    lines.extend(["## 未完成行动", ""])
    if unfinished:
        action_status_labels = {
            "pending": "待确认",
            "confirmed": "已确认、待派发",
        }
        for action in unfinished:
            candidate = ledger.get("candidates", {}).get(action.get("candidate_id"), {})
            display_action = candidate.get("display_claim_zh") or action.get("action")
            owner = action.get("owner", UNSPECIFIED)
            due = action.get("due", UNSPECIFIED)
            lines.append(
                f"- [{action_status_labels.get(action.get('status', 'pending'), action.get('status'))}] "
                f"{display_action} — 负责人：{'未指定' if owner == UNSPECIFIED else owner} "
                f"— 期限：{'未指定' if due == UNSPECIFIED else due}"
            )
    else:
        lines.append("- 未记录。")
    lines.extend(
        [
            "",
            "## 校准",
            "",
            "- 已根据台账复核自动晋升，未删除历史记录。",
            "- 失效决策必须保持可见；只有在明确存在并链接后继决策时，才可标记为“已被取代”。",
            "",
        ]
    )
    return "\n".join(lines)


def _build_monthly_review_unlocked(
    month: str, source_dir: Path, cycle_dir: Path, memory_root: Path, *, dry_run: bool
) -> dict[str, Any]:
    start, end = month_bounds(month)
    cycles: list[dict[str, Any]] = []
    for path in sorted(cycle_dir.glob("review-cycle-*.json")):
        cycle = load_json(path, {})
        published_at = cycle.get("published_at", "")
        try:
            published_date = datetime.fromisoformat(published_at).date()
        except ValueError:
            continue
        if start <= published_date < end and cycle.get("status") == "closed":
            cycles.append(cycle)
    daily_sources: list[dict[str, Any]] = []
    migration_issues: list[dict[str, str]] = []
    daily_paths = {
        *source_dir.rglob("chatgpt-daily-report-*.md"),
        *source_dir.rglob("codex-daily-report-*.md"),
    }
    for path in sorted(daily_paths):
        try:
            source = load_daily_source(path, allow_legacy=True, memory_root=memory_root)
        except PipelineError as exc:
            migration_issues.append(
                {
                    "path": portable_source_reference(path, memory_root),
                    "date": "unknown",
                    "reason": str(exc),
                }
            )
            continue
        if in_month(source["date"], start, end):
            daily_sources.append(source)
            if source["legacy"]:
                migration_issues.append(
                    {
                        "path": source["path"],
                        "date": source["date"],
                        "reason": "legacy source has no validated Structured Candidates block",
                    }
                )
    covered = {day for cycle in cycles for day in cycle.get("source_dates", [])}
    uncovered = sorted(
        {source["date"] for source in daily_sources if source["date"] not in covered}
    )
    ledger = load_ledger(cycle_dir / "promotion-ledger.json")
    output = memory_root / "wiki" / "reviews" / f"monthly-review-{month}.md"
    case_manifest = memory_root / "wiki" / "reviews" / f"monthly-case-manifest-{month}.json"
    cases = load_case_manifest(case_manifest, month, memory_root) if case_manifest.exists() else []
    text = render_monthly_review(
        month, cycles, daily_sources, uncovered, ledger, migration_issues, cases
    )
    write = "dry-run" if dry_run else atomic_write_text(output, text)
    return {
        "ok": True,
        "month": month,
        "closed_cycles": [cycle["review_cycle"] for cycle in cycles],
        "uncovered_daily_reports": uncovered,
        "daily_source_counts": {
            kind: sum(source.get("source_kind") == kind for source in daily_sources)
            for kind in ["chatgpt", "codex", "weekly_review", "legacy"]
        },
        "source_migration_issues": migration_issues,
        "case_manifest": case_manifest.as_posix() if case_manifest.exists() else None,
        "case_count": len(cases),
        "work_case_count": sum(case["line"] == "work" for case in cases),
        "life_case_count": sum(case["line"] == "life" for case in cases),
        "unfinished_case_count": sum(
            case["status"] not in {"completed", "archived"} for case in cases
        ),
        "archived_work_case_count": sum(
            case["line"] == "work" and case["status"] == "archived" for case in cases
        ),
        "output": output.as_posix(),
        "write": write,
    }


def build_monthly_review(
    month: str, source_dir: Path, cycle_dir: Path, memory_root: Path, *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return _build_monthly_review_unlocked(
            month, source_dir, cycle_dir, memory_root, dry_run=True
        )
    with exclusive_deposition_write_lock():
        return _build_monthly_review_unlocked(
            month, source_dir, cycle_dir, memory_root, dry_run=False
        )


def _revoke_candidate_unlocked(ledger_path: Path, candidate_id: str, reason: str, revoked_at: str) -> dict[str, Any]:
    ledger = load_ledger(ledger_path)
    candidate = ledger["candidates"].get(candidate_id)
    if not candidate:
        raise PipelineError(f"unknown candidate: {candidate_id}")
    reversal = {
        "candidate_id": candidate_id,
        "reason": reason,
        "revoked_at": revoked_at,
        "previous_state": candidate.get("state"),
    }
    if reversal not in ledger["reversals"]:
        ledger["reversals"].append(reversal)
    candidate["state"] = "revoked"
    page_path = Path(candidate.get("promotion_path", "")) if candidate.get("promotion_path") else None
    page_write = "not-promoted"
    if page_path and page_path.exists():
        text = page_path.read_text(encoding="utf-8")
        text = re.sub(r"^status:\s*active\s*$", "status: revoked", text, count=1, flags=re.MULTILINE)
        marker = f"- {revoked_at}: {reason}"
        if marker not in text:
            text = text.rstrip() + f"\n\n## Reversal\n\n{marker}\n"
        page_write = atomic_write_text(page_path, text)
    ledger_write = atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "state": "revoked",
        "ledger_write": ledger_write,
        "page_write": page_write,
    }


def revoke_candidate(ledger_path: Path, candidate_id: str, reason: str, revoked_at: str) -> dict[str, Any]:
    with exclusive_deposition_write_lock():
        return _revoke_candidate_unlocked(ledger_path, candidate_id, reason, revoked_at)


def _supersede_decision_unlocked(
    ledger_path: Path,
    candidate_id: str,
    successor_id: str,
    reason: str,
    superseded_at: str,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_path)
    candidate = ledger["candidates"].get(candidate_id)
    successor = ledger["candidates"].get(successor_id)
    if not candidate or candidate.get("type") != "decision":
        raise PipelineError(f"superseded candidate must be a known decision: {candidate_id}")
    if not successor or successor.get("type") != "decision":
        raise PipelineError(f"successor must be a known decision: {successor_id}")
    if "explicit" not in successor.get("assertions", []) or not successor.get("adoption_evidence"):
        raise PipelineError("successor decision needs explicit adoption evidence")
    candidate["state"] = "superseded"
    candidate["superseded_by"] = successor_id
    event = {
        "candidate_id": candidate_id,
        "successor_id": successor_id,
        "reason": reason,
        "superseded_at": superseded_at,
        "previous_state": candidate.get("promotion_state") or "promoted",
    }
    if event not in ledger["reversals"]:
        ledger["reversals"].append(event)
    page_path = Path(candidate.get("promotion_path", "")) if candidate.get("promotion_path") else None
    page_write = "not-promoted"
    if page_path and page_path.exists():
        text = page_path.read_text(encoding="utf-8")
        text = re.sub(
            r"^status:\s*(?:active|revoked)\s*$",
            "status: superseded",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if "superseded_by:" not in text:
            text = text.replace(
                f"candidate_id: {yaml_string(candidate_id)}\n",
                f"candidate_id: {yaml_string(candidate_id)}\n"
                f"superseded_by: {yaml_string(successor_id)}\n",
                1,
            )
        marker = f"- {superseded_at}: {reason}; successor `{successor_id}`"
        if marker not in text:
            text = text.rstrip() + f"\n\n## Supersession\n\n{marker}\n"
        page_write = atomic_write_text(page_path, text)
    ledger_write = atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "state": "superseded",
        "superseded_by": successor_id,
        "ledger_write": ledger_write,
        "page_write": page_write,
    }


def supersede_decision(
    ledger_path: Path,
    candidate_id: str,
    successor_id: str,
    reason: str,
    superseded_at: str,
) -> dict[str, Any]:
    with exclusive_deposition_write_lock():
        return _supersede_decision_unlocked(
            ledger_path, candidate_id, successor_id, reason, superseded_at
        )


def replay_legacy(daily_source: Path, weekly_review: Path) -> dict[str, Any]:
    review = validate_review(weekly_review)
    source = load_daily_source(daily_source, allow_legacy=True)
    warnings: list[str] = []
    if source["legacy"]:
        warnings.append("legacy source has no machine-validated Structured Candidates block")
    if review["status"] == "draft":
        warnings.append("draft Weekly Review cannot close a cycle or count toward promotion")
    return {
        "ok": True,
        "mode": "read-only-replay",
        "daily_source": daily_source.as_posix(),
        "daily_date": source["date"],
        "daily_coverage": source["coverage"],
        "candidate_count": len(source["records"]),
        "review_cycle": review["review_cycle"],
        "review_status": review["status"],
        "would_close_cycle": review["status"] == "published",
        "writes": [],
        "warnings": warnings,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    candidate_id = sub.add_parser("candidate-id")
    candidate_id.add_argument("--type", required=True)
    candidate_id.add_argument("--domain", required=True)
    candidate_id.add_argument("--claim", required=True)

    case_id = sub.add_parser("case-id")
    case_id.add_argument("--line", choices=sorted(CASE_LINES), required=True)
    case_id.add_argument("--title", required=True)

    validate_cases = sub.add_parser("validate-cases")
    validate_cases.add_argument("--manifest", type=Path, required=True)
    validate_cases.add_argument("--month", required=True)
    validate_cases.add_argument("--memory-root", type=Path, required=True)

    validate_source = sub.add_parser("validate-source")
    validate_source.add_argument("input", type=Path)

    daily = sub.add_parser("daily")
    daily.add_argument("--daily-source", type=Path, required=True)
    daily.add_argument("--memory-root", type=Path, required=True)
    daily.add_argument("--cognitive-root", type=Path, required=True)
    daily.add_argument("--dry-run", action="store_true")

    review = sub.add_parser("review")
    review.add_argument("--weekly-review", type=Path, required=True)
    review.add_argument("--daily-source", type=Path, action="append", default=[])
    review.add_argument("--memory-root", type=Path, required=True)
    review.add_argument("--cognitive-root", type=Path, required=True)
    review.add_argument("--dry-run", action="store_true")
    review.add_argument("--repair-confirmed-capture-mistake", action="store_true")
    review.add_argument("--expected-prior-weekly-hash", default="")

    confirm = sub.add_parser("confirm-actions")
    confirm.add_argument("--cycle", type=Path, required=True)
    confirm.add_argument("candidate_ids", nargs="+")

    complete = sub.add_parser("complete-actions")
    complete.add_argument("--cycle", type=Path, required=True)
    complete.add_argument("--ledger", type=Path, required=True)
    complete.add_argument("--completed-at", required=True)
    complete.add_argument("--evidence", required=True)
    complete.add_argument("candidate_ids", nargs="+")

    monthly = sub.add_parser("monthly")
    monthly.add_argument("--month", required=True)
    monthly.add_argument("--source-dir", type=Path, required=True)
    monthly.add_argument("--cycle-dir", type=Path, required=True)
    monthly.add_argument("--memory-root", type=Path, required=True)
    monthly.add_argument("--dry-run", action="store_true")

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--ledger", type=Path, required=True)
    revoke.add_argument("--candidate-id", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--revoked-at", required=True)

    supersede = sub.add_parser("supersede")
    supersede.add_argument("--ledger", type=Path, required=True)
    supersede.add_argument("--candidate-id", required=True)
    supersede.add_argument("--successor-id", required=True)
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--superseded-at", required=True)

    replay = sub.add_parser("replay")
    replay.add_argument("--daily-source", type=Path, required=True)
    replay.add_argument("--weekly-review", type=Path, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "candidate-id":
            result: dict[str, Any] = {
                "candidate_id": stable_candidate_id(args.type, args.domain, args.claim)
            }
        elif args.command == "case-id":
            result = {"case_id": stable_case_id(args.line, args.title)}
        elif args.command == "validate-cases":
            cases = load_case_manifest(args.manifest, args.month, args.memory_root)
            result = {
                "ok": True,
                "month": args.month,
                "case_count": len(cases),
                "work_case_count": sum(case["line"] == "work" for case in cases),
                "life_case_count": sum(case["line"] == "life" for case in cases),
                "unfinished_case_count": sum(
                    case["status"] not in {"completed", "archived"} for case in cases
                ),
                "archived_work_case_count": sum(
                    case["line"] == "work" and case["status"] == "archived"
                    for case in cases
                ),
            }
        elif args.command == "validate-source":
            source = load_daily_source(args.input)
            result = {
                "ok": True,
                "date": source["date"],
                "coverage": source["coverage"],
                "source_kind": source["source_kind"],
                "candidate_count": len(source["records"]),
            }
        elif args.command == "daily":
            result = process_daily_source(
                args.daily_source,
                args.memory_root,
                args.cognitive_root,
                dry_run=args.dry_run,
            )
        elif args.command == "review":
            result = process_review(
                args.weekly_review,
                args.daily_source,
                args.memory_root,
                args.cognitive_root,
                dry_run=args.dry_run,
                repair_confirmed_capture_mistake=args.repair_confirmed_capture_mistake,
                expected_prior_weekly_hash=args.expected_prior_weekly_hash,
            )
        elif args.command == "confirm-actions":
            result = confirm_actions(args.cycle, args.candidate_ids)
        elif args.command == "complete-actions":
            result = complete_actions(
                args.cycle,
                args.ledger,
                args.candidate_ids,
                args.completed_at,
                args.evidence,
            )
        elif args.command == "monthly":
            result = build_monthly_review(
                args.month,
                args.source_dir,
                args.cycle_dir,
                args.memory_root,
                dry_run=args.dry_run,
            )
        elif args.command == "revoke":
            result = revoke_candidate(
                args.ledger, args.candidate_id, args.reason, args.revoked_at
            )
        elif args.command == "supersede":
            result = supersede_decision(
                args.ledger,
                args.candidate_id,
                args.successor_id,
                args.reason,
                args.superseded_at,
            )
        else:
            result = replay_legacy(args.daily_source, args.weekly_review)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, PipelineError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
