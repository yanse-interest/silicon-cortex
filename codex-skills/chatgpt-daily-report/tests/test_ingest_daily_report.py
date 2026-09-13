from __future__ import annotations

import importlib.util
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "ingest_daily_report.py"
TEMPLATE = Path(__file__).parents[1] / "references" / "daily-report-template.md"
SPEC = importlib.util.spec_from_file_location("ingest_daily_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def sample(session_count: int = 1) -> str:
    digest = ""
    index = ""
    if session_count:
        digest = """
### S01 — Example

**我们聊了什么：** 我问了一个问题，ChatGPT 解释了答案。

**结论：** 已理解答案。

**下一步：** 无。

<details>
<summary>技术记录（需要时展开）</summary>

- 领域: other

</details>
"""
        index = "\n- S01 — Example — other — done\n"
    return f"""---
type: chatgpt_daily_report
date: 2026-06-24
timezone: Asia/Shanghai
coverage_start: 2026-06-24T00:00:00+08:00
coverage_end: 2026-06-24T23:59:59+08:00
generated_at: 2026-06-25T07:00:00+08:00
source: chatgpt
coverage: complete
status: ready
session_count: {session_count}
---

# ChatGPT 每日会话归档 — 2026-06-24

## 覆盖范围与限制

## 今天用人话说

## 会话摘要
{digest}
## 跨会话综合

## 会话索引
{index}"""


class DailyReportTests(unittest.TestCase):
    def test_template_is_plain_language_first(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("**我们聊了什么：**", template)
        self.assertIn("**结论：**", template)
        self.assertIn("**下一步：**", template)
        self.assertLess(template.index("**我们聊了什么：**"), template.index("技术记录"))
        self.assertIn("原问题与上下文", template)
        self.assertIn("回答要点", template)
        self.assertIn("推理或诊断链", template)
        self.assertIn("关键术语、条件与边界", template)
        self.assertIn("later retrieval", template)
        self.assertIn("来源 URL / 原会话 ID", template)
        self.assertIn("`/c/<UUID>`", template)
        self.assertIn("`unavailable`", template)
        self.assertIn("every distinct reusable exam point", template)
        self.assertIn("multiple points from one session", template)

    def test_validate_and_archive(self) -> None:
        metadata = MODULE.validate(sample())
        with tempfile.TemporaryDirectory() as directory:
            target, result = MODULE.archive(sample(), Path(directory), metadata, False)
            self.assertEqual(result, "created")
            self.assertTrue(target.exists())
            _, second = MODULE.archive(sample(), Path(directory), metadata, False)
            self.assertEqual(second, "unchanged")

    def test_rejects_session_count_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_count"):
            MODULE.validate(sample().replace("session_count: 1", "session_count: 2"))

    def test_missing_generation_time_preserves_raw_and_can_be_analyzed(self) -> None:
        text = sample().replace("generated_at: 2026-06-25T07:00:00+08:00\n", "")
        self.assertEqual(MODULE.validate(text)["session_count"], 1)
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
            MODULE.validate(sample().replace("2026-06-25T07:00:00+08:00", "not-a-time"))
        with self.assertRaisesRegex(ValueError, "Asia/Shanghai UTC offset"):
            MODULE.validate(sample().replace("2026-06-25T07:00:00+08:00", "2026-06-25T07:00:00Z"))

    def test_accepts_legacy_daily_log_type(self) -> None:
        metadata = MODULE.validate(
            sample().replace("type: chatgpt_daily_report", "type: chatgpt_daily_log")
        )
        self.assertIn("legacy type", metadata["warnings"][0])

    def test_accepts_legacy_executive_summary_heading(self) -> None:
        metadata = MODULE.validate(
            sample().replace("## 今天用人话说", "## Executive Summary")
        )
        self.assertEqual(metadata["session_count"], 1)

    def test_accepts_legacy_english_headings(self) -> None:
        text = (
            sample()
            .replace("# ChatGPT 每日会话归档", "# ChatGPT Daily Report")
            .replace("## 覆盖范围与限制", "## Coverage & Limitations")
            .replace("## 今天用人话说", "## Today in Plain Language")
            .replace("## 会话摘要", "## Session Digest")
            .replace("## 跨会话综合", "## Cross-session Synthesis")
            .replace("## 会话索引", "## Session Index")
        )
        metadata = MODULE.validate(text)
        self.assertEqual(metadata["session_count"], 1)

    def test_accepts_complete_zero_session_day(self) -> None:
        text = sample(session_count=0).replace("status: ready", "status: no_sessions")
        metadata = MODULE.validate(text)
        self.assertEqual(metadata["session_count"], 0)
        self.assertEqual(metadata["coverage"], "complete")

    def test_accepts_partial_access_as_evidence_state(self) -> None:
        text = sample().replace("coverage: complete", "coverage: partial").replace(
            "status: ready", "status: access_incomplete"
        )
        metadata = MODULE.validate(text)
        self.assertEqual(metadata["coverage"], "partial")
        self.assertEqual(metadata["status"], "access_incomplete")

    def test_rejects_different_existing_report(self) -> None:
        metadata = MODULE.validate(sample())
        with tempfile.TemporaryDirectory() as directory:
            target, _ = MODULE.archive(sample(), Path(directory), metadata, False)
            target.write_text("different", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE.archive(sample(), Path(directory), metadata, False)

    def test_concurrent_different_reports_never_overwrite(self) -> None:
        original = sample()
        changed = original.replace("已理解答案", "仍需核对答案")
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda text: self._archive_outcome(text, vault), [original, changed]
                ))
            self.assertEqual(sorted(results), ["conflict", "created"])
            target = vault / "raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-06-24.md"
            self.assertIn(target.read_text(encoding="utf-8"), [original, changed])

    @staticmethod
    def _archive_outcome(text: str, vault: Path) -> str:
        try:
            return MODULE.archive(text, vault, MODULE.validate_capture(text), False)[1]
        except FileExistsError:
            return "conflict"

    def test_capture_metadata_failure_does_not_undo_raw_and_retry_repairs(self) -> None:
        text = sample()
        with tempfile.TemporaryDirectory() as directory:
            target, result = MODULE.archive(text, Path(directory), MODULE.validate_capture(text), False)
            with mock.patch.object(MODULE.capture_provenance.os, "link", side_effect=OSError("temporary failure")):
                first = MODULE.capture_provenance.record(target, result=result, raw=text.encode())
            self.assertIsNone(first["observed_at"])
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            second = MODULE.capture_provenance.record(target, result="unchanged", raw=text.encode())
            self.assertIsNotNone(second["observed_at"])


if __name__ == "__main__":
    unittest.main()
