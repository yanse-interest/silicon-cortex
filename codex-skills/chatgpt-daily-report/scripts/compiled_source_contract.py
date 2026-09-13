#!/usr/bin/env python3
"""Versioned contract for compiled ChatGPT and Codex daily sources.

This module is the canonical parser/validator shared by the daily deposition
pipeline and read-only Dashboard consumers.  It deliberately separates source
trust from consumer-specific presentation and routing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 1
RECEIPT_VERSION = 1
RECEIPT_REQUIRED_FROM = "2026-08-23"
ALLOWED_COVERAGE = {"complete", "partial", "unknown"}
PRIVACY_MODES = {"non_reconstructable", "compliance_abstracted"}
INSTRUMENT_TYPES = {
    "通用分析仪器", "质谱", "高分辨质谱", "飞行时间质谱", "三重四极杆质谱",
    "液相色谱", "自动进样器", "仪器软件",
}
INSTRUMENT_SOURCE_TYPES = {"daily_report", "chatgpt_conversation"}
INSTRUMENT_SUPPORT_LEVELS = {"direct_answer", "source_summary"}


class FamilySpec:
    def __init__(
        self,
        family: str,
        directory: str,
        source_type: str,
        raw_type: str,
        filename_prefix: str,
        unit_prefix: str,
        allowed_statuses: frozenset[str],
    ) -> None:
        self.family = family
        self.directory = directory
        self.source_type = source_type
        self.raw_type = raw_type
        self.filename_prefix = filename_prefix
        self.unit_prefix = unit_prefix
        self.allowed_statuses = allowed_statuses

    def raw_source(self, source_date: str) -> str:
        return (
            f"raw/conversations/{self.directory}/{source_date[:4]}/"
            f"{self.filename_prefix}-daily-report-{source_date}.md"
        )


FAMILY_SPECS = {
    "chatgpt": FamilySpec(
        "chatgpt", "chatgpt-daily", "chatgpt_daily_source_summary",
        "chatgpt_daily_report", "chatgpt", "S",
        frozenset({"ready", "access_incomplete", "no_sessions", "no_relevant_content"}),
    ),
    "codex": FamilySpec(
        "codex", "codex-daily", "codex_daily_source_summary",
        "codex_daily_report", "codex", "T",
        frozenset({"ready", "access_incomplete", "no_tasks", "no_relevant_content"}),
    ),
}
TYPE_TO_FAMILY = {spec.source_type: family for family, spec in FAMILY_SPECS.items()}
DIRECTORY_TO_FAMILY = {spec.directory: family for family, spec in FAMILY_SPECS.items()}


class SourceContractError(ValueError):
    """Stable, machine-readable validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CompiledDailySource:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


def _error(code: str, message: str) -> SourceContractError:
    return SourceContractError(code, message)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[\s\-_—–]+", " ", value)
    value = re.sub(r"[^\w\u4e00-\u9fff ]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def stable_candidate_id(candidate_type: str, domain: str, normalized_claim: str) -> str:
    basis = "|".join(normalize_text(value) for value in (candidate_type, domain, normalized_claim))
    return "cand-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def normalize_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def validate_structured_candidate_evidence(
    candidate: dict[str, Any],
    *,
    unit_ids: list[str],
    corpus: str,
    validate_text: Any = None,
) -> dict[str, Any]:
    """Validate receipt-era privacy and verbatim evidence fields.

    ``validate_text`` is an optional consumer policy hook for secret or
    domain-specific identifier checks; the shared contract owns the stable
    shape, unit binding, and verbatim grounding semantics.
    """
    candidate_id = str(candidate.get("candidate_id") or "<unknown>")
    privacy_mode = str(candidate.get("privacy_mode") or "").strip()
    if privacy_mode not in PRIVACY_MODES:
        raise _error("invalid_candidate_privacy", f"candidate {candidate_id} needs an allowed privacy_mode")
    refs = candidate.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise _error("missing_candidate_evidence", f"candidate {candidate_id} needs evidence_refs")
    normalized_corpus = normalize_excerpt(corpus)
    cited: set[str] = set()
    normalized_refs: list[dict[str, str]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise _error("invalid_candidate_evidence", "candidate evidence_refs must be objects")
        session_id = str(ref.get("session_id") or "").strip()
        excerpt = normalize_excerpt(str(ref.get("excerpt") or ""))
        if session_id not in unit_ids:
            raise _error("invalid_candidate_unit", f"candidate evidence ref {session_id} is not a formal source unit")
        if len(excerpt) < 20 or excerpt not in normalized_corpus:
            raise _error("ungrounded_candidate_evidence", f"candidate evidence excerpt for {session_id} is not grounded verbatim")
        if validate_text is not None:
            validate_text(excerpt, field=f"candidate evidence excerpt {session_id}")
        cited.add(session_id)
        normalized_refs.append({
            "session_id": session_id,
            "support_level": str(ref.get("support_level") or "direct_answer"),
            "excerpt": excerpt,
        })
    sessions = sorted(set(str(item).strip() for item in candidate.get("sessions") or []))
    if sessions != sorted(cited):
        raise _error("candidate_session_mismatch", f"candidate {candidate_id} sessions must exactly match evidence_refs")
    result = dict(candidate)
    result["privacy_mode"] = privacy_mode
    result["sessions"] = sessions
    result["evidence_refs"] = normalized_refs
    return result


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise _error("missing_frontmatter", "missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise _error("unterminated_frontmatter", "unterminated YAML frontmatter")
    values: dict[str, str] = {}
    current_key = ""
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            item = line[4:].strip().strip('"').strip("'")
            values[current_key] = f"{values.get(current_key, '')}\n{item}".strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        values[current_key] = value.strip().strip('"').strip("'")
    return values, text[end + 5:]


def markdown_section(body: str, headings: list[str]) -> str:
    for heading in headings:
        match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
        if match:
            return match.group(1).strip()
    return ""


def _json_array_section(
    body: str,
    headings: list[str],
    *,
    required: bool,
    error_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    occurrences = sum(len(re.findall(rf"^## {re.escape(heading)}\s*$", body, re.M)) for heading in headings)
    if occurrences == 0:
        if required:
            raise _error(f"missing_{error_prefix}_section", f"missing ## {headings[0]} section")
        return [], [{"code": f"missing_{error_prefix}_section", "message": f"missing ## {headings[0]} section"}]
    if occurrences != 1:
        raise _error(f"duplicate_{error_prefix}_section", f"{headings[0]} must appear exactly once")
    section = markdown_section(body, headings)
    matches = re.findall(r"```json\s*\n(.*?)\n```", section, re.S | re.I)
    if len(matches) != 1:
        raise _error(f"invalid_{error_prefix}_fence", f"{headings[0]} must contain one fenced JSON array")
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise _error(f"invalid_{error_prefix}_json", f"invalid {headings[0]} JSON: {exc}") from exc
    if not isinstance(value, list):
        raise _error(f"invalid_{error_prefix}_array", f"{headings[0]} JSON must be an array")
    if not all(isinstance(item, dict) for item in value):
        raise _error(f"invalid_{error_prefix}_item", f"every {headings[0]} item must be an object")
    return [dict(item) for item in value], []


def extract_structured_candidates(body: str, *, required: bool = True) -> list[dict[str, Any]]:
    return _json_array_section(
        body, ["Structured Candidates"], required=required, error_prefix="structured_candidates"
    )[0]


def _normalized_instrument_candidate(candidate: dict[str, Any], source_date: str) -> dict[str, Any] | None:
    question = str(candidate.get("question") or "").strip()
    answer = str(candidate.get("answer") or "").strip()
    topic = str(candidate.get("topic") or "仪器方法").strip()
    knowledge_status = str(candidate.get("knowledge_status") or "").strip().lower()
    privacy_mode = str(candidate.get("privacy_mode") or "").strip().lower()
    instrument_types = list(dict.fromkeys(
        str(item).strip() for item in candidate.get("instrument_types") or []
        if str(item).strip() in INSTRUMENT_TYPES
    ))
    refs: list[dict[str, str]] = []
    for raw_ref in candidate.get("evidence_refs") or []:
        if not isinstance(raw_ref, dict):
            continue
        source_type = str(raw_ref.get("source_type") or "").strip().lower()
        session_id = str(raw_ref.get("session_id") or "").strip()
        support_level = str(raw_ref.get("support_level") or "").strip().lower()
        excerpt = str(raw_ref.get("excerpt") or "").strip()
        if (
            source_type not in INSTRUMENT_SOURCE_TYPES
            or not re.fullmatch(r"S\d{2,}", session_id)
            or support_level not in INSTRUMENT_SUPPORT_LEVELS
            or len(excerpt) < 20
        ):
            continue
        ref = {
            "source_type": source_type,
            "source_date": source_date,
            "session_id": session_id,
            "session_title": str(raw_ref.get("session_title") or "").strip(),
            "support_level": support_level,
            "excerpt": excerpt,
        }
        locator = str(raw_ref.get("source_locator") or "").strip()
        if locator and source_type == "chatgpt_conversation" and re.fullmatch(
            r"https://chatgpt\.com/c/[0-9A-Za-z-]+", locator
        ):
            ref["source_locator"] = locator
        refs.append(ref)
    private_text = f"{question} {answer} {' '.join(ref['excerpt'] for ref in refs)}"
    if (
        not question or len(answer) < 30 or not instrument_types or not refs
        or knowledge_status != "reusable" or privacy_mode != "non_reconstructable"
        or any(marker in private_text for marker in ("客户", "样品编号", "批次", "项目编号", "内部链接"))
    ):
        return None
    normalized = dict(candidate)
    normalized.update({
        "topic": topic,
        "question": question,
        "answer": answer,
        "summary": str(candidate.get("summary") or "").strip(),
        "knowledge_status": knowledge_status,
        "privacy_mode": privacy_mode,
        "instrument_types": instrument_types,
        "evidence_refs": refs,
    })
    return normalized


def extract_instrument_candidates(
    body: str,
    *,
    source_date: str,
    required: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates, issues = _json_array_section(
        body,
        ["Instrument Knowledge Candidates", "仪器知识候选"],
        required=required,
        error_prefix="instrument_candidates",
    )
    return [item for item in (_normalized_instrument_candidate(value, source_date) for value in candidates) if item], issues


def _family_from_path(path: Path) -> str:
    for part in path.parts:
        if part in DIRECTORY_TO_FAMILY:
            return DIRECTORY_TO_FAMILY[part]
    match = re.fullmatch(r"(chatgpt|codex)-daily-report-\d{4}-\d{2}-\d{2}\.md", path.name)
    return match.group(1) if match else ""


def load_compiled_source(
    path: Path,
    *,
    memory_root: Path | None = None,
    expected_family: str = "",
    legacy_compatible: bool = False,
    require_structured: bool | None = None,
    require_instrument: bool | None = None,
) -> CompiledDailySource:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    family = TYPE_TO_FAMILY.get(frontmatter.get("type", ""), "")
    if not family and legacy_compatible:
        family = expected_family or _family_from_path(path)
    if family not in FAMILY_SPECS or (expected_family and family != expected_family):
        raise _error("invalid_source_family", "daily source must be a ChatGPT or Codex source summary")
    spec = FAMILY_SPECS[family]
    source_date = frontmatter.get("date", "")
    try:
        date.fromisoformat(source_date)
    except ValueError as exc:
        raise _error("invalid_source_date", "daily source date must be YYYY-MM-DD") from exc
    coverage = frontmatter.get("coverage", "unknown") or "unknown"
    if coverage not in ALLOWED_COVERAGE:
        raise _error("invalid_coverage", f"{path} has invalid coverage")
    status = frontmatter.get("status", "unknown") or "unknown"
    if not legacy_compatible and status not in spec.allowed_statuses:
        raise _error("invalid_status", f"{path} has invalid status for {family}")
    raw_source = frontmatter.get("raw_source", "")
    expected_raw = spec.raw_source(source_date)
    if not legacy_compatible and raw_source != expected_raw:
        raise _error("invalid_raw_source", f"{path} raw_source must be {expected_raw}")
    errors: list[dict[str, str]] = []
    structured_required = (not legacy_compatible) if require_structured is None else require_structured
    try:
        structured, issues = _json_array_section(
            body, ["Structured Candidates"], required=structured_required,
            error_prefix="structured_candidates",
        )
        errors.extend(issues)
    except SourceContractError:
        if not legacy_compatible:
            raise
        structured = []
        errors.append({"code": "invalid_structured_candidates", "message": "invalid Structured Candidates section"})
    instrument_required = (
        family == "chatgpt" and not legacy_compatible
        if require_instrument is None else require_instrument
    )
    try:
        instrument, issues = extract_instrument_candidates(
            body, source_date=source_date, required=instrument_required and family == "chatgpt"
        ) if family == "chatgpt" else ([], [])
        errors.extend(issues)
    except SourceContractError:
        if not legacy_compatible:
            raise
        instrument = []
        errors.append({"code": "invalid_instrument_candidates", "message": "invalid Instrument Knowledge Candidates section"})

    raw_path: Path | None = None
    raw_text = ""
    if memory_root is not None and raw_source == expected_raw:
        candidate_path = (memory_root / raw_source).resolve()
        allowed = (memory_root / f"raw/conversations/{spec.directory}").resolve()
        try:
            candidate_path.relative_to(allowed)
        except ValueError:
            candidate_path = None
        if candidate_path and candidate_path.is_file():
            raw_candidate = candidate_path.read_text(encoding="utf-8")
            try:
                raw_frontmatter, _ = parse_frontmatter(raw_candidate)
            except SourceContractError:
                raw_frontmatter = {}
            if raw_frontmatter.get("type") == spec.raw_type and raw_frontmatter.get("date") == source_date:
                raw_path, raw_text = candidate_path, raw_candidate
    if memory_root is not None and not legacy_compatible and (raw_path is None or not raw_text):
        raise _error("missing_raw_source", "immutable raw source is missing or does not match source summary")

    corpus = raw_text + "\n" + text
    unit_ids = sorted(set(re.findall(rf"(?:^|\n)[-# ]*({spec.unit_prefix}\d{{2,}})\s+[—-]", corpus)))
    return CompiledDailySource(
        version=CONTRACT_VERSION,
        path=path,
        text=text,
        body=body,
        frontmatter=frontmatter,
        family=family,
        source_type=spec.source_type,
        source_date=source_date,
        coverage=coverage,
        status=status,
        raw_source=raw_source,
        unit_ids=unit_ids,
        structured_candidates=structured,
        instrument_candidates=instrument,
        raw_path=raw_path,
        raw_text=raw_text,
        validation_errors=errors,
    )


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt_path(memory_root: Path, family: str, source_date: str) -> Path:
    return (
        memory_root / "wiki/review-cycles/daily-deposition" / source_date[:4]
        / f"{family}-daily-deposition-{source_date}.json"
    )


def source_deposition_state(
    source_path: Path,
    memory_root: Path,
    *,
    receipt_required_from: str = RECEIPT_REQUIRED_FROM,
) -> dict[str, Any]:
    path_family = _family_from_path(source_path)
    if path_family not in FAMILY_SPECS:
        return {"ready": False, "reason": "unsupported_source_family"}
    spec = FAMILY_SPECS[path_family]
    match = re.fullmatch(
        rf"{re.escape(spec.filename_prefix)}-daily-report-(\d{{4}}-\d{{2}}-\d{{2}})\.md",
        source_path.name,
    )
    if not match or not source_path.is_file():
        return {"ready": False, "reason": "invalid_source_path"}
    source_date = match.group(1)
    receipt_required = source_date >= receipt_required_from
    try:
        source = load_compiled_source(
            source_path,
            memory_root=memory_root,
            expected_family=path_family,
            legacy_compatible=not receipt_required,
            require_structured=receipt_required,
            require_instrument=False,
        )
    except (OSError, SourceContractError):
        return {"ready": False, "reason": "invalid_source_metadata"}
    if source.frontmatter.get("type") != spec.source_type or source.source_date != source_date:
        return {"ready": False, "reason": "invalid_source_metadata"}
    if not receipt_required:
        return {"ready": True, "reason": "legacy_before_receipt_gate", "source_date": source_date}
    receipt = receipt_path(memory_root, path_family, source_date)
    if not receipt.is_file():
        return {"ready": False, "reason": "daily_deposition_receipt_missing", "source_date": source_date}
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "reason": "daily_deposition_receipt_invalid", "source_date": source_date}
    if not isinstance(payload, dict):
        return {"ready": False, "reason": "daily_deposition_receipt_invalid", "source_date": source_date}
    expected = {
        "version": RECEIPT_VERSION,
        "status": "completed",
        "source_family": path_family,
        "source_date": source_date,
        "source_path": source_path.resolve().as_posix(),
        "source_sha256": sha256_path(source_path),
        "raw_files_modified": 0,
        "actions_dispatched": 0,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return {"ready": False, "reason": "daily_deposition_receipt_mismatch", "source_date": source_date}
    if source.raw_path is None or payload.get("raw_sha256") != sha256_path(source.raw_path):
        return {"ready": False, "reason": "daily_deposition_receipt_mismatch", "source_date": source_date}
    candidate_ids = payload.get("candidate_ids")
    extraction_result = payload.get("extraction_result")
    if (
        not isinstance(candidate_ids, list)
        or not all(isinstance(value, str) and re.fullmatch(r"cand-[0-9a-f]{16}", value) for value in candidate_ids)
        or candidate_ids != sorted(set(candidate_ids))
        or payload.get("candidate_count") != len(candidate_ids)
        or payload.get("reviewed_unit_count") != len(source.unit_ids)
        or extraction_result not in {"candidates", "no_relevant_content"}
        or (extraction_result == "candidates") != bool(candidate_ids)
    ):
        return {"ready": False, "reason": "daily_deposition_receipt_invalid", "source_date": source_date}
    return {
        "ready": True,
        "reason": "daily_deposition_completed",
        "source_date": source_date,
        "receipt": receipt.as_posix(),
        "candidate_count": len(candidate_ids),
    }
