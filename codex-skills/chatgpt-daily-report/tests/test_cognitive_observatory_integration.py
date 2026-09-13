from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_cognitive_observatory.py"
INGEST_SCRIPT = ROOT / "scripts" / "ingest_weekly_review.py"
SPEC = importlib.util.spec_from_file_location("audit_cognitive_observatory", AUDIT_SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def weekly(status: str, body: str = "Body", published_at: str = "") -> str:
    published = f"published_at: {published_at}\n" if published_at else ""
    return f"""---
type: weekly_review
review_cycle: C1
status: {status}
{published}tags:
  - weekly-review
  - cognitive-observatory
---

# C1 Weekly Review

{body}
"""


class CognitiveObservatoryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for rel in [
            "00_conversations",
            "02_observations",
            "03_frameworks",
            "04_decisions",
            "05_projects",
            "06_reviews",
            "99_index",
        ]:
            (self.root / rel).mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ingest(self, text: str) -> subprocess.CompletedProcess[str]:
        input_path = self.root / "input.md"
        input_path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(INGEST_SCRIPT),
                "--vault",
                str(self.root),
                "--cognitive-root",
                str(self.root),
                "--input-file",
                str(input_path),
            ],
            capture_output=True,
            text=True,
        )

    def test_weekly_review_ingest_archives_and_refreshes_index(self) -> None:
        result = self.ingest(
            weekly(
                "published",
                body="## 新概念\n\n- Candidate",
                published_at="2026-06-21T09:00:00+08:00",
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["write_result"], "created")
        self.assertIn("raw/reviews/weekly/", payload["path"])
        self.assertTrue((self.root / "99_index" / "cognitive-index.md").exists())
        self.assertTrue((self.root / "99_index" / "extraction-candidates.md").exists())

    def test_draft_is_preview_only_and_published_is_the_single_raw_record(self) -> None:
        draft = self.ingest(weekly("draft"))
        self.assertEqual(draft.returncode, 0, draft.stderr)
        draft_payload = json.loads(draft.stdout)
        self.assertFalse(draft_payload["archived"])
        self.assertEqual(draft_payload["write_result"], "skipped_draft")
        self.assertFalse((self.root / "raw").exists())
        published = self.ingest(
            weekly("published", published_at="2026-06-21T09:00:00+08:00")
        )
        self.assertEqual(published.returncode, 0, published.stderr)
        published_payload = json.loads(published.stdout)
        self.assertTrue(published_payload["archived"])
        self.assertEqual(published_payload["write_result"], "created")
        self.assertNotIn("draft", published_payload["path"])

    def test_year_week_filename_keeps_canonical_uppercase_w(self) -> None:
        text = weekly(
            "published", published_at="2026-06-21T09:00:00+08:00"
        ).replace("review_cycle: C1", "week: 2026-W7")
        result = self.ingest(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["path"],
            "raw/reviews/weekly/2026/weekly-review-2026-W07.md",
        )

    def test_audit_accepts_complete_auto_promotion_metadata(self) -> None:
        page = self.root / "02_observations" / "cand-123.md"
        page.write_text(
            """---
type: observation
status: active
promotion: auto
candidate_id: cand-123
evidence_count: 3
first_seen: 2026-06-20
last_seen: 2026-06-21
confidence: 0.9
source_links:
  - memory/wiki/sources/daily.md
---

# Observation
""",
            encoding="utf-8",
        )
        notes = [AUDIT.read_note(page, self.root)]
        self.assertEqual(AUDIT.audit(self.root, notes), [])
        page.write_text(
            page.read_text(encoding="utf-8").replace("confidence: 0.9\n", ""),
            encoding="utf-8",
        )
        notes = [AUDIT.read_note(page, self.root)]
        self.assertTrue(any("confidence" in issue for issue in AUDIT.audit(self.root, notes)))


if __name__ == "__main__":
    unittest.main()
