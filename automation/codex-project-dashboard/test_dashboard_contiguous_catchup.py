from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard_contiguous_catchup import (
    DashboardCatchupError,
    RefreshTransaction,
    atomic_write_json,
    registry_daily_max_date,
    run_contiguous_catchup,
)
from dashboard_model import CaseRegistry
from export_h5_snapshot import build_h5_snapshot
from registry_split_store import SplitStoreCoordinator


class DashboardContiguousCatchupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.memory = self.root / "memory"
        self.wiki = self.memory / "wiki"
        self.registry = self.wiki / "project-dashboard-case-registry.json"
        self.output = self.root / "h5/dashboard-snapshot.json"
        self.stage2 = self.wiki / "review-cycles/registry-shadow-compare"
        self.refresh_receipts = self.wiki / "review-cycles/dashboard-refresh"
        self.sources = self.wiki / "sources/conversations"
        self.transaction = self.root / "transaction"
        self.lock = self.root / "refresh.lock"
        self.now = datetime(2026, 8, 29, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        atomic_write_json(self.registry, {"events": []})
        self._write_h5("2026-08-26")
        atomic_write_json(self.stage2 / "stage-2-streak.json", {
            "consecutive_eligible_successes": 1,
            "observations": [{"observation_id": "baseline"}],
        })
        self.snapshot_dates: list[str] = []
        self.compare_dates: list[str] = []
        self.projection_flags: list[tuple[str, bool]] = []
        self.fail_gate_date = ""
        self.invalid_gate_date = ""
        self.fail_compare_date = ""
        self.input_files = [self.root / "input-chatgpt", self.root / "input-codex"]
        for path in self.input_files:
            path.write_text(path.name, encoding="utf-8")
        self.input_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.input_files}
        self.split_store: SplitStoreCoordinator | None = None

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_h5(self, source_date: str) -> None:
        atomic_write_json(self.output, {
            "schema_version": 3,
            "generated_at": f"{source_date}T23:59:59+08:00",
            "daily_updated_through": source_date,
            "summary": {},
            "work_sections": [],
            "life_cases": [],
            "closed_cases": [],
            "capability_domains": [],
        })

    def source_gate(self, target: str) -> dict:
        if target == self.fail_gate_date:
            return {"ready": False, "target_date": target, "missing_sources": ["codex"]}
        if target == self.invalid_gate_date:
            return {"ready": False, "target_date": target, "invalid_sources": ["chatgpt"]}
        generation = hashlib.sha256(f"two-source:{target}".encode()).hexdigest()
        return {
            "ready": True,
            "target_date": target,
            "source_generation_sha256": generation,
            "inputs": {
                "chatgpt": {"source_path": str(self.input_files[0]), "source_sha256": self.input_hashes[self.input_files[0]]},
                "codex": {"source_path": str(self.input_files[1]), "source_sha256": self.input_hashes[self.input_files[1]]},
            },
        }

    def _write_registry(self, registry: dict) -> None:
        if self.split_store is None:
            atomic_write_json(self.registry, registry)
        else:
            self.split_store.write(registry, writer_role="test_source_refresh")

    def enable_split_store(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry.setdefault("version", 4)
        registry.setdefault("cases", [])
        registry.setdefault("value_candidates", [])
        atomic_write_json(self.registry, registry)
        self.split_store = SplitStoreCoordinator(
            manual_path=self.registry.with_name("project-dashboard-manual-state.json"),
            replay_path=self.registry.with_name("project-dashboard-replay-state.json"),
            compatibility_path=self.registry,
            journal_path=self.root / "split-transaction/journal.json",
            lock_path=self.root / "split.lock",
        )
        self.split_store.initialize_from_compatibility()

    def snapshot(self, *, source_through_date: str, read_only_projection: bool = False) -> dict:
        self.snapshot_dates.append(source_through_date)
        self.projection_flags.append((source_through_date, read_only_projection))
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        if read_only_projection:
            registry["events"] = [
                event for event in registry["events"]
                if event.get("source_kind") not in {"daily", "daily_instrument_knowledge", "codex_daily"}
                or str(event.get("date") or "") <= source_through_date
            ]
        if not any(event.get("date") == source_through_date for event in registry["events"]):
            registry["events"].append({
                "event_id": f"daily:{source_through_date}",
                "date": source_through_date,
                "source_kind": "daily",
            })
            if not read_only_projection:
                self._write_registry(registry)
        projects = [
            {
                "case_id": "private-new",
                "title": "New",
                "updated_at": source_through_date,
                "logs": [{"event_id": "private-event", "date": source_through_date, "detail": "new", "source": "chatgpt"}],
            },
            {"case_id": "private-old", "title": "Old", "updated_at": "2026-08-01", "logs": []},
        ]
        return {
            "generated_at": f"{source_through_date}T23:59:59+08:00",
            "daily_updated_through": source_through_date,
            "summary": {"case_count": 2, "value_item_count": 1},
            "categories": [{"id": "automation", "label": "Automation"}],
            "branches": [],
            "capability_domains": [{
                "title": "仪器知识与应用方法论",
                "tag_label": "仪器类型",
                "items": [{
                    "question": "Q?", "answer": "A source grounded answer", "detail": "A source grounded answer",
                    "answer_status": "source_grounded", "instrument_types": ["质谱"],
                    "evidence_refs": [{"source_type": "daily_report", "session_id": "S01", "source_locator": "/private/source"}],
                }],
            }],
            "work_groups": {"automation": projects},
            "life_cases": [],
            "closed_cases": [],
        }

    def shadow_compare(self, **arguments) -> dict:
        target = str(arguments["synced_through"])
        self.compare_dates.append(target)
        state_path = self.stage2 / "stage-2-streak.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if target == self.fail_compare_date:
            raise RuntimeError("injected second-day compare failure")
        receipt = self.stage2 / f"project-dashboard-registry-shadow-compare-{target}.json"
        atomic_write_json(receipt, {
            "result": "passed",
            "observation_id": arguments["observation_id"],
            "source_registry": {"sha256": hashlib.sha256(self.registry.read_bytes()).hexdigest()},
        })
        return {
            "receipt_path": str(receipt),
            "consecutive_successes": state["consecutive_eligible_successes"],
            "streak_action": "legacy_streak_frozen_no_change",
        }

    def execute_catchup(self) -> dict:
        return run_contiguous_catchup(
            output_path=self.output,
            registry_path=self.registry,
            wiki_root=self.wiki,
            memory_root=self.memory,
            source_root=self.sources,
            stage1_receipt_path=self.root / "stage1.json",
            stage2_root=self.stage2,
            refresh_receipt_root=self.refresh_receipts,
            h5_builder=build_h5_snapshot,
            source_gate=self.source_gate,
            snapshot_fn=self.snapshot,
            shadow_compare_fn=self.shadow_compare,
            atomic_h5_writer=atomic_write_json,
            now=self.now,
            transaction_root=self.transaction,
            lock_path=self.lock,
        )

    def test_empty_noop_is_idempotent(self) -> None:
        self._write_h5("2026-08-28")
        result = self.execute_catchup()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["committed_dates"], [])
        self.assertEqual(self.snapshot_dates, [])
        self.assertEqual(self.compare_dates, [])

    def test_one_and_multiple_contiguous_days(self) -> None:
        self._write_h5("2026-08-27")
        one = self.execute_catchup()
        self.assertEqual([item["source_date"] for item in one["committed_dates"]], ["2026-08-28"])
        self.assertEqual(json.loads(self.output.read_text())["daily_updated_through"], "2026-08-28")

        self._write_h5("2026-08-26")
        atomic_write_json(self.registry, {"events": []})
        atomic_write_json(self.stage2 / "stage-2-streak.json", {"consecutive_eligible_successes": 1, "observations": []})
        for path in self.refresh_receipts.glob("dashboard-refresh-*.json"):
            path.unlink()
        self.snapshot_dates.clear()
        self.compare_dates.clear()
        multiple = self.execute_catchup()
        self.assertEqual([item["source_date"] for item in multiple["committed_dates"]], ["2026-08-27", "2026-08-28"])
        self.assertEqual(self.snapshot_dates, ["2026-08-27", "2026-08-28"])
        self.assertEqual(self.compare_dates, ["2026-08-27", "2026-08-28"])

    def test_gap_stops_and_keeps_later_dates_unapplied(self) -> None:
        self.fail_gate_date = "2026-08-28"
        with self.assertRaisesRegex(DashboardCatchupError, "stopped at 2026-08-28"):
            self.execute_catchup()
        self.assertEqual(json.loads(self.output.read_text())["daily_updated_through"], "2026-08-27")
        self.assertEqual(registry_daily_max_date(self.registry), "2026-08-27")
        self.assertEqual(self.snapshot_dates, ["2026-08-27"])

    def test_invalid_receipt_fails_before_any_mutation(self) -> None:
        before_registry = self.registry.read_bytes()
        before_h5 = self.output.read_bytes()
        self.invalid_gate_date = "2026-08-27"
        with self.assertRaises(DashboardCatchupError) as raised:
            self.execute_catchup()
        self.assertEqual(raised.exception.code, "contiguous_source_gate_failed")
        self.assertEqual(self.registry.read_bytes(), before_registry)
        self.assertEqual(self.output.read_bytes(), before_h5)
        self.assertEqual(self.snapshot_dates, [])

    def test_second_day_failure_preserves_first_complete_checkpoint(self) -> None:
        self.fail_compare_date = "2026-08-28"
        with self.assertRaisesRegex(RuntimeError, "second-day"):
            self.execute_catchup()
        self.assertEqual(json.loads(self.output.read_text())["daily_updated_through"], "2026-08-27")
        self.assertEqual(registry_daily_max_date(self.registry), "2026-08-27")
        self.assertEqual(json.loads((self.stage2 / "stage-2-streak.json").read_text())["consecutive_eligible_successes"], 1)
        self.assertEqual(len(list(self.refresh_receipts.glob("dashboard-refresh-2026-08-27-*.json"))), 1)
        self.assertEqual(len(list(self.refresh_receipts.glob("dashboard-refresh-2026-08-28-*.json"))), 0)

    def test_same_input_rerun_does_not_increment(self) -> None:
        first = self.execute_catchup()
        count = json.loads((self.stage2 / "stage-2-streak.json").read_text())["consecutive_eligible_successes"]
        second = self.execute_catchup()
        self.assertEqual(count, 1)
        self.assertTrue(second["skipped"])
        self.assertEqual(json.loads((self.stage2 / "stage-2-streak.json").read_text())["consecutive_eligible_successes"], 1)
        self.assertEqual(len(first["committed_dates"]), 2)

    def test_stage2_compare_never_mutates_frozen_legacy_streak(self) -> None:
        before = (self.stage2 / "stage-2-streak.json").read_bytes()
        result = self.execute_catchup()
        self.assertEqual([item["legacy_stage2_streak_before"] for item in result["committed_dates"]], [1, 1])
        self.assertEqual([item["legacy_stage2_streak_after"] for item in result["committed_dates"]], [1, 1])
        self.assertEqual((self.stage2 / "stage-2-streak.json").read_bytes(), before)
        self.assertTrue(all(item["observation_id"].startswith(f"daily-refresh:{item['source_date']}:") for item in result["committed_dates"]))
        self.assertTrue(all(item["migration_gate_observation"]["eligible"] is False for item in result["committed_dates"]))

    def test_h5_ordering_privacy_counts_and_canonical_inputs_untouched(self) -> None:
        self.execute_catchup()
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        projects = payload["work_sections"][0]["projects"]
        self.assertEqual([item["title"] for item in projects], ["New", "Old"])
        self.assertEqual(payload["summary"]["case_count"], 2)
        self.assertEqual(payload["summary"]["value_item_count"], 1)
        self.assertEqual(payload["capability_domains"][0]["item_count"], 1)
        encoded = json.dumps(payload, ensure_ascii=False)
        for private in ("private-new", "private-old", "private-event", "/private/source"):
            self.assertNotIn(private, encoded)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.input_files}, self.input_hashes)

    def test_incomplete_transaction_is_recovered_before_dates_are_derived(self) -> None:
        transaction = RefreshTransaction(
            transaction_root=self.transaction,
            registry_path=self.registry,
            output_path=self.output,
            stage2_root=self.stage2,
            refresh_receipt_root=self.refresh_receipts,
        )
        generation = hashlib.sha256(b"two-source:2026-08-27").hexdigest()
        transaction.begin(
            target_date="2026-08-27",
            refresh_receipt_name=f"dashboard-refresh-2026-08-27-{generation[:16]}.json",
        )
        atomic_write_json(self.registry, {"events": [{"date": "2026-08-28", "source_kind": "daily"}]})
        self._write_h5("2026-08-27")
        result = self.execute_catchup()
        self.assertTrue(result["recovered_incomplete_transaction"])
        self.assertEqual(result["daily_updated_through"], "2026-08-28")

    def test_valid_split_registry_ahead_uses_read_only_historical_projection(self) -> None:
        self._write_h5("2026-08-26")
        atomic_write_json(self.registry, {
            "events": [{"event_id": "daily:2026-08-28", "date": "2026-08-28", "source_kind": "daily"}],
        })
        self.enable_split_store()
        before = {
            path: path.read_bytes()
            for path in (
                self.registry,
                self.registry.with_name("project-dashboard-manual-state.json"),
                self.registry.with_name("project-dashboard-replay-state.json"),
            )
        }

        result = self.execute_catchup()

        self.assertEqual([item["source_date"] for item in result["committed_dates"]], ["2026-08-27", "2026-08-28"])
        self.assertEqual(self.projection_flags, [("2026-08-27", True), ("2026-08-28", False)])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(json.loads(self.output.read_text())["daily_updated_through"], "2026-08-28")

    def test_bounded_projection_filters_only_future_replay_and_preserves_manual_authority(self) -> None:
        manual_event = {"event_id": "manual:future", "date": "2026-08-30", "source_kind": "manual", "state": "routed"}
        atomic_write_json(self.registry, {
            "version": 4,
            "cases": [{
                "case_id": "case-1",
                "title": "Manual title",
                "line": "life",
                "status": "on_hold",
                "current_summary": "Manual summary",
                "next_step": "Manual next step",
                "value_items": [
                    {"value_id": "value-29", "date": "2026-08-29", "source_kind": "explicit_daily_candidate", "detail": "keep"},
                    {"value_id": "value-30", "date": "2026-08-30", "source_kind": "explicit_daily_candidate", "detail": "hide"},
                ],
            }],
            "events": [
                {"event_id": "daily:29", "date": "2026-08-29", "source_kind": "daily"},
                {"event_id": "daily:30", "date": "2026-08-30", "source_kind": "daily"},
                manual_event,
            ],
            "value_candidates": [
                {"candidate_id": "candidate-29", "value_id": "candidate-value-29", "date": "2026-08-29", "source_kind": "codex_daily"},
                {"candidate_id": "candidate-30", "value_id": "candidate-value-30", "date": "2026-08-30", "source_kind": "codex_daily"},
            ],
        })
        self.enable_split_store()
        protected = [
            self.registry,
            self.registry.with_name("project-dashboard-manual-state.json"),
            self.registry.with_name("project-dashboard-replay-state.json"),
        ]
        before = {path: path.read_bytes() for path in protected}
        store = CaseRegistry(
            wiki_root=self.wiki,
            registry_path=self.registry,
            reviews_root=self.wiki / "reviews",
            daily_root=self.wiki / "sources/chatgpt",
            codex_daily_root=self.wiki / "sources/codex",
            live_state_path=self.root / "missing-live-state.json",
            memory_root=self.memory,
            source_through_date="2026-08-29",
        )

        projected = store.load()

        self.assertEqual([event["event_id"] for event in projected["events"]], ["daily:29", "manual:future"])
        self.assertEqual([item["candidate_id"] for item in projected["value_candidates"]], ["candidate-29"])
        self.assertEqual([item["value_id"] for item in projected["cases"][0]["value_items"]], ["value-29"])
        self.assertEqual(projected["cases"][0]["status"], "on_hold")
        self.assertEqual(projected["cases"][0]["current_summary"], "Manual summary")
        self.assertEqual({path: path.read_bytes() for path in protected}, before)

    def test_bounded_projection_preserves_absent_value_items_during_live_save(self) -> None:
        atomic_write_json(self.registry, {
            "version": 4,
            "cases": [{
                "case_id": "case-without-values",
                "title": "Manual title",
                "line": "life",
                "status": "in_progress",
            }],
            "events": [],
            "value_candidates": [],
        })
        self.enable_split_store()
        live_state = self.root / "live-state.json"
        atomic_write_json(live_state, {
            "schema_version": 1,
            "tasks": [{
                "session_id": "live-task",
                "status": "completed",
                "cwd": "/unmapped",
                "preview": "Completed task",
                "ended_at": "2026-08-29T07:19:00+08:00",
                "risk": "low",
            }],
        })
        store = CaseRegistry(
            wiki_root=self.wiki,
            registry_path=self.registry,
            reviews_root=self.wiki / "reviews",
            daily_root=self.wiki / "sources/chatgpt",
            codex_daily_root=self.wiki / "sources/codex",
            live_state_path=live_state,
            memory_root=self.memory,
            source_through_date="2026-08-29",
            split_journal_path=self.root / "live-split-transaction/journal.json",
            split_lock_path=self.root / "live-split.lock",
        )

        store.snapshot()

        persisted = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertNotIn("value_items", persisted["cases"][0])
        self.assertEqual(
            len([event for event in persisted["events"] if event.get("source_kind") == "codex_live"]),
            1,
        )
        self.assertFalse((self.root / "live-split-transaction/journal.json").exists())

    def test_invalid_unreceipted_ahead_date_fails_before_any_mutation(self) -> None:
        atomic_write_json(self.registry, {
            "events": [{"event_id": "daily:2026-08-28", "date": "2026-08-28", "source_kind": "daily"}],
        })
        self.enable_split_store()
        self.invalid_gate_date = "2026-08-28"
        protected = [
            self.registry,
            self.registry.with_name("project-dashboard-manual-state.json"),
            self.registry.with_name("project-dashboard-replay-state.json"),
            self.output,
        ]
        before = {path: path.read_bytes() for path in protected}

        with self.assertRaises(DashboardCatchupError) as raised:
            self.execute_catchup()

        self.assertEqual(raised.exception.code, "registry_ahead_source_gate_failed")
        self.assertEqual(raised.exception.target_date, "2026-08-28")
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertEqual(self.snapshot_dates, [])

    def test_multiple_day_registry_ahead_requires_complete_interval_and_catches_up_contiguously(self) -> None:
        self.now = datetime(2026, 8, 31, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        atomic_write_json(self.registry, {
            "events": [{"event_id": "daily:2026-08-30", "date": "2026-08-30", "source_kind": "daily"}],
        })
        self.enable_split_store()

        result = self.execute_catchup()

        self.assertEqual(
            [item["source_date"] for item in result["committed_dates"]],
            ["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30"],
        )
        self.assertEqual(
            self.projection_flags,
            [("2026-08-27", True), ("2026-08-28", True), ("2026-08-29", True), ("2026-08-30", False)],
        )

    def test_projected_checkpoint_failure_rolls_back_h5_and_preserves_split_generation(self) -> None:
        atomic_write_json(self.registry, {
            "events": [{"event_id": "daily:2026-08-28", "date": "2026-08-28", "source_kind": "daily"}],
        })
        self.enable_split_store()
        self.fail_compare_date = "2026-08-27"
        protected = [
            self.registry,
            self.registry.with_name("project-dashboard-manual-state.json"),
            self.registry.with_name("project-dashboard-replay-state.json"),
            self.output,
        ]
        before = {path: path.read_bytes() for path in protected}

        with self.assertRaisesRegex(RuntimeError, "second-day"):
            self.execute_catchup()

        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertFalse((self.transaction / "journal.json").exists())

    def test_split_compatibility_drift_and_manual_metadata_corruption_fail_closed(self) -> None:
        atomic_write_json(self.registry, {
            "events": [{"event_id": "daily:2026-08-28", "date": "2026-08-28", "source_kind": "daily"}],
        })
        self.enable_split_store()
        clean = {
            path: path.read_bytes()
            for path in (
                self.registry,
                self.registry.with_name("project-dashboard-manual-state.json"),
                self.registry.with_name("project-dashboard-replay-state.json"),
            )
        }
        drifted = json.loads(self.registry.read_text())
        drifted["events"].append({"event_id": "drift", "date": "2026-08-27", "source_kind": "daily"})
        atomic_write_json(self.registry, drifted)
        with self.assertRaises(DashboardCatchupError) as drift:
            self.execute_catchup()
        self.assertEqual(drift.exception.code, "canonical_registry_invalid")

        for path, payload in clean.items():
            path.write_bytes(payload)
        manual_path = self.registry.with_name("project-dashboard-manual-state.json")
        manual = json.loads(manual_path.read_text())
        manual["classification"] = "derived_view"
        atomic_write_json(manual_path, manual)
        with self.assertRaises(DashboardCatchupError) as corruption:
            self.execute_catchup()
        self.assertEqual(corruption.exception.code, "canonical_registry_invalid")


if __name__ == "__main__":
    unittest.main()
