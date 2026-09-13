from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_h5_snapshot import build_h5_snapshot, run_export
from registry_shadow_compare import ShadowCompareError, capability_projection, run_shadow_compare
from registry_shadow_split import write_shadow_split


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
LIVE_REGISTRY = WIKI / "project-dashboard-case-registry.json"
LIVE_H5 = Path(__file__).resolve().parent.parent / "codex-project-dashboard-h5/public/dashboard-snapshot.json"


def hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        digest.update((item.name if path.is_file() else item.relative_to(path).as_posix()).encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class RegistryShadowCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.registry = self.root / "registry.json"
        self.registry.write_bytes(LIVE_REGISTRY.read_bytes())
        self.h5 = self.root / "dashboard-snapshot.json"
        self.h5.write_text("{}\n", encoding="utf-8")
        self.stage1 = self._stage1_receipt(self.registry, "stage1.json")
        self.output = self.root / "receipts"
        self.output.mkdir(parents=True)
        (self.output / "stage-2-streak.json").write_text(
            json.dumps({"consecutive_eligible_successes": 3, "observations": [{"observation_id": "legacy"}]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stage1_receipt(self, registry: Path, name: str) -> Path:
        generation = self.root / f"{name}.generation"
        write_shadow_split(registry, generation)
        archived = self.root / name
        archived.write_bytes((generation / "parity-receipt.json").read_bytes())
        return archived

    def compare(self, **overrides):
        arguments = {
            "registry_path": self.registry,
            "wiki_root": WIKI,
            "memory_root": VAULT,
            "stage1_receipt_path": self.stage1,
            "output_root": self.output,
            "h5_snapshot_path": self.h5,
            "h5_builder": build_h5_snapshot,
            "synced_through": "2026-08-26",
            "observation_id": "stage2-test:one",
            "guard_paths": {},
        }
        arguments.update(overrides)
        return run_shadow_compare(**arguments)

    def receipt(self, result: dict) -> dict:
        return json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))

    def test_post_fix_capability_exact_semantic_parity_and_dedupe(self) -> None:
        source = json.loads(self.registry.read_text(encoding="utf-8"))
        capability = capability_projection(source)
        self.assertEqual(capability["counts"]["domains"], 1)
        self.assertEqual(capability["counts"]["active_items"], 28)
        self.assertEqual(capability["counts"]["source_grounded_items"], 28)
        self.assertEqual(capability["counts"]["unique_item_ids"], 28)
        self.assertEqual(capability["counts"]["unique_source_question_pairs"], 28)
        receipt = self.receipt(self.compare())
        self.assertEqual(receipt["capability_parity"]["semantic_sha256"]["source"], receipt["capability_parity"]["semantic_sha256"]["recomposed"])
        self.assertTrue(receipt["capability_parity"]["dedupe_unique"])

    def test_dashboard_h5_protected_fields_and_privacy_all_pass(self) -> None:
        receipt = self.receipt(self.compare())
        self.assertEqual(receipt["result"], "passed")
        self.assertTrue(receipt["registry_parity"]["protected_field_parity"])
        self.assertEqual(receipt["dashboard_snapshot_parity"]["semantic_sha256"]["canonical_registry_snapshot"], receipt["dashboard_snapshot_parity"]["semantic_sha256"]["recomposed_registry_snapshot"])
        self.assertEqual(receipt["h5_snapshot_parity"]["semantic_sha256"]["canonical_registry_h5"], receipt["h5_snapshot_parity"]["semantic_sha256"]["recomposed_registry_h5"])
        self.assertEqual(receipt["privacy_checks"]["nested_private_locator_confinement"], "passed")
        self.assertEqual(receipt["privacy_checks"]["h5_public_whitelist"], "passed")
        self.assertGreater(receipt["privacy_checks"]["private_nested_field_occurrences"], 0)

    def test_same_input_rerun_is_idempotent_and_never_touches_legacy_streak(self) -> None:
        before = (self.output / "stage-2-streak.json").read_bytes()
        first = self.compare()
        second = self.compare()
        self.assertEqual(first["receipt_path"], second["receipt_path"])
        self.assertEqual(second["streak_action"], "legacy_streak_frozen_no_change")
        self.assertEqual((self.output / "stage-2-streak.json").read_bytes(), before)
        self.assertEqual(len(list(self.output.glob("project-dashboard-registry-shadow-compare-*.json"))), 1)

    def test_source_hash_change_creates_distinct_compare_evidence_without_legacy_mutation(self) -> None:
        before = (self.output / "stage-2-streak.json").read_bytes()
        self.compare()
        changed = json.loads(self.registry.read_text(encoding="utf-8"))
        changed["updated_at"] = "2026-08-28T02:00:00+08:00"
        self.registry.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        result = self.compare()
        self.assertEqual(result["streak_action"], "legacy_streak_frozen_no_change")
        self.assertEqual(len(list(self.output.glob("project-dashboard-registry-shadow-compare-*.json"))), 2)
        self.assertEqual((self.output / "stage-2-streak.json").read_bytes(), before)

    def test_static_stage1_anchor_allows_new_valid_runtime_generation(self) -> None:
        anchor = self.stage1.read_bytes()
        changed = json.loads(self.registry.read_text(encoding="utf-8"))
        changed["updated_at"] = "2026-08-28T03:00:00+08:00"
        self.registry.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        result = self.compare(observation_id="stage2-test:new-generation")
        receipt = self.receipt(result)
        self.assertEqual(self.stage1.read_bytes(), anchor)
        self.assertNotEqual(
            receipt["stage_1_anchor_receipt"]["generation_id"],
            receipt["stage_1_runtime_generation"]["generation_id"],
        )
        self.assertEqual(
            receipt["stage_1_runtime_generation"]["source_registry_sha256"],
            result["source_registry_sha256"],
        )

    def test_mismatch_publishes_hashed_diagnostics_and_preserves_legacy_streak(self) -> None:
        self.compare()
        before = (self.output / "stage-2-streak.json").read_bytes()

        def mutate(recomposed: dict) -> None:
            recomposed["cases"][0]["title"] = "shadow mismatch"

        with self.assertRaises(ShadowCompareError) as raised:
            self.compare(observation_id="stage2-test:two", recomposed_mutator=mutate)
        receipt = json.loads(raised.exception.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["result"], "failed")
        self.assertIn("registry", receipt["diagnostics"])
        self.assertIn("dashboard_snapshot", receipt["diagnostics"])
        self.assertEqual((self.output / "stage-2-streak.json").read_bytes(), before)
        self.assertNotIn("shadow mismatch", json.dumps(receipt, ensure_ascii=False))

    def test_failed_rerun_of_same_observation_gets_separate_receipt_without_streak_effect(self) -> None:
        passed = self.compare()
        before = (self.output / "stage-2-streak.json").read_bytes()

        def mutate(recomposed: dict) -> None:
            recomposed["cases"][0]["title"] = "same-observation mismatch"

        with self.assertRaises(ShadowCompareError) as raised:
            self.compare(recomposed_mutator=mutate)
        self.assertNotEqual(Path(passed["receipt_path"]), raised.exception.receipt_path)
        self.assertTrue(Path(passed["receipt_path"]).exists())
        self.assertTrue(raised.exception.receipt_path.exists())
        self.assertEqual((self.output / "stage-2-streak.json").read_bytes(), before)

    def test_receipt_publication_rolls_back_after_injected_crash(self) -> None:
        first = self.compare()
        state_path = self.output / "stage-2-streak.json"
        before = state_path.read_bytes()
        with self.assertRaises(ShadowCompareError):
            self.compare(observation_id="stage2-test:after-receipt", inject_failure="after_receipt")
        self.assertEqual(state_path.read_bytes(), before)
        receipts = list(self.output.glob("project-dashboard-registry-shadow-compare-*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0], Path(first["receipt_path"]))

    def test_caller_controlled_eligibility_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "eligible_real_refresh"):
            self.compare(eligible_real_refresh=True)

    def test_compare_mutates_no_canonical_registry_evidence_ledger_or_snapshot(self) -> None:
        protected = {
            "registry": LIVE_REGISTRY,
            "raw": VAULT / "raw",
            "sources": WIKI / "sources",
            "daily_receipts": WIKI / "review-cycles/daily-deposition",
            "promotion_ledger": WIKI / "review-cycles/promotion-ledger.json",
            "h5_snapshot": LIVE_H5,
        }
        before = {key: hash_path(path) for key, path in protected.items()}
        self.compare()
        after = {key: hash_path(path) for key, path in protected.items()}
        self.assertEqual(before, after)

    @patch("export_h5_snapshot.run_contiguous_catchup", side_effect=ShadowCompareError("mismatch"))
    @patch("export_h5_snapshot.account_guard", return_value={"account_id": "account-1"})
    @patch("export_h5_snapshot.load_config", return_value={"account_policy": {}})
    def test_production_export_keeps_last_validated_h5_on_shadow_failure(self, *_mocks) -> None:
        output = self.root / "production.json"
        output.write_bytes(b"last-validated\n")
        before_registry = LIVE_REGISTRY.read_bytes()
        with self.assertRaises(ShadowCompareError):
            run_export(config_path=self.root / "config.json", output=output, enforce_gates=True)
        self.assertEqual(output.read_bytes(), b"last-validated\n")
        self.assertEqual(LIVE_REGISTRY.read_bytes(), before_registry)


if __name__ == "__main__":
    unittest.main()
