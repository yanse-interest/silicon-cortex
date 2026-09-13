from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from registry_historical_replay import (
    HistoricalReplayError,
    _validated_bindings,
    run_historical_replay,
    run_series,
)
from registry_shadow_split import aggregate_path_hash


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
REGISTRY = WIKI / "project-dashboard-case-registry.json"
STAGE1 = WIKI / "review-cycles/registry-shadow-split/project-dashboard-registry-shadow-split-post-capability-fix-2026-08-28.json"
H5 = Path(__file__).resolve().parent.parent / "codex-project-dashboard-h5/public/dashboard-snapshot.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RegistryHistoricalReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.gate_root = self.root / "gate"
        self.scratch_parent = self.root / "scratch"
        self.scratch_parent.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_real_archived_date_replays_in_isolation_without_canonical_mutation(self) -> None:
        protected = {
            "registry": REGISTRY,
            "h5": H5,
            "raw": VAULT / "raw",
            "sources": WIKI / "sources",
            "daily_receipts": WIKI / "review-cycles/daily-deposition",
            "legacy_streak": WIKI / "review-cycles/registry-shadow-compare/stage-2-streak.json",
        }
        before = {key: aggregate_path_hash(path) for key, path in protected.items()}
        result = run_historical_replay(
            source_date="2026-08-28",
            registry_path=REGISTRY,
            wiki_root=WIKI,
            memory_root=VAULT,
            source_root=WIKI / "sources/conversations",
            stage1_receipt_path=STAGE1,
            h5_snapshot_path=H5,
            gate_root=self.gate_root,
            scratch_parent=self.scratch_parent,
        )
        after = {key: aggregate_path_hash(path) for key, path in protected.items()}
        self.assertEqual(before, after)
        receipt = result["receipt"]
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["date_contract_classification"], "receipt_required_contract")
        self.assertTrue(receipt["checks"]["all_required_checks_passed"])
        self.assertTrue(receipt["checks"]["legacy_stage2_streak_unchanged"])
        self.assertTrue(receipt["temporary_scratch_deleted"])
        self.assertEqual(list(self.scratch_parent.iterdir()), [])

    def test_compare_failure_cleans_scratch_and_publishes_no_replay_receipt(self) -> None:
        def fail_compare(**_arguments):
            raise RuntimeError("injected compare failure")

        with self.assertRaisesRegex(RuntimeError, "injected compare failure"):
            run_historical_replay(
                source_date="2026-08-28",
                registry_path=REGISTRY,
                wiki_root=WIKI,
                memory_root=VAULT,
                source_root=WIKI / "sources/conversations",
                stage1_receipt_path=STAGE1,
                h5_snapshot_path=H5,
                gate_root=self.gate_root,
                scratch_parent=self.scratch_parent,
                shadow_compare_fn=fail_compare,
            )
        self.assertEqual(list(self.scratch_parent.iterdir()), [])
        self.assertEqual(list((self.gate_root / "historical-replays").glob("*.json")), [])

    def test_binding_hash_tamper_fails_closed(self) -> None:
        memory = self.root / "memory"
        inputs = {}
        for family in ("chatgpt", "codex"):
            files = {}
            for kind in ("source", "raw", "receipt"):
                path = memory / f"{family}-{kind}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{family}-{kind}\n", encoding="utf-8")
                files[f"{kind}_path"] = path.as_posix()
                files[f"{kind}_sha256"] = sha(path)
            inputs[family] = files
        generation = hashlib.sha256(
            json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        gate = {"ready": True, "inputs": inputs, "source_generation_sha256": generation}
        Path(inputs["chatgpt"]["raw_path"]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(HistoricalReplayError, "hash mismatch"):
            _validated_bindings(gate, memory_root=memory)

    def test_official_series_requires_exactly_seven_distinct_consecutive_dates(self) -> None:
        common = {
            "registry_path": REGISTRY,
            "wiki_root": WIKI,
            "memory_root": VAULT,
            "source_root": WIKI / "sources/conversations",
            "stage1_receipt_path": STAGE1,
            "h5_snapshot_path": H5,
            "gate_root": self.gate_root,
        }
        with self.assertRaisesRegex(HistoricalReplayError, "exactly seven"):
            run_series(dates=["2026-08-28"], **common)
        with self.assertRaisesRegex(HistoricalReplayError, "consecutive"):
            run_series(
                dates=["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-28"],
                **common,
            )


if __name__ == "__main__":
    unittest.main()
