from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "daily_value_deposition.py"
SPEC = importlib.util.spec_from_file_location("daily_value_deposition", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class DailyValueDepositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.memory = Path(self.temp.name) / "memory"
        self.cognitive = self.memory / "wiki/cognitive-observatory"
        self.source = (
            self.memory
            / "wiki/sources/conversations/codex-daily/2026/codex-daily-report-2026-08-23.md"
        )
        self.raw = (
            self.memory
            / "raw/conversations/codex-daily/2026/codex-daily-report-2026-08-23.md"
        )
        self.source.parent.mkdir(parents=True)
        self.raw.parent.mkdir(parents=True)
        (self.memory / "wiki/review-cycles").mkdir(parents=True)
        self.raw.write_text(
            """---
type: codex_daily_report
date: 2026-08-23
coverage: partial
status: access_incomplete
---
# Codex 日报

### T01 — 日报完成链路

**结果：** 候选抽取完成后运行沉淀，再刷新项目看板；这一顺序已经通过回归测试。
""",
            encoding="utf-8",
        )
        self.source.write_text(
            """---
type: codex_daily_source_summary
date: 2026-08-23
coverage: partial
status: access_incomplete
raw_source: raw/conversations/codex-daily/2026/codex-daily-report-2026-08-23.md
---
# Codex source

## Cross-task Summary

- T01 — 日报完成链路 — completed — 候选抽取完成后运行沉淀，再刷新项目看板；这一顺序已经通过回归测试。

## Structured Candidates

```json
[]
```
""",
            encoding="utf-8",
        )
        self.extraction = Path(self.temp.name) / "extraction.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prepared(self) -> dict[str, object]:
        result = MODULE.prepare_manifest(self.source, self.memory, self.extraction)
        self.assertEqual(result["stage"], "candidate_extraction_required")
        return json.loads(self.extraction.read_text(encoding="utf-8"))

    def candidate(self) -> dict[str, object]:
        return {
            "type": "workflow",
            "domain": "daily deposition",
            "normalized_claim": "候选抽取完成后运行沉淀，再刷新项目看板",
            "sessions": ["T01"],
            "evidence_boundary": "仅证明该日报完成顺序已通过可见回归测试。",
            "evidence_complete": True,
            "assertion": "explicit",
            "confidence": 0.95,
            "risk": "low",
            "suggested_target": "workflow",
            "status": "candidate",
            "conflicts_with": [],
            "privacy_mode": "non_reconstructable",
            "evidence_refs": [
                {
                    "session_id": "T01",
                    "support_level": "direct_answer",
                    "excerpt": "候选抽取完成后运行沉淀，再刷新项目看板；这一顺序已经通过回归测试。",
                }
            ],
        }

    def write_extraction(self, *, result: str, candidates: list[dict[str, object]]) -> None:
        manifest = self.prepared()
        manifest.update({"reviewed_units": ["T01"], "result": result, "candidates": candidates})
        self.extraction.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_prepare_inventory_requires_explicit_extraction_invocation(self) -> None:
        before = self.source.read_bytes()
        manifest = self.prepared()
        self.assertEqual(manifest["unit_ids"], ["T01"])
        self.assertEqual(manifest["result"], "pending")
        self.assertEqual(before, self.source.read_bytes())

    def test_finalize_runs_deposition_then_publishes_receipt(self) -> None:
        self.write_extraction(result="candidates", candidates=[self.candidate()])
        result = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        self.assertEqual(result["candidate_count"], 1)
        receipt = Path(result["receipt"])
        self.assertTrue(receipt.is_file())
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["raw_files_modified"], 0)
        self.assertIn("cand-", self.source.read_text(encoding="utf-8"))

    def test_empty_but_valid_no_relevant_content_is_receipted(self) -> None:
        self.write_extraction(result="no_relevant_content", candidates=[])
        result = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("[]", self.source.read_text(encoding="utf-8"))
        payload = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["extraction_result"], "no_relevant_content")
        self.assertEqual(payload["reviewed_unit_count"], 1)

    def test_idempotent_finalize_keeps_receipt(self) -> None:
        self.write_extraction(result="candidates", candidates=[self.candidate()])
        first = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        first_receipt = Path(first["receipt"]).read_bytes()
        # Re-prepare against the canonical candidate-bearing source, then give
        # the exact same extraction conclusion.
        manifest = self.prepared()
        manifest.update({"reviewed_units": ["T01"], "result": "candidates", "candidates": [self.candidate()]})
        self.extraction.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        second = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        self.assertEqual(second["status"], "idempotent_complete")
        self.assertEqual(second["receipt_write"], "unchanged")
        self.assertEqual(first_receipt, Path(second["receipt"]).read_bytes())

    def test_tampered_receipt_is_rebuilt_instead_of_accepted_as_idempotent(self) -> None:
        self.write_extraction(result="candidates", candidates=[self.candidate()])
        first = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        receipt = Path(first["receipt"])
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["actions_dispatched"] = 1
        receipt.write_text(json.dumps(payload), encoding="utf-8")

        manifest = self.prepared()
        manifest.update({"reviewed_units": ["T01"], "result": "candidates", "candidates": [self.candidate()]})
        self.extraction.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        rebuilt = MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)

        self.assertEqual(rebuilt["status"], "normal_complete")
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["actions_dispatched"], 0)

    def test_duplicate_stable_candidate_ids_are_rejected(self) -> None:
        candidate = self.candidate()
        self.write_extraction(result="candidates", candidates=[candidate, dict(candidate)])
        with self.assertRaisesRegex(MODULE.DailyDepositionError, "duplicate stable candidate IDs"):
            MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)

    def test_failure_never_publishes_receipt(self) -> None:
        self.write_extraction(result="candidates", candidates=[self.candidate()])
        with patch.object(MODULE.PIPELINE, "process_daily_source", side_effect=MODULE.PIPELINE.PipelineError("ledger failed")):
            with self.assertRaisesRegex(MODULE.PIPELINE.PipelineError, "ledger failed"):
                MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)
        self.assertFalse(MODULE.receipt_path(self.memory, "codex", "2026-08-23").exists())
        self.assertEqual(MODULE.sha256_path(self.raw), json.loads(self.extraction.read_text())["raw_sha256"])

    def test_rejects_unreviewed_units_ungrounded_excerpt_and_private_data(self) -> None:
        manifest = self.prepared()
        manifest.update({"reviewed_units": [], "result": "no_relevant_content", "candidates": []})
        self.extraction.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.DailyDepositionError, "review every"):
            MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)

        private = self.candidate()
        private["normalized_claim"] = "读取 /Users/example/private 后刷新"
        self.write_extraction(result="candidates", candidates=[private])
        with self.assertRaisesRegex(MODULE.DailyDepositionError, "prohibited"):
            MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)

        ungrounded = self.candidate()
        ungrounded["evidence_refs"][0]["excerpt"] = "这是一段足够长但在正式来源里完全不存在的伪造证据文本。"
        self.write_extraction(result="candidates", candidates=[ungrounded])
        with self.assertRaisesRegex(MODULE.DailyDepositionError, "not grounded"):
            MODULE.finalize(self.source, self.extraction, self.memory, self.cognitive)


if __name__ == "__main__":
    unittest.main()
