#!/usr/bin/env python3
"""Capture one ChatGPT daily report immutably before downstream analysis."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

CAPTURE_SPEC = importlib.util.spec_from_file_location("capture_provenance", Path(__file__).with_name("capture_provenance.py"))
capture_provenance = importlib.util.module_from_spec(CAPTURE_SPEC)
assert CAPTURE_SPEC.loader
CAPTURE_SPEC.loader.exec_module(capture_provenance)


REQUIRED_FIELDS = {
    "type",
    "date",
    "timezone",
    "coverage_start",
    "coverage_end",
    "source",
    "coverage",
    "status",
    "session_count",
}
CAPTURE_FIELDS = {"type", "date", "source"}
REQUIRED_SECTION_GROUPS = (
    ("coverage section", ("## Coverage & Limitations", "## 覆盖范围与限制")),
    ("session digest section", ("## Session Digest", "## 会话摘要")),
    ("cross-session synthesis section", ("## Cross-session Synthesis", "## 跨会话综合")),
    ("session index section", ("## Session Index", "## 会话索引")),
)
SUMMARY_SECTIONS = (
    "## Today in Plain Language",
    "## Executive Summary",
    "## 今天用人话说",
)
ALLOWED_COVERAGE = {"complete", "partial", "unknown"}
ALLOWED_STATUS = {"ready", "access_incomplete", "no_sessions", "no_relevant_content"}


def validate_generated_at(value: str) -> str:
    """Require an actual, timezone-qualified generation timestamp."""
    try:
        generated_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO 8601 timestamp") from exc
    if generated_at.utcoffset() is None:
        raise ValueError("generated_at must include the Asia/Shanghai UTC offset")
    if generated_at.utcoffset().total_seconds() != 8 * 60 * 60:
        raise ValueError("generated_at must use the Asia/Shanghai UTC offset (+08:00)")
    return generated_at.isoformat()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML frontmatter")
    raw = text[4:end]
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values, text[end + 5 :]


def validate(text: str) -> dict[str, object]:
    frontmatter, body = parse_frontmatter(text)
    missing = sorted(REQUIRED_FIELDS - frontmatter.keys())
    if missing:
        raise ValueError("missing frontmatter fields: " + ", ".join(missing))
    if frontmatter["type"] not in {"chatgpt_daily_report", "chatgpt_daily_log"}:
        raise ValueError("type must be chatgpt_daily_report")
    try:
        report_date = date.fromisoformat(frontmatter["date"])
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    if frontmatter["timezone"] != "Asia/Shanghai":
        raise ValueError("timezone must be Asia/Shanghai")
    if frontmatter.get("generated_at"):
        validate_generated_at(frontmatter["generated_at"])
    if frontmatter["source"] != "chatgpt":
        raise ValueError("source must be chatgpt")
    if frontmatter["coverage"] not in ALLOWED_COVERAGE:
        raise ValueError("invalid coverage value")
    if frontmatter["status"] not in ALLOWED_STATUS:
        raise ValueError("invalid status value")
    try:
        session_count = int(frontmatter["session_count"])
    except ValueError as exc:
        raise ValueError("session_count must be an integer") from exc
    if session_count < 0:
        raise ValueError("session_count must be non-negative")
    if session_count == 0:
        if frontmatter["status"] == "access_incomplete":
            raise ValueError(
                "zero-session access_incomplete reports are access-boundary placeholders; "
                "discard them without persisting a raw file or source summary"
            )
        if frontmatter["coverage"] != "complete" or frontmatter["status"] not in {
            "no_sessions",
            "no_relevant_content",
        }:
            raise ValueError(
                "zero-session daily reports require coverage complete and status "
                "no_sessions or no_relevant_content"
            )
    missing_sections = [
        f"{label} ({' or '.join(variants)})"
        for label, variants in REQUIRED_SECTION_GROUPS
        if not any(section in body for section in variants)
    ]
    if not any(section in body for section in SUMMARY_SECTIONS):
        missing_sections.append(
            "## 今天用人话说 (or legacy ## Today in Plain Language / ## Executive Summary)"
        )
    if missing_sections:
        raise ValueError("missing sections: " + ", ".join(missing_sections))
    actual_sessions = len(re.findall(r"^### S\d{2,}\s+[—-]\s+", body, flags=re.MULTILINE))
    if actual_sessions != session_count:
        raise ValueError(
            f"session_count is {session_count}, but found {actual_sessions} session entries"
        )
    warnings: list[str] = []
    if frontmatter["type"] == "chatgpt_daily_log":
        warnings.append("legacy type chatgpt_daily_log accepted; use chatgpt_daily_report")
    if frontmatter["coverage"] == "complete" and frontmatter["status"] == "access_incomplete":
        warnings.append("coverage complete conflicts with status access_incomplete")
    if session_count == 0 and frontmatter["status"] == "ready":
        warnings.append("zero sessions normally uses status no_sessions")
    return {
        "date": report_date.isoformat(),
        "year": str(report_date.year),
        "session_count": session_count,
        "coverage": frontmatter["coverage"],
        "status": frontmatter["status"],
        "warnings": warnings,
    }


def validate_capture(text: str) -> dict[str, object]:
    """Check only what is needed to file the original report by source and date."""
    frontmatter, body = parse_frontmatter(text)
    missing = sorted(CAPTURE_FIELDS - frontmatter.keys())
    if missing:
        raise ValueError("missing capture fields: " + ", ".join(missing))
    if frontmatter["type"] not in {"chatgpt_daily_report", "chatgpt_daily_log"}:
        raise ValueError("type must be chatgpt_daily_report")
    if frontmatter["source"] != "chatgpt":
        raise ValueError("source must be chatgpt")
    try:
        report_date = date.fromisoformat(frontmatter["date"])
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    if not body.strip():
        raise ValueError("daily report body must not be empty")
    warnings = []
    count = frontmatter.get("session_count")
    if count is not None:
        try:
            count = int(count)
        except ValueError:
            warnings.append("session_count is not numeric; defer count validation to analysis")
            count = None
        if count is not None and count < 0:
            warnings.append("session_count is negative; defer count validation to analysis")
            count = None
        if count == 0 and frontmatter.get("status") == "access_incomplete":
            raise ValueError("zero-session access_incomplete is a source-access placeholder")
    if not frontmatter.get("generated_at"):
        warnings.append("generated_at missing; observed_at is capture time, not generation time")
    else:
        try:
            validate_generated_at(frontmatter["generated_at"])
        except ValueError:
            warnings.append("generated_at invalid; preserve source text for later analysis")
    return {
        "date": report_date.isoformat(),
        "year": str(report_date.year),
        "session_count": count,
        "coverage": frontmatter.get("coverage", "unknown"),
        "status": frontmatter.get("status", "unreviewed"),
        "warnings": warnings,
        "generated_at": frontmatter.get("generated_at") or None,
    }


def archive(text: str, vault: Path, metadata: dict[str, object], dry_run: bool) -> tuple[Path, str]:
    target = (
        vault
        / "raw"
        / "conversations"
        / "chatgpt-daily"
        / str(metadata["year"])
        / f"chatgpt-daily-report-{metadata['date']}.md"
    )
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if target.exists():
        existing = target.read_bytes()
        if existing == encoded:
            return target, "unchanged"
        raise FileExistsError(
            f"different raw report already exists for {metadata['date']}: {target}"
        )
    if dry_run:
        return target, "validated"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".chatgpt-daily-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_name, target)
        except FileExistsError:
            if target.read_bytes() == encoded:
                return target, "unchanged"
            raise FileExistsError(f"different raw report already exists for {metadata['date']}: {target}")
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target, "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="UTF-8 Markdown daily report")
    parser.add_argument("--vault", type=Path, required=True, help="Obsidian memory vault root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--source-locator", default="", help="verified source task/thread locator")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8")
        metadata = validate_capture(text)
        target, result = archive(text, args.vault, metadata, args.dry_run)
        provenance = capture_provenance.record(
            target, result=result, raw=text.encode("utf-8"), source_locator=args.source_locator
        ) if not args.dry_run else {"capture_metadata": None, "observed_at": None}
        payload = {
            "ok": True,
            "result": result,
            "target": str(target),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            **provenance,
            "observed_at_kind": "local_capture_clock_not_source_generation" if provenance["observed_at"] else None,
            **metadata,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"{result}: {target}")
            for warning in metadata["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
