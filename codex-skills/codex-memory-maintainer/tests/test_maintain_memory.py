import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/maintain_memory.py"
SPEC = importlib.util.spec_from_file_location("maintain_memory_test_module", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MaintainMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("_index.md", "# Root\n\n## 规则\n\nHuman rule.\n")
        self.write("wiki/index.md", "# Wiki\n\nHuman judgment.\n\n## 规则\n\nKeep it.\n")
        self.write("wiki/page.md", "# Curated Page\n\nJudgment.\n")
        self.write(
            "wiki/sources/conversations/codex-daily/2026/codex-daily-report-2026-08-28.md",
            "---\ndate: 2026-08-28\ncoverage: complete\nstatus: ready\n---\n# Codex source\n",
        )
        self.write(
            "wiki/log.md",
            "# Legacy\n\n"
            "## [2026-08-28] lint | Same heading\n\n- Newer.\n\n"
            "## [2026-08-28] lint | Same heading\n\n- Distinct same-title entry.\n\n"
            "## [2026-07-31] update | Older\n\n- Preserved exactly.\n",
        )

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_migration_is_lossless_and_generated_views_are_deterministic(self):
        before = module.parse_entries((self.root / "wiki/log.md").read_text())
        entries = module.migrate_legacy_log(self.root)
        after = module.load_shard_entries(self.root)
        self.assertEqual(module.entry_stream_sha256(before), module.entry_stream_sha256(after))
        self.assertEqual([entry.text for entry in before], [entry.text for entry in after])
        self.assertEqual(module.generation_issues(self.root), [])
        self.assertIn(module.GENERATED_NOTICE, (self.root / "wiki/log.md").read_text())
        self.assertIn("Human judgment.", (self.root / "wiki/index.md").read_text())
        self.assertIn("codex-daily-report-2026-08-28", (self.root / "wiki/sources/_index.md").read_text())
        first = {path: path.read_bytes() for path in module.generated_targets(self.root, entries, include_shards=True)}
        module.atomic_replace_many(module.generated_targets(self.root, entries, include_shards=True))
        self.assertEqual(first, {path: path.read_bytes() for path in first})

    def test_insert_is_idempotent_and_updates_shard_and_compatibility(self):
        module.migrate_legacy_log(self.root)
        text = "## [2026-08-29] ingest | New\n\n- Receipt.\n"
        self.assertTrue(module.insert_entry(self.root, text))
        (self.root / "wiki/log.md").write_text("drift\n", encoding="utf-8")
        self.assertFalse(module.insert_entry(self.root, text))
        entries = module.load_shard_entries(self.root)
        self.assertEqual(sum(entry.text == text for entry in entries), 1)
        self.assertTrue(entries[0].heading.endswith("ingest | New"))
        self.assertIn("Receipt.", (self.root / "wiki/log.md").read_text())
        self.assertEqual(module.generation_issues(self.root), [])

    def test_audit_detects_duplicate_drift_and_wrong_month(self):
        module.migrate_legacy_log(self.root)
        shard = self.root / "wiki/logs/2026/2026-08.md"
        shard.write_text(shard.read_text() + "\n" + module.load_shard_entries(self.root)[0].text, encoding="utf-8")
        issues = module.generation_issues(self.root)
        self.assertTrue(any("duplicate canonical" in issue for issue in issues))
        self.assertTrue(any("generated artifact drift" in issue for issue in issues))

    def test_group_replace_rolls_back_after_replace_failure(self):
        a = self.root / "a.md"
        b = self.root / "b.md"
        a.write_text("a", encoding="utf-8")
        b.write_text("b", encoding="utf-8")
        real_replace = module.os.replace
        calls = 0

        def failing_replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected")
            return real_replace(source, target)

        with mock.patch.object(module.os, "replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                module.atomic_replace_many({a: "new a", b: "new b"})
        self.assertEqual(a.read_text(), "a")
        self.assertEqual(b.read_text(), "b")

    def test_project_only_catalog_generates_compatibility_without_touching_decisions(self):
        self.write("wiki/projects.md", "# Projects\n\nLegacy project body.\n")
        self.write("wiki/decisions.md", "# Decisions\n\nMust remain byte-identical.\n")
        section = "## Legacy Project A\n\nProject body.\n"
        self.write(
            "wiki/catalog/projects/project-a.md",
            "# Project A\n\n<!-- BEGIN LOSSLESS LEGACY PROJECT SECTION -->\n"
            + section
            + "<!-- END LOSSLESS LEGACY PROJECT SECTION -->\n",
        )
        self.write(
            "wiki/catalog/catalog.json",
            '{\n'
            '  "schema_version": "1.0",\n'
            '  "entries": [{\n'
            '    "id": "project-a",\n'
            '    "kind": "project",\n'
            '    "title": "Project A",\n'
            '    "legacy_heading": "Legacy Project A",\n'
            '    "aliases": [{"type": "legacy_heading", "value": "Legacy Project A"}],\n'
            '    "canonical_locator": "wiki/catalog/projects/project-a.md",\n'
            f'    "source_section_sha256": "{hashlib.sha256(section.encode()).hexdigest()}"\n'
            '  }]\n'
            '}\n',
        )
        before_decisions = (self.root / "wiki/decisions.md").read_bytes()

        module.migrate_legacy_log(self.root)

        compatibility = (self.root / "wiki/projects.md").read_text(encoding="utf-8")
        self.assertIn("## Legacy Project A", compatibility)
        self.assertIn("[[catalog/projects/project-a|读取完整正文]]", compatibility)
        catalog_index = (self.root / "wiki/catalog/_index.md").read_text(encoding="utf-8")
        self.assertIn("# 项目目录", catalog_index)
        self.assertNotIn("## 决策", catalog_index)
        self.assertEqual((self.root / "wiki/decisions.md").read_bytes(), before_decisions)
        targets = module.generated_targets(self.root, module.load_shard_entries(self.root), include_shards=True)
        self.assertIn(self.root / "wiki/projects.md", targets)
        self.assertNotIn(self.root / "wiki/decisions.md", targets)
        self.assertEqual(module.generation_issues(self.root), [])

    def test_cli_write_commands_take_the_cross_process_maintenance_lock(self):
        module.migrate_legacy_log(self.root)
        entry = self.root / "entry.md"
        entry.write_text("## [2026-08-30] lint | Locked\n\n- Verified.\n", encoding="utf-8")
        section = self.root / "section.md"
        section.write_text("## Legacy Project A\n\nUpdated body.\n", encoding="utf-8")
        self._write_project_catalog_fixture()
        lock = mock.Mock(side_effect=lambda: nullcontext())
        with mock.patch.object(module, "exclusive_memory_maintenance_lock", lock):
            with mock.patch.object(sys, "argv", ["maintain_memory.py", "generate", "--vault", str(self.root)]):
                module.main()
            with mock.patch.object(sys, "argv", [
                "maintain_memory.py", "insert", "--vault", str(self.root), "--entry-file", str(entry)
            ]):
                module.main()
            with mock.patch.object(sys, "argv", [
                "maintain_memory.py", "update-catalog-section", "--vault", str(self.root),
                "--catalog-id", "project-a", "--section-file", str(section)
            ]):
                module.main()
        self.assertEqual(lock.call_count, 3)

    def _write_project_catalog_fixture(self):
        section = "## Legacy Project A\n\nProject body.\n"
        self.write(
            "wiki/catalog/projects/project-a.md",
            "# Project A\n\n"
            f"- source section SHA-256：`{hashlib.sha256(section.encode()).hexdigest()}`\n\n"
            "<!-- BEGIN LOSSLESS LEGACY PROJECT SECTION -->\n"
            + section
            + "<!-- END LOSSLESS LEGACY PROJECT SECTION -->\n",
        )
        self.write(
            "wiki/catalog/catalog.json",
            json.dumps({
                "schema_version": "1.0",
                "entries": [{
                    "id": "project-a",
                    "kind": "project",
                    "title": "Project A",
                    "legacy_heading": "Legacy Project A",
                    "aliases": [{"type": "legacy_heading", "value": "Legacy Project A"}],
                    "canonical_locator": "wiki/catalog/projects/project-a.md",
                    "source_section_sha256": hashlib.sha256(section.encode()).hexdigest(),
                }],
            }, ensure_ascii=False, indent=2) + "\n",
        )

    def test_catalog_section_update_is_atomic_and_hash_consistent(self):
        self._write_project_catalog_fixture()
        updated = "## Legacy Project A\n\nProject body.\n\n- Stable update.\n"
        self.assertTrue(module.update_catalog_section(self.root, "project-a", updated))
        catalog = module.load_catalog(self.root)
        expected = hashlib.sha256(updated.encode()).hexdigest()
        self.assertEqual(catalog["entries"][0]["source_section_sha256"], expected)
        page = (self.root / "wiki/catalog/projects/project-a.md").read_text(encoding="utf-8")
        self.assertIn("- Stable update.", page)
        self.assertIn(expected, page)
        self.assertEqual(module.catalog_issues(self.root), [])
        self.assertFalse(module.update_catalog_section(self.root, "project-a", updated))

    def test_catalog_section_update_rejects_heading_change_without_writes(self):
        self._write_project_catalog_fixture()
        page = self.root / "wiki/catalog/projects/project-a.md"
        catalog = self.root / "wiki/catalog/catalog.json"
        before = (page.read_bytes(), catalog.read_bytes())
        with self.assertRaisesRegex(ValueError, "unchanged legacy heading"):
            module.update_catalog_section(self.root, "project-a", "## Renamed\n\nBody.\n")
        self.assertEqual((page.read_bytes(), catalog.read_bytes()), before)

    def test_catalog_section_update_rejects_preexisting_drift_before_writes(self):
        self._write_project_catalog_fixture()
        page = self.root / "wiki/catalog/projects/project-a.md"
        catalog = self.root / "wiki/catalog/catalog.json"
        page.write_text(page.read_text(encoding="utf-8").replace("Project body.", "Drifted body."), encoding="utf-8")
        before = (page.read_bytes(), catalog.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "catalog must be healthy before update"):
            module.update_catalog_section(
                self.root,
                "project-a",
                "## Legacy Project A\n\nReplacement body.\n",
            )
        self.assertEqual((page.read_bytes(), catalog.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
