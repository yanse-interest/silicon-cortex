from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import compiled_source_contract as contract
from daily_deposition_receipt import receipt_path
from dashboard_model import parse_instrument_knowledge_candidates
from export_h5_snapshot import value_item


PROJECT_DIR = Path(__file__).resolve().parent


class DashboardCompiledSourceContractTests(unittest.TestCase):
    def test_dashboard_loader_resolves_canonical_contract_from_any_cwd(self) -> None:
        self.assertTrue(contract.CANONICAL_CONTRACT_PATH.is_file())
        self.assertIn("codex-skills/chatgpt-daily-report/scripts", contract.CANONICAL_CONTRACT_PATH.as_posix())
        with tempfile.TemporaryDirectory() as workdir:
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        f"sys.path.insert(0, {str(PROJECT_DIR)!r}); "
                        "import compiled_source_contract as contract; "
                        "print(contract.CONTRACT_VERSION)"
                    ),
                ],
                cwd=workdir,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.stdout.strip(), str(contract.CONTRACT_VERSION))

    def test_receipt_compatibility_export_uses_canonical_path_builder(self) -> None:
        root = Path("/tmp/contract-path-test")
        self.assertEqual(
            receipt_path(root, "chatgpt-daily", "2026-08-28"),
            contract.receipt_path(root, "chatgpt", "2026-08-28"),
        )

    def test_private_locator_stays_internal_and_is_excluded_from_h5(self) -> None:
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
        text = (
            "## Instrument Knowledge Candidates\n\n```json\n"
            + json.dumps([candidate], ensure_ascii=False)
            + "\n```\n"
        )
        events = parse_instrument_knowledge_candidates(
            text,
            date="2026-08-28",
            source="sources/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-28.md",
            source_status="ready",
            coverage="complete",
        )
        self.assertEqual(events[0]["evidence_refs"][0]["source_locator"], locator)

        public = value_item(events[0])
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("source_locator", serialized)
        self.assertNotIn(locator, serialized)
        self.assertNotIn("excerpt", public["evidence_refs"][0])


if __name__ == "__main__":
    unittest.main()
