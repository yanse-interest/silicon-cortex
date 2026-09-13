from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "deposition_pipeline.py"
SPEC = importlib.util.spec_from_file_location("deposition_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def candidate(
    *,
    candidate_type: str,
    domain: str,
    claim: str,
    source_date: str,
    sessions: list[str],
    target: str,
    assertion: str = "inferred",
    risk: str = "low",
    confidence: float = 0.8,
    evidence_complete: bool = True,
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "candidate_id": MODULE.stable_candidate_id(candidate_type, domain, claim),
        "type": candidate_type,
        "domain": domain,
        "normalized_claim": claim,
        "source_date": source_date,
        "sessions": sessions,
        "coverage": "partial",
        "evidence_boundary": "Positive cited statements only; not complete-day frequency.",
        "evidence_complete": evidence_complete,
        "assertion": assertion,
        "confidence": confidence,
        "risk": risk,
        "suggested_target": target,
        "status": "candidate",
        "conflicts_with": [],
    }
    value.update(extra)
    return value


def source_text(day: str, records: list[dict[str, object]], coverage: str = "partial") -> str:
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    return f"""---
type: chatgpt_daily_source_summary
date: {day}
coverage: {coverage}
status: ready
raw_source: raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-{day}.md
---

# ChatGPT Daily Report — {day}

## Summary

Compiled source summary.

## Structured Candidates

```json
{payload}
```
"""


def weekly_source_text(day: str, records: list[dict[str, object]], coverage: str = "partial") -> str:
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    return f"""---
type: weekly_review_source_summary
date: {day}
review_cycle: W25
coverage: {coverage}
status: published
raw_source: raw/reviews/weekly/2026/weekly-review-2026-W25.md
---

# W25 Weekly Review

## Structured Candidates

```json
{payload}
```
"""


def codex_source_text(day: str, records: list[dict[str, object]], coverage: str = "partial") -> str:
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    return f"""---
type: codex_daily_source_summary
date: {day}
coverage: {coverage}
status: ready
raw_source: raw/conversations/codex-daily/{day[:4]}/codex-daily-report-{day}.md
---

# Codex Daily Summary — {day}

## Structured Candidates

```json
{payload}
```
"""


def review_text(cycle: str, status: str, published_at: str = "") -> str:
    published = f"published_at: {published_at}\n" if published_at else ""
    return f"""---
type: weekly_review
review_cycle: {cycle}
status: {status}
{published}---

# {cycle} Weekly Review
"""


class DepositionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = self.root / "memory"
        self.cognitive = self.root / "cognitive"
        for rel in ["02_observations", "03_frameworks"]:
            (self.cognitive / rel).mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_source(self, name: str, day: str, records: list[dict[str, object]]) -> Path:
        path = self.root / name
        path.write_text(source_text(day, records), encoding="utf-8")
        return path

    def write_weekly_source(self, name: str, day: str, records: list[dict[str, object]]) -> Path:
        path = self.root / name
        path.write_text(weekly_source_text(day, records), encoding="utf-8")
        return path

    def write_review(
        self, cycle: str, status: str, published_at: str = ""
    ) -> Path:
        path = self.root / f"weekly-{cycle}.md"
        path.write_text(review_text(cycle, status, published_at), encoding="utf-8")
        return path

    def test_candidate_id_is_stable_under_spacing_and_punctuation(self) -> None:
        first = MODULE.stable_candidate_id("observation", "AI Workflow", "Run first — optimize later")
        second = MODULE.stable_candidate_id(" observation ", "ai-workflow", "Run first optimize later!")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^cand-[0-9a-f]{16}$")

    def test_canonical_weekly_input_gate_reports_complete_week(self) -> None:
        review = {
            "review_cycle": "2026-W35",
            "coverage_start": "2026-08-24T00:00:00+08:00",
            "coverage_end": "2026-08-30T23:59:59+08:00",
        }
        loaded = []
        paths = []
        for day in range(24, 31):
            for family in ("chatgpt", "codex"):
                path = self.root / f"{family}-{day}.md"
                path.write_text("source\n", encoding="utf-8")
                paths.append(path)
                loaded.append({"date": f"2026-08-{day:02d}", "source_kind": family})
        with mock.patch.object(MODULE.CONTRACT, "source_deposition_state", return_value={"ready": True}):
            gate = MODULE.weekly_input_gate(review, loaded, paths, self.memory)
        self.assertTrue(gate["ready"])
        self.assertTrue(gate["complete"])
        self.assertEqual(gate["observed_source_count"], 14)
        self.assertEqual(gate["missing"], [])

    def test_canonical_weekly_input_gate_reports_missing_date_families(self) -> None:
        review = {
            "review_cycle": "2026-W35",
            "coverage_start": "2026-08-24T00:00:00+08:00",
            "coverage_end": "2026-08-30T23:59:59+08:00",
        }
        loaded = [
            {"date": f"2026-08-{day:02d}", "source_kind": family}
            for day in range(24, 30)
            for family in ("chatgpt", "codex")
        ]
        paths = []
        for index in range(len(loaded)):
            path = self.root / f"source-{index}.md"
            path.write_text("source\n", encoding="utf-8")
            paths.append(path)
        with mock.patch.object(MODULE.CONTRACT, "source_deposition_state", return_value={"ready": True}):
            gate = MODULE.weekly_input_gate(review, loaded, paths, self.memory)
        self.assertTrue(gate["ready"])
        self.assertFalse(gate["complete"])
        self.assertEqual(gate["missing"], ["2026-08-30:chatgpt", "2026-08-30:codex"])

    def test_bounded_week_can_publish_without_promoting_unreviewed_source(self) -> None:
        review = self.write_review("2026-W35", "published", "2026-08-31T08:00:00+08:00")
        review.write_text(
            review.read_text(encoding="utf-8").replace(
                "status: published\n",
                "status: published\ncoverage_start: 2026-08-24T00:00:00+08:00\n"
                "coverage_end: 2026-08-30T23:59:59+08:00\n",
            ),
            encoding="utf-8",
        )
        source = self.root / "pending-source.md"
        source.write_text("pending analysis\n", encoding="utf-8")
        candidate_record = candidate(
            candidate_type="observation", domain="workflow", claim="Unreviewed candidate",
            source_date="2026-08-24", sessions=["S01"], target="cognitive_observation",
            risk="low", uncertainty="Pending review",
        )
        loaded = {"date": "2026-08-24", "source_kind": "chatgpt", "records": [candidate_record], "path": "pending-source.md"}
        with mock.patch.object(MODULE, "load_daily_source", return_value=loaded), mock.patch.object(
            MODULE.CONTRACT, "source_deposition_state", return_value={"ready": False, "reason": "receipt_missing"}
        ):
            result = MODULE._process_review_unlocked(
                review, [source], self.memory, self.cognitive, dry_run=False
            )
        self.assertTrue(result["closed"])
        self.assertFalse(result["input_gate"]["complete"])
        self.assertEqual(result["input_gate"]["evidence_source_count"], 0)
        self.assertEqual(result["candidate_results"], [])

    def test_published_canonical_week_refuses_incomplete_input_before_writes(self) -> None:
        review = self.write_review("2026-W35", "published", "2026-08-31T08:00:00+08:00")
        with mock.patch.object(MODULE, "load_daily_source", return_value={"records": []}), mock.patch.object(
            MODULE,
            "weekly_input_gate",
            return_value={"enforced": True, "ready": False, "missing": ["2026-08-30:chatgpt"]},
        ), mock.patch.object(MODULE, "load_ledger") as ledger:
            with self.assertRaisesRegex(MODULE.PipelineError, "input gate is not ready"):
                MODULE._process_review_unlocked(
                    review, [], self.memory, self.cognitive, dry_run=False
                )
        ledger.assert_not_called()

    def test_published_canonical_week_cannot_close_on_or_before_sunday(self) -> None:
        review = self.write_review("2026-W35", "published", "2026-08-30T23:59:59+08:00")
        gate = {
            "enforced": True,
            "ready": True,
            "expected_dates": [
                "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27",
                "2026-08-28", "2026-08-29", "2026-08-30",
            ],
        }
        with mock.patch.object(MODULE, "weekly_input_gate", return_value=gate), mock.patch.object(
            MODULE, "load_ledger"
        ) as ledger:
            with self.assertRaisesRegex(MODULE.PipelineError, "cannot close before"):
                MODULE._process_review_unlocked(
                    review, [], self.memory, self.cognitive, dry_run=False
                )
        ledger.assert_not_called()

    def test_validate_partial_source_and_reject_unstable_id(self) -> None:
        record = candidate(
            candidate_type="observation",
            domain="health",
            claim="A dated symptom was observed",
            source_date="2026-06-20",
            sessions=["S01"],
            target="cognitive_observation",
            risk="high",
            uncertainty="Single observation; no causal inference.",
        )
        path = self.write_source("daily.md", "2026-06-20", [record])
        loaded = MODULE.load_daily_source(path)
        self.assertEqual(loaded["coverage"], "partial")
        self.assertEqual(len(loaded["records"]), 1)
        record["candidate_id"] = "cand-wrong"
        path.write_text(source_text("2026-06-20", [record]), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.PipelineError, "unstable"):
            MODULE.load_daily_source(path)

    def test_codex_source_requires_t_refs_and_exact_raw_family(self) -> None:
        record = candidate(
            candidate_type="project_state",
            domain="bridge",
            claim="Codex progress bridge entered validation",
            source_date="2026-08-01",
            sessions=["T01"],
            target="project",
            assertion="explicit",
        )
        path = self.root / "codex-daily-report-2026-08-01.md"
        path.write_text(codex_source_text("2026-08-01", [record]), encoding="utf-8")
        loaded = MODULE.load_daily_source(path)
        self.assertEqual(loaded["source_kind"], "codex")
        record["sessions"] = ["S01"]
        path.write_text(codex_source_text("2026-08-01", [record]), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.PipelineError, "Txx"):
            MODULE.load_daily_source(path)
        record["sessions"] = ["T01"]
        path.write_text(
            codex_source_text("2026-08-01", [record]).replace(
                "raw/conversations/codex-daily/2026/", "raw/conversations/chatgpt-daily/2026/"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.PipelineError, "raw_source"):
            MODULE.load_daily_source(path)

    def test_cross_source_candidate_dedupes_into_one_ledger_entry(self) -> None:
        claim = "Use one stable candidate ledger for ChatGPT and Codex evidence"
        chatgpt_record = candidate(
            candidate_type="project_state",
            domain="deposition",
            claim=claim,
            source_date="2026-08-01",
            sessions=["S01"],
            target="project",
            assertion="explicit",
        )
        codex_record = candidate(
            candidate_type="project_state",
            domain="deposition",
            claim=claim,
            source_date="2026-08-01",
            sessions=["T01"],
            target="project",
            assertion="explicit",
        )
        chatgpt = self.write_source("chatgpt-daily-report-2026-08-01.md", "2026-08-01", [chatgpt_record])
        codex = self.root / "codex-daily-report-2026-08-01.md"
        codex.write_text(codex_source_text("2026-08-01", [codex_record]), encoding="utf-8")
        weekly = self.write_review("C1", "published", "2026-08-02T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [chatgpt, codex], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(len(result["candidate_results"]), 1)
        ledger = json.loads(
            (self.memory / "wiki" / "review-cycles" / "promotion-ledger.json").read_text(
                encoding="utf-8"
            )
        )
        aggregate = ledger["candidates"][chatgpt_record["candidate_id"]]
        self.assertEqual(len(ledger["candidates"]), 1)
        self.assertEqual({item["session"] for item in aggregate["evidence"]}, {"S01", "T01"})
        self.assertEqual(len({item["source_path"] for item in aggregate["evidence"]}), 2)

    def test_rejects_simple_candidate_routed_to_cognitive_observatory(self) -> None:
        record = candidate(
            candidate_type="project_state",
            domain="project",
            claim="Project entered validation",
            source_date="2026-06-20",
            sessions=["S01"],
            target="cognitive_observation",
            assertion="explicit",
        )
        path = self.write_source("daily.md", "2026-06-20", [record])
        with self.assertRaisesRegex(MODULE.PipelineError, "invalid candidate route"):
            MODULE.load_daily_source(path)

    def test_rejects_framework_routed_to_memory_knowledge(self) -> None:
        record = candidate(
            candidate_type="framework",
            domain="AI workflow",
            claim="Run first then optimize after evidence accumulates",
            source_date="2026-06-20",
            sessions=["S01"],
            target="knowledge",
        )
        path = self.write_source("daily.md", "2026-06-20", [record])
        with self.assertRaisesRegex(MODULE.PipelineError, "cognitive candidate types"):
            MODULE.load_daily_source(path)

    def test_draft_does_not_close_cycle_or_write(self) -> None:
        record = candidate(
            candidate_type="decision",
            domain="workflow",
            claim="Use Review Cycle boundaries",
            source_date="2026-06-20",
            sessions=["S01"],
            target="decision",
            assertion="explicit",
            adoption_evidence="User said: adopt Review Cycle boundaries.",
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "draft")
        result = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        self.assertFalse(result["closed"])
        self.assertEqual(result["writes"], [])
        self.assertFalse((self.memory / "wiki" / "review-cycles").exists())

    def test_explicit_decision_promotes_but_inferred_decision_does_not(self) -> None:
        explicit = candidate(
            candidate_type="decision",
            domain="workflow",
            claim="Use Review Cycle boundaries",
            source_date="2026-06-20",
            sessions=["S01"],
            target="decision",
            assertion="explicit",
            adoption_evidence="User explicitly adopted Review Cycle boundaries.",
        )
        inferred = candidate(
            candidate_type="decision",
            domain="workflow",
            claim="Use calendar weeks for all reviews",
            source_date="2026-06-20",
            sessions=["S02"],
            target="decision",
            assertion="inferred",
        )
        daily = self.write_source("daily.md", "2026-06-20", [explicit, inferred])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        states = {item["candidate_id"]: item["state"] for item in result["candidate_results"]}
        self.assertEqual(states[explicit["candidate_id"]], "promote_memory")
        self.assertEqual(states[inferred["candidate_id"]], "manual_confirmation")
        page = self.memory / "wiki" / "auto-promotions" / "decision" / f"{explicit['candidate_id']}.md"
        self.assertTrue(page.exists())
        self.assertIn("promotion: auto", page.read_text(encoding="utf-8"))

    def test_daily_partial_report_promotes_complete_explicit_positive_evidence(self) -> None:
        record = candidate(
            candidate_type="preference",
            domain="workflow",
            claim="Prefer Review Cycle over fixed calendar weeks",
            source_date="2026-06-20",
            sessions=["S01"],
            target="preference",
            assertion="explicit",
            evidence_complete=True,
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        result = MODULE.process_daily_source(
            daily, self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["candidate_results"][0]["state"], "promote_memory")
        self.assertEqual(result["raw_files_modified"], 0)
        rerun = MODULE.process_daily_source(
            daily, self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(rerun["candidate_results"][0]["state"], "already_promoted")

    def test_observation_threshold_dedupes_and_promotes_once(self) -> None:
        claim = "Repeated tool failures should be captured as a reusable workflow signal"
        first = candidate(
            candidate_type="observation",
            domain="AI workflow",
            claim=claim,
            source_date="2026-06-20",
            sessions=["S01", "S02"],
            target="cognitive_observation",
        )
        second = candidate(
            candidate_type="observation",
            domain="AI workflow",
            claim=claim,
            source_date="2026-06-21",
            sessions=["S03"],
            target="cognitive_observation",
        )
        daily1 = self.write_source("daily1.md", "2026-06-20", [first])
        daily2 = self.write_source("daily2.md", "2026-06-21", [second])
        weekly = self.write_review("C1", "published", "2026-06-22T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily1, daily2, daily1], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(result["candidate_results"][0]["state"], "promote_observation")
        page = self.cognitive / "02_observations" / f"{first['candidate_id']}.md"
        self.assertTrue(page.exists())
        self.assertIn("evidence_count: 3", page.read_text(encoding="utf-8"))
        rerun = MODULE.process_review(
            weekly, [daily1, daily2], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(rerun["candidate_results"][0]["state"], "already_promoted")
        ledger = json.loads(
            (self.memory / "wiki" / "review-cycles" / "promotion-ledger.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(ledger["candidates"]), 1)
        self.assertEqual(len(ledger["candidates"][first["candidate_id"]]["evidence"]), 3)

    def test_closed_cycle_locks_daily_evidence_hashes(self) -> None:
        record = candidate(
            candidate_type="project_state",
            domain="project",
            claim="Project entered validation",
            source_date="2026-06-20",
            sessions=["S01"],
            target="project",
            assertion="explicit",
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        changed = candidate(
            candidate_type="knowledge",
            domain="project",
            claim="A different derived claim",
            source_date="2026-06-20",
            sessions=["S02"],
            target="knowledge",
        )
        daily.write_text(source_text("2026-06-20", [record, changed]), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.PipelineError, "daily evidence changed"):
            MODULE.process_review(
                weekly, [daily], self.memory, self.cognitive, dry_run=False
            )

    def test_confirmed_capture_repair_is_guarded_and_keeps_history(self) -> None:
        daily = self.write_source("daily.md", "2026-06-20", [])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        prior = MODULE.review_hash(weekly)
        cycle = self.memory / "wiki/review-cycles/review-cycle-c1.json"
        before = cycle.read_bytes()
        weekly.write_text(weekly.read_text() + "\nCorrected missing synthesis.\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.PipelineError, "changed"):
            MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        with self.assertRaisesRegex(MODULE.PipelineError, "hash mismatch"):
            MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False,
                repair_confirmed_capture_mistake=True, expected_prior_weekly_hash="0" * 64)
        self.assertEqual(before, cycle.read_bytes())
        preview = MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=True,
            repair_confirmed_capture_mistake=True, expected_prior_weekly_hash=prior)
        self.assertTrue(preview["ok"])
        self.assertEqual(before, cycle.read_bytes())
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False,
            repair_confirmed_capture_mistake=True, expected_prior_weekly_hash=prior)
        corrected = json.loads(cycle.read_text())
        self.assertEqual(corrected["capture_corrections"][0]["previous_review_sha256"], prior)
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        self.assertEqual(corrected["capture_corrections"], json.loads(cycle.read_text())["capture_corrections"])

    def test_review_accepts_weekly_source_summary_candidates(self) -> None:
        record = candidate(
            candidate_type="decision",
            domain="cognitive-observatory",
            claim="Use Weekly Review as the source of truth for downstream extraction",
            source_date="2026-06-26",
            sessions=["S01"],
            target="decision",
            assertion="explicit",
            adoption_evidence="User adopted Weekly Review as source of truth.",
        )
        weekly_source = self.write_weekly_source("weekly-source.md", "2026-06-26", [record])
        weekly_review = self.write_review(
            "W25", "published", "2026-06-26T23:21:57+08:00"
        )
        result = MODULE.process_review(
            weekly_review, [weekly_source], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(result["candidate_results"][0]["state"], "promote_memory")
        self.assertTrue(
            (
                self.memory
                / "wiki"
                / "auto-promotions"
                / "decision"
                / f"{record['candidate_id']}.md"
            ).exists()
        )

    def test_framework_needs_two_cycles_and_two_applications(self) -> None:
        claim = "Smoke test before expanding automation scope"
        observation_claim = "Smoke tests repeatedly prevent unsafe automation expansion"
        observation_id = MODULE.stable_candidate_id(
            "observation", "AI workflow", observation_claim
        )
        observation1 = candidate(
            candidate_type="observation",
            domain="AI workflow",
            claim=observation_claim,
            source_date="2026-06-10",
            sessions=["S01", "S02"],
            target="cognitive_observation",
        )
        observation2 = candidate(
            candidate_type="observation",
            domain="AI workflow",
            claim=observation_claim,
            source_date="2026-06-11",
            sessions=["S03"],
            target="cognitive_observation",
        )
        first = candidate(
            candidate_type="framework",
            domain="AI workflow",
            claim=claim,
            source_date="2026-06-10",
            sessions=["S04"],
            target="cognitive_framework",
            application_cases=["browser automation"],
            linked_observation_ids=[observation_id],
        )
        daily1 = self.write_source("daily1.md", "2026-06-10", [observation1, first])
        daily1b = self.write_source("daily1b.md", "2026-06-11", [observation2])
        weekly1 = self.write_review("C1", "published", "2026-06-11T09:00:00+08:00")
        one = MODULE.process_review(
            weekly1, [daily1, daily1b], self.memory, self.cognitive, dry_run=False
        )
        framework_one = next(
            item for item in one["candidate_results"] if item["candidate_id"] == first["candidate_id"]
        )
        self.assertEqual(framework_one["state"], "continue_observing")
        observation3 = candidate(
            candidate_type="observation",
            domain="AI workflow",
            claim=observation_claim,
            source_date="2026-06-18",
            sessions=["S05"],
            target="cognitive_observation",
        )
        second = candidate(
            candidate_type="framework",
            domain="AI workflow",
            claim=claim,
            source_date="2026-06-18",
            sessions=["S06"],
            target="cognitive_framework",
            application_cases=["browser automation", "email routing"],
            linked_observation_ids=[observation_id],
        )
        daily2 = self.write_source("daily2.md", "2026-06-18", [observation3, second])
        weekly2 = self.write_review("C2", "published", "2026-06-19T09:00:00+08:00")
        two = MODULE.process_review(
            weekly2, [daily2], self.memory, self.cognitive, dry_run=False
        )
        framework_two = next(
            item for item in two["candidate_results"] if item["candidate_id"] == second["candidate_id"]
        )
        self.assertEqual(framework_two["state"], "promote_framework")

    def test_conflict_and_high_risk_rules(self) -> None:
        conflict = candidate(
            candidate_type="project_state",
            domain="project",
            claim="Project is paused",
            source_date="2026-06-20",
            sessions=["S01"],
            target="project",
            assertion="explicit",
            conflicts_with=["cand-existing-active-state"],
        )
        causal = candidate(
            candidate_type="knowledge",
            domain="health",
            claim="A symptom was caused by one intervention",
            source_date="2026-06-20",
            sessions=["S02"],
            target="knowledge",
            assertion="explicit",
            risk="high",
        )
        research_decision = candidate(
            candidate_type="decision",
            domain="research",
            claim="Adopt one experimental method based on chat evidence",
            source_date="2026-06-20",
            sessions=["S03"],
            target="decision",
            assertion="explicit",
            risk="high",
            adoption_evidence="User discussed adopting the method.",
        )
        daily = self.write_source(
            "daily.md", "2026-06-20", [conflict, causal, research_decision]
        )
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        states = {item["candidate_id"]: item["state"] for item in result["candidate_results"]}
        self.assertEqual(states[conflict["candidate_id"]], "conflict")
        self.assertEqual(states[causal["candidate_id"]], "manual_confirmation")
        self.assertEqual(states[research_decision["candidate_id"]], "manual_confirmation")

    def test_high_risk_observation_may_promote_with_uncertainty(self) -> None:
        claim = "A dated health pattern recurred under the recorded conditions"
        first = candidate(
            candidate_type="observation",
            domain="health",
            claim=claim,
            source_date="2026-06-20",
            sessions=["S01", "S02"],
            target="cognitive_observation",
            risk="high",
            uncertainty="Association only; no causal or treatment conclusion.",
        )
        second = candidate(
            candidate_type="observation",
            domain="health",
            claim=claim,
            source_date="2026-06-21",
            sessions=["S03"],
            target="cognitive_observation",
            risk="high",
            uncertainty="Association only; no causal or treatment conclusion.",
        )
        daily1 = self.write_source("daily1.md", "2026-06-20", [first])
        daily2 = self.write_source("daily2.md", "2026-06-21", [second])
        weekly = self.write_review("C1", "published", "2026-06-22T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily1, daily2], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(result["candidate_results"][0]["state"], "promote_observation")

    def test_cognitive_shift_requires_complete_change_evidence_and_promotes_as_observation(self) -> None:
        incomplete = candidate(
            candidate_type="cognitive_shift",
            domain="workflow",
            claim="A possible shift without a recorded old judgment",
            source_date="2026-06-20",
            sessions=["S01"],
            target="cognitive_observation",
        )
        claim = "Shift from calendar-week review to evidence-bounded Review Cycles"
        complete1 = candidate(
            candidate_type="cognitive_shift",
            domain="workflow",
            claim=claim,
            source_date="2026-06-20",
            sessions=["S02", "S03"],
            target="cognitive_observation",
            old_judgment="Review on fixed calendar weeks",
            new_judgment="Review on evidence-bounded cycles",
            change_evidence="User explicitly contrasted and adopted the new boundary.",
        )
        complete2 = candidate(
            candidate_type="cognitive_shift",
            domain="workflow",
            claim=claim,
            source_date="2026-06-21",
            sessions=["S04"],
            target="cognitive_observation",
            old_judgment="Review on fixed calendar weeks",
            new_judgment="Review on evidence-bounded cycles",
            change_evidence="User explicitly contrasted and adopted the new boundary.",
        )
        daily1 = self.write_source("daily1.md", "2026-06-20", [incomplete, complete1])
        daily2 = self.write_source("daily2.md", "2026-06-21", [complete2])
        weekly = self.write_review("C1", "published", "2026-06-22T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily1, daily2], self.memory, self.cognitive, dry_run=False
        )
        states = {item["candidate_id"]: item["state"] for item in result["candidate_results"]}
        self.assertEqual(states[incomplete["candidate_id"]], "manual_confirmation")
        self.assertEqual(states[complete1["candidate_id"]], "promote_observation")
        page = self.cognitive / "02_observations" / f"{complete1['candidate_id']}.md"
        self.assertIn("type: observation", page.read_text(encoding="utf-8"))

    def test_skill_candidate_does_not_edit_skill_code(self) -> None:
        record = candidate(
            candidate_type="skill",
            domain="workflow",
            claim="Automate a repeated report validation workflow",
            source_date="2026-06-20",
            sessions=["S01"],
            target="skill",
            validation_successes=2,
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(result["candidate_results"][0]["state"], "skill_candidate")
        self.assertFalse((self.memory / "wiki" / "auto-promotions" / "skill").exists())

    def test_actions_require_confirmation_and_are_not_dispatched(self) -> None:
        record = candidate(
            candidate_type="action",
            domain="workflow",
            claim="Send the confirmed follow-up",
            source_date="2026-06-20",
            sessions=["S01"],
            target="action",
            action="Send the confirmed follow-up",
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        result = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        action = result["actions"][0]
        self.assertEqual(action["status"], "pending")
        self.assertEqual(action["owner"], "unspecified")
        self.assertEqual(action["due"], "unspecified")
        self.assertEqual(result["actions_dispatched"], 0)
        cycle = self.memory / "wiki" / "review-cycles" / "review-cycle-c1.json"
        confirmed = MODULE.confirm_actions(cycle, [record["candidate_id"]])
        self.assertEqual(confirmed["dispatch_ready"][0]["status"], "confirmed")
        self.assertEqual(confirmed["actions_dispatched"], 0)

    def test_completed_internal_action_is_terminal_and_not_monthly_unfinished(self) -> None:
        record = candidate(
            candidate_type="action",
            domain="workflow",
            claim="Verify the extraction workflow",
            source_date="2026-06-20",
            sessions=["S01"],
            target="action",
            action="Verify the extraction workflow",
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        cycle = self.memory / "wiki" / "review-cycles" / "review-cycle-c1.json"
        ledger = self.memory / "wiki" / "review-cycles" / "promotion-ledger.json"
        completed = MODULE.complete_actions(
            cycle, ledger, [record["candidate_id"]], "2026-06-21", "Review Cycle validation passed."
        )
        self.assertEqual(completed["completed"], [record["candidate_id"]])
        completed_cycle = json.loads(cycle.read_text(encoding="utf-8"))
        self.assertEqual(completed_cycle["candidate_results"][0]["state"], "completed")
        rerun = MODULE.process_review(
            weekly, [daily], self.memory, self.cognitive, dry_run=False
        )
        self.assertEqual(rerun["candidate_results"][0]["state"], "completed")
        self.assertEqual(rerun["actions"][0]["status"], "completed")
        source_dir = self.memory / "wiki" / "sources"
        source_dir.mkdir(exist_ok=True)
        monthly_daily = source_dir / "chatgpt-daily-report-2026-06-20.md"
        monthly_daily.write_text(source_text("2026-06-20", [record]), encoding="utf-8")
        result = MODULE.build_monthly_review(
            "2026-06", source_dir, cycle.parent, self.memory, dry_run=False
        )
        output = Path(result["output"]).read_text(encoding="utf-8")
        self.assertIn("## 未完成行动\n\n- 未记录。", output)

    def test_monthly_review_includes_uncovered_daily_and_is_idempotent(self) -> None:
        record1 = candidate(
            candidate_type="project_state",
            domain="project",
            claim="Project entered validation",
            source_date="2026-06-20",
            sessions=["S01"],
            target="project",
            assertion="explicit",
        )
        record2 = candidate(
            candidate_type="observation",
            domain="workflow",
            claim="An uncovered month-end report exists",
            source_date="2026-06-30",
            sessions=["S01"],
            target="cognitive_observation",
        )
        source_dir = self.memory / "wiki" / "sources"
        daily_dir = source_dir / "conversations" / "chatgpt-daily" / "2026"
        daily_dir.mkdir(parents=True)
        daily1 = daily_dir / "chatgpt-daily-report-2026-06-20.md"
        daily1.write_text(source_text("2026-06-20", [record1]), encoding="utf-8")
        daily2 = daily_dir / "chatgpt-daily-report-2026-06-30.md"
        daily2.write_text(source_text("2026-06-30", [record2]), encoding="utf-8")
        legacy = daily_dir / "chatgpt-daily-report-2026-06-29.md"
        legacy.write_text(
            """---
type: chatgpt_daily_report
date: 2026-06-29
coverage: partial
---

# Legacy compiled summary without structured candidates
""",
            encoding="utf-8",
        )
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        MODULE.process_review(
            weekly, [daily1], self.memory, self.cognitive, dry_run=False
        )
        cycle_dir = self.memory / "wiki" / "review-cycles"
        first = MODULE.build_monthly_review(
            "2026-06", source_dir, cycle_dir, self.memory, dry_run=False
        )
        second = MODULE.build_monthly_review(
            "2026-06", source_dir, cycle_dir, self.memory, dry_run=False
        )
        self.assertEqual(first["uncovered_daily_reports"], ["2026-06-29", "2026-06-30"])
        self.assertEqual(first["source_migration_issues"][0]["date"], "2026-06-29")
        self.assertEqual(second["write"], "unchanged")
        output = Path(first["output"]).read_text(encoding="utf-8")
        self.assertIn("未被复盘周期覆盖的日报：2026-06-29、2026-06-30", output)
        self.assertIn("来源迁移或验证问题", output)

    def test_monthly_review_discovers_both_daily_source_families(self) -> None:
        chatgpt_record = candidate(
            candidate_type="knowledge",
            domain="deposition",
            claim="ChatGPT evidence remains in its own raw family",
            source_date="2026-08-01",
            sessions=["S01"],
            target="knowledge",
        )
        codex_record = candidate(
            candidate_type="project_state",
            domain="deposition",
            claim="Codex daily deposition entered validation",
            source_date="2026-08-02",
            sessions=["T01"],
            target="project",
            assertion="explicit",
        )
        source_dir = self.memory / "wiki" / "sources"
        chatgpt_dir = source_dir / "conversations" / "chatgpt-daily" / "2026"
        codex_dir = source_dir / "conversations" / "codex-daily" / "2026"
        chatgpt_dir.mkdir(parents=True)
        codex_dir.mkdir(parents=True)
        (chatgpt_dir / "chatgpt-daily-report-2026-08-01.md").write_text(
            source_text("2026-08-01", [chatgpt_record]), encoding="utf-8"
        )
        (codex_dir / "codex-daily-report-2026-08-02.md").write_text(
            codex_source_text("2026-08-02", [codex_record]), encoding="utf-8"
        )
        MODULE.process_daily_source(
            codex_dir / "codex-daily-report-2026-08-02.md",
            self.memory,
            self.cognitive,
            dry_run=False,
        )
        cycle_dir = self.memory / "wiki" / "review-cycles"
        result = MODULE.build_monthly_review(
            "2026-08", source_dir, cycle_dir, self.memory, dry_run=False
        )
        self.assertEqual(result["uncovered_daily_reports"], ["2026-08-01", "2026-08-02"])
        self.assertEqual(result["daily_source_counts"]["chatgpt"], 1)
        self.assertEqual(result["daily_source_counts"]["codex"], 1)
        self.assertEqual(result["source_migration_issues"], [])

    def test_monthly_review_renders_work_and_life_cases_with_timeline(self) -> None:
        source_dir = self.memory / "wiki" / "sources"
        source_dir.mkdir(parents=True)
        source = source_dir / "chatgpt-daily-report-2026-06-20.md"
        source.write_text(source_text("2026-06-20", []), encoding="utf-8")
        source_ref = "wiki/sources/chatgpt-daily-report-2026-06-20"
        work_title = "Extraction workflow validation"
        life_title = "Hotel preference review"
        manifest = {
            "version": 1,
            "month": "2026-06",
            "cases": [
                {
                    "case_id": MODULE.stable_case_id("work", work_title),
                    "title": work_title,
                    "line": "work",
                    "status": "in_progress",
                    "evidence_mode": "bounded_partial",
                    "summary": "Validate a repeatable extraction workflow.",
                    "current_progress": "The first validation cycle passed.",
                    "progress_basis": "reported",
                    "timeline_scope": "2026-06-20 through month end.",
                    "evidence_boundary": "Only the cited source is included.",
                    "timeline": [
                        {
                            "date": "2026-06-20",
                            "event": "Completed the first validation cycle.",
                            "source": source_ref,
                            "evidence_type": "reported",
                        }
                    ],
                    "lessons": [
                        {
                            "text": "Stable IDs make reruns auditable.",
                            "source": source_ref,
                            "evidence_type": "inferred",
                        }
                    ],
                    "open_items": [
                        {"text": "Run a second validation cycle.", "source": source_ref}
                    ],
                },
                {
                    "case_id": MODULE.stable_case_id("life", life_title),
                    "title": life_title,
                    "line": "life",
                    "status": "completed",
                    "evidence_mode": "bounded_partial",
                    "summary": "Review a hotel experience.",
                    "current_progress": "Review completed.",
                    "progress_basis": "reported",
                    "timeline_scope": "2026-06-20.",
                    "evidence_boundary": "One bounded source.",
                    "timeline": [
                        {
                            "date": "2026-06-20",
                            "event": "Recorded the hotel comparison.",
                            "source": source_ref,
                            "evidence_type": "reported",
                        }
                    ],
                    "lessons": [],
                    "open_items": [],
                },
            ],
        }
        reviews = self.memory / "wiki" / "reviews"
        reviews.mkdir(parents=True)
        manifest_path = reviews / "monthly-case-manifest-2026-06.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        cycle_dir = self.memory / "wiki" / "review-cycles"
        cycle_dir.mkdir(parents=True)
        result = MODULE.build_monthly_review(
            "2026-06", source_dir, cycle_dir, self.memory, dry_run=False
        )
        self.assertEqual(result["work_case_count"], 1)
        self.assertEqual(result["life_case_count"], 1)
        self.assertEqual(result["unfinished_case_count"], 1)
        output = Path(result["output"]).read_text(encoding="utf-8")
        self.assertIn("## 工作线", output)
        self.assertIn("## 生活线", output)
        self.assertIn("#### 时间线", output)
        self.assertIn("Run a second validation cycle.", output)

    def test_case_continuation_reuses_id_and_carries_prior_timeline(self) -> None:
        source_dir = self.memory / "wiki" / "sources"
        source_dir.mkdir(parents=True)
        source = source_dir / "case-source.md"
        source.write_text("# source\n", encoding="utf-8")
        source_ref = "wiki/sources/case-source"
        reviews = self.memory / "wiki" / "reviews"
        reviews.mkdir(parents=True)
        title = "Continuing case"
        case_id = MODULE.stable_case_id("work", title)
        prior_event = {
            "date": "2026-05-20",
            "event": "Started the case.",
            "source": source_ref,
            "evidence_type": "reported",
        }
        base = {
            "case_id": case_id,
            "title": title,
            "line": "work",
            "status": "in_progress",
            "evidence_mode": "bounded_partial",
            "summary": "A continuing case.",
            "current_progress": "Work continues.",
            "progress_basis": "reported",
            "timeline_scope": "2026-05 onward.",
            "evidence_boundary": "Cited evidence only.",
            "timeline": [prior_event],
            "lessons": [],
            "open_items": [],
        }
        prior = {"version": 1, "month": "2026-05", "cases": [base]}
        (reviews / "monthly-case-manifest-2026-05.json").write_text(
            json.dumps(prior), encoding="utf-8"
        )
        current_case = dict(base)
        current_case["timeline"] = [
            prior_event,
            {
                "date": "2026-06-20",
                "event": "Continued the case.",
                "source": source_ref,
                "evidence_type": "reported",
            },
        ]
        current_case["prior_links"] = [
            {
                "month": "2026-05",
                "case_id": case_id,
                "relation": "continuation",
                "note": "Continues the same case.",
            }
        ]
        current_path = reviews / "monthly-case-manifest-2026-06.json"
        current_path.write_text(
            json.dumps({"version": 1, "month": "2026-06", "cases": [current_case]}),
            encoding="utf-8",
        )
        loaded = MODULE.load_case_manifest(current_path, "2026-06", self.memory)
        self.assertEqual(loaded[0]["prior_links"][0]["relation"], "continuation")
        current_case["timeline"] = current_case["timeline"][1:]
        current_path.write_text(
            json.dumps({"version": 1, "month": "2026-06", "cases": [current_case]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.PipelineError, "carry the prior timeline"):
            MODULE.load_case_manifest(current_path, "2026-06", self.memory)

    def test_stale_work_case_must_archive_and_is_not_unfinished(self) -> None:
        source_dir = self.memory / "wiki" / "sources"
        source_dir.mkdir(parents=True)
        source = source_dir / "case-source.md"
        source.write_text("# source\n", encoding="utf-8")
        title = "Stale work case"
        case = {
            "case_id": MODULE.stable_case_id("work", title),
            "title": title,
            "line": "work",
            "status": "in_progress",
            "evidence_mode": "compliance_abstracted",
            "summary": "A stale work case.",
            "current_progress": "No recent update.",
            "progress_basis": "reported",
            "timeline_scope": "Last update in June.",
            "evidence_boundary": "Compliance-abstracted evidence.",
            "timeline": [
                {
                    "date": "2026-06-20",
                    "event": "Last recorded update.",
                    "source": "wiki/sources/case-source",
                    "evidence_type": "reported",
                }
            ],
            "lessons": [],
            "open_items": [],
        }
        reviews = self.memory / "wiki" / "reviews"
        reviews.mkdir(parents=True)
        manifest = reviews / "monthly-case-manifest-2026-09.json"
        manifest.write_text(
            json.dumps({"version": 1, "month": "2026-09", "cases": [case]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.PipelineError, "must be archived"):
            MODULE.load_case_manifest(manifest, "2026-09", self.memory)
        case["status"] = "archived"
        manifest.write_text(
            json.dumps({"version": 1, "month": "2026-09", "cases": [case]}),
            encoding="utf-8",
        )
        loaded = MODULE.load_case_manifest(manifest, "2026-09", self.memory)
        self.assertEqual(loaded[0]["inactivity_months"], 3)

    def test_planned_codex_case_can_reference_core_memory(self) -> None:
        wiki = self.memory / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "projects.md").write_text("# Projects\n", encoding="utf-8")
        title = "Hermes health assessment development"
        case = {
            "case_id": MODULE.stable_case_id("work", title),
            "title": title,
            "line": "work",
            "status": "planned",
            "evidence_mode": "direct_source",
            "summary": "Develop a new Hermes assessment capability.",
            "current_progress": "Planned but not implemented.",
            "progress_basis": "reported",
            "timeline_scope": "2026-07-01.",
            "evidence_boundary": "Project decision only; no implementation yet.",
            "timeline": [
                {
                    "date": "2026-07-01",
                    "event": "Recorded the planned project.",
                    "source": "wiki/projects",
                    "evidence_type": "reported",
                }
            ],
            "lessons": [],
            "open_items": [
                {"text": "Design and implement the capability.", "source": "wiki/projects"}
            ],
        }
        reviews = wiki / "reviews"
        reviews.mkdir(parents=True)
        manifest = reviews / "monthly-case-manifest-2026-07.json"
        manifest.write_text(
            json.dumps({"version": 1, "month": "2026-07", "cases": [case]}),
            encoding="utf-8",
        )
        loaded = MODULE.load_case_manifest(manifest, "2026-07", self.memory)
        self.assertEqual(loaded[0]["status"], "planned")

    def test_revoke_preserves_page_and_blocks_automatic_restore(self) -> None:
        record = candidate(
            candidate_type="preference",
            domain="workflow",
            claim="Prefer compact status updates",
            source_date="2026-06-20",
            sessions=["S01"],
            target="preference",
            assertion="explicit",
        )
        daily = self.write_source("daily.md", "2026-06-20", [record])
        weekly = self.write_review("C1", "published", "2026-06-21T09:00:00+08:00")
        MODULE.process_review(weekly, [daily], self.memory, self.cognitive, dry_run=False)
        ledger = self.memory / "wiki" / "review-cycles" / "promotion-ledger.json"
        revoked = MODULE.revoke_candidate(
            ledger, record["candidate_id"], "User withdrew the preference", "2026-06-22"
        )
        self.assertEqual(revoked["state"], "revoked")
        page = self.memory / "wiki" / "auto-promotions" / "preference" / f"{record['candidate_id']}.md"
        self.assertIn("status: revoked", page.read_text(encoding="utf-8"))
        weekly2 = self.write_review("C2", "published", "2026-06-23T09:00:00+08:00")
        rerun = MODULE.process_review(weekly2, [daily], self.memory, self.cognitive, dry_run=False)
        self.assertEqual(rerun["candidate_results"][0]["state"], "manual_confirmation")

    def test_superseded_decision_preserves_history_and_links_successor(self) -> None:
        old = candidate(
            candidate_type="decision",
            domain="workflow",
            claim="Use fixed calendar weeks",
            source_date="2026-06-20",
            sessions=["S01"],
            target="decision",
            assertion="explicit",
            adoption_evidence="User explicitly adopted fixed weeks at the time.",
        )
        new = candidate(
            candidate_type="decision",
            domain="workflow",
            claim="Use Review Cycle boundaries",
            source_date="2026-06-20",
            sessions=["S02"],
            target="decision",
            assertion="explicit",
            adoption_evidence="User explicitly replaced fixed weeks with Review Cycles.",
        )
        daily = self.write_source("daily.md", "2026-06-20", [old, new])
        MODULE.process_daily_source(daily, self.memory, self.cognitive, dry_run=False)
        ledger = self.memory / "wiki" / "review-cycles" / "promotion-ledger.json"
        result = MODULE.supersede_decision(
            ledger,
            old["candidate_id"],
            new["candidate_id"],
            "User adopted the successor decision",
            "2026-06-21",
        )
        self.assertEqual(result["state"], "superseded")
        page = self.memory / "wiki" / "auto-promotions" / "decision" / f"{old['candidate_id']}.md"
        text = page.read_text(encoding="utf-8")
        self.assertIn("status: superseded", text)
        self.assertIn(f"superseded_by: \"{new['candidate_id']}\"", text)

    def test_all_public_shared_state_writers_take_the_deposition_lock(self) -> None:
        lock = mock.Mock(side_effect=lambda: nullcontext())
        with (
            mock.patch.object(MODULE, "exclusive_deposition_write_lock", lock),
            mock.patch.object(MODULE, "_process_daily_source_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_process_review_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_confirm_actions_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_complete_actions_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_build_monthly_review_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_revoke_candidate_unlocked", return_value={"ok": True}),
            mock.patch.object(MODULE, "_supersede_decision_unlocked", return_value={"ok": True}),
        ):
            MODULE.process_daily_source(Path("daily"), Path("memory"), Path("cognitive"), dry_run=False)
            MODULE.process_review(Path("weekly"), [], Path("memory"), Path("cognitive"), dry_run=False)
            MODULE.confirm_actions(Path("cycle"), ["cand-a"])
            MODULE.complete_actions(Path("cycle"), Path("ledger"), ["cand-a"], "2026-08-30", "done")
            MODULE.build_monthly_review("2026-08", Path("sources"), Path("cycles"), Path("memory"), dry_run=False)
            MODULE.revoke_candidate(Path("ledger"), "cand-a", "reason", "2026-08-30")
            MODULE.supersede_decision(Path("ledger"), "cand-a", "cand-b", "reason", "2026-08-30")
        self.assertEqual(lock.call_count, 7)


if __name__ == "__main__":
    unittest.main()
