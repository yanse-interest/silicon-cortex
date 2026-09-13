from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from dashboard_model import (
    CaseRegistry,
    _candidate_id,
    bootstrap_registry,
    build_inbox_review_units,
    build_project_evidence_units,
    build_latest_daily_progress,
    looks_like_confidential_work,
    plan_inbox_convergence,
    plan_inbox_suggestions,
    parse_codex_daily_source,
    parse_daily_source,
    parse_explicit_value_candidates,
)


ENGLISH_DAILY = """---
type: chatgpt_daily_source_summary
date: 2026-08-01
coverage: partial
status: access_incomplete
raw_source: raw/example.md
---
# Daily

## Session Inventory

- S01 - `5C充电解释`: explanation of battery C-rate meaning.
- S02 — `UPLC基线下降问题分析`: troubleshooting a falling UPLC baseline after a method run.

## Cross-session Summary

Do not parse this aggregate.
"""


CHINESE_DAILY = """---
type: chatgpt_daily_source_summary
date: 2026-08-02
coverage: partial
status: access_incomplete
raw_source: raw/example-zh.md
---
# 日报

## 已报告事实

- S01 `硫醚环肽碎裂问题`：CE ramp 后仍未完全碎裂，需要继续排查。

## 决策、项目状态与行动项

- 这条 source-level action 不应成为 session event。
"""


KNOWLEDGE_DAILY = """---
type: chatgpt_daily_source_summary
date: 2026-08-03
coverage: partial
status: access_incomplete
raw_source: raw/knowledge-example.md
---
# 日报

## Session Inventory

- S01 — `SIM与MRM区别`: instrument acquisition-mode learning.

## Instrument Knowledge Candidates

```json
[
  {
    "knowledge_id": "instrument-knowledge-sim-mrm-20260803",
    "topic": "采集模式",
    "summary": "SIM 与 MRM 的选择性不同。",
    "question": "SIM 和 MRM 为什么具有不同的选择性？",
    "answer": "SIM 只监测选定的质荷比，而 MRM 同时限定前体离子和产物离子的跃迁关系，因此通常能提供更高的结构选择性，但仍需标准和色谱行为验证。",
    "instrument_types": ["质谱", "三重四极杆质谱"],
    "knowledge_status": "reusable",
    "confidence": "bounded_synthesis",
    "privacy_mode": "non_reconstructable",
    "evidence_boundary": "通用原理；具体实现以型号和软件版本为准。",
    "evidence_refs": [
      {
        "source_type": "daily_report",
        "session_id": "S01",
        "session_title": "SIM与MRM区别",
        "support_level": "source_summary",
        "excerpt": "日报记录 SIM 监测指定一级 m/z，MRM 监测前体到产物离子的跃迁。"
      }
    ],
    "related_projects": ["sequencing", "CAAA"]
  }
]
```
"""


CODEX_DAILY = """---
type: codex_daily_source_summary
date: 2026-08-01
coverage: partial
status: access_incomplete
raw_source: raw/codex-example.md
---
# Codex Daily

## 任务摘要

### T01 — 归档 ChatGPT 日报

- 目标：把日报结果沉淀到项目知识层。
- 状态：completed
- 结果：日报已归档，幂等验证通过。
- 下一步：等待下一次日报增量。
- 证据：`wiki/source.md`；`validate` 通过。
- 边界：只代表可访问任务。
"""


CODEX_DAILY_WITH_PROJECT = CODEX_DAILY.replace(
    "- 证据：`wiki/source.md`；`validate` 通过。",
    "- 来源线程 ID：thread-project\n"
    "- Codex 项目 ID：project-authoritative\n"
    "- 工作目录：/worktrees/outside-saved-project\n"
    "- 项目身份来源：thread_project_id\n"
    "- 证据：`wiki/source.md`；`validate` 通过。",
)


CURRENT_CODEX_DAILY = """---
type: codex_daily_source_summary
date: 2026-08-21
coverage: partial
status: access_incomplete
raw_source: raw/conversations/codex-daily/2026/codex-daily-report-2026-08-21.md
---
# Codex Daily

## Cross-task Summary

- T01 — 日报沉淀兼容 — completed — 当前格式的正式任务结果已解析并验证。
- T02 — 汇总但不猜测 — blocked — 缺少正式任务证据，因此保持阻塞边界。
- T01 — 重复行不应复制事件 — completed — 同一个稳定任务 ID 只保留一个事件。

## Actions and Open Items

- T02 — 后续动作：等待正式证据。
"""


class DashboardModelTests(unittest.TestCase):
    def test_latest_daily_progress_uses_all_routed_daily_events_only(self) -> None:
        events = [
            {"event_id": "codex-live:newer", "date": "2026-09-09", "detail": "live preview", "source_kind": "codex_live", "state": "routed"},
            {"event_id": "codex-daily:2026-09-08:T02", "date": "2026-09-08", "task_id": "T02", "title": "第二项", "detail": "第二项进展", "source_kind": "codex_daily", "state": "routed", "task_status": "completed"},
            {"event_id": "codex-daily:2026-09-08:T02", "date": "2026-09-08", "task_id": "T02", "title": "第二项", "detail": "第二项进展", "source_kind": "codex_daily", "state": "routed", "task_status": "completed"},
            {"event_id": "codex-daily:2026-09-08:T01", "date": "2026-09-08", "task_id": "T01", "title": "第一项", "detail": "第一项进展", "source_kind": "codex_daily", "state": "routed", "task_status": "completed"},
            {"event_id": "codex-daily:2026-09-08:T03", "date": "2026-09-08", "detail": "未归属", "source_kind": "codex_daily", "state": "inbox", "association_kind": "suggested"},
            {"event_id": "daily:linked", "date": "2026-09-08", "detail": "仅相关", "source_kind": "daily", "state": "linked"},
            {"event_id": "codex-daily:2026-09-07:T01", "date": "2026-09-07", "detail": "旧进展", "source_kind": "codex_daily", "state": "routed"},
        ]

        progress = build_latest_daily_progress(events)

        self.assertEqual(progress["date"], "2026-09-08")
        self.assertEqual([item["event_id"] for item in progress["items"]], [
            "codex-daily:2026-09-08:T01",
            "codex-daily:2026-09-08:T02",
        ])
        self.assertEqual(build_latest_daily_progress(list(reversed(events))), progress)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wiki = self.root / "wiki"
        self.reviews = self.wiki / "reviews"
        self.daily = self.wiki / "sources/conversations/chatgpt-daily/2026"
        self.reviews.mkdir(parents=True)
        self.daily.mkdir(parents=True)
        self.manifest_path = self.reviews / "monthly-case-manifest-2026-07.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "month": "2026-07",
                    "cases": [
                        {
                            "case_id": "case-c15a153c9e934f8e",
                            "title": "复杂修饰多肽与硫醚环肽氧化辅助解序",
                            "line": "work",
                            "status": "in_progress",
                            "current_progress": "仍在解序",
                            "timeline": [
                                {
                                    "date": "2026-07-31",
                                    "event": "完成一次 CE ramp。",
                                    "source": "wiki/source-a",
                                    "evidence_type": "reported",
                                }
                            ],
                            "open_items": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.registry_path = self.wiki / "project-dashboard-case-registry.json"
        self.store = CaseRegistry(
            wiki_root=self.wiki,
            registry_path=self.registry_path,
            reviews_root=self.reviews,
            daily_root=self.wiki / "sources/conversations/chatgpt-daily",
            live_state_path=self.root / "missing-live-state.json",
        )

    def _write_codex_source(self, text: str) -> Path:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_inbox_projection_clusters_evidence_without_deletion_or_project_creation(self) -> None:
        events = [
            {"event_id": "live-1", "source_kind": "codex_live", "thread_id": "thread-a", "date": "2026-09-01", "title": "Codex 任务", "detail": "中间状态", "live_routing_reason": "unknown_project"},
            {"event_id": "live-2", "source_kind": "codex_live", "thread_id": "thread-a", "date": "2026-09-02", "title": "Codex 任务", "detail": "最终状态", "live_routing_reason": "unknown_project"},
            {"event_id": "live-3", "source_kind": "codex_live", "thread_id": "thread-b", "date": "2026-09-02", "title": "Codex 任务", "detail": "存在冲突", "live_routing_reason": "mapping_conflict"},
            {"event_id": "daily-1", "source_kind": "daily", "date": "2026-09-01", "title": "一次性问答", "detail": "参考材料", "event_kind": "case_candidate"},
            {"event_id": "codex-1", "source_kind": "codex_daily", "date": "2026-09-01", "title": "日报归档", "detail": "第一天"},
            {"event_id": "codex-2", "source_kind": "codex_daily", "date": "2026-09-02", "title": "日报归档", "detail": "第二天"},
        ]
        before = json.loads(json.dumps(events))
        review_units, reference_units = build_inbox_review_units(events)
        self.assertEqual(events, before)
        self.assertEqual(len(review_units), 1)
        self.assertEqual(review_units[0]["representative_event_id"], "live-3")
        self.assertEqual(review_units[0]["action_scope"], "representative_only")
        self.assertEqual(len(reference_units), 3)
        self.assertEqual(sum(unit["member_count"] for unit in review_units + reference_units), len(events))
        self.assertEqual(next(unit for unit in reference_units if unit["representative_event_id"] == "live-2")["member_count"], 2)
        self.assertFalse(any("case_id" in unit and unit["case_id"] for unit in review_units + reference_units))

    def test_unresolved_codex_parent_metadata_never_becomes_user_routing_review(self) -> None:
        events = [
            {
                "event_id": "unknown-id",
                "source_kind": "codex_daily",
                "state": "inbox",
                "title": "Codex 任务",
                "project_metadata_present": True,
                "codex_project_id": "unknown-project",
                "candidate_eligible": True,
                "suggested_case_ids": ["case-c15a153c9e934f8e"],
                "routing_reason": "unmapped_codex_project_id",
            },
            {
                "event_id": "id-conflict",
                "source_kind": "codex_live",
                "state": "inbox",
                "thread_id": "thread-conflict",
                "title": "Codex 任务",
                "project_metadata_present": True,
                "codex_project_id": "conflicting-project",
                "routing_reason": "mapping_conflict",
            },
        ]
        review_units, reference_units = build_inbox_review_units(events)
        self.assertEqual(review_units, [])
        self.assertEqual(len(reference_units), 2)
        self.assertTrue(all(unit["reference_only"] for unit in reference_units))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_english_and_redact_confidential_work(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        events = parse_daily_source(path, self.wiki)
        self.assertEqual([event["event_id"] for event in events], ["chatgpt-daily:2026-08-01:S01", "chatgpt-daily:2026-08-01:S02"])
        self.assertEqual(events[0]["state"], "reference")
        self.assertEqual(events[1]["state"], "compliance_review")
        self.assertEqual(events[1]["title"], "实验工作内容（待合规抽象）")
        self.assertNotIn("UPLC", events[1]["detail"])

    def test_parse_chinese_session_fact(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-02.md"
        path.write_text(CHINESE_DAILY, encoding="utf-8")
        events = parse_daily_source(path, self.wiki)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session_id"], "S01")
        self.assertEqual(events[0]["state"], "compliance_review")

    def test_current_chatgpt_summary_uses_exact_session_ids_without_duplicates(self) -> None:
        raw = self.root / "raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-21.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("""---
type: chatgpt_daily_report
date: 2026-08-21
---
## 会话摘要

### S01 — 日报沉淀兼容
**我们聊了什么：** 验证当前日报格式。
**结论：** 正式会话摘要可作为有界证据。
""", encoding="utf-8")
        path = self.daily / "chatgpt-daily-report-2026-08-21.md"
        path.write_text("""---
type: chatgpt_daily_source_summary
date: 2026-08-21
coverage: partial
status: access_incomplete
raw_source: raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-21.md
---
## 跨会话摘要
- S01：正式的当前格式摘要。
- S01：重复记录最终仍只有一个稳定事件。
- S01–S02：范围摘要不能伪造独立事件。
""", encoding="utf-8")

        events = parse_daily_source(path, self.wiki)

        self.assertEqual([item["event_id"] for item in events], ["chatgpt-daily:2026-08-21:S01"])
        self.assertEqual(events[0]["date"], "2026-08-21")
        self.assertEqual(events[0]["source"], "sources/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-21.md")
        self.assertEqual(events[0]["detail"], "重复记录最终仍只有一个稳定事件。")

    def test_current_chatgpt_raw_fallback_requires_exact_attestation(self) -> None:
        raw = self.root / "raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-20.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("""---
type: chatgpt_daily_report
date: 2026-08-20
---
## 会话摘要

### S01 — 日报解析
**我们聊了什么：** 检查正式会话摘要。
**结论：** 可确定性提取项目事件。

### S02 — UPLC 样品批次 B-7
**我们聊了什么：** 排查具体实验结果。
**结论：** 保持实验隐私边界。
""", encoding="utf-8")
        path = self.daily / "chatgpt-daily-report-2026-08-20.md"
        source = """---
type: chatgpt_daily_source_summary
date: 2026-08-20
coverage: partial
status: access_incomplete
raw_source: raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-08-20.md
---
## 摘要
仅有聚合摘要。
"""
        path.write_text(source, encoding="utf-8")

        events = parse_daily_source(path, self.wiki)

        self.assertEqual([item["event_id"] for item in events], [
            "chatgpt-daily:2026-08-20:S01", "chatgpt-daily:2026-08-20:S02",
        ])
        self.assertEqual(events[1]["state"], "compliance_review")
        self.assertNotIn("B-7", json.dumps(events[1], ensure_ascii=False))
        path.write_text(source.replace("2026-08-20.md", "2026-08-19.md"), encoding="utf-8")
        self.assertEqual(parse_daily_source(path, self.wiki), [])

    def test_parse_reusable_instrument_knowledge_as_capability_not_progress(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-03.md"
        path.write_text(KNOWLEDGE_DAILY, encoding="utf-8")
        events = parse_daily_source(path, self.wiki)
        knowledge = next(event for event in events if event.get("event_kind") == "instrument_knowledge")
        self.assertEqual(knowledge["state"], "capability_item")
        self.assertEqual(knowledge["capability_domain_id"], "instrument_methodology")
        self.assertEqual(knowledge["related_project_ids"], ["project-sequencing", "project-caaa"])
        self.assertFalse(knowledge["progress_node"])
        self.assertTrue(knowledge["question"].endswith("？"))
        self.assertGreater(len(knowledge["answer"]), 30)
        self.assertEqual(knowledge["instrument_types"], ["质谱", "三重四极杆质谱"])
        self.assertEqual(knowledge["answer_status"], "source_grounded")
        self.assertEqual(knowledge["answer_origin"], "daily_report")
        self.assertEqual(knowledge["evidence_refs"][0]["session_id"], "S01")

    def test_instrument_knowledge_without_valid_instrument_type_is_rejected(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-03.md"
        path.write_text(KNOWLEDGE_DAILY.replace('["质谱", "三重四极杆质谱"]', '["未知设备"]'), encoding="utf-8")
        events = parse_daily_source(path, self.wiki)
        self.assertFalse(any(event.get("event_kind") == "instrument_knowledge" for event in events))

    def test_instrument_knowledge_without_source_evidence_is_rejected(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-03.md"
        without_refs = re.sub(r'\n    "evidence_refs": \[.*?\n    \],', '', KNOWLEDGE_DAILY, flags=re.S)
        path.write_text(without_refs, encoding="utf-8")
        events = parse_daily_source(path, self.wiki)
        self.assertFalse(any(event.get("event_kind") == "instrument_knowledge" for event in events))

    def test_capability_repair_merges_legacy_item_without_stable_id(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-03.md"
        source_without_id = KNOWLEDGE_DAILY.replace('    "knowledge_id": "instrument-knowledge-sim-mrm-20260803",\n', '')
        path.write_text(source_without_id, encoding="utf-8")
        parsed = next(event for event in parse_daily_source(path, self.wiki) if event.get("event_kind") == "instrument_knowledge")
        registry = self.store.refresh()
        legacy = dict(parsed)
        legacy["event_id"] = "instrument-knowledge-legacy-hash"
        legacy["created_at"] = "2026-08-02T00:00:00+08:00"
        legacy.pop("answer_status")
        legacy.pop("answer_origin")
        legacy["evidence_refs"] = []
        registry["events"].append(legacy)
        self.store.save(registry)

        refreshed = self.store.refresh()
        items = [event for event in refreshed["events"] if event.get("state") == "capability_item"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["event_id"], "instrument-knowledge-legacy-hash")
        self.assertEqual(items[0]["answer_status"], "source_grounded")
        self.assertTrue(items[0]["evidence_refs"])

    def test_duplicate_capability_item_is_retired_from_current_projection(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-03.md"
        path.write_text(KNOWLEDGE_DAILY, encoding="utf-8")
        canonical = next(event for event in parse_daily_source(path, self.wiki) if event.get("event_kind") == "instrument_knowledge")
        duplicate = dict(canonical)
        duplicate["event_id"] = "instrument-knowledge-duplicate"
        duplicate["created_at"] = "2026-08-30T00:00:00+08:00"
        registry = self.store.refresh()
        registry["events"].extend([canonical, duplicate])
        self.store.save(registry)

        refreshed = self.store.refresh()
        active = [event for event in refreshed["events"] if event.get("state") == "capability_item"]
        retired = next(event for event in refreshed["events"] if event.get("event_id") == "instrument-knowledge-duplicate")
        self.assertEqual(len(active), 1)
        self.assertEqual(retired["state"], "superseded_capability_item")
        self.assertEqual(retired["superseded_by"], active[0]["event_id"])

    def test_parse_codex_daily_task_outcome(self) -> None:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(CODEX_DAILY, encoding="utf-8")
        events = parse_codex_daily_source(path, self.wiki)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "codex-daily:2026-08-01:T01")
        self.assertEqual(events[0]["source_kind"], "codex_daily")
        self.assertEqual(events[0]["verification_refs"], ["wiki/source.md", "validate"])
        self.assertEqual(events[0]["goal"], "把日报结果沉淀到项目知识层。")
        self.assertEqual(events[0]["next_step"], "等待下一次日报增量。")
        self.assertEqual(events[0]["evidence_boundary"], "只代表可访问任务。")

    def test_parse_codex_parent_project_metadata(self) -> None:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(CODEX_DAILY_WITH_PROJECT, encoding="utf-8")
        event = parse_codex_daily_source(path, self.wiki)[0]
        self.assertTrue(event["project_metadata_present"])
        self.assertEqual(event["thread_id"], "thread-project")
        self.assertEqual(event["codex_project_id"], "project-authoritative")
        self.assertEqual(event["codex_cwd"], "/worktrees/outside-saved-project")

    def test_codex_project_id_precedes_cwd_and_keywords(self) -> None:
        registry = self.store.refresh()
        registry["cases"].append({
            "case_id": "case-other", "title": "Other", "line": "work",
            "category": "ai_automation", "status": "in_progress", "routing_terms": ["日报"],
        })
        registry["project_mappings"] = [
            {"codex_project_id": "project-authoritative", "case_id": "case-c15a153c9e934f8e"},
            {"path": "/worktrees", "case_id": "case-other"},
        ]
        self.store.save(registry)
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(CODEX_DAILY_WITH_PROJECT, encoding="utf-8")
        event = next(item for item in self.store.refresh()["events"] if item.get("task_id") == "T01")
        self.assertEqual(event["case_id"], "case-c15a153c9e934f8e")
        self.assertEqual(event["routing_reason"], "mapped_by_codex_project_id")
        self.assertEqual(event["suggested_case_ids"], [])

    def test_unknown_project_id_and_projectless_task_stay_reference_only(self) -> None:
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["日报"]
        registry["project_mappings"] = [
            {"path": "/worktrees/outside-saved-project", "case_id": "case-c15a153c9e934f8e"}
        ]
        unknown = parse_codex_daily_source(
            self._write_codex_source(CODEX_DAILY_WITH_PROJECT), self.wiki
        )[0]
        self.store._apply_codex_routing(registry, unknown, preserve_manual=False)
        self.assertEqual(unknown["state"], "inbox")
        self.assertEqual(unknown["routing_reason"], "unmapped_codex_project_id")
        self.assertEqual(unknown["suggested_case_ids"], [])
        review_units, reference_units = build_inbox_review_units([unknown])
        self.assertEqual(review_units, [])
        self.assertEqual(len(reference_units), 1)

        projectless_text = CODEX_DAILY_WITH_PROJECT.replace(
            "project-authoritative", "无"
        ).replace("/worktrees/outside-saved-project", "无").replace(
            "thread_project_id", "projectless"
        )
        projectless = parse_codex_daily_source(
            self._write_codex_source(projectless_text), self.wiki
        )[0]
        self.store._apply_codex_routing(registry, projectless, preserve_manual=False)
        self.assertEqual(projectless["state"], "inbox")
        self.assertEqual(projectless["routing_reason"], "projectless")
        self.assertEqual(projectless["suggested_case_ids"], [])

    def test_current_codex_cross_task_summary_is_exact_and_idempotent(self) -> None:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-21.md"
        path.parent.mkdir(parents=True)
        path.write_text(CURRENT_CODEX_DAILY, encoding="utf-8")

        events = parse_codex_daily_source(path, self.wiki)

        self.assertEqual([item["event_id"] for item in events], [
            "codex-daily:2026-08-21:T01", "codex-daily:2026-08-21:T02",
        ])
        self.assertEqual(events[0]["detail"], "同一个稳定任务 ID 只保留一个事件。")
        self.assertEqual(events[1]["task_status"], "blocked")
        self.assertEqual(events[1]["next_step"], "等待正式证据。")
        self.assertEqual(events[1]["source"], "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-21.md")

    def test_current_codex_aggregate_and_sensitive_details_are_bounded(self) -> None:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-22.md"
        path.parent.mkdir(parents=True)
        aggregate = CURRENT_CODEX_DAILY.replace("date: 2026-08-21", "date: 2026-08-22")
        aggregate = re.sub(r"- T01 .*?- T01 .*?事件。", "- 今日只有聚合总结，不能从标题或 preview 发明任务。", aggregate, flags=re.S)
        aggregate = aggregate.replace("- T02 — 后续动作：等待正式证据。", "")
        path.write_text(aggregate, encoding="utf-8")
        self.assertEqual(parse_codex_daily_source(path, self.wiki), [])

        path.write_text(CURRENT_CODEX_DAILY.replace("汇总但不猜测", "UPLC 样品批次 B-7"), encoding="utf-8")
        events = parse_codex_daily_source(path, self.wiki)
        sensitive = next(item for item in events if item["task_id"] == "T02")
        self.assertEqual(sensitive["state"], "compliance_review")
        self.assertNotIn("B-7", json.dumps(sensitive, ensure_ascii=False))

    def test_current_codex_raw_evidence_survives_cross_task_summary_and_routes_exactly(self) -> None:
        date = "2026-08-23"
        raw = self.root / f"raw/conversations/codex-daily/2026/codex-daily-report-{date}.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("""---
type: codex_daily_report
date: 2026-08-23
coverage: partial
status: access_incomplete
---
## 任务摘要与证据

### T01 — Dashboard 解析修复

**目标：** 修复项目日报关联。
**结果：** 已通过严格来源回读恢复逐任务证据。
**状态：** completed
**文件证据：** `/Users/shiba/Documents/codex/projects/dashboard/dashboard_model.py`。
**下一步：** 无。
**证据边界：** 只代表当前日报任务。

### T02 — 无项目定位的独立任务

**目标：** 完成一个独立任务。
**结果：** 已完成，但没有项目定位证据。
**状态：** completed
**文件证据：** 无。
**下一步：** 无。
**证据边界：** 不推断项目归属。
""", encoding="utf-8")
        source = self.wiki / f"sources/conversations/codex-daily/2026/codex-daily-report-{date}.md"
        source.parent.mkdir(parents=True)
        source.write_text(f"""---
type: codex_daily_source_summary
date: {date}
coverage: partial
status: access_incomplete
raw_source: raw/conversations/codex-daily/2026/codex-daily-report-{date}.md
---
## 综合摘要

- 只有聚合项目变化，不应从这里发明任务或项目归属。

## Cross-task Summary

- T01 — Dashboard 解析修复 — completed — 聚合行不应覆盖 raw 中的逐任务证据。
- T02 — 无项目定位的独立任务 — completed — 聚合行不应发明项目归属。
""", encoding="utf-8")
        registry = self.store.refresh()
        registry["project_mappings"] = [
            {"path": "/Users/shiba/Documents/codex/projects/dashboard", "case_id": "case-c15a153c9e934f8e"}
        ]
        self.store.save(registry)

        refreshed = self.store.refresh()
        events = {
            item["task_id"]: item
            for item in refreshed["events"]
            if item.get("date") == date and item.get("source_kind") == "codex_daily"
        }

        self.assertEqual(set(events), {"T01", "T02"})
        self.assertEqual(events["T01"]["verification_refs"], [
            "/Users/shiba/Documents/codex/projects/dashboard/dashboard_model.py"
        ])
        self.assertEqual(events["T01"]["detail"], "已通过严格来源回读恢复逐任务证据。")
        self.assertEqual(events["T01"]["state"], "routed")
        self.assertEqual(events["T01"]["case_id"], "case-c15a153c9e934f8e")
        self.assertEqual(events["T02"]["state"], "inbox")
        self.assertIsNone(events["T02"]["case_id"])

    def test_explicit_value_candidate_requires_formal_stable_low_risk_record(self) -> None:
        claim = "正式日报解析必须只依赖结构化来源。"
        candidate = {
            "candidate_id": _candidate_id("workflow", "日报解析", claim),
            "type": "workflow", "domain": "日报解析", "normalized_claim": claim,
            "source_date": "2026-08-21", "sessions": ["T01"], "coverage": "partial",
            "evidence_boundary": "只证明正式结构化来源中的复用规则。", "evidence_complete": True,
            "assertion": "explicit", "confidence": 0.9, "risk": "low",
            "suggested_target": "workflow", "status": "candidate", "conflicts_with": [],
        }
        text = "## Structured Candidates\n\n```json\n" + json.dumps([candidate], ensure_ascii=False) + "\n```"
        parsed = parse_explicit_value_candidates(
            text, date="2026-08-21", source="sources/codex.md", source_kind="codex_daily",
            source_status="ready", coverage="partial",
        )
        self.assertEqual([item["candidate_id"] for item in parsed], [candidate["candidate_id"]])
        ordinary_completion = {**candidate, "type": "project_state", "suggested_target": "project"}
        unsafe = {**candidate, "normalized_claim": "UPLC 样品批次 B-7 结果稳定"}
        wrong_date = {**candidate, "source_date": "2026-08-20"}
        for rejected in (ordinary_completion, unsafe, wrong_date):
            body = "## Structured Candidates\n\n```json\n" + json.dumps([rejected], ensure_ascii=False) + "\n```"
            self.assertEqual(parse_explicit_value_candidates(
                body, date="2026-08-21", source="sources/codex.md", source_kind="codex_daily",
                source_status="ready", coverage="partial",
            ), [])

    def test_explicit_value_deposition_is_routed_and_idempotent(self) -> None:
        claim = "日报沉淀兼容规则可稳定复用。"
        candidate = {
            "candidate_id": _candidate_id("workflow", "日报沉淀", claim),
            "type": "workflow", "domain": "日报沉淀", "normalized_claim": claim,
            "source_date": "2026-08-21", "sessions": ["T01"], "coverage": "partial",
            "evidence_boundary": "只证明该日报中的显式工作流结论。", "evidence_complete": True,
            "assertion": "explicit", "confidence": 0.9, "risk": "low",
            "suggested_target": "workflow", "status": "candidate", "conflicts_with": [],
        }
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["稳定任务 ID"]
        self.store.save(registry)
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-21.md"
        path.parent.mkdir(parents=True)
        path.write_text(CURRENT_CODEX_DAILY + "\n## Structured Candidates\n\n```json\n" + json.dumps([candidate], ensure_ascii=False) + "\n```\n", encoding="utf-8")

        first = self.store.refresh()
        second = self.store.refresh()
        case = second["cases"][0]

        self.assertEqual(len(first["value_candidates"]), 1)
        self.assertEqual(len(second["value_candidates"]), 1)
        self.assertEqual(second["value_candidates"][0]["state"], "accepted")
        self.assertEqual(len([item for item in case["value_items"] if item.get("candidate_id") == candidate["candidate_id"]]), 1)

    def test_codex_daily_evidence_path_routes_to_unique_project(self) -> None:
        registry = self.store.refresh()
        registry["project_mappings"] = [
            {"path": "/Users/shiba/Documents/codex/projects/dashboard", "case_id": "case-c15a153c9e934f8e"}
        ]
        self.store.save(registry)
        codex = CODEX_DAILY.replace("wiki/source.md", "projects/dashboard/dashboard_model.py")
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(codex, encoding="utf-8")
        event = next(item for item in self.store.refresh()["events"] if item.get("source_kind") == "codex_daily")
        self.assertEqual(event["state"], "routed")
        self.assertEqual(event["case_id"], "case-c15a153c9e934f8e")
        self.assertEqual(event["routing_reason"], "mapped_by_evidence")

    def test_codex_daily_can_route_one_cross_cutting_task_to_two_projects(self) -> None:
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["日报", "沉淀"]
        registry["cases"].append({
            "case_id": "case-router",
            "title": "账号路由治理",
            "line": "work",
            "category": "ai_automation",
            "status": "in_progress",
            "routing_terms": ["固定账号", "门禁"],
        })
        registry.setdefault("source_policy", {})["allow_multi_project_routing"] = True
        self.store.save(registry)
        codex = CODEX_DAILY.replace(
            "把日报结果沉淀到项目知识层。",
            "通过固定账号门禁把日报结果沉淀到项目知识层。",
        )
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(codex, encoding="utf-8")

        snapshot = self.store.snapshot()
        projects = {case["case_id"]: case for case in snapshot["all_cases"]}

        event_id = "codex-daily:2026-08-01:T01"
        self.assertEqual(sum(log["event_id"] == event_id for log in projects["case-c15a153c9e934f8e"]["logs"]), 1)
        self.assertEqual(sum(log["event_id"] == event_id for log in projects["case-router"]["logs"]), 1)
        event = next(log for log in projects["case-router"]["logs"] if log["event_id"] == event_id)
        self.assertEqual(event["routing_reason"], "multiple_keyword_matches")

    def test_codex_evidence_mapping_uses_most_specific_path(self) -> None:
        registry = self.store.refresh()
        registry["cases"].append({"case_id": "case-child", "title": "子项目", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["project_mappings"] = [
            {"path": "/Users/shiba/Documents/codex/projects/dashboard", "case_id": "case-c15a153c9e934f8e"},
            {"path": "/Users/shiba/Documents/codex/projects/dashboard/sub", "case_id": "case-child"},
        ]
        self.store.save(registry)
        codex = CODEX_DAILY.replace("wiki/source.md", "projects/dashboard/sub/model.py")
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(codex, encoding="utf-8")
        event = next(item for item in self.store.refresh()["events"] if item.get("source_kind") == "codex_daily")
        self.assertEqual(event["case_id"], "case-child")
        self.assertEqual(event["routing_reason"], "mapped_by_evidence")

    def test_codex_daily_redacts_identifiers_from_evidence_and_next_step(self) -> None:
        codex = CODEX_DAILY.replace("wiki/source.md", "/lab/sample/sample-ABC123/result.csv").replace(
            "等待下一次日报增量。", "继续处理 sample ABC123。"
        )
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(codex, encoding="utf-8")
        event = parse_codex_daily_source(path, self.wiki)[0]
        self.assertEqual(event["state"], "compliance_review")
        self.assertEqual(event["verification_refs"], [])
        self.assertEqual(event["next_step"], "")
        self.assertEqual(event["evidence_boundary"], "compliance_abstracted")
        self.assertNotIn("ABC123", json.dumps(event, ensure_ascii=False))

    def test_dashboard_workspace_evidence_is_not_misclassified_as_experimental(self) -> None:
        safe_path = "projects/automation/codex-project-dashboard/dashboard-mobile-entry.md"
        self.assertFalse(looks_like_confidential_work("更新项目看板入口", safe_path))
        self.assertFalse(
            looks_like_confidential_work(
                "project dashboard manual state",
                "项目人工进度与状态更新应写入独立时间线且移动 H5 保持只读",
            )
        )
        codex = CODEX_DAILY.replace("wiki/source.md", safe_path)
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(codex, encoding="utf-8")

        event = parse_codex_daily_source(path, self.wiki)[0]

        self.assertNotEqual(event["state"], "compliance_review")
        self.assertEqual(event["verification_refs"], [safe_path, "validate"])

    def test_source_owned_compliance_state_recovers_after_parser_correction(self) -> None:
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(CODEX_DAILY.replace("wiki/source.md", "ProjectA复核"), encoding="utf-8")
        first = next(item for item in self.store.refresh()["events"] if item.get("source_kind") == "codex_daily")
        self.assertEqual(first["state"], "compliance_review")
        self.assertEqual(first["route_origin"], "source")

        safe_path = "projects/automation/codex-project-dashboard/README.md"
        path.write_text(CODEX_DAILY.replace("wiki/source.md", safe_path), encoding="utf-8")
        second = next(item for item in self.store.refresh()["events"] if item.get("source_kind") == "codex_daily")

        self.assertEqual(second["state"], "inbox")
        self.assertNotEqual(second.get("routing_reason"), "source_compliance_review")
        self.assertEqual(second["verification_refs"], [safe_path, "validate"])

    def test_privacy_filter_catches_contiguous_identifiers_and_internal_urls(self) -> None:
        samples = [
            "customerA", "client_A", "客户A", "样品ABC", "batch123", "https://internal.example.local/path",
            "customerA部署", "batch123结果", "ProjectA复核", "CustomerA复核", "sample_ABC结果",
            "https://server.local/path", "http://172.16.1.2/private", "http://172.31.255.1/private", "smb://internal/share", "file:///private/result.txt",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(looks_like_confidential_work("普通任务", sample))
                codex = CODEX_DAILY.replace("wiki/source.md", sample)
                path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(codex, encoding="utf-8")
                event = parse_codex_daily_source(path, self.wiki)[0]
                self.assertEqual(event["state"], "compliance_review")
                self.assertNotIn(sample, json.dumps(event, ensure_ascii=False))

    def test_codex_privacy_scan_covers_all_persisted_task_fields(self) -> None:
        injections = {
            "title": ("归档 ChatGPT 日报", "customerA部署"),
            "goal": ("把日报结果沉淀到项目知识层。", "ProjectA复核"),
            "detail": ("日报已归档，幂等验证通过。", "batch123结果"),
            "next_step": ("等待下一次日报增量。", "http://172.16.1.2/private"),
            "evidence": ("wiki/source.md", "https://server.local/private"),
            "boundary": ("只代表可访问任务。", "smb://internal/share"),
        }
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        for field, (old, sensitive) in injections.items():
            with self.subTest(field=field):
                path.write_text(CODEX_DAILY.replace(old, sensitive), encoding="utf-8")
                event = parse_codex_daily_source(path, self.wiki)[0]
                self.assertEqual(event["state"], "compliance_review")
                self.assertNotIn(sensitive, json.dumps(event, ensure_ascii=False))

    def test_chatgpt_unique_project_candidate_auto_routes(self) -> None:
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["自动化部署"]
        self.store.save(registry)
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY.replace("UPLC基线下降问题分析", "自动化部署问题").replace("troubleshooting a falling UPLC baseline after a method run", "需要继续部署并验证自动化服务"), encoding="utf-8")
        event = next(item for item in self.store.refresh()["events"] if item["event_id"].endswith(":S02"))
        self.assertEqual(event["state"], "routed")
        self.assertEqual(event["case_id"], "case-c15a153c9e934f8e")

    def test_values_are_cumulative_across_monthly_manifests(self) -> None:
        older = self.reviews / "monthly-case-manifest-2026-06.json"
        older.write_text(json.dumps({"month": "2026-06", "cases": [{"case_id": "case-c15a153c9e934f8e", "lessons": [{"text": "旧月稳定价值。", "source": "wiki/old", "evidence_type": "reported"}]}]}, ensure_ascii=False), encoding="utf-8")
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["all_cases"][0]["value_count"], 1)
        self.assertEqual(snapshot["all_cases"][0]["value_items"][0]["detail"], "旧月稳定价值。")

    def test_same_value_from_two_sources_merges_evidence(self) -> None:
        older = self.reviews / "monthly-case-manifest-2026-06.json"
        older.write_text(json.dumps({"month": "2026-06", "cases": [{"case_id": "case-c15a153c9e934f8e", "lessons": [{"text": "稳定价值。", "source": "wiki/source-a"}]}]}, ensure_ascii=False), encoding="utf-8")
        current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        current["cases"][0]["lessons"] = [{"text": "稳定价值。", "source": "wiki/source-b"}]
        self.manifest_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        case = self.store.snapshot()["all_cases"][0]
        self.assertEqual(case["value_count"], 1)
        self.assertEqual(len(case["value_items"][0]["evidence_sources"]), 2)

    def test_partial_coverage_counts_sources_and_events_separately(self) -> None:
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["日报"]
        self.store.save(registry)
        second_task = CODEX_DAILY.split("### T01", 1)[1].replace(" —", "### T02 —", 1)
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(CODEX_DAILY + second_task, encoding="utf-8")
        case = self.store.snapshot()["all_cases"][0]
        codex_rollup = next(source for source in case["source_mix"] if source["family"] == "codex")
        self.assertEqual(codex_rollup["partial_source_count"], 1)
        self.assertEqual(codex_rollup["partial_event_count"], 2)
        self.assertIn("partial", codex_rollup["coverage_states"])

    def test_capability_items_are_included_in_value_total(self) -> None:
        registry = self.store.refresh()
        registry["capability_domains"] = [{"capability_domain_id": "cap-1", "title": "证据治理", "summary": "跨项目能力"}]
        registry["events"].append({"event_id": "cap-event-1", "state": "capability_item", "capability_domain_id": "cap-1", "detail": "保留证据边界", "date": "2026-08-01"})
        self.store.save(registry)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["summary"]["capability_value_count"], 1)
        self.assertEqual(snapshot["summary"]["value_item_count"], 1)

    def test_abstract_project_keeps_registry_values_and_related_questions(self) -> None:
        registry = self.store.refresh()
        project = registry["cases"][0]
        project["category"] = "experimental_work"
        project["value_items"] = [{
            "date": "2026-07",
            "detail": "只保存不可还原的抽象价值。",
            "source": "compliance_abstracted_from_prior_memory",
            "evidence_mode": "compliance_abstracted",
        }]
        registry["events"].append({
            "event_id": "question-1",
            "date": "2026-07",
            "detail": "继续积累证据边界。",
            "source": "compliance_abstracted_from_prior_memory",
            "source_kind": "compliance_abstracted",
            "state": "linked",
            "case_id": project["case_id"],
        })
        self.store.save(registry)

        result = self.store.snapshot()["all_cases"][0]

        self.assertEqual(result["category_label"], "实验工作")
        self.assertEqual(result["value_count"], 1)
        self.assertEqual(result["related_count"], 1)

    def test_snapshot_integrates_chatgpt_values_and_codex_progress_by_project(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["cases"][0]["lessons"] = [
            {"text": "稳定的项目沉淀需要证据边界。", "source": "wiki/sources/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-07-31", "evidence_type": "reported"}
        ]
        manifest["cases"][0]["open_items"] = [{"text": "补一次验证。", "source": "wiki/source-a"}]
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        registry = self.store.refresh()
        registry["cases"][0]["routing_terms"] = ["日报"]
        self.store.save(registry)
        codex_path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        codex_path.parent.mkdir(parents=True)
        codex_path.write_text(CODEX_DAILY, encoding="utf-8")

        snapshot = self.store.snapshot()
        case = snapshot["all_cases"][0]
        self.assertEqual(case["value_count"], 1)
        self.assertEqual(case["open_item_count"], 1)
        self.assertEqual({source["family"] for source in case["source_mix"]}, {"chatgpt", "codex", "memory"})
        self.assertEqual(snapshot["summary"]["chatgpt_project_count"], 1)
        self.assertEqual(snapshot["summary"]["codex_project_count"], 1)
        self.assertEqual(snapshot["summary"]["value_item_count"], 1)

    def test_bootstrap_preserves_case_id_and_uses_confirmed_title(self) -> None:
        registry = bootstrap_registry(self.manifest_path, json.loads(self.manifest_path.read_text(encoding="utf-8")))
        self.assertEqual(registry["cases"][0]["case_id"], "case-c15a153c9e934f8e")
        self.assertEqual(registry["cases"][0]["title"], "硫醚环肽氧化辅助解序")
        self.assertEqual(registry["cases"][0]["category"], "rd_case")
        self.assertEqual(len(registry["events"]), 1)

    def test_refresh_is_idempotent_and_plain_question_does_not_create_case(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        first = self.store.snapshot()
        second = self.store.snapshot()
        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(first["summary"]["case_count"], 1)
        self.assertEqual(first["summary"]["reference_count"], 1)
        self.assertEqual(first["summary"]["inbox_count"], 0)
        self.assertEqual(first["summary"]["compliance_review_count"], 1)
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(len(registry["events"]), 3)

    def test_route_event_and_manual_metadata_survive_refresh(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        self.store.snapshot()
        self.store.route_event("chatgpt-daily:2026-08-01:S01", "case-c15a153c9e934f8e")
        self.store.update_case(
            {
                "case_id": "case-c15a153c9e934f8e",
                "title": "硫醚环肽氧化辅助解序",
                "line": "work",
                "category": "instrument_methodology",
                "status": "on_hold",
                "current_summary": "人工摘要",
                "next_step": "等待谱图",
            }
        )
        snapshot = self.store.snapshot()
        case = snapshot["all_cases"][0]
        self.assertEqual(snapshot["summary"]["inbox_count"], 0)
        self.assertEqual(case["category"], "instrument_methodology")
        self.assertEqual(case["status"], "on_hold")
        self.assertEqual(case["log_count"], 2)

    def test_source_correction_updates_content_but_preserves_manual_route(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        self.store.snapshot()
        event_id = "chatgpt-daily:2026-08-01:S01"
        self.store.route_event(event_id, "case-c15a153c9e934f8e")
        path.write_text(ENGLISH_DAILY.replace("explanation of battery C-rate meaning", "corrected explanation of battery charging rate"), encoding="utf-8")
        snapshot = self.store.snapshot()
        event = next(item for item in snapshot["all_cases"][0]["logs"] if item["event_id"] == event_id)
        self.assertEqual(event["state"], "routed")
        self.assertEqual(event["case_id"], "case-c15a153c9e934f8e")
        self.assertEqual(event["detail"], "corrected explanation of battery charging rate.")
        self.assertEqual(event["route_origin"], "manual")

    def test_sensitive_source_correction_overrides_manual_route(self) -> None:
        safe = CODEX_DAILY.replace("归档 ChatGPT 日报", "整理文档")
        path = self.wiki / "sources/conversations/codex-daily/2026/codex-daily-report-2026-08-01.md"
        path.parent.mkdir(parents=True)
        path.write_text(safe, encoding="utf-8")
        self.store.refresh()
        event_id = "codex-daily:2026-08-01:T01"
        self.store.route_event(event_id, "case-c15a153c9e934f8e")
        path.write_text(safe.replace("wiki/source.md", "smb://internal/share"), encoding="utf-8")
        corrected = next(item for item in self.store.refresh()["events"] if item["event_id"] == event_id)
        self.assertEqual(corrected["state"], "compliance_review")
        self.assertIsNone(corrected.get("case_id"))
        self.assertNotIn("smb://internal/share", json.dumps(corrected, ensure_ascii=False))
        self.assertTrue(any(item.get("action") == "source_compliance_review" for item in self.store.load().get("routing_audit", [])))

        path.write_text(safe, encoding="utf-8")
        still_review = next(item for item in self.store.refresh()["events"] if item["event_id"] == event_id)
        self.assertEqual(still_review["state"], "compliance_review")

    def test_compliance_review_cannot_create_case(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "compliance-review"):
            self.store.create_case(
                {
                    "title": "UPLC 基线下降排查",
                    "line": "work",
                    "category": "rd_case",
                    "status": "in_progress",
                    "current_summary": "",
                    "next_step": "核对压力曲线",
                },
                "chatgpt-daily:2026-08-01:S02",
            )

    def test_reference_can_route_set_current_move_and_undo(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        self.store.snapshot()
        second = self.store.create_case(
            {
                "title": "电池充电知识验证",
                "line": "work",
                "category": "instrument_methodology",
                "status": "in_progress",
                "current_summary": "原第二摘要",
                "next_step": "",
            }
        )
        event_id = "chatgpt-daily:2026-08-01:S01"
        self.store.route_event(event_id, "case-c15a153c9e934f8e", set_current=True)
        first_snapshot = self.store.snapshot()
        first_case = next(item for item in first_snapshot["all_cases"] if item["case_id"] == "case-c15a153c9e934f8e")
        self.assertEqual(first_case["current_summary"], "explanation of battery C-rate meaning.")

        self.store.route_event(event_id, second["case_id"], set_current=True)
        moved = self.store.snapshot()
        first_case = next(item for item in moved["all_cases"] if item["case_id"] == "case-c15a153c9e934f8e")
        second_case = next(item for item in moved["all_cases"] if item["case_id"] == second["case_id"])
        self.assertEqual(first_case["current_summary"], "仍在解序")
        self.assertEqual(second_case["current_summary"], "explanation of battery C-rate meaning.")

        self.store.undo_event_route(event_id)
        undone = self.store.snapshot()
        first_case = next(item for item in undone["all_cases"] if item["case_id"] == "case-c15a153c9e934f8e")
        second_case = next(item for item in undone["all_cases"] if item["case_id"] == second["case_id"])
        self.assertEqual(first_case["current_summary"], "explanation of battery C-rate meaning.")
        self.assertEqual(second_case["current_summary"], "原第二摘要")
        self.assertEqual(next(log for log in first_case["logs"] if log["event_id"] == event_id)["can_undo_route"], True)

    def test_reference_can_link_to_case_without_becoming_progress(self) -> None:
        path = self.daily / "chatgpt-daily-report-2026-08-01.md"
        path.write_text(ENGLISH_DAILY, encoding="utf-8")
        self.store.snapshot()
        event_id = "chatgpt-daily:2026-08-01:S01"
        self.store.route_event(event_id, "case-c15a153c9e934f8e", link_only=True)
        snapshot = self.store.snapshot()
        case = next(item for item in snapshot["all_cases"] if item["case_id"] == "case-c15a153c9e934f8e")
        self.assertEqual(case["log_count"], 1)
        self.assertEqual(case["related_count"], 1)
        self.assertEqual(case["related_events"][0]["event_id"], event_id)
        self.assertEqual(case["current_summary"], "仍在解序")

    def test_v3_registry_migrates_without_losing_manual_fields(self) -> None:
        self.registry_path.write_text(json.dumps({"version": 3, "cases": [{"case_id": "case-manual", "title": "保留的手工 Case", "line": "work", "status": "in_progress", "custom_field": "keep"}], "events": [], "manual_top_level": {"keep": True}}, ensure_ascii=False), encoding="utf-8")
        snapshot = self.store.snapshot()
        persisted = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], 4)
        self.assertEqual(persisted["manual_top_level"], {"keep": True})
        self.assertEqual(snapshot["all_cases"][0]["custom_field"], "keep")
        self.assertIn("project_mappings", persisted)

    def test_missing_live_state_is_not_reported_as_schema_drift(self) -> None:
        self.store.live_state_path = self.root / "missing-live-state.json"
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["live_tasks"], [])
        self.assertEqual(snapshot["live_state_error"], "live state not initialized")

    def test_live_completed_low_risk_maps_to_most_specific_project_and_is_idempotent(self) -> None:
        live = self.root / "live-state.json"
        live.write_text(json.dumps({"schema_version": 1, "tasks": [{"session_id": "t-1", "thread_id": "t-1", "turn_id": "turn-1", "status": "completed", "cwd": "/Users/shiba/Documents/codex", "changed_files": [{"path": "projects/app/sub/ui.py", "sha256": "abc"}], "preview": "完成可逆 UI 调整", "ended_at": "2026-08-02T10:00:00+08:00", "risk": "low"}]}), encoding="utf-8")
        self.store.live_state_path = live
        registry = self.store.refresh()
        registry["project_mappings"] = [
            {"path": "/Users/shiba/Documents/codex/projects/app", "case_id": "case-c15a153c9e934f8e"},
            {"path": "/Users/shiba/Documents/codex/projects/app/sub", "case_id": "case-specific"},
        ]
        registry["cases"].append({"case_id": "case-specific", "title": "Specific", "line": "work", "category": "ai_automation", "status": "in_progress"})
        self.store.save(registry)
        first = self.store.snapshot()
        second = self.store.snapshot()
        event = next(item for item in second["all_cases"] if item["case_id"] == "case-specific")["logs"][0]
        self.assertEqual(event["source_kind"], "codex_live")
        self.assertEqual(event["verification_refs"], [])
        self.assertEqual(len([event for event in self.store.load()["events"] if event["source_kind"] == "codex_live"]), 1)
        self.assertEqual(first["live_tasks"][0]["project_id"], "case-specific")

    def test_live_cwd_prefers_saved_project_parent_over_legacy_semantic_child(self) -> None:
        registry = self.store.refresh()
        registry["cases"].append({"case_id": "case-parent", "title": "Hermes parent", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["cases"].append({"case_id": "case-child", "title": "Legacy child", "line": "life", "category": "life", "status": "in_progress"})
        registry["project_mappings"] = [
            {
                "codex_project_id": "saved-hermes",
                "path": "/workspace/hermes-skills",
                "case_id": "case-parent",
            },
            {
                "path": "/workspace/hermes-skills/personal-asset-manager",
                "case_id": "case-child",
            },
        ]
        kind, target, reason = self.store._map_live_project(
            registry,
            "/workspace/hermes-skills/personal-asset-manager",
            [],
        )
        self.assertEqual((kind, target, reason), (
            "case", "case-parent", "mapped_by_saved_project_path",
        ))

    def test_live_parent_project_precedes_changed_file_cross_link(self) -> None:
        live = self.root / "live-state.json"
        live.write_text(json.dumps({"schema_version": 1, "tasks": [{
            "session_id": "parent", "thread_id": "parent", "turn_id": "turn-1",
            "projectId": "project-parent", "status": "completed", "cwd": "/parent",
            "changed_files": [{"path": "/other/file.py"}], "preview": "mentions Other",
            "ended_at": "2026-08-02T10:00:00+08:00", "risk": "low", "has_value": True,
        }]}), encoding="utf-8")
        self.store.live_state_path = live
        registry = self.store.refresh()
        registry["cases"].append({"case_id": "case-parent", "title": "Parent", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["cases"].append({"case_id": "case-other", "title": "Other", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["project_mappings"] = [
            {"codex_project_id": "project-parent", "path": "/parent", "case_id": "case-parent"},
            {"path": "/other", "case_id": "case-other"},
        ]
        self.store.save(registry)
        snapshot = self.store.snapshot()
        task = snapshot["live_tasks"][0]
        self.assertEqual(task["project_id"], "case-parent")
        self.assertEqual(task["mapping"], "mapped_by_codex_project_id")
        event = next(event for event in self.store.load()["events"] if event.get("source_kind") == "codex_live")
        self.assertEqual(event["state"], "linked")
        self.assertEqual(event["case_id"], "case-parent")
        self.assertTrue(task["review_required"])
        self.assertEqual(snapshot["summary"]["live_inbox_count"], 1)

    def test_live_experimental_work_is_redacted_unless_explicitly_abstracted(self) -> None:
        live = self.root / "live-state.json"
        live.write_text(json.dumps({"schema_version": 1, "tasks": [{"session_id": "secret", "thread_id": "secret", "turn_id": "turn-secret", "status": "completed", "cwd": "/lab", "preview": "肽图原始结果 具体实验结果", "changed_files": [{"path": "raw-result.csv"}], "ended_at": "2026-08-02T10:00:00+08:00"}]}), encoding="utf-8")
        self.store.live_state_path = live
        registry = self.store.refresh()
        registry["project_mappings"] = [{"path": "/lab", "case_id": "case-c15a153c9e934f8e"}]
        self.store.save(registry)
        snapshot = self.store.snapshot()
        task = snapshot["live_tasks"][0]
        self.assertEqual(task["title"], "实验工作内容（待合规抽象）")
        self.assertEqual(task["file_refs"], [])
        event = next(event for event in self.store.load()["events"] if event.get("source_kind") == "codex_live")
        self.assertEqual(event["state"], "compliance_review")
        self.assertNotIn("肽图", event["title"] + event["detail"])

    def test_unmapped_lab_path_is_redacted_before_inbox_persistence(self) -> None:
        live = self.root / "live-state.json"
        live.write_text(json.dumps({"schema_version": 1, "tasks": [{"session_id": "lab-unmapped", "status": "completed", "cwd": "/Users/shiba/Documents/codex", "preview": "具体实验结果", "changed_files": [{"path": "projects/lab/private/result.csv"}], "ended_at": "2026-08-02T10:00:00+08:00"}]}), encoding="utf-8")
        self.store.live_state_path = live
        snapshot = self.store.snapshot()
        task = snapshot["live_tasks"][0]
        self.assertTrue(task["compliance_redacted"])
        event = next(event for event in self.store.load()["events"] if event.get("source_kind") == "codex_live")
        self.assertEqual(event["state"], "compliance_review")
        self.assertNotIn("具体实验结果", json.dumps(event, ensure_ascii=False))

    def test_live_unknown_conflict_and_high_risk_completed_are_inbox(self) -> None:
        live = self.root / "live-state.json"
        live.write_text(json.dumps({"schema_version": 1, "tasks": [
            {"session_id": "unknown", "status": "completed", "cwd": "/unknown", "preview": "未知", "ended_at": "2026-08-02T10:00:00+08:00"},
            {"session_id": "review", "status": "completed", "cwd": "/mapped", "preview": "决策", "ended_at": "2026-08-02T10:01:00+08:00", "needs_review": True},
            {"session_id": "conflict", "status": "completed", "cwd": "/Users/shiba/Documents/codex", "changed_files": [{"path": "/a/x.py"}, {"path": "/b/y.py"}], "preview": "冲突", "ended_at": "2026-08-02T10:02:00+08:00"},
            {"session_id": "old", "status": "stale", "cwd": "/mapped", "preview": "旧任务", "updated_at": "2026-08-01T00:00:00+08:00"}
        ]}), encoding="utf-8")
        self.store.live_state_path = live
        registry = self.store.refresh()
        registry["cases"].append({"case_id": "case-safe", "title": "安全自动化", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["cases"].append({"case_id": "case-safe-2", "title": "安全自动化二", "line": "work", "category": "ai_automation", "status": "in_progress"})
        registry["project_mappings"] = [{"path": "/mapped", "case_id": "case-safe"}, {"path": "/a", "case_id": "case-safe"}, {"path": "/b", "case_id": "case-safe-2"}]
        self.store.save(registry)
        snapshot = self.store.snapshot()
        live_inbox = [event for event in snapshot["inbox"] if event.get("source_kind") == "codex_live"]
        self.assertEqual(len(live_inbox), 2)
        self.assertEqual({event["live_routing_reason"] for event in live_inbox}, {"unknown_project", "mapping_conflict"})
        linked = next(event for event in self.store.load()["events"] if event.get("thread_id") == "review")
        self.assertEqual(linked["state"], "linked")
        self.assertEqual(linked["case_id"], "case-safe")
        self.assertEqual(snapshot["summary"]["live_inbox_count"], 1)
        self.assertEqual(next(task for task in snapshot["live_tasks"] if task["task_id"] == "old")["status"], "stale")


class InboxProjectAssociationTests(unittest.TestCase):
    def registry(self) -> dict:
        return {
            "cases": [
                {"case_id": "case-db88755f08e823fe", "title": "Dashboard"},
                {"case_id": "case-3e47bd58f785c139", "title": "Nutrition"},
                {"case_id": "case-141f8976c72c3370", "title": "Deartime"},
            ],
            "project_mappings": [
                {"path": "/Users/example/projects/codex-project-dashboard", "case_id": "case-db88755f08e823fe"},
            ],
            "events": [],
        }

    def event(self, event_id: str, **values: object) -> dict:
        return {"event_id": event_id, "state": "inbox", "source_kind": "codex_live", "thread_id": event_id, "title": "Codex 任务", "detail": "", **values}

    def test_exact_mapping_thread_continuity_and_exclusive_identifier(self) -> None:
        registry = self.registry()
        registry["events"] = [
            self.event("path", detail="cwd /Users/example/projects/codex-project-dashboard"),
            {"event_id": "prior", "state": "routed", "source_kind": "codex_daily", "thread_id": "same", "case_id": "case-db88755f08e823fe"},
            self.event("continuity", thread_id="same"),
            self.event("named", source_kind="codex_daily", title="Deartime 电子墨水屏诊断"),
        ]
        decisions = {item["cluster_identity"]: item for item in plan_inbox_convergence(registry)}
        self.assertEqual(decisions["thread:path"]["reason"], "exact_mapping")
        self.assertEqual(decisions["thread:same"]["reason"], "thread_continuity")
        self.assertEqual(decisions["topic:deartime电子墨水屏诊断"]["case_id"], "case-141f8976c72c3370")

    def test_conflict_refuses_association_and_manual_history_does_not_seed_continuity(self) -> None:
        registry = self.registry()
        registry["events"] = [
            self.event("both", source_kind="codex_daily", title="Deartime 与 Nutrition Tracker 联合检查"),
            {"event_id": "manual", "state": "routed", "source_kind": "manual", "thread_id": "manual-thread", "case_id": "case-db88755f08e823fe"},
            self.event("manual-followup", thread_id="manual-thread"),
        ]
        decisions = plan_inbox_convergence(registry)
        conflict = next(item for item in decisions if item["cluster_identity"].startswith("topic:"))
        followup = next(item for item in decisions if item["cluster_identity"] == "thread:manual-thread")
        self.assertEqual(conflict["reason"], "ambiguous_distinctive_terms")
        self.assertFalse(conflict["case_id"])
        self.assertEqual(followup["reason"], "unmatched")

    def test_suggestions_are_idempotent_projection_and_preserve_all_members(self) -> None:
        registry = self.registry()
        registry["events"] = [
            self.event("a", source_kind="codex_daily", title="Hermes 标题故障"),
            self.event("b", source_kind="codex_daily", title="Hermes 标题故障", thread_id="other"),
            self.event("generic", source_kind="codex_daily", title="日报归档修复完成"),
        ]
        before = json.dumps(registry, ensure_ascii=False, sort_keys=True)
        first = plan_inbox_suggestions(registry)
        second = plan_inbox_suggestions(registry)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(registry, ensure_ascii=False, sort_keys=True), before)
        suggestion = next(item for item in first if item["case_id"])
        self.assertEqual(suggestion["case_id"], "case-3e47bd58f785c139")
        self.assertEqual(set(suggestion["event_ids"]), {"a", "b"})
        self.assertFalse(next(item for item in first if "generic" in item["event_ids"])["case_id"])
        units = build_project_evidence_units(registry["events"][:2])
        self.assertEqual(units[0]["member_count"], 2)
        self.assertEqual(len(registry["cases"]), 3)


if __name__ == "__main__":
    unittest.main()
