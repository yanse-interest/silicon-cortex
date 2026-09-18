from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workbench  # noqa: E402
from workbench import Store, WorkbenchError, atomic_write, new_map, parse_map, render_map, sha256, validate_map  # noqa: E402


WORKBENCH = Path(__file__).resolve().parents[1] / "workbench.py"


class M3StoreAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "private"
        self.map_path = self.root / "PROJECT_MAP.md"
        value = new_map("m3-project", "M3 虚构项目", "隔离故障验收", "active-outcome", "活动成果")
        atomic_write(self.map_path, render_map("保留手写正文。\n", value))
        self.store = Store(self.state)
        self.store.register("m3-project", self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, goal: str = "更新后的目标") -> dict:
        read = self.store.read("m3-project")
        return {
            "doc_type": "mutation_request", "schema_version": "1.0.0",
            "project_id": "m3-project", "expected_revision": read["project_map"]["revision"],
            "expected_sha256": read["sha256"], "change_reason": "M3 isolated fault probe",
            "set_project": {"goal": goal},
        }

    def test_two_process_writers_allow_exactly_one_old_version(self) -> None:
        request_path = self.base / "request.json"
        request_path.write_text(json.dumps(self.request(), ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, str(WORKBENCH), "--data-dir", str(self.state), "update", "--input", str(request_path)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
        self.assertEqual([0, 2], sorted(result[2] for result in results))
        loser = next(result for result in results if result[2] == 2)
        self.assertEqual("revision_conflict", json.loads(loser[1])["code"])
        reread = Store(self.state).read("m3-project")
        self.assertEqual(2, reread["project_map"]["revision"])
        self.assertEqual("更新后的目标", reread["project_map"]["goal"])

    def _crash_update(self, body: str) -> int:
        request_path = self.base / "crash-request.json"
        request_path.write_text(json.dumps(self.request(), ensure_ascii=False), encoding="utf-8")
        script = textwrap.dedent(body)
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.state), str(request_path)],
            cwd=WORKBENCH.parent, check=False, capture_output=True, text=True, timeout=10,
        )
        return result.returncode

    def test_crash_before_map_replace_keeps_original_and_valid_backup(self) -> None:
        before = self.map_path.read_bytes()
        code = self._crash_update("""
            import json, os, sys, workbench
            from pathlib import Path
            original = os.replace
            calls = 0
            def crash_on_map(source, target):
                global calls
                calls += 1
                if calls == 2: os._exit(92)
                return original(source, target)
            workbench.os.replace = crash_on_map
            workbench.Store(Path(sys.argv[1])).update(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        """)
        self.assertEqual(92, code)
        self.assertEqual(before, self.map_path.read_bytes())
        backup = self.state / "backups" / "m3-project.md"
        self.assertEqual(before, backup.read_bytes())
        parse_map(backup.read_bytes())

    @unittest.skipIf(os.name == "nt", "Windows does not support directory fsync")
    def test_crash_after_map_replace_never_leaves_partial_map(self) -> None:
        code = self._crash_update("""
            import json, os, stat, sys, workbench
            from pathlib import Path
            original = os.fsync
            directory_syncs = 0
            def crash_after_second_replace(fd):
                global directory_syncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 2: os._exit(93)
                return original(fd)
            workbench.os.fsync = crash_after_second_replace
            workbench.Store(Path(sys.argv[1])).update(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        """)
        self.assertEqual(93, code)
        restored = parse_map(self.map_path.read_bytes())
        self.assertEqual(2, restored["revision"])
        self.assertEqual("更新后的目标", restored["goal"])
        parse_map((self.state / "backups" / "m3-project.md").read_bytes())

    def test_permission_and_replace_failures_preserve_original(self) -> None:
        before = self.map_path.read_bytes()
        with patch("workbench.tempfile.mkstemp", side_effect=PermissionError("read only")):
            with self.assertRaises(WorkbenchError) as caught:
                self.store.update(self.request())
        self.assertEqual("write_failed", caught.exception.code)
        self.assertEqual(before, self.map_path.read_bytes())

        target = self.base / "atomic.txt"
        target.write_bytes(b"old")
        with patch("workbench.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(WorkbenchError): atomic_write(target, b"new")
        self.assertEqual(b"old", target.read_bytes())
        self.assertEqual([], list(self.base.glob(".atomic.txt.*")))

    def test_corrupt_duplicate_unknown_and_oversize_maps_fail_closed(self) -> None:
        raw = self.map_path.read_bytes()
        variants = [
            raw[: len(raw) // 2],
            raw.replace(b'"revision": 1', b'"revision":'),
            raw.replace(b'"schema_version": "1.0.0"', b'"schema_version": "9.0.0"'),
            raw.replace(b'"project_id": "m3-project",', b'"project_id": "m3-project",\n  "project_id": "duplicate",'),
            raw + (b"x" * (1024 * 1024)),
        ]
        codes = []
        for candidate in variants:
            with self.assertRaises(WorkbenchError) as caught: parse_map(candidate)
            codes.append(caught.exception.code)
        self.assertEqual(["invalid_map", "invalid_map", "unsupported_version", "invalid_map", "invalid_map"], codes)

    def test_symlink_root_map_escape_and_relative_escape_are_rejected(self) -> None:
        symlink_root = self.base / "root-link"
        try:
            symlink_root.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")
        with self.assertRaises(WorkbenchError) as caught:
            Store(self.base / "state-link").register("link-project", symlink_root)
        self.assertEqual("path_outside_project", caught.exception.code)

        outside = self.base / "outside"
        outside.mkdir()
        escaped = new_map("escape-project", "逃逸", "不得读取", "escape-outcome", "逃逸成果")
        atomic_write(outside / "PROJECT_MAP.md", render_map("", escaped))
        inside = self.base / "inside"
        inside.mkdir()
        (inside / "PROJECT_MAP.md").symlink_to(outside / "PROJECT_MAP.md")
        with self.assertRaises(WorkbenchError) as caught:
            Store(self.base / "state-escape").register("escape-project", inside)
        self.assertEqual("path_outside_project", caught.exception.code)
        with self.assertRaises(WorkbenchError): self.store.register("other-project", self.root, "../PROJECT_MAP.md")

    def test_backup_recovery_requires_cas_and_preserves_rejected_bytes(self) -> None:
        original = self.map_path.read_bytes()
        self.store.update(self.request())
        broken = self.map_path.read_bytes()[:100]
        self.map_path.write_bytes(broken)
        with self.assertRaises(WorkbenchError) as caught:
            self.store.restore_backup("m3-project", "0" * 64)
        self.assertEqual("revision_conflict", caught.exception.code)
        result = self.store.restore_backup("m3-project", sha256(broken))
        self.assertEqual(original, self.map_path.read_bytes())
        self.assertEqual(broken, (self.state / "backups" / result["rejected_copy"]).read_bytes())
        self.assertEqual(1, Store(self.state).read("m3-project")["project_map"]["revision"])

    def test_completion_relationship_and_lifecycle_gates(self) -> None:
        value = copy.deepcopy(self.store.read("m3-project")["project_map"])
        stamp = value["updated_at"]
        evidence = {"id": "verified-file", "kind": "file", "locator": "evidence.txt", "summary": "已核验",
                    "verified_at": stamp, "verified_by": "test_runner"}
        done = {"id": "done-target", "title": "不能越过依赖", "status": "done", "depends_on": ["dependency"],
                "acceptance": [{"id": "verified", "text": "必须核验", "result": "passed", "evidence_ids": ["verified-file"]}],
                "evidence": [evidence], "checkpoint": {"saved_at": stamp, "last_result": "已核验", "remaining": [],
                                                        "next_action": None, "next_action_basis": "none"}}
        dependency = {"id": "dependency", "title": "未完成依赖", "status": "in_progress",
                      "acceptance": [{"id": "dep-check", "text": "依赖检查", "result": "unverified"}],
                      "checkpoint": {"saved_at": stamp, "last_result": "仍在进行", "remaining": ["完成依赖"],
                                     "next_action": "继续依赖", "next_action_basis": "existing_scope"}}
        value["outcomes"].extend([dependency, done])
        with self.assertRaisesRegex(WorkbenchError, "unfinished prerequisite"): validate_map(value)

        parent = copy.deepcopy(done); parent.update({"id": "parent", "depends_on": []})
        child = copy.deepcopy(dependency); child.update({"id": "child", "parent_id": "parent"})
        value["outcomes"] = [parent, child]; value["focus_outcome_id"] = None
        with self.assertRaisesRegex(WorkbenchError, "unfinished prerequisite"): validate_map(value)

        paused = copy.deepcopy(dependency); paused["status"] = "paused"
        value["outcomes"] = [paused]
        with self.assertRaisesRegex(WorkbenchError, "pause_reason"): validate_map(value)

    def test_pending_review_cancelled_and_done_transitions_obey_gates(self) -> None:
        outcome = copy.deepcopy(self.store.read("m3-project", "active-outcome")["outcome"])
        outcome["status"] = "pending_review"
        self.store.update({**self.request(), "set_project": {"focus_outcome_id": "active-outcome"}, "upsert_outcomes": [outcome]})
        pending = copy.deepcopy(self.store.read("m3-project", "active-outcome")["outcome"])
        pending["status"] = "cancelled"; pending["cancel_reason"] = "用户明确停止此范围"
        self.store.update({**self.request(), "set_project": {"focus_outcome_id": None}, "upsert_outcomes": [pending]})
        self.assertEqual("cancelled", self.store.read("m3-project", "active-outcome")["outcome"]["status"])
        illegal = copy.deepcopy(self.store.read("m3-project", "active-outcome")["outcome"])
        illegal["status"] = "done"
        with self.assertRaises(WorkbenchError) as caught:
            self.store.update({**self.request(), "set_project": {"focus_outcome_id": None}, "upsert_outcomes": [illegal]})
        self.assertEqual("invalid_transition", caught.exception.code)

    def test_export_copy_and_restart_retain_stable_fields(self) -> None:
        first = self.store.read("m3-project", "active-outcome")
        export_root = self.base / "exported"
        export_root.mkdir()
        atomic_write(export_root / "PROJECT_MAP.md", self.map_path.read_bytes())
        imported = Store(self.base / "import-state")
        imported.register("m3-project", export_root)
        second = imported.read("m3-project", "active-outcome")
        self.assertEqual(first["project_map"], second["project_map"])
        self.assertEqual(first["outcome"], second["outcome"])
        self.assertEqual(first["sha256"], second["sha256"])

    def test_timezone_and_url_schemes_fail_closed(self) -> None:
        value = copy.deepcopy(self.store.read("m3-project")["project_map"])
        value["updated_at"] = "2026-09-17T12:00:00"
        with self.assertRaises(WorkbenchError): validate_map(value)
        value = copy.deepcopy(self.store.read("m3-project")["project_map"])
        outcome = value["outcomes"][0]
        outcome["evidence"] = [{"id": "unsafe-url", "kind": "url", "locator": "file:///tmp/secret", "summary": "不安全",
                                "verified_at": value["updated_at"], "verified_by": "test_runner"}]
        with self.assertRaisesRegex(WorkbenchError, "http or https"): validate_map(value)
        value = copy.deepcopy(self.store.read("m3-project")["project_map"])
        outcome = value["outcomes"][0]
        outcome["deadline"] = {"date": "2026-09-30T12:00:00", "timezone": "Asia/Shanghai", "basis": "user_confirmed"}
        with self.assertRaisesRegex(WorkbenchError, "deadline.date"): validate_map(value)
        outcome.pop("deadline"); outcome["checkpoint"]["recheck_paths"] = None
        with self.assertRaisesRegex(WorkbenchError, "recheck_paths"): validate_map(value)

    def test_checkpoint_accepts_absolute_windows_and_posix_roots(self) -> None:
        value = copy.deepcopy(self.store.read("m3-project")["project_map"])
        checkpoint = value["outcomes"][0]["checkpoint"]
        for root in (r"C:\Users\friend\code\project", "/home/friend/code/project"):
            checkpoint["worktree_root"] = root
            validate_map(value)
        checkpoint["worktree_root"] = r"code\project"
        with self.assertRaisesRegex(WorkbenchError, "worktree_root must be absolute"):
            validate_map(value)


if __name__ == "__main__":
    unittest.main()
