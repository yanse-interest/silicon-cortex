from __future__ import annotations

import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from dashboard_h5_open_snapshot import PublicSnapshotError, _copy_valid_sources, generate_fresh_snapshot, validate_public_snapshot
from dashboard_refresh_support import REGISTRY_PATH


class DashboardH5OpenSnapshotTests(unittest.TestCase):
    def test_generation_is_read_only_and_public(self):
        before = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        first = generate_fresh_snapshot()
        middle = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
        second = generate_fresh_snapshot()
        after = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()

        self.assertEqual(before, middle)
        self.assertEqual(before, after)
        self.assertEqual(first["schema_version"], 3)
        self.assertEqual(first["daily_updated_through"], second["daily_updated_through"])
        self.assertNotEqual(first["generated_at"], "")
        encoded = json.dumps(first, ensure_ascii=False)
        for marker in ("/Users/", "chatgpt.com/c/", "raw/conversations", "source_locator", '"case_id"', '"event_id"', '"thread_id"'):
            self.assertNotIn(marker, encoded)

    def test_validator_rejects_internal_keys_and_paths(self):
        base = {"schema_version": 3, "generated_at": "2026-08-23T15:00:00+08:00"}
        with self.assertRaises(PublicSnapshotError):
            validate_public_snapshot({**base, "case_id": "case-private"})
        with self.assertRaises(PublicSnapshotError):
            validate_public_snapshot({**base, "note": "/Users/example/private"})
        with self.assertRaises(PublicSnapshotError):
            validate_public_snapshot({**base, "note": "contains thread_id and prompt"})

    def test_open_refresh_skips_new_source_without_completed_deposition(self):
        with tempfile.TemporaryDirectory() as source_root, tempfile.TemporaryDirectory() as stage_root:
            canonical = Path(source_root) / "wiki"
            source = canonical / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-23.md"
            source.parent.mkdir(parents=True)
            source.write_text(
                "---\ntype: codex_daily_source_summary\ndate: 2026-08-23\nstatus: access_incomplete\n---\n",
                encoding="utf-8",
            )
            with patch("dashboard_h5_open_snapshot.WIKI_ROOT", canonical):
                dates = _copy_valid_sources(Path(stage_root))
            self.assertNotIn("2026-08-23", dates["codex-daily"])


if __name__ == "__main__":
    unittest.main()
