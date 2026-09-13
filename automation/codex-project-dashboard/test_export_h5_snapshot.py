import json
import unittest

from export_h5_snapshot import build_h5_snapshot


class H5SnapshotTest(unittest.TestCase):
    def test_whitelist_removes_internal_identifiers_and_sources(self):
        raw = {
            "generated_at": "2026-08-03T07:30:00+08:00",
            "daily_updated_through": "2026-08-02",
            "summary": {"case_count": 1, "value_item_count": 1},
            "categories": [{"id": "industry", "label": "产业"}],
            "branches": [],
            "capability_domains": [],
            "work_groups": {
                "industry": [
                    {
                        "case_id": "case-secret",
                        "title": "项目 A",
                        "status": "in_progress",
                        "logs": [
                            {
                                "event_id": "event-secret",
                                "date": "2026-08-02",
                                "detail": "完成摘要",
                                "source": "/Users/example/private/report.md",
                                "verification_refs": ["/Users/example/private/raw.log"],
                            }
                        ],
                        "value_items": [],
                        "open_items": [{"source": "/private/path", "text": "下一步"}],
                        "related_events": [{
                            "event_id": "related-secret",
                            "period": "2026-07",
                            "detail": "待继续积累",
                            "source": "compliance_abstracted",
                        }],
                    }
                ]
            },
            "life_cases": [],
            "closed_cases": [],
            "inbox": [{"event_id": "private"}],
            "daily_references": ["/private/path"],
            "live_tasks": [{"id": "private"}],
        }

        exported = build_h5_snapshot(raw)
        encoded = str(exported)

        self.assertNotIn("case-secret", encoded)
        self.assertNotIn("event-secret", encoded)
        self.assertNotIn("related-secret", encoded)
        self.assertNotIn("/Users/", encoded)
        self.assertNotIn("/private/", encoded)
        self.assertNotIn("inbox", exported)
        self.assertNotIn("daily_references", exported)
        self.assertNotIn("live_tasks", exported)
        project = exported["work_sections"][0]["projects"][0]
        self.assertEqual(project["logs"][0]["source"], "Memory")
        self.assertEqual(project["related_items"][0]["detail"], "待继续积累")
        self.assertEqual(exported["daily_digest"]["source_counts"]["Memory"], 1)
        self.assertEqual(exported["daily_digest"]["items"][0]["scope"], "项目 A")
        self.assertNotIn("case_id", exported["daily_digest"]["items"][0])

    def test_completed_source_gate_date_overrides_latest_routable_event(self):
        raw = {
            "generated_at": "2026-08-04T07:30:00+08:00",
            "daily_updated_through": "2026-08-02",
            "summary": {},
            "categories": [],
            "branches": [],
            "capability_domains": [],
            "work_groups": {},
            "life_cases": [],
            "closed_cases": [],
        }

        exported = build_h5_snapshot(raw, synced_through="2026-08-03")

        self.assertEqual(exported["daily_updated_through"], "2026-08-03")
        self.assertEqual(exported["daily_digest"]["date"], "2026-08-03")
        self.assertEqual(exported["daily_digest"]["items"], [])

    def test_capability_instrument_types_are_exported(self):
        raw = {
            "generated_at": "2026-08-04T07:30:00+08:00", "summary": {}, "categories": [], "branches": [],
            "capability_domains": [{"title": "仪器知识与应用方法论", "tag_label": "仪器类型", "items": [{
                "question": "SIM 与 MRM 有什么区别？", "answer": "完整答案", "detail": "完整答案",
                "instrument_types": ["质谱", "三重四极杆质谱"],
                "answer_status": "source_grounded", "answer_origin": "daily_report",
                "evidence_refs": [{"source_type": "daily_report", "source_date": "2026-08-03", "session_id": "S01", "session_title": "SIM与MRM区别", "support_level": "source_summary", "excerpt": "不会导出"}],
            }]}],
            "work_groups": {}, "life_cases": [], "closed_cases": [],
        }
        exported = build_h5_snapshot(raw)
        self.assertEqual(exported["capability_domains"][0]["items"][0]["instrument_types"], ["质谱", "三重四极杆质谱"])
        self.assertEqual(exported["capability_domains"][0]["tag_label"], "仪器类型")
        self.assertEqual(exported["capability_domains"][0]["items"][0]["capability_tags"], ["质谱", "三重四极杆质谱"])
        self.assertEqual(exported["capability_domains"][0]["items"][0]["answer_status"], "source_grounded")
        self.assertNotIn("excerpt", exported["capability_domains"][0]["items"][0]["evidence_refs"][0])

    def test_legacy_generated_capability_answer_is_not_exported_as_verified(self):
        raw = {
            "generated_at": "2026-08-04T07:30:00+08:00", "summary": {}, "categories": [], "branches": [],
            "capability_domains": [{"title": "仪器知识", "items": [{
                "question": "旧考点？", "answer": "迁移时重新生成的答案", "detail": "迁移时重新生成的答案",
            }]}],
            "work_groups": {}, "life_cases": [], "closed_cases": [],
        }
        item = build_h5_snapshot(raw)["capability_domains"][0]["items"][0]
        self.assertEqual(item["answer_status"], "needs_source_review")
        self.assertEqual(item["answer"], "")
        self.assertEqual(item["detail"], "")

    def test_latest_visible_log_controls_project_recency(self):
        raw = {
            "generated_at": "2026-08-04T07:30:00+08:00",
            "daily_updated_through": "2026-08-03",
            "summary": {},
            "categories": [],
            "branches": [],
            "capability_domains": [],
            "work_groups": {"automation": [{
                "title": "项目 A",
                "updated_at": "2026-08-02T10:00:00+08:00",
                "logs": [{
                    "date": "2026-08-03",
                    "title": "日报新增",
                    "detail": "完成 H5",
                    "source": "codex-daily",
                }],
            }]},
            "life_cases": [],
            "closed_cases": [],
        }

        exported = build_h5_snapshot(raw)

        self.assertEqual(exported["work_sections"][0]["projects"][0]["updated_at"], "2026-08-03")
        self.assertEqual(exported["daily_digest"]["source_counts"]["Codex"], 1)

    def test_latest_daily_progress_is_public_without_overwriting_manual_fields(self):
        raw = {
            "generated_at": "2026-09-09T07:20:00+08:00",
            "daily_updated_through": "2026-09-08",
            "summary": {}, "categories": [{"id": "automation", "label": "自动化"}],
            "branches": [], "capability_domains": [], "life_cases": [], "closed_cases": [],
            "work_groups": {"automation": [{
                "title": "日报系统", "status": "in_progress", "status_label": "进行中",
                "current_summary": "人工摘要", "next_step": "人工下一步", "updated_at": "2026-08-03",
                "latest_daily_progress": {"date": "2026-09-08", "items": [
                    {"event_id": "private-one", "date": "2026-09-08", "task_id": "T01", "title": "校验日报", "detail": "日报校验已完成。", "source": "codex-daily", "source_kind": "codex_daily", "state": "routed", "task_status": "completed"},
                    {"event_id": "private-two", "date": "2026-09-08", "task_id": "T02", "title": "刷新卡片", "detail": "卡片刷新已验证。", "source": "codex-daily", "source_kind": "codex_daily", "state": "routed", "task_status": "completed"},
                ]},
            }]},
        }

        exported = build_h5_snapshot(raw)
        project = exported["work_sections"][0]["projects"][0]

        self.assertEqual(project["current_summary"], "人工摘要")
        self.assertEqual(project["next_step"], "人工下一步")
        self.assertEqual(project["status"], "in_progress")
        self.assertEqual(project["updated_at"], "2026-09-08")
        self.assertEqual(project["latest_daily_progress"]["date"], "2026-09-08")
        self.assertEqual([item["title"] for item in project["latest_daily_progress"]["items"]], ["校验日报", "刷新卡片"])
        encoded = json.dumps(project, ensure_ascii=False)
        self.assertNotIn("private-one", encoded)
        self.assertNotIn("task_id", encoded)

    def test_one_project_contains_multiple_daily_tasks_with_provenance(self):
        raw = {
            "generated_at": "2026-09-08T18:00:00+08:00",
            "daily_updated_through": "2026-09-07",
            "summary": {"case_count": 1, "active_case_count": 1, "codex_project_count": 1},
            "categories": [{"id": "automation", "label": "AI 与自动化前瞻"}],
            "branches": [], "capability_domains": [], "life_cases": [], "closed_cases": [],
            "work_groups": {"automation": [{
                "title": "项目看板",
                "status": "in_progress",
                "logs": [
                    {"date": "2026-09-07", "title": "T01 修复路由", "detail": "保留逐任务证据。", "source_kind": "codex_daily", "source": "codex-daily", "evidence_type": "reported"},
                    {"date": "2026-09-07", "title": "T02 验证 H5", "detail": "完成项目聚合验证。", "source_kind": "codex_daily", "source": "codex-daily", "evidence_type": "reported"},
                ],
            }]},
        }

        exported = build_h5_snapshot(raw)
        projects = exported["work_sections"][0]["projects"]

        self.assertEqual(len(projects), 1)
        self.assertEqual([item["title"] for item in projects[0]["logs"]], ["T01 修复路由", "T02 验证 H5"])
        self.assertTrue(all(item["source"] == "Codex" for item in projects[0]["logs"]))
        self.assertEqual(exported["summary"]["case_count"], 1)

    def test_public_schema_removes_internal_routing_keys_and_live_previews(self):
        raw = {
            "generated_at": "2026-08-23T15:00:00+08:00", "daily_updated_through": "2026-08-22",
            "summary": {}, "categories": [{"id": "ai_automation", "label": "AI 与自动化前瞻"}],
            "branches": [], "capability_domains": [], "life_cases": [], "closed_cases": [],
            "work_groups": {"ai_automation": [{
                "title": "Dashboard", "line": "work", "category": "ai_automation",
                "logs": [
                    {"date": "2026-08-22", "detail": "正式日报结果", "source_kind": "codex_daily"},
                    {"date": "2026-08-23", "detail": "不可公开的 live preview", "source_kind": "codex_live"},
                ],
            }]},
        }
        exported = build_h5_snapshot(raw)
        encoded = json.dumps(exported, ensure_ascii=False)
        self.assertEqual(exported["schema_version"], 3)
        self.assertNotIn("categories", exported)
        self.assertNotIn("work_groups", exported)
        self.assertNotIn("ai_automation", encoded)
        self.assertNotIn("live preview", encoded)
        project = exported["work_sections"][0]["projects"][0]
        self.assertNotIn("category", project)
        self.assertNotIn("line", project)

    def test_public_text_rewrites_internal_field_names_and_prompt_terms(self):
        raw = {
            "generated_at": "2026-08-23T20:00:00+08:00",
            "daily_updated_through": "2026-08-22", "summary": {}, "branches": [],
            "capability_domains": [], "life_cases": [], "closed_cases": [],
            "categories": [{"id": "ai_automation", "label": "AI 与自动化前瞻"}],
            "work_groups": {"ai_automation": [{
                "title": "Example",
                "current_summary": "Resolve thread_id + turn_id before continuing.",
                "next_step": "Unify the Weekly Review prompt and canonical raw boundary.",
            }]},
        }
        rendered = json.dumps(build_h5_snapshot(raw), ensure_ascii=False)
        for marker in ("thread_id", "turn_id", "prompt", "canonical raw"):
            self.assertNotIn(marker, rendered.lower())
        self.assertIn("内部任务引用", rendered)
        self.assertIn("模板", rendered)


if __name__ == "__main__":
    unittest.main()
