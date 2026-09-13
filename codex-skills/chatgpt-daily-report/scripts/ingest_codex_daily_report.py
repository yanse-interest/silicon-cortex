#!/usr/bin/env python3
"""Validate and immutably archive one Codex daily summary-and-evidence report."""

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
    "task_count",
}
CAPTURE_FIELDS = {"type", "date", "source"}
REQUIRED_SECTIONS = (
    "## 覆盖范围与限制",
    "## 今日进度摘要",
    "## 任务摘要与证据",
    "## 跨任务综合",
    "## 任务索引",
)
REQUIRED_TASK_FIELDS = (
    "目标",
    "结果",
    "状态",
    "工作类型",
    "证据模式",
    "文件证据",
    "Commit 证据",
    "测试证据",
    "下一步",
    "证据边界",
)
PROJECT_CONTEXT_FIELDS = (
    "来源线程 ID",
    "Codex 项目 ID",
    "工作目录",
    "项目身份来源",
)
ALLOWED_PROJECT_IDENTITY_SOURCES = {
    "thread_project_id",
    "thread_cwd",
    "bridge_cwd",
    "projectless",
    "compliance_abstracted",
}
ALLOWED_COVERAGE = {"complete", "partial", "unknown"}
ALLOWED_STATUS = {
    "ready",
    "access_incomplete",
    "no_tasks",
    "no_relevant_content",
}
ALLOWED_TASK_STATUS = {
    "completed",
    "in_progress",
    "blocked",
    "cancelled",
    "unknown",
}
ALLOWED_WORK_TYPES = {"general", "experimental"}
ALLOWED_EVIDENCE_MODES = {"summary_evidence", "compliance_abstracted"}
TASK_RE = re.compile(r"^### (T\d{2,})\s+[—-]\s+.+$", re.MULTILINE)
FIELD_RE = re.compile(r"^\*\*(?P<name>[^*：:]+)[：:]\*\*\s*(?P<value>.+)$", re.MULTILINE)
FORBIDDEN_FULL_RECORD_MARKERS = (
    "## 完整对话",
    "## 完整 transcript",
    "## Full Transcript",
    "## 工具输出",
    "## Tool Output",
    "tool_output:",
    "transcript_path:",
)


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
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values, text[end + 5 :]


def _task_blocks(body: str) -> list[tuple[str, str]]:
    matches = list(TASK_RE.finditer(body))
    return [
        (
            match.group(1),
            body[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(body)],
        )
        for index, match in enumerate(matches)
    ]


def enrich_project_context(text: str, projection: dict[str, object]) -> tuple[str, int]:
    """Inject controller-owned project identity before immutable archival.

    The reporting model supplies only the exact source thread ID.  Project ID,
    cwd and provenance are replaced from the controller sidecar, so prose or a
    task title cannot alter parent-project membership.
    """
    frontmatter, body = parse_frontmatter(text)
    if str(projection.get("target_date") or "") != frontmatter.get("date"):
        raise ValueError("evidence projection target_date must match report date")
    contexts: dict[str, dict[str, str]] = {}
    for row in projection.get("threads") or []:
        if not isinstance(row, dict) or int(row.get("found_turns") or 0) < 1:
            continue
        thread_id = str(row.get("thread_id") or "").strip()
        context = row.get("project_context") or {}
        if not thread_id or not isinstance(context, dict):
            continue
        normalized = {
            "project_id": str(context.get("project_id") or "").strip(),
            "cwd": str(context.get("cwd") or "").strip(),
            "identity_source": str(context.get("identity_source") or "projectless").strip(),
        }
        if normalized["identity_source"] not in ALLOWED_PROJECT_IDENTITY_SOURCES - {"compliance_abstracted"}:
            raise ValueError(f"thread {thread_id} has invalid project identity source")
        if thread_id in contexts and contexts[thread_id] != normalized:
            raise ValueError(f"thread {thread_id} has conflicting project context")
        contexts[thread_id] = normalized

    matches = list(TASK_RE.finditer(body))
    replacements: list[tuple[int, int, str]] = []
    for index, match in enumerate(matches):
        task_id = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        block = body[match.end():end]
        fields = {
            field.group("name").strip(): field.group("value").strip()
            for field in FIELD_RE.finditer(block)
        }
        thread_id = fields.get("来源线程 ID", "").strip()
        if not thread_id or thread_id in {"无", "unavailable", "已脱敏"}:
            raise ValueError(f"task {task_id} must cite one exact 来源线程 ID when evidence projection is supplied")
        if thread_id not in contexts:
            raise ValueError(f"task {task_id} source thread is absent from the evidence projection")
        context = contexts[thread_id]
        experimental = fields.get("工作类型") == "experimental"
        values = {
            "Codex 项目 ID": "已脱敏" if experimental else context["project_id"] or "无",
            "工作目录": "已脱敏" if experimental else context["cwd"] or "无",
            "项目身份来源": "compliance_abstracted" if experimental else context["identity_source"],
        }
        cleaned = block
        for field_name in PROJECT_CONTEXT_FIELDS[1:]:
            cleaned = re.sub(
                rf"^\*\*{re.escape(field_name)}[：:]\*\*\s*.*(?:\n|$)",
                "",
                cleaned,
                flags=re.MULTILINE,
            )
        source_line = re.search(r"^\*\*来源线程 ID[：:]\*\*\s*.+$", cleaned, re.MULTILINE)
        assert source_line is not None
        if experimental:
            redacted_source_line = "**来源线程 ID：** 已脱敏"
            cleaned = cleaned[:source_line.start()] + redacted_source_line + cleaned[source_line.end():]
            source_line = re.search(r"^\*\*来源线程 ID[：:]\*\*\s*.+$", cleaned, re.MULTILINE)
            assert source_line is not None
        injected = "\n\n".join(f"**{name}：** {value}" for name, value in values.items())
        cleaned = cleaned[:source_line.end()] + "\n\n" + injected + cleaned[source_line.end():]
        replacements.append((match.end(), end, cleaned))
    for start, end, replacement in reversed(replacements):
        body = body[:start] + replacement + body[end:]
    frontmatter_end = text.find("\n---\n", 4)
    return text[: frontmatter_end + 5] + body, len(replacements)


def validate(text: str) -> dict[str, object]:
    frontmatter, body = parse_frontmatter(text)
    missing = sorted(REQUIRED_FIELDS - frontmatter.keys())
    if missing:
        raise ValueError("missing frontmatter fields: " + ", ".join(missing))
    if frontmatter["type"] != "codex_daily_report":
        raise ValueError("type must be codex_daily_report")
    if frontmatter["source"] != "codex":
        raise ValueError("source must be codex")
    if frontmatter["timezone"] != "Asia/Shanghai":
        raise ValueError("timezone must be Asia/Shanghai")
    if frontmatter.get("generated_at"):
        validate_generated_at(frontmatter["generated_at"])
    try:
        report_date = date.fromisoformat(frontmatter["date"])
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    if frontmatter["coverage"] not in ALLOWED_COVERAGE:
        raise ValueError("invalid coverage value")
    if frontmatter["status"] not in ALLOWED_STATUS:
        raise ValueError("invalid status value")
    try:
        task_count = int(frontmatter["task_count"])
    except ValueError as exc:
        raise ValueError("task_count must be an integer") from exc
    if task_count < 0:
        raise ValueError("task_count must be non-negative")
    if task_count == 0:
        if frontmatter["status"] == "access_incomplete":
            raise ValueError(
                "zero-task access_incomplete reports are access-boundary placeholders; "
                "discard them without persisting a raw file or source summary"
            )
        if frontmatter["coverage"] != "complete" or frontmatter["status"] not in {
            "no_tasks",
            "no_relevant_content",
        }:
            raise ValueError(
                "zero-task Codex reports require coverage complete and status "
                "no_tasks or no_relevant_content"
            )
    missing_sections = [section for section in REQUIRED_SECTIONS if section not in body]
    if missing_sections:
        raise ValueError("missing sections: " + ", ".join(missing_sections))
    for marker in FORBIDDEN_FULL_RECORD_MARKERS:
        if marker.lower() in text.lower():
            raise ValueError(
                "Codex durable reports accept summary and evidence only, not full transcripts or tool output"
            )
    blocks = _task_blocks(body)
    if len(blocks) != task_count:
        raise ValueError(f"task_count is {task_count}, but found {len(blocks)} task entries")
    for task_id, block in blocks:
        fields = {
            match.group("name").strip(): match.group("value").strip()
            for match in FIELD_RE.finditer(block)
        }
        missing_task_fields = [name for name in REQUIRED_TASK_FIELDS if not fields.get(name)]
        if missing_task_fields:
            raise ValueError(
                f"task {task_id} missing summary/evidence fields: "
                + ", ".join(missing_task_fields)
            )
        if fields["状态"] not in ALLOWED_TASK_STATUS:
            raise ValueError(f"task {task_id} has invalid 状态")
        if fields["工作类型"] not in ALLOWED_WORK_TYPES:
            raise ValueError(f"task {task_id} has invalid 工作类型")
        if fields["证据模式"] not in ALLOWED_EVIDENCE_MODES:
            raise ValueError(f"task {task_id} has invalid 证据模式")
        if fields["工作类型"] == "experimental" and fields["证据模式"] != "compliance_abstracted":
            raise ValueError(
                f"task {task_id} experimental work must use compliance_abstracted evidence"
            )
        present_context = [name for name in PROJECT_CONTEXT_FIELDS if fields.get(name)]
        if present_context and len(present_context) != len(PROJECT_CONTEXT_FIELDS):
            missing_context = [name for name in PROJECT_CONTEXT_FIELDS if not fields.get(name)]
            raise ValueError(f"task {task_id} has incomplete project context: " + ", ".join(missing_context))
        if present_context:
            identity_source = fields["项目身份来源"]
            if identity_source not in ALLOWED_PROJECT_IDENTITY_SOURCES:
                raise ValueError(f"task {task_id} has invalid 项目身份来源")
            if fields["工作类型"] == "experimental" and identity_source != "compliance_abstracted":
                raise ValueError(f"task {task_id} experimental project context must be compliance_abstracted")
            if fields["工作类型"] == "experimental":
                for name in ("来源线程 ID", "Codex 项目 ID", "工作目录"):
                    if fields[name] != "已脱敏":
                        raise ValueError(f"task {task_id} experimental {name} must be redacted")
    return {
        "date": report_date.isoformat(),
        "year": str(report_date.year),
        "task_count": task_count,
        "coverage": frontmatter["coverage"],
        "status": frontmatter["status"],
        "warnings": [],
    }


def validate_capture(text: str) -> dict[str, object]:
    """File bounded Codex evidence before the detailed analysis pass."""
    frontmatter, body = parse_frontmatter(text)
    missing = sorted(CAPTURE_FIELDS - frontmatter.keys())
    if missing:
        raise ValueError("missing capture fields: " + ", ".join(missing))
    if frontmatter["type"] != "codex_daily_report" or frontmatter["source"] != "codex":
        raise ValueError("source must be codex_daily_report/codex")
    try:
        report_date = date.fromisoformat(frontmatter["date"])
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    if not body.strip():
        raise ValueError("daily report body must not be empty")
    for marker in FORBIDDEN_FULL_RECORD_MARKERS:
        if marker.lower() in text.lower():
            raise ValueError("Codex capture accepts summary and evidence only, not full transcripts")
    warnings = []
    count = frontmatter.get("task_count")
    if count is not None:
        try:
            count = int(count)
        except ValueError:
            warnings.append("task_count is not numeric; defer count validation to analysis")
            count = None
        if count is not None and count < 0:
            warnings.append("task_count is negative; defer count validation to analysis")
            count = None
        if count == 0 and frontmatter.get("status") == "access_incomplete":
            raise ValueError("zero-task access_incomplete is a source-access placeholder")
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
        "task_count": count,
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
        / "codex-daily"
        / str(metadata["year"])
        / f"codex-daily-report-{metadata['date']}.md"
    )
    encoded = text.encode("utf-8")
    if target.exists():
        if target.read_bytes() == encoded:
            return target, "unchanged"
        raise FileExistsError(
            f"different raw Codex report already exists for {metadata['date']}: {target}"
        )
    if dry_run:
        return target, "validated"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".codex-daily-", dir=target.parent)
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
            raise FileExistsError(f"different raw Codex report already exists for {metadata['date']}: {target}")
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target, "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="UTF-8 Markdown Codex daily report")
    parser.add_argument("--vault", type=Path, required=True, help="Obsidian memory vault root")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--source-locator", default="", help="verified source controller locator")
    parser.add_argument(
        "--evidence-projection",
        type=Path,
        help="controller JSON used to inject source-owned Codex project context",
    )
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8")
        enriched_count = 0
        if args.evidence_projection:
            projection = json.loads(args.evidence_projection.read_text(encoding="utf-8"))
            if not isinstance(projection, dict):
                raise ValueError("evidence projection must be a JSON object")
            text, enriched_count = enrich_project_context(text, projection)
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
            "project_context_enriched": enriched_count,
            **metadata,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"{result}: {target}")
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
