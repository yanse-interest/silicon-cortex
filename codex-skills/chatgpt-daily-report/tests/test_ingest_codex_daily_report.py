from __future__ import annotations

import importlib.util
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "ingest_codex_daily_report.py"
TEMPLATE = Path(__file__).parents[1] / "references" / "codex-daily-report-template.md"
SPEC = importlib.util.spec_from_file_location("ingest_codex_daily_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def sample(
    *, work_type: str = "general", evidence_mode: str = "summary_evidence", source_thread: bool = False
) -> str:
    source_thread_field = "\n**来源线程 ID：** thread-1\n" if source_thread else ""
    return f"""---
type: codex_daily_report
date: 2026-08-01
timezone: Asia/Shanghai
coverage_start: 2026-08-01T00:00:00+08:00
coverage_end: 2026-08-01T23:59:59+08:00
generated_at: 2026-08-02T07:05:00+08:00
source: codex
coverage: complete
status: ready
task_count: 1
---

# Codex 每日任务沉淀 — 2026-08-01

## 覆盖范围与限制

- visible task references only

## 今日进度摘要

- one task completed

## 任务摘要与证据

### T01 — Unified deposition

**目标：** Bridge Codex progress into durable memory.

**结果：** Implemented and verified the bounded ingestion path.

**状态：** completed

**工作类型：** {work_type}

**证据模式：** {evidence_mode}
{source_thread_field}

**文件证据：** projects/codex-skills/chatgpt-daily-report/scripts/ingest_codex_daily_report.py

**Commit 证据：** 无

**测试证据：** unittest passed

**下一步：** Run human end-to-end automation verification.

**证据边界：** Source messages and file changes were read in memory; no full transcript retained.

## 跨任务综合

- one project-state candidate

## 任务索引

- T01 — Unified deposition — completed — ingestion path verified
"""


class CodexDailyReportTests(unittest.TestCase):
    @staticmethod
    def projection(*, project_id: str = "project-1") -> dict:
        return {
            "target_date": "2026-08-01",
            "threads": [{
                "thread_id": "thread-1",
                "found_turns": 1,
                "project_context": {
                    "project_id": project_id,
                    "cwd": "/worktrees/isolated-checkout",
                    "identity_source": "thread_project_id" if project_id else "thread_cwd",
                },
            }],
        }

    def test_template_requires_summary_and_evidence_not_transcript(self) -> None:
        text = TEMPLATE.read_text(encoding="utf-8")
        for field in ["目标", "结果", "状态", "文件证据", "Commit 证据", "测试证据", "下一步", "证据边界"]:
            self.assertIn(f"**{field}：**", text)
        self.assertIn("not a transcript archive", text)
        self.assertIn("`generated_at` is mandatory", text)
        self.assertIn("actual generation time", text)

    def test_validate_archive_and_idempotent_reingest(self) -> None:
        text = sample()
        metadata = MODULE.validate(text)
        self.assertEqual(metadata["task_count"], 1)
        with tempfile.TemporaryDirectory() as directory:
            target, first = MODULE.archive(text, Path(directory), metadata, False)
            self.assertEqual(first, "created")
            self.assertEqual(
                target.relative_to(directory).as_posix(),
                "raw/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md",
            )
            _, second = MODULE.archive(text, Path(directory), metadata, False)
            self.assertEqual(second, "unchanged")

    def test_rejects_missing_required_evidence_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "测试证据"):
            MODULE.validate(sample().replace("**测试证据：** unittest passed\n", ""))

    def test_missing_generation_time_preserves_raw_and_can_be_analyzed(self) -> None:
        text = sample().replace("generated_at: 2026-08-02T07:05:00+08:00\n", "")
        self.assertEqual(MODULE.validate(text)["task_count"], 1)
        metadata = MODULE.validate_capture(text)
        with tempfile.TemporaryDirectory() as directory:
            target, result = MODULE.archive(text, Path(directory), metadata, False)
            self.assertEqual(result, "created")
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            provenance = MODULE.capture_provenance.record(target, result=result, raw=text.encode())
            self.assertIsNotNone(provenance["observed_at"])
            self.assertNotIn("generated_at:", target.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.archive(text, Path(directory), metadata, False)[1], "unchanged")

    def test_rejects_invalid_generated_at_during_analysis(self) -> None:
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            MODULE.validate(sample().replace("2026-08-02T07:05:00+08:00", "not-a-time"))
        with self.assertRaisesRegex(ValueError, "Asia/Shanghai UTC offset"):
            MODULE.validate(sample().replace("2026-08-02T07:05:00+08:00", "2026-08-02T07:05:00Z"))

    def test_rejects_full_transcript_or_tool_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary and evidence only"):
            MODULE.validate(sample() + "\n## Tool Output\n\nfull output\n")

    def test_experimental_work_requires_compliance_abstracted(self) -> None:
        with self.assertRaisesRegex(ValueError, "compliance_abstracted"):
            MODULE.validate(sample(work_type="experimental"))
        metadata = MODULE.validate(
            sample(work_type="experimental", evidence_mode="compliance_abstracted")
        )
        self.assertEqual(metadata["task_count"], 1)

    def test_different_existing_codex_report_is_rejected(self) -> None:
        text = sample()
        metadata = MODULE.validate(text)
        with tempfile.TemporaryDirectory() as directory:
            target, _ = MODULE.archive(text, Path(directory), metadata, False)
            target.write_text("different", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE.archive(text, Path(directory), metadata, False)

    def test_concurrent_different_reports_never_overwrite(self) -> None:
        original = sample()
        changed = original.replace("Implemented and verified", "Partially implemented")
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(
                    lambda text: self._archive_outcome(text, vault), [original, changed]
                ))
            self.assertEqual(sorted(outcomes), ["conflict", "created"])
            target = vault / "raw/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
            self.assertIn(target.read_text(encoding="utf-8"), [original, changed])

    @staticmethod
    def _archive_outcome(text: str, vault: Path) -> str:
        try:
            return MODULE.archive(text, vault, MODULE.validate_capture(text), False)[1]
        except FileExistsError:
            return "conflict"

    def test_codex_ingest_never_rewrites_chatgpt_raw_family(self) -> None:
        text = sample()
        metadata = MODULE.validate(text)
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            chatgpt = (
                vault
                / "raw"
                / "conversations"
                / "chatgpt-daily"
                / "2026"
                / "chatgpt-daily-report-2026-08-01.md"
            )
            chatgpt.parent.mkdir(parents=True)
            chatgpt.write_bytes(b"immutable-chatgpt-evidence")
            MODULE.archive(text, vault, metadata, False)
            self.assertEqual(chatgpt.read_bytes(), b"immutable-chatgpt-evidence")

    def test_controller_project_context_is_injected_before_archival(self) -> None:
        enriched, count = MODULE.enrich_project_context(sample(source_thread=True), self.projection())
        self.assertEqual(count, 1)
        self.assertIn("**Codex 项目 ID：** project-1", enriched)
        self.assertIn("**工作目录：** /worktrees/isolated-checkout", enriched)
        self.assertIn("**项目身份来源：** thread_project_id", enriched)
        MODULE.validate(enriched)

    def test_projection_rejects_missing_or_unknown_source_thread(self) -> None:
        with self.assertRaisesRegex(ValueError, "来源线程 ID"):
            MODULE.enrich_project_context(sample(), self.projection())
        with self.assertRaisesRegex(ValueError, "absent from the evidence projection"):
            MODULE.enrich_project_context(
                sample(source_thread=True).replace("thread-1", "thread-other"), self.projection()
            )

    def test_experimental_project_context_is_fully_redacted(self) -> None:
        enriched, _ = MODULE.enrich_project_context(
            sample(
                work_type="experimental", evidence_mode="compliance_abstracted", source_thread=True
            ),
            self.projection(),
        )
        self.assertIn("**来源线程 ID：** 已脱敏", enriched)
        self.assertIn("**Codex 项目 ID：** 已脱敏", enriched)
        self.assertIn("**工作目录：** 已脱敏", enriched)
        self.assertIn("**项目身份来源：** compliance_abstracted", enriched)
        self.assertNotIn("project-1", enriched)
        self.assertNotIn("/worktrees/isolated-checkout", enriched)
        self.assertNotIn("thread-1", enriched)
        MODULE.validate(enriched)


if __name__ == "__main__":
    unittest.main()
