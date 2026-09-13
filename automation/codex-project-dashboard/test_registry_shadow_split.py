from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dashboard_model import CaseRegistry
from export_h5_snapshot import build_h5_snapshot
from registry_shadow_split import (
    ShadowSplitError,
    _privacy_violations,
    protected_state_hash,
    recompose_registry,
    semantic_normalize,
    sha256_bytes,
    split_registry,
    write_shadow_split,
)


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
LIVE_REGISTRY = VAULT / "wiki/project-dashboard-case-registry.json"


def read_live() -> tuple[bytes, dict]:
    raw = LIVE_REGISTRY.read_bytes()
    return raw, json.loads(raw)


def aggregate_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    for path in sorted(files, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(VAULT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class RegistryShadowSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw, self.registry = read_live()
        self.source_hash = sha256_bytes(self.raw)

    def projections(self, registry: dict | None = None):
        return split_registry(registry or self.registry, self.source_hash)

    def test_full_semantic_and_manual_hash_parity_for_live_registry(self) -> None:
        manual, replay, inventory = self.projections()
        recomposed = recompose_registry(manual, replay)
        self.assertEqual(semantic_normalize(recomposed), semantic_normalize(self.registry))
        recomposed_manual, _, _ = split_registry(recomposed, self.source_hash)
        self.assertEqual(protected_state_hash(manual), protected_state_hash(recomposed_manual))
        self.assertEqual(inventory["field_ownership_coverage"]["unknown_or_unclassified_fields"], [])

    def test_live_ids_and_counts_are_discovered_without_stale_hardcoding(self) -> None:
        manual, replay, inventory = self.projections()
        counts = inventory["counts"]
        self.assertEqual(counts["cases"], len(self.registry["cases"]))
        self.assertEqual(counts["events"], len(self.registry["events"]))
        self.assertEqual(counts["value_candidates"], len(self.registry["value_candidates"]))
        self.assertEqual(replay["ordering"]["case_ids"], [item["case_id"] for item in self.registry["cases"]])
        self.assertEqual(replay["ordering"]["event_ids"], [item["event_id"] for item in self.registry["events"]])
        self.assertEqual(replay["ordering"]["candidate_ids"], [item["candidate_id"] for item in self.registry["value_candidates"]])

    def test_replay_is_idempotent(self) -> None:
        manual_one, replay_one, _ = self.projections()
        recomposed = recompose_registry(manual_one, replay_one)
        manual_two, replay_two, _ = split_registry(recomposed, self.source_hash)
        self.assertEqual(manual_one["state"], manual_two["state"])
        self.assertEqual(replay_one["state"], replay_two["state"])
        self.assertEqual(replay_one["ordering"], replay_two["ordering"])

    def test_protected_routing_survives_replay_and_source_correction(self) -> None:
        source = copy.deepcopy(self.registry)
        event = next(item for item in source["events"] if item.get("source_kind") != "manual")
        event.update(
            {
                "state": "routed",
                "case_id": source["cases"][0]["case_id"],
                "additional_case_ids": [source["cases"][-1]["case_id"]],
                "routing_reason": "manual_test_route",
                "route_origin": "manual",
                "routing_history": [{"action": "route", "actor": "test"}],
            }
        )
        manual, replay, _ = split_registry(source, self.source_hash)
        replay_event = next(item for item in replay["state"]["events"] if item["event_id"] == event["event_id"])
        replay_event["detail"] = "source-owned corrected detail"
        merged = recompose_registry(manual, replay)
        merged_event = next(item for item in merged["events"] if item["event_id"] == event["event_id"])
        self.assertEqual(merged_event["detail"], "source-owned corrected detail")
        for field in ("state", "case_id", "additional_case_ids", "routing_reason", "route_origin", "routing_history"):
            self.assertEqual(merged_event[field], event[field])
        merged_manual, _, _ = split_registry(merged, self.source_hash)
        self.assertEqual(protected_state_hash(manual), protected_state_hash(merged_manual))

    def test_private_fields_are_whitelisted_out_of_replay_projection(self) -> None:
        source = copy.deepcopy(self.registry)
        source["events"][0].update({
            "codex_project_id": "project-private",
            "codex_cwd": "/Users/example/private-project",
            "codex_project_identity_source": "thread_project_id",
            "project_metadata_present": True,
        })
        manual, replay, inventory = self.projections(source)
        self.assertEqual(_privacy_violations(replay), [])
        private_overlays = sum(
            len(item.get("private_overlays") or [])
            for key in ("event_overlays", "value_candidate_overlays", "case_value_routes")
            for item in manual["state"][key]
        )
        self.assertEqual(private_overlays, inventory["field_ownership_coverage"]["private_nested_field_occurrences"])
        self.assertGreater(private_overlays, 0, "live registry should exercise private source-locator routing")
        self.assertIn("source_locator", json.dumps(manual, ensure_ascii=False))
        self.assertNotIn("source_locator", json.dumps(replay, ensure_ascii=False))
        self.assertIn("project-private", json.dumps(manual, ensure_ascii=False))
        self.assertNotIn("project-private", json.dumps(replay, ensure_ascii=False))
        self.assertNotIn("/Users/example/private-project", json.dumps(replay, ensure_ascii=False))

    def test_unknown_fields_missing_ids_and_duplicates_fail_closed(self) -> None:
        missing = copy.deepcopy(self.registry)
        missing["events"][0].pop("event_id", None)
        duplicate = copy.deepcopy(self.registry)
        duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]
        unknown_nested = copy.deepcopy(self.registry)
        unknown_nested["value_candidates"][0]["new_unclassified_field"] = True
        with self.assertRaisesRegex(ShadowSplitError, "top-level"):
            split_registry({**copy.deepcopy(self.registry), "unknown_stage1_field": True}, self.source_hash)
        with self.assertRaisesRegex(ShadowSplitError, "missing stable ID"):
            split_registry(missing, self.source_hash)
        with self.assertRaisesRegex(ShadowSplitError, "duplicate stable IDs"):
            split_registry(duplicate, self.source_hash)
        with self.assertRaisesRegex(ShadowSplitError, "unknown/unclassified"):
            split_registry(unknown_nested, self.source_hash)

    def test_atomic_fresh_output_and_injected_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            parent = Path(temporary)
            source = parent / "registry.json"
            source.write_bytes(self.raw)
            failed = parent / "failed-generation"
            with self.assertRaisesRegex(ShadowSplitError, "injected failure"):
                write_shadow_split(source, failed, inject_failure="before_commit")
            self.assertFalse(failed.exists())
            self.assertEqual(list(parent.glob(".failed-generation.staging-*")), [])
            success = parent / "success-generation"
            write_shadow_split(source, success, expected_source_sha256=self.source_hash)
            self.assertEqual(
                sorted(item.name for item in success.iterdir()),
                ["manual-state.json", "parity-receipt.json", "recomposed-registry.json", "replay-state.json"],
            )
            with self.assertRaisesRegex(ShadowSplitError, "fresh and absent"):
                write_shadow_split(source, success)

    def test_source_hash_change_before_commit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            source = root / "registry.json"
            source.write_bytes(self.raw)
            output = root / "generation"

            def mutate_source() -> None:
                changed = json.loads(source.read_text(encoding="utf-8"))
                changed["updated_at"] = "source-changed-during-split"
                source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ShadowSplitError, "changed during split"):
                write_shadow_split(source, output, before_commit_hook=mutate_source)
            self.assertFalse(output.exists())

    def test_snapshot_and_h5_projection_parity_when_called_in_isolation(self) -> None:
        manual, replay, _ = self.projections()
        recomposed = recompose_registry(manual, replay)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            snapshots = []
            for index, registry in enumerate((self.registry, recomposed)):
                wiki = root / str(index) / "wiki"
                wiki.mkdir(parents=True)
                registry_path = wiki / "project-dashboard-case-registry.json"
                registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
                store = CaseRegistry(
                    wiki_root=wiki,
                    registry_path=registry_path,
                    reviews_root=wiki / "reviews",
                    daily_root=wiki / "sources/chatgpt",
                    codex_daily_root=wiki / "sources/codex",
                    live_state_path=root / "missing-live-state.json",
                    memory_root=wiki.parent,
                )
                snapshot = store.snapshot()
                snapshot.pop("generated_at", None)
                snapshots.append(snapshot)
            self.assertEqual(snapshots[0], snapshots[1])
            public_one = build_h5_snapshot({**snapshots[0], "generated_at": "normalized"})
            public_two = build_h5_snapshot({**snapshots[1], "generated_at": "normalized"})
            self.assertEqual(public_one, public_two)

    def test_live_shadow_split_mutates_no_canonical_inputs(self) -> None:
        protected = [
            VAULT / "raw",
            VAULT / "wiki/sources",
            VAULT / "wiki/review-cycles/daily-deposition",
            VAULT / "wiki/review-cycles/promotion-ledger.json",
            LIVE_REGISTRY,
        ]
        live_raw = LIVE_REGISTRY.read_bytes()
        live_hash = sha256_bytes(live_raw)
        before = aggregate_hash(protected)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            output = Path(temporary) / "shadow"
            receipt = write_shadow_split(
                LIVE_REGISTRY,
                output,
                expected_source_sha256=live_hash,
                guard_paths={
                    "raw": VAULT / "raw",
                    "sources": VAULT / "wiki/sources",
                    "daily_receipts": VAULT / "wiki/review-cycles/daily-deposition",
                    "promotion_ledger": VAULT / "wiki/review-cycles/promotion-ledger.json",
                },
            )
            receipt = json.loads((output / "parity-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(receipt["canonical_input_guard"]["status"], "unchanged")
            self.assertEqual(
                receipt["canonical_input_guard"]["hashes_before"],
                receipt["canonical_input_guard"]["hashes_after"],
            )
        self.assertEqual(before, aggregate_hash(protected))


if __name__ == "__main__":
    unittest.main()
