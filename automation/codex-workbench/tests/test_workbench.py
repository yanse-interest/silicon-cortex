from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench import (  # noqa: E402
    STATE_BEGIN,
    Store,
    WorkbenchError,
    atomic_write,
    new_map,
    parse_map,
    render_map,
)


class WorkbenchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "fictional-project"
        self.root.mkdir()
        self.state = base / "private-state"
        self.map_path = self.root / "PROJECT_MAP.md"
        original = new_map("fictional-project", "虚构项目", "验证可靠保存", "first-outcome", "第一个成果")
        atomic_write(self.map_path, render_map("手写引言。\n", original))
        self.store = Store(self.state)
        self.store.register("fictional-project", self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request_for(self, value: dict, **change: object) -> dict:
        read = self.store.read("fictional-project")
        request = {
            "doc_type": "mutation_request", "schema_version": "1.0.0",
            "project_id": "fictional-project", "expected_revision": read["project_map"]["revision"],
            "expected_sha256": read["sha256"], "change_reason": "isolated test update",
        }
        request.update(change)
        return request

    def test_save_reread_and_resume_by_outcome_id(self) -> None:
        outcome = copy.deepcopy(self.store.read("fictional-project", "first-outcome")["outcome"])
        outcome["checkpoint"]["last_result"] = "状态已保存，新的上下文可按稳定 ID 读回。"
        result = self.store.update(self.request_for({}, upsert_outcomes=[outcome]))
        reread = self.store.read("fictional-project", "first-outcome")
        self.assertEqual(2, result["revision"])
        self.assertEqual("状态已保存，新的上下文可按稳定 ID 读回。", reread["outcome"]["checkpoint"]["last_result"])
        self.assertEqual(result["sha256"], reread["sha256"])
        self.assertIn("Generated from WORKBENCH_STATE", self.map_path.read_text(encoding="utf-8"))
        self.assertTrue((self.state / "backups" / "fictional-project.md").is_file())

    def test_r02_rejects_ambiguous_or_duplicate_state(self) -> None:
        raw = self.map_path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(WorkbenchError, "exactly one state"):
            parse_map((raw + "\n" + STATE_BEGIN).encode())
        duplicate = raw.replace('"project_id": "fictional-project",', '"project_id": "fictional-project",\n  "project_id": "other-project",')
        with self.assertRaisesRegex(WorkbenchError, "duplicate JSON key"):
            parse_map(duplicate.encode())

    def test_r03_completion_and_transition_gates(self) -> None:
        outcome = copy.deepcopy(self.store.read("fictional-project", "first-outcome")["outcome"])
        outcome["status"] = "done"
        outcome["checkpoint"] = {"saved_at": outcome["checkpoint"]["saved_at"], "last_result": "错误完成", "remaining": [], "next_action": None, "next_action_basis": "none"}
        with self.assertRaisesRegex(WorkbenchError, "completion gate"):
            self.store.update(self.request_for({}, upsert_outcomes=[outcome]))
        outcome = copy.deepcopy(self.store.read("fictional-project", "first-outcome")["outcome"])
        outcome["status"] = "not_started"  # in_progress cannot move backwards without reopening path
        with self.assertRaisesRegex(WorkbenchError, "invalid transition"):
            self.store.update(self.request_for({}, upsert_outcomes=[outcome]))

    def test_r04_stale_revision_or_raw_hash_never_overwrites(self) -> None:
        stale = self.store.read("fictional-project")
        first = self.request_for({}, set_project={"goal": "第一次有效保存"})
        self.store.update(first)
        before = self.map_path.read_bytes()
        stale_request = {"doc_type": "mutation_request", "schema_version": "1.0.0", "project_id": "fictional-project", "expected_revision": stale["project_map"]["revision"], "expected_sha256": stale["sha256"], "change_reason": "stale writer", "set_project": {"goal": "不得覆盖"}}
        with self.assertRaisesRegex(WorkbenchError, "changed"):
            self.store.update(stale_request)
        self.assertEqual(before, self.map_path.read_bytes())
        # An out-of-band edit retaining the revision is also detected by raw SHA.
        changed = before.replace("第一次有效保存".encode(), "人工编辑仍保留 revision".encode())
        self.map_path.write_bytes(changed)
        current_request = self.request_for({}, set_project={"goal": "another"})
        self.map_path.write_bytes(changed + b"\n")
        with self.assertRaises(WorkbenchError) as caught:
            self.store.update(current_request)
        self.assertEqual("revision_conflict", caught.exception.code)

    def test_r05_registration_escape_and_latest_valid_backup(self) -> None:
        with self.assertRaisesRegex(WorkbenchError, "safe relative"):
            self.store.register("another-project", self.root, "../PROJECT_MAP.md")
        original = self.map_path.read_bytes()
        request = self.request_for({}, set_project={"goal": "已保存的新目标"})
        self.store.update(request)
        self.assertEqual(original, (self.state / "backups" / "fictional-project.md").read_bytes())
        self.assertEqual("已保存的新目标", self.store.read("fictional-project")["project_map"]["goal"])

    def test_missing_map_preview_can_only_be_promoted_after_a_valid_map_exists(self) -> None:
        preview_root = Path(self.temp.name) / "preview-project"
        preview_root.mkdir()
        self.store.register_preview("preview-project", preview_root)
        with self.assertRaisesRegex(WorkbenchError, "still missing"):
            self.store.promote_preview("preview-project")
        value = new_map("preview-project", "预览项目", "仅基于已核对事实初始化", "first-outcome", "首个成果")
        atomic_write(preview_root / "PROJECT_MAP.md", render_map("", value))
        self.store.promote_preview("preview-project")
        self.assertEqual("preview-project", self.store.read("preview-project")["project_map"]["project_id"])
        with self.assertRaisesRegex(WorkbenchError, "not a missing-map preview"):
            self.store.promote_preview("preview-project")

    def test_membership_preview_archive_restore_remove_is_cas_and_never_deletes_map_or_backup(self) -> None:
        root = Path(self.temp.name) / "member"; root.mkdir()
        atomic_write(root / "PROJECT_MAP.md", render_map("", new_map("member-project", "成员", "成员目标", "member-outcome", "成员成果")))
        preview = self.store.preview_binding("member-project", root)
        self.assertEqual("ready", preview["map_status"])
        applied = self.store.apply_binding("member-project", root, "PROJECT_MAP.md", preview["registry_revision"])
        self.assertEqual("active", next(x for x in self.store.membership_view()["projects"] if x["project_id"] == "member-project")["visibility"])
        with self.assertRaisesRegex(WorkbenchError, "membership changed"):
            self.store.change_visibility("member-project", "archived", preview["registry_revision"])
        archived = self.store.change_visibility("member-project", "archived", applied["registry_revision"])
        restored = self.store.change_visibility("member-project", "active", archived["registry_revision"])
        backup = self.store.backup_dir / "member-project.initial.md"
        atomic_write(backup, b"private backup fixture")
        removed = self.store.remove_binding("member-project", restored["registry_revision"])
        self.assertTrue(removed["removed"])
        self.assertTrue((root / "PROJECT_MAP.md").is_file())
        self.assertTrue(backup.is_file())

    def test_membership_rejects_symlink_escape_duplicate_and_missing_map_preview(self) -> None:
        root = Path(self.temp.name) / "missing-member"; root.mkdir()
        preview = self.store.preview_binding("missing-member", root)
        self.assertEqual("missing", preview["map_status"])
        self.store.apply_binding("missing-member", root, "PROJECT_MAP.md", preview["registry_revision"])
        with self.assertRaises(WorkbenchError): self.store.preview_binding("missing-member", root)
        link = Path(self.temp.name) / "link"
        try:
            link.symlink_to(root, target_is_directory=True)
        except OSError:
            pass  # Windows may require Developer Mode to create test symlinks.
        else:
            with self.assertRaises(WorkbenchError): self.store.preview_binding("other-member", link)
        with self.assertRaises(WorkbenchError): self.store.preview_binding("other-member", root, "../PROJECT_MAP.md")


if __name__ == "__main__":
    unittest.main()
