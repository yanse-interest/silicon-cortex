import tempfile
import unittest
from pathlib import Path

import memory_log_compat


class MemoryLogCompatibilityTest(unittest.TestCase):
    def test_historical_memory_writers_route_through_adapter(self):
        root = Path(__file__).resolve().parent
        writers = [
            "migrate_experimental_knowledge_taxonomy_20260804.py",
            "migrate_dual_project_routing_20260804.py",
            "correct_low_quota_alert_status_20260804.py",
            "migrate_branches_to_projects_20260804.py",
            "update_memory_after_knowledge_backcheck_20260805.py",
            "update_memory_after_final_three_20260805.py",
        ]
        for name in writers:
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("from memory_log_compat import", text, name)
            self.assertNotIn("log_path.write_text", text, name)
            self.assertNotIn("LOG.write_text", text, name)

    def test_adapter_uses_canonical_shards_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, text in {
                "_index.md": "# Root\n",
                "wiki/index.md": "# Wiki\n",
                "wiki/log.md": "# Log\n\n## [2026-08-01] lint | Baseline\n\n- Kept.\n",
                "wiki/page.md": "# Page\n",
            }.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            memory_log_compat._MODULE.migrate_legacy_log(root)
            entry = "## [2026-08-02] update | Adapter\n\n- Works.\n"
            self.assertTrue(memory_log_compat.insert_memory_log_entry(root, entry))
            self.assertFalse(memory_log_compat.insert_memory_log_entry(root, entry))
            shard = root / "wiki/logs/2026/2026-08.md"
            self.assertEqual(shard.read_text().count("update | Adapter"), 1)
            self.assertIn("update | Adapter", (root / "wiki/log.md").read_text())


if __name__ == "__main__":
    unittest.main()
