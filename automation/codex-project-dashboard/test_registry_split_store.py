from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from dashboard_model import CaseRegistry, parse_codex_daily_source, parse_daily_source
from registry_shadow_split import semantic_normalize
from registry_split_store import SplitStoreCoordinator


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
LIVE_REGISTRY = VAULT / "wiki/project-dashboard-case-registry.json"


class RegistrySplitStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.wiki = self.root / "wiki"
        self.wiki.mkdir()
        self.compatibility = self.wiki / "project-dashboard-case-registry.json"
        self.manual = self.wiki / "project-dashboard-manual-state.json"
        self.replay = self.wiki / "project-dashboard-replay-state.json"
        self.compatibility.write_bytes(LIVE_REGISTRY.read_bytes())
        self.coordinator = SplitStoreCoordinator(
            manual_path=self.manual,
            replay_path=self.replay,
            compatibility_path=self.compatibility,
            journal_path=self.root / "split-transaction/journal.json",
            lock_path=self.root / "split.lock",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cutover_initialization_and_compatibility_projection_parity(self) -> None:
        before = json.loads(self.compatibility.read_text(encoding="utf-8"))
        initialized = self.coordinator.initialize_from_compatibility()
        self.assertTrue(self.manual.is_file())
        self.assertTrue(self.replay.is_file())
        self.assertEqual(semantic_normalize(initialized), semantic_normalize(before))
        self.assertEqual(
            semantic_normalize(self.coordinator.load()),
            semantic_normalize(json.loads(self.compatibility.read_text(encoding="utf-8"))),
        )
        self.assertEqual(json.loads(self.manual.read_text())["classification"], "canonical_state")
        self.assertEqual(json.loads(self.replay.read_text())["classification"], "derived_view")

    def test_manual_create_update_route_undo_ignore_round_trip(self) -> None:
        self.coordinator.initialize_from_compatibility()
        store = CaseRegistry(
            wiki_root=self.wiki,
            registry_path=self.compatibility,
            reviews_root=self.wiki / "reviews",
            daily_root=self.wiki / "sources/chatgpt",
            codex_daily_root=self.wiki / "sources/codex",
            live_state_path=self.root / "missing-live-state.json",
            memory_root=self.root,
            split_journal_path=self.root / "split-transaction/journal.json",
            split_lock_path=self.root / "split.lock",
        )
        created = store.create_case({
            "title": "Stage 4 round trip",
            "line": "life",
            "status": "in_progress",
            "current_summary": "created",
            "next_step": "update",
        })
        updated = store.update_case({
            "case_id": created["case_id"],
            "title": "Stage 4 round trip updated",
            "line": "life",
            "status": "on_hold",
            "current_summary": "updated",
            "next_step": "verify",
        })
        self.assertEqual(updated["status"], "on_hold")
        event_id = next(
            event["event_id"]
            for event in store.load()["events"]
            if event.get("source_kind") != "manual" and event.get("state") != "compliance_review"
        )
        store.route_event(event_id, created["case_id"], set_current=True)
        self.assertEqual(store.load()["events"][[e["event_id"] for e in store.load()["events"]].index(event_id)]["state"], "routed")
        store.undo_event_route(event_id)
        store.ignore_event(event_id)
        final = store.load()
        event = next(item for item in final["events"] if item["event_id"] == event_id)
        self.assertEqual(event["state"], "ignored")
        self.assertTrue(event["routing_history"])
        self.assertEqual(
            semantic_normalize(final),
            semantic_normalize(json.loads(self.compatibility.read_text(encoding="utf-8"))),
        )

    def test_injected_failures_restore_the_previous_complete_generation(self) -> None:
        baseline = self.coordinator.initialize_from_compatibility()
        before = {path: path.read_bytes() for path in (self.manual, self.replay, self.compatibility)}
        changed = copy.deepcopy(baseline)
        changed["cases"][0]["current_summary"] = "must roll back"
        for point in ("before_replace", "after_manual", "after_replay", "after_compatibility"):
            with self.assertRaisesRegex(Exception, "injected failure"):
                self.coordinator.write(changed, writer_role="crash_test", inject_failure=point)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertFalse(self.coordinator.journal_path.exists())
            self.assertEqual(semantic_normalize(self.coordinator.load()), semantic_normalize(baseline))

    def test_abandoned_journal_is_recovered_before_read(self) -> None:
        baseline = self.coordinator.initialize_from_compatibility()
        before_manual = self.manual.read_bytes()

        class SimulatedCrash(BaseException):
            pass

        changed = copy.deepcopy(baseline)
        changed["cases"][0]["current_summary"] = "partial generation"
        with self.assertRaises(SimulatedCrash):
            self.coordinator.write(
                changed,
                writer_role="crash_test",
                after_replace=lambda key: (_ for _ in ()).throw(SimulatedCrash()) if key == "manual" else None,
            )
        self.assertTrue(self.coordinator.journal_path.is_file())
        self.assertNotEqual(self.manual.read_bytes(), before_manual)
        recovered = self.coordinator.load()
        self.assertEqual(semantic_normalize(recovered), semantic_normalize(baseline))
        self.assertEqual(self.manual.read_bytes(), before_manual)
        self.assertFalse(self.coordinator.journal_path.exists())

    def test_source_parsers_have_no_manual_store_write_dependency(self) -> None:
        # Parsers return source-owned records only.  The coordinator is the sole
        # persistence boundary and receives an explicit writer role from refresh.
        for parser in (parse_daily_source, parse_codex_daily_source):
            names = set(parser.__code__.co_names)
            self.assertNotIn("SplitStoreCoordinator", names)
            self.assertNotIn("project-dashboard-manual-state.json", parser.__code__.co_consts)


if __name__ == "__main__":
    unittest.main()
