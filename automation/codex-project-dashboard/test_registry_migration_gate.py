from __future__ import annotations

import hashlib
import inspect
import json
import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import registry_migration_gate as gate_module

from registry_migration_gate import (
    ENFORCEMENT_VERSION,
    build_gate_summary,
    capture_scheduled_runtime_provenance,
    install_enforcement_marker,
    plist_semantic_sha256,
    publish_gate_summary,
    record_committed_scheduled_refresh,
    refresh_commit_chain,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RegistryMigrationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name)
        self.gate_root = self.root / "gate"
        self.legacy = self.root / "stage-2-streak.json"
        write_json(self.legacy, {
            "baseline_policy_version": "stage2-dual-read-post-capability-fix-v1",
            "consecutive_eligible_successes": 3,
            "required_successes": 7,
            "observations": [{}, {}, {}],
        })
        install_enforcement_marker(
            gate_root=self.gate_root,
            installed_at="2026-08-29T11:30:00+08:00",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_historical_receipts(self, count: int = 7) -> None:
        for offset in range(count):
            day = 22 + offset
            source_date = f"2026-08-{day:02d}"
            write_json(
                self.gate_root / "historical-replays" / f"registry-historical-replay-{source_date}-test.json",
                {
                    "artifact_type": "project_dashboard_registry_historical_replay_receipt",
                    "status": "passed",
                    "source_date": source_date,
                    "date_contract_classification": (
                        "legacy_pre_gate_date_with_valid_completed_receipts" if day == 22 else "receipt_required_contract"
                    ),
                    "source_generation_sha256": hashlib.sha256(source_date.encode()).hexdigest(),
                    "checks": {"all_required_checks_passed": True},
                },
            )

    def make_refresh(self, *, source_date: str = "2026-08-29") -> tuple[Path, Path, Path]:
        registry = self.root / "registry.json"
        h5 = self.root / "h5.json"
        registry.write_text("registry-current\n", encoding="utf-8")
        h5.write_text("h5-current\n", encoding="utf-8")
        inputs: dict[str, dict] = {}
        for family in ("chatgpt", "codex"):
            source = self.root / f"{family}-source.md"
            raw = self.root / f"{family}-raw.md"
            receipt = self.root / f"{family}-receipt.json"
            source.write_text(f"{family} source\n", encoding="utf-8")
            raw.write_text(f"{family} raw\n", encoding="utf-8")
            receipt.write_text(f"{family} receipt\n", encoding="utf-8")
            inputs[family] = {
                "source_path": source.as_posix(),
                "source_sha256": sha(source),
                "raw_path": raw.as_posix(),
                "raw_sha256": sha(raw),
                "receipt_path": receipt.as_posix(),
                "receipt_sha256": sha(receipt),
                "candidate_count": 0,
            }
        generation = hashlib.sha256(
            json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        observation_id = f"daily-refresh:{source_date}:{generation[:16]}"
        stage2 = self.root / "stage2.json"
        write_json(stage2, {
            "result": "passed",
            "observation_id": observation_id,
            "source_registry": {"sha256": sha(registry)},
        })
        refresh = {
            "schema_version": 2,
            "kind": "dashboard_contiguous_refresh_receipt",
            "status": "completed",
            "source_date": source_date,
            "source_generation_sha256": generation,
            "observation_id": observation_id,
            "inputs": inputs,
            "registry_before_sha256": "before-registry",
            "registry_after_sha256": sha(registry),
            "h5_before_sha256": "before-h5",
            "h5_after_sha256": sha(h5),
            "stage2_receipt": stage2.as_posix(),
            "stage2_receipt_sha256": sha(stage2),
            "production_source": "canonical_compatibility_registry",
            "provenance_enforcement_version": ENFORCEMENT_VERSION,
            "dual_write_enabled": False,
            "cutover_enabled": False,
            "committed_at": "2026-08-30T07:25:00+08:00",
        }
        refresh["commit_chain_sha256"] = refresh_commit_chain(refresh)
        refresh_path = self.root / "dashboard-refresh.json"
        write_json(refresh_path, refresh)
        return refresh_path, registry, h5

    @staticmethod
    def eligible_runtime(target_date: str = "2026-08-29") -> dict:
        return {
            "schema_version": 1,
            "enforcement_version": ENFORCEMENT_VERSION,
            "process_started_at": "2026-08-30T07:20:00+08:00",
            "observed_at": "2026-08-30T08:05:01+08:00",
            "target_date": target_date,
            "eligible_runtime": True,
            "failure_reasons": [],
            "checks": {"all_internal_runtime_checks": True},
        }

    def test_seven_replays_are_insufficient_without_one_live_refresh(self) -> None:
        self.write_historical_receipts()
        summary = publish_gate_summary(gate_root=self.gate_root, legacy_streak_path=self.legacy)
        self.assertEqual(summary["historical_replay_gate"]["status"], "passed")
        self.assertEqual(summary["post_enforcement_live_refresh_gate"]["status"], "incomplete")
        self.assertFalse(summary["ready_for_stage3_approval_request"])
        self.assertIn("one provenance-eligible scheduler-originated", summary["remaining_event"])
        self.assertEqual(summary["legacy_stage2_streak"]["consecutive_eligible_successes"], 3)
        self.assertFalse(summary["stage3_authorized"])

    def test_direct_or_synthetic_runtime_cannot_record_live_success(self) -> None:
        self.write_historical_receipts()
        refresh, registry, h5 = self.make_refresh()
        result = record_committed_scheduled_refresh(
            refresh_receipt_path=refresh,
            registry_path=registry,
            h5_snapshot_path=h5,
            gate_root=self.gate_root,
            legacy_streak_path=self.legacy,
        )
        self.assertFalse(result["recorded"])
        self.assertIn("runtime_schedule_provenance", result["failure_reasons"])
        self.assertEqual(list((self.gate_root / "live-refreshes").glob("*.json")), [])
        self.assertFalse(build_gate_summary(gate_root=self.gate_root, legacy_streak_path=self.legacy)["ready_for_stage3_approval_request"])

    def test_verified_post_enforcement_0720_commit_satisfies_live_requirement_only_once(self) -> None:
        self.write_historical_receipts()
        refresh, registry, h5 = self.make_refresh()
        with patch("registry_migration_gate.capture_scheduled_runtime_provenance", return_value=self.eligible_runtime()):
            first = record_committed_scheduled_refresh(
                refresh_receipt_path=refresh,
                registry_path=registry,
                h5_snapshot_path=h5,
                gate_root=self.gate_root,
                legacy_streak_path=self.legacy,
            )
            second = record_committed_scheduled_refresh(
                refresh_receipt_path=refresh,
                registry_path=registry,
                h5_snapshot_path=h5,
                gate_root=self.gate_root,
                legacy_streak_path=self.legacy,
            )
        self.assertTrue(first["recorded"])
        self.assertTrue(second["recorded"])
        self.assertEqual(first["receipt_path"], second["receipt_path"])
        self.assertEqual(len(list((self.gate_root / "live-refreshes").glob("*.json"))), 1)
        summary = second["gate"]
        self.assertTrue(summary["ready_for_stage3_approval_request"])
        self.assertEqual(summary["post_enforcement_live_refresh_gate"]["observed"], 1)
        self.assertFalse(summary["stage3_authorized"])
        self.assertFalse(summary["dual_write_enabled"])
        self.assertFalse(summary["cutover_enabled"])

    def test_cutover_refresh_binds_canonical_split_generation_and_keeps_deferred_state(self) -> None:
        self.write_historical_receipts()
        refresh, registry, h5 = self.make_refresh()
        generation_id = "registry-gen-test"
        manual = registry.with_name("project-dashboard-manual-state.json")
        replay = registry.with_name("project-dashboard-replay-state.json")
        write_json(manual, {"generation_id": generation_id, "classification": "canonical_state"})
        write_json(replay, {"generation_id": generation_id, "classification": "derived_view"})
        payload = json.loads(refresh.read_text(encoding="utf-8"))
        payload.update({
            "production_source": "canonical_manual_split_store",
            "manual_state_sha256": sha(manual),
            "replay_state_sha256": sha(replay),
            "split_generation_id": generation_id,
            "dual_write_enabled": True,
            "cutover_enabled": True,
        })
        payload["commit_chain_sha256"] = refresh_commit_chain(payload)
        write_json(refresh, payload)
        write_json(self.gate_root / "migration-stage-state.json", {
            "artifact_type": "project_dashboard_registry_migration_stage_state",
            "status": "passed",
            "scheduled_runtime_validation_deferred": True,
            "stage3_authorized": True,
            "dual_write_enabled": True,
            "cutover_enabled": True,
        })
        with patch("registry_migration_gate.capture_scheduled_runtime_provenance", return_value=self.eligible_runtime()):
            result = record_committed_scheduled_refresh(
                refresh_receipt_path=refresh,
                registry_path=registry,
                h5_snapshot_path=h5,
                gate_root=self.gate_root,
                legacy_streak_path=self.legacy,
            )
        self.assertTrue(result["recorded"])
        self.assertTrue(result["gate"]["cutover_enabled"])
        self.assertTrue(result["gate"]["scheduled_runtime_validation_deferred"])
        live = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertTrue(live["checks"]["migration_mode_consistent"])

    def test_bound_source_tamper_and_commit_chain_tamper_fail_closed(self) -> None:
        refresh, registry, h5 = self.make_refresh()
        payload = json.loads(refresh.read_text(encoding="utf-8"))
        Path(payload["inputs"]["chatgpt"]["source_path"]).write_text("tampered\n", encoding="utf-8")
        with patch("registry_migration_gate.capture_scheduled_runtime_provenance", return_value=self.eligible_runtime()):
            tampered_source = record_committed_scheduled_refresh(
                refresh_receipt_path=refresh,
                registry_path=registry,
                h5_snapshot_path=h5,
                gate_root=self.gate_root,
                legacy_streak_path=self.legacy,
            )
        self.assertIn("immutable_raw_source_receipt_bindings", tampered_source["failure_reasons"])

        refresh, registry, h5 = self.make_refresh()
        payload = json.loads(refresh.read_text(encoding="utf-8"))
        payload["commit_chain_sha256"] = "0" * 64
        write_json(refresh, payload)
        with patch("registry_migration_gate.capture_scheduled_runtime_provenance", return_value=self.eligible_runtime()):
            tampered_chain = record_committed_scheduled_refresh(
                refresh_receipt_path=refresh,
                registry_path=registry,
                h5_snapshot_path=h5,
                gate_root=self.gate_root,
                legacy_streak_path=self.legacy,
            )
        self.assertIn("commit_chain", tampered_chain["failure_reasons"])
        self.assertEqual(list((self.gate_root / "live-refreshes").glob("*.json")), [])

    def test_runtime_provenance_is_derived_and_direct_test_process_is_ineligible(self) -> None:
        runtime = capture_scheduled_runtime_provenance(target_date="2026-08-28")
        self.assertFalse(runtime["eligible_runtime"])
        self.assertTrue(runtime["failure_reasons"])
        self.assertNotIn("eligible_real_refresh", runtime)

    def launchd_contract(self) -> tuple[Path, Path, Path]:
        runner = self.root / "dashboard_h5_refresh.py"
        runner.write_text("# test runner\n", encoding="utf-8")
        project_plist = self.root / "project.plist"
        installed_plist = self.root / "installed.plist"
        payload = {
            "Label": gate_module.LAUNCHD_LABEL,
            "ProgramArguments": ["/usr/bin/python3", runner.as_posix()],
            "StartCalendarInterval": {"Hour": 7, "Minute": 20},
        }
        project_plist.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML))
        installed_plist.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_BINARY))
        return runner, project_plist, installed_plist

    def capture_in_launchd_contract(
        self,
        *,
        target_date: str,
        process_started_at: datetime | None,
        observed_at: datetime,
    ) -> dict:
        runner, project_plist, installed_plist = self.launchd_contract()
        with (
            patch.object(gate_module, "PROJECT_PLIST", project_plist),
            patch.object(gate_module, "INSTALLED_PLIST", installed_plist),
            patch.object(gate_module, "EXPECTED_RUNNER", runner),
            patch.dict(gate_module.os.environ, {"XPC_SERVICE_NAME": gate_module.LAUNCHD_LABEL}),
            patch.object(gate_module.os, "getppid", return_value=1),
            patch.object(gate_module.sys, "argv", [runner.as_posix()]),
            patch.object(gate_module, "_current_process_started_at", return_value=process_started_at),
            patch.object(gate_module, "_observation_time", return_value=observed_at),
        ):
            return capture_scheduled_runtime_provenance(target_date=target_date)

    def test_process_start_window_survives_commit_after_0739(self) -> None:
        started = datetime(2026, 8, 31, 7, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
        committed = datetime(2026, 8, 31, 8, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        runtime = self.capture_in_launchd_contract(
            target_date="2026-08-30",
            process_started_at=started,
            observed_at=committed,
        )
        self.assertTrue(runtime["eligible_runtime"])
        self.assertEqual(runtime["process_started_at"], started.isoformat())
        self.assertEqual(runtime["observed_at"], committed.isoformat())
        self.assertEqual(runtime["expected_source_date"], "2026-08-30")
        self.assertTrue(runtime["checks"]["scheduled_window_0720"])

    def test_process_start_outside_window_fails_even_when_commit_is_inside(self) -> None:
        started = datetime(2026, 8, 31, 7, 19, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
        committed = datetime(2026, 8, 31, 7, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        runtime = self.capture_in_launchd_contract(
            target_date="2026-08-30",
            process_started_at=started,
            observed_at=committed,
        )
        self.assertFalse(runtime["eligible_runtime"])
        self.assertEqual(runtime["failure_reasons"], ["scheduled_window_0720"])

    def test_process_start_lookup_failure_fails_closed(self) -> None:
        committed = datetime(2026, 8, 31, 7, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        runtime = self.capture_in_launchd_contract(
            target_date="2026-08-30",
            process_started_at=None,
            observed_at=committed,
        )
        self.assertFalse(runtime["eligible_runtime"])
        self.assertEqual(runtime["process_started_at"], "")
        self.assertEqual(runtime["expected_source_date"], "")
        self.assertEqual(
            runtime["failure_reasons"],
            ["authoritative_process_start_time", "previous_calendar_date", "scheduled_window_0720"],
        )

    def test_exact_date_bound_scheduler_one_shot_is_eligible_without_weakening_regular_contract(self) -> None:
        runner, project_plist, installed_plist = self.launchd_contract()
        scheduled = datetime(2026, 9, 2, 18, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
        one_shot_plist = self.root / "one-shot.plist"
        one_shot_plist.write_bytes(plistlib.dumps({
            "Label": gate_module.ONE_SHOT_LAUNCHD_LABEL,
            "ProgramArguments": ["/usr/bin/python3", runner.as_posix()],
            "StartCalendarInterval": {
                "Year": scheduled.year,
                "Month": scheduled.month,
                "Day": scheduled.day,
                "Hour": scheduled.hour,
                "Minute": scheduled.minute,
            },
        }, fmt=plistlib.FMT_XML))
        authorization_path = self.root / "authorization.json"
        write_json(authorization_path, {
            "schema_version": 1,
            "artifact_type": "dashboard_refresh_scheduler_one_shot_authorization",
            "status": "authorized",
            "run_once": True,
            "launchd_label": gate_module.ONE_SHOT_LAUNCHD_LABEL,
            "target_date": "2026-08-15",
            "scheduled_at": scheduled.isoformat(),
            "expires_at": "2026-09-02T18:55:00+08:00",
            "installed_plist_sha256": sha(one_shot_plist),
        })
        with (
            patch.object(gate_module, "PROJECT_PLIST", project_plist),
            patch.object(gate_module, "INSTALLED_PLIST", installed_plist),
            patch.object(gate_module, "INSTALLED_ONE_SHOT_PLIST", one_shot_plist),
            patch.object(gate_module, "DEFAULT_ONE_SHOT_AUTHORIZATION", authorization_path),
            patch.object(gate_module, "EXPECTED_RUNNER", runner),
            patch.dict(gate_module.os.environ, {"XPC_SERVICE_NAME": gate_module.ONE_SHOT_LAUNCHD_LABEL}),
            patch.object(gate_module.os, "getppid", return_value=1),
            patch.object(gate_module.sys, "argv", [runner.as_posix()]),
            patch.object(gate_module, "_current_process_started_at", return_value=scheduled),
            patch.object(gate_module, "_observation_time", return_value=scheduled),
        ):
            runtime = capture_scheduled_runtime_provenance(target_date="2026-08-15")
        self.assertTrue(runtime["eligible_runtime"])
        self.assertEqual(runtime["schedule_kind"], "authorized_one_shot")
        self.assertTrue(runtime["checks"]["authorized_one_shot"])
        self.assertTrue(runtime["checks"]["installed_plist_matches_project"])

        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["installed_plist_sha256"] = "0" * 64
        write_json(authorization_path, authorization)
        with (
            patch.object(gate_module, "PROJECT_PLIST", project_plist),
            patch.object(gate_module, "INSTALLED_PLIST", installed_plist),
            patch.object(gate_module, "INSTALLED_ONE_SHOT_PLIST", one_shot_plist),
            patch.object(gate_module, "DEFAULT_ONE_SHOT_AUTHORIZATION", authorization_path),
            patch.object(gate_module, "EXPECTED_RUNNER", runner),
            patch.dict(gate_module.os.environ, {"XPC_SERVICE_NAME": gate_module.ONE_SHOT_LAUNCHD_LABEL}),
            patch.object(gate_module.os, "getppid", return_value=1),
            patch.object(gate_module.sys, "argv", [runner.as_posix()]),
            patch.object(gate_module, "_current_process_started_at", return_value=scheduled),
            patch.object(gate_module, "_observation_time", return_value=scheduled),
        ):
            rejected = capture_scheduled_runtime_provenance(target_date="2026-08-15")
        self.assertFalse(rejected["eligible_runtime"])
        self.assertIn("authorized_one_shot_plist_hash", rejected["failure_reasons"])

    def test_process_start_reader_uses_exact_ps_and_current_os_pid(self) -> None:
        completed = gate_module.subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Sun Aug 30 07:20:05 2026\n", stderr=""
        )
        with (
            patch.object(gate_module.os, "getpid", return_value=43210),
            patch.object(gate_module.subprocess, "run", return_value=completed) as run,
        ):
            started = gate_module._current_process_started_at()
        self.assertEqual(
            started,
            datetime(2026, 8, 30, 7, 20, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        run.assert_called_once_with(
            ["/bin/ps", "-o", "lstart=", "-p", "43210"],
            check=True,
            capture_output=True,
            text=True,
            env={"LC_ALL": "C"},
            timeout=5,
        )

    def test_runtime_api_has_no_caller_eligibility_or_time_escape_hatch(self) -> None:
        capture_parameters = inspect.signature(capture_scheduled_runtime_provenance).parameters
        record_parameters = inspect.signature(record_committed_scheduled_refresh).parameters
        self.assertEqual(set(capture_parameters), {"target_date"})
        self.assertNotIn("eligible_real_refresh", record_parameters)
        self.assertNotIn("observation_id", record_parameters)
        self.assertNotIn("_now", capture_parameters)

    def test_plist_semantic_hash_ignores_wire_format_and_xml_whitespace(self) -> None:
        payload = {
            "Label": "example.agent",
            "ProgramArguments": ["/usr/bin/python3", "/tmp/runner.py"],
            "StartCalendarInterval": {"Hour": 7, "Minute": 20},
            "RunAtLoad": False,
        }
        xml = self.root / "agent.xml.plist"
        binary = self.root / "agent.binary.plist"
        xml.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))
        xml.write_text(xml.read_text(encoding="utf-8").replace("\t", "    "), encoding="utf-8")
        binary.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_BINARY, sort_keys=True))
        self.assertEqual(plist_semantic_sha256(xml), plist_semantic_sha256(binary))

    def test_plist_semantic_hash_rejects_value_extra_key_and_type_changes(self) -> None:
        baseline = {
            "Label": "example.agent",
            "StartCalendarInterval": {"Hour": 7, "Minute": 20},
        }
        variants = [
            {**baseline, "StartCalendarInterval": {"Hour": 7, "Minute": 21}},
            {**baseline, "KeepAlive": False},
            {**baseline, "StartCalendarInterval": {"Hour": 7, "Minute": "20"}},
        ]
        source = self.root / "source.plist"
        source.write_bytes(plistlib.dumps(baseline, fmt=plistlib.FMT_XML))
        baseline_hash = plist_semantic_sha256(source)
        for index, payload in enumerate(variants):
            candidate = self.root / f"candidate-{index}.plist"
            candidate.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_BINARY))
            self.assertNotEqual(baseline_hash, plist_semantic_sha256(candidate))

    def test_multiday_catchup_only_previous_calendar_date_is_live_eligible(self) -> None:
        started = datetime(2026, 8, 31, 7, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        committed = datetime(2026, 8, 31, 8, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        older_checkpoint = self.capture_in_launchd_contract(
            target_date="2026-08-29", process_started_at=started, observed_at=committed
        )
        previous_day = self.capture_in_launchd_contract(
            target_date="2026-08-30", process_started_at=started, observed_at=committed
        )
        self.assertFalse(older_checkpoint["eligible_runtime"])
        self.assertEqual(older_checkpoint["failure_reasons"], ["previous_calendar_date"])
        self.assertTrue(previous_day["eligible_runtime"])
        self.assertEqual(previous_day["failure_reasons"], [])


if __name__ == "__main__":
    unittest.main()
