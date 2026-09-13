from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "reformat_daily_report.py"
SPEC = importlib.util.spec_from_file_location("reformat_daily_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


LEGACY = """---
type: chatgpt_daily_report
date: 2026-06-25
source: chatgpt
---

# ChatGPT Daily Report — 2026-06-25

## Coverage & Limitations

- partial

## Executive Summary

- summary

## Session Digest

### S01 — Example

- **Domain:** other
- **Access time:** 2026-06-25 08:00:00 Asia/Shanghai
- **Objective:** 理解一个问题。
- **Key context and observations:** 用户提供了背景。
- **Outcome or current conclusion:** 已确认当前结论。
- **Decisions:** 无。
- **Open questions:** 仍缺一项信息。
- **Follow-ups:** 补充该信息。
- **Reusable knowledge:** 可复用结论。
- **Evidence and uncertainty:** 未读取完整回复。

## Cross-session Synthesis

- synthesis

## Session Index

- S01
"""


class ReformatDailyReportTests(unittest.TestCase):
    def test_reformats_legacy_session(self) -> None:
        result = MODULE.reformat(LEGACY, "2026-06-27T12:00:00+08:00")
        self.assertIn("## 今天用人话说", result)
        self.assertIn("**我们聊了什么：** 理解一个问题。", result)
        self.assertIn("**结论：** 已确认当前结论。", result)
        self.assertIn("**下一步：** 补充该信息。", result)
        self.assertIn("<summary>技术记录（需要时展开）</summary>", result)
        self.assertIn("- 访问时间: 2026-06-25 08:00:00 Asia/Shanghai", result)
        self.assertIn("regenerated_by: codex", result)
        self.assertNotIn("- **Objective:**", result)

    def test_is_idempotent_for_new_layout(self) -> None:
        first = MODULE.reformat(LEGACY, "2026-06-27T12:00:00+08:00")
        self.assertEqual(MODULE.reformat(first, "2026-06-27T13:00:00+08:00"), first)


if __name__ == "__main__":
    unittest.main()
