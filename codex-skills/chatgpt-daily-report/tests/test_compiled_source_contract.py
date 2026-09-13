from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "compiled_source_contract.py"
SPEC = importlib.util.spec_from_file_location("compiled_source_contract_test", SCRIPT)
CONTRACT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(CONTRACT)


class CompiledSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.memory = Path(self.temp.name) / "memory"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_source(
        self,
        family: str,
        source_date: str,
        *,
        instrument_payload: list[dict[str, object]] | None = None,
    ) -> tuple[Path, Path]:
        spec = CONTRACT.FAMILY_SPECS[family]
        source = (
            self.memory / "wiki/sources/conversations" / spec.directory / source_date[:4]
            / f"{family}-daily-report-{source_date}.md"
        )
        raw = self.memory / spec.raw_source(source_date)
        source.parent.mkdir(parents=True, exist_ok=True)
        raw.parent.mkdir(parents=True, exist_ok=True)
        unit_id = f"{spec.unit_prefix}01"
        raw.write_text(
            "---\n"
            f"type: {spec.raw_type}\n"
            f"date: {source_date}\n"
            "coverage: complete\n"
            "status: ready\n"
            "---\n"
            f"# Raw\n\n### {unit_id} — Contract evidence\n\n"
            "This formal source unit contains enough exact evidence for contract validation.\n",
            encoding="utf-8",
        )
        body = (
            "---\n"
            f"type: {spec.source_type}\n"
            f"date: {source_date}\n"
            "coverage: complete\n"
            "status: ready\n"
            f"raw_source: {spec.raw_source(source_date)}\n"
            "---\n"
            "# Compiled source\n\n"
            f"## {'Session Inventory' if family == 'chatgpt' else 'Cross-task Summary'}\n\n"
            f"- {unit_id} — Contract evidence\n\n"
            "## Structured Candidates\n\n```json\n[]\n```\n"
        )
        if family == "chatgpt":
            payload = [] if instrument_payload is None else instrument_payload
            body += (
                "\n## Instrument Knowledge Candidates\n\n```json\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
                + "\n```\n"
            )
        source.write_text(body, encoding="utf-8")
        return source, raw

    def assert_contract_error(self, code: str, callback) -> None:
        with self.assertRaises(CONTRACT.SourceContractError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_strict_contract_loads_both_source_families(self) -> None:
        for family, unit_id in (("chatgpt", "S01"), ("codex", "T01")):
            with self.subTest(family=family):
                source, raw = self.write_source(family, "2026-08-28")
                parsed = CONTRACT.load_compiled_source(
                    source,
                    memory_root=self.memory,
                    expected_family=family,
                )
                self.assertEqual(parsed.version, CONTRACT.CONTRACT_VERSION)
                self.assertEqual(parsed.family, family)
                self.assertEqual(parsed.unit_ids, [unit_id])
                self.assertEqual(parsed.structured_candidates, [])
                self.assertEqual(parsed.raw_path, raw.resolve())

    def test_legacy_mode_preserves_pre_contract_sources_without_sections(self) -> None:
        source = (
            self.memory
            / "wiki/sources/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-01.md"
        )
        source.parent.mkdir(parents=True)
        source.write_text("---\ndate: 2026-08-01\n---\n# Legacy source\n", encoding="utf-8")
        parsed = CONTRACT.load_compiled_source(
            source,
            expected_family="chatgpt",
            legacy_compatible=True,
            require_structured=False,
            require_instrument=False,
        )
        self.assertEqual(parsed.family, "chatgpt")
        self.assertEqual(parsed.status, "unknown")
        self.assertEqual(
            {item["code"] for item in parsed.validation_errors},
            {"missing_structured_candidates_section", "missing_instrument_candidates_section"},
        )
        self.assert_contract_error(
            "invalid_source_family",
            lambda: CONTRACT.load_compiled_source(source, expected_family="chatgpt"),
        )

    def test_missing_and_duplicate_json_sections_are_machine_readable(self) -> None:
        source, _ = self.write_source("chatgpt", "2026-08-28")
        original = source.read_text(encoding="utf-8")
        structured = "## Structured Candidates\n\n```json\n[]\n```\n"
        instrument = "## Instrument Knowledge Candidates\n\n```json\n[]\n```\n"

        source.write_text(original.replace(structured + "\n", ""), encoding="utf-8")
        self.assert_contract_error(
            "missing_structured_candidates_section",
            lambda: CONTRACT.load_compiled_source(source, memory_root=self.memory),
        )

        source.write_text(original + "\n" + structured, encoding="utf-8")
        self.assert_contract_error(
            "duplicate_structured_candidates_section",
            lambda: CONTRACT.load_compiled_source(source, memory_root=self.memory),
        )

        source.write_text(original.replace("\n" + instrument, ""), encoding="utf-8")
        self.assert_contract_error(
            "missing_instrument_candidates_section",
            lambda: CONTRACT.load_compiled_source(source, memory_root=self.memory),
        )

        source.write_text(original + "\n## 仪器知识候选\n\n```json\n[]\n```\n", encoding="utf-8")
        self.assert_contract_error(
            "duplicate_instrument_candidates_section",
            lambda: CONTRACT.load_compiled_source(source, memory_root=self.memory),
        )

    def test_receipt_gate_validates_source_and_raw_hashes_for_both_families(self) -> None:
        for family in ("chatgpt", "codex"):
            with self.subTest(family=family):
                source, raw = self.write_source(family, "2026-08-28")
                missing = CONTRACT.source_deposition_state(source, self.memory)
                self.assertEqual(missing["reason"], "daily_deposition_receipt_missing")
                receipt = CONTRACT.receipt_path(self.memory, family, "2026-08-28")
                receipt.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": CONTRACT.RECEIPT_VERSION,
                    "source_contract_version": CONTRACT.CONTRACT_VERSION,
                    "status": "completed",
                    "source_family": family,
                    "source_date": "2026-08-28",
                    "source_path": source.resolve().as_posix(),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "extraction_result": "no_relevant_content",
                    "reviewed_unit_count": 1,
                    "candidate_count": 0,
                    "candidate_ids": [],
                    "raw_files_modified": 0,
                    "actions_dispatched": 0,
                }
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                self.assertTrue(CONTRACT.source_deposition_state(source, self.memory)["ready"])

                source_bytes = source.read_bytes()
                source.write_bytes(source_bytes + b"\n")
                self.assertEqual(
                    CONTRACT.source_deposition_state(source, self.memory)["reason"],
                    "daily_deposition_receipt_mismatch",
                )
                source.write_bytes(source_bytes)
                raw.write_text(raw.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
                self.assertEqual(
                    CONTRACT.source_deposition_state(source, self.memory)["reason"],
                    "daily_deposition_receipt_mismatch",
                )

    def test_receipt_gate_keeps_pre_cutoff_legacy_compatibility(self) -> None:
        source = (
            self.memory
            / "wiki/sources/conversations/codex-daily/2026/codex-daily-report-2026-08-22.md"
        )
        source.parent.mkdir(parents=True)
        source.write_text(
            "---\ntype: codex_daily_source_summary\ndate: 2026-08-22\nstatus: access_incomplete\n---\n",
            encoding="utf-8",
        )
        state = CONTRACT.source_deposition_state(source, self.memory)
        self.assertTrue(state["ready"])
        self.assertEqual(state["reason"], "legacy_before_receipt_gate")

    def test_receipt_schema_fails_closed_for_non_objects_and_duplicate_ids(self) -> None:
        source, raw = self.write_source("codex", "2026-08-28")
        receipt = CONTRACT.receipt_path(self.memory, "codex", "2026-08-28")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("[]", encoding="utf-8")
        self.assertEqual(
            CONTRACT.source_deposition_state(source, self.memory)["reason"],
            "daily_deposition_receipt_invalid",
        )

        candidate_id = "cand-0123456789abcdef"
        receipt.write_text(json.dumps({
            "version": CONTRACT.RECEIPT_VERSION,
            "status": "completed",
            "source_family": "codex",
            "source_date": "2026-08-28",
            "source_path": source.resolve().as_posix(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "extraction_result": "candidates",
            "reviewed_unit_count": 1,
            "candidate_count": 2,
            "candidate_ids": [candidate_id, candidate_id],
            "raw_files_modified": 0,
            "actions_dispatched": 0,
        }), encoding="utf-8")
        self.assertEqual(
            CONTRACT.source_deposition_state(source, self.memory)["reason"],
            "daily_deposition_receipt_invalid",
        )

    def test_private_source_locator_is_retained_by_canonical_projection(self) -> None:
        locator = "https://chatgpt.com/c/12345678-abcd-4321-abcd-1234567890ab"
        candidate = {
            "topic": "采集模式",
            "summary": "来源支持的模式区别。",
            "question": "两种采集模式的选择性为什么不同？",
            "answer": "一种模式只约束单个离子，而另一种模式同时约束前体与产物关系，因此结构选择性通常更高，但仍需结合具体仪器配置确认。",
            "instrument_types": ["质谱"],
            "knowledge_status": "reusable",
            "privacy_mode": "non_reconstructable",
            "confidence": "bounded_synthesis",
            "evidence_boundary": "具体实现仍受仪器配置限制。",
            "evidence_refs": [{
                "source_type": "chatgpt_conversation",
                "session_id": "S01",
                "session_title": "采集模式",
                "support_level": "direct_answer",
                "excerpt": "正式会话明确说明前体与产物关系会共同提高结构选择性。",
                "source_locator": locator,
            }],
            "related_projects": [],
        }
        source, _ = self.write_source("chatgpt", "2026-08-28", instrument_payload=[candidate])
        parsed = CONTRACT.load_compiled_source(source, memory_root=self.memory)
        self.assertEqual(parsed.instrument_candidates[0]["evidence_refs"][0]["source_locator"], locator)


if __name__ == "__main__":
    unittest.main()
