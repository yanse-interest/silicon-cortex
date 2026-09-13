import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recipe_library import _candidate, _direct_candidate, _ingredient, _nas_candidates, build_recipe_collection


class RecipeLibraryTests(unittest.TestCase):
    def test_candidate_rejects_string_fields_and_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw.md"; source = Path(directory) / "source.md"
            raw.write_text("### S01 — 菜\n证据文本\n", encoding="utf-8"); source.write_text("", encoding="utf-8")
            base = {"title":"菜","session_id":"S01","session_title":"菜","evidence_excerpt":"证据文本","ingredients":[],"steps":[]}
            self.assertIsNone(_candidate({**base,"ingredients":"x"}, source, raw, "2026-01-01"))
            self.assertIsNone(_candidate({**base,"evidence_excerpt":"不存在"}, source, raw, "2026-01-01"))

    def test_versions_have_distinct_stable_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw.md"; source = Path(directory) / "source.md"
            raw.write_text("### S01 — 菜\n证据文本\n", encoding="utf-8"); source.write_text("", encoding="utf-8")
            common = {"title":"菜","session_id":"S01","session_title":"菜","evidence_excerpt":"证据文本","ingredients":["甲"]}
            self.assertNotEqual(_candidate({**common,"steps":["做法甲"]},source,raw,"2026-01-01")["recipe_id"], _candidate({**common,"steps":["做法乙"]},source,raw,"2026-01-01")["recipe_id"])

    def test_unready_and_hash_changed_backfill_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"sources/chatgpt-daily/2026/chatgpt-daily-report-2026-01-01.md"; raw=root/"raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-01-01.md"
            source.parent.mkdir(parents=True); raw.parent.mkdir(parents=True)
            source.write_text("date: 2026-01-01\nraw_source: raw/conversations/chatgpt-daily/2026/chatgpt-daily-report-2026-01-01.md\n",encoding="utf-8"); raw.write_text("type: chatgpt_daily_report\ndate: 2026-01-01\n",encoding="utf-8")
            (root/"wiki").mkdir(); (root/"wiki/project-dashboard-recipe-backfill.json").write_text(json.dumps({"entries":[{"source_date":"2026-01-01","source_sha256":"wrong","raw_sha256":"wrong","title":"菜","session_id":"S01","session_title":"菜","evidence_excerpt":"x","ingredients":[],"steps":[]}]}),encoding="utf-8")
            with patch("recipe_library.source_deposition_state", return_value={"ready":False}): self.assertEqual(build_recipe_collection(source_root=root/"sources",memory_root=root),[])
            with patch("recipe_library.source_deposition_state", return_value={"ready":True}): self.assertEqual(build_recipe_collection(source_root=root/"sources",memory_root=root),[])

    def test_empty_source_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/"sources").mkdir()
            self.assertEqual(build_recipe_collection(source_root=root/"sources",memory_root=root),[])
            self.assertEqual(build_recipe_collection(source_root=root/"sources",memory_root=root),[])

    def test_fixed_template_keeps_units_packages_and_marks_legacy_fragments_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw.md"; source = Path(directory) / "source.md"
            raw.write_text("### S01 — 菜\n证据文本\n", encoding="utf-8"); source.write_text("", encoding="utf-8")
            base = {"title":"菜","session_id":"S01","session_title":"菜","evidence_excerpt":"证据文本"}
            complete = _candidate({**base, "ingredients":[{"name":"酱料","role":"调料","amount":1,"unit":"包","package_spec":"每包 50 g"}], "steps":["拌匀。"], "method_status":"complete"}, source, raw, "2026-01-01")
            self.assertEqual(complete["status"], "complete")
            self.assertEqual(complete["ingredients"][0]["package_spec"], "每包 50 g")
            fragment = _candidate({**base, "ingredients":["20 g 米"], "steps":["煮熟。"]}, source, raw, "2026-01-01")
            self.assertEqual(fragment["method_status"], "pending")
            self.assertEqual(fragment["status"], "needs_completion")

    def test_amount_normalization_requires_positive_finite_supported_units(self):
        self.assertEqual(_ingredient("350 g 猪肉末")["amount"], 350.0)
        self.assertEqual(_ingredient("350 g 猪肉末")["amount_status"], "recorded")
        self.assertEqual(_ingredient({"name":"酱料", "amount":1, "unit":"包"})["amount_status"], "recorded")
        self.assertEqual(_ingredient({"name":"猪肉", "amount":350, "unit":"g"})["quantity_text"], "350")
        self.assertEqual(_ingredient({"name":"猪肉", "amount":10, "unit":"g"})["quantity_text"], "10")
        self.assertEqual(_ingredient({"name":"母鸡", "amount":500, "unit":"g", "amount_status":"pending"})["amount_status"], "pending")
        for amount, unit in ((0, "g"), (-1, "g"), (float("inf"), "g"), (1, "适量")):
            self.assertEqual(_ingredient({"name":"食材", "amount":amount, "unit":unit})["amount_status"], "pending")

    def test_direct_preserves_groups_and_non_exact_quantities(self):
        recipe = _direct_candidate({"title":"煲", "ingredients":[
            {"group":"腌料", "name":"盐", "amount":"0.5–1", "unit":"g"},
            {"group":"腌料", "name":"白胡椒", "amount":"少许", "unit":""},
            {"group":"煲汁", "name":"水", "amount":"220", "unit":"mL"}], "steps":["煮开。"],
            "method_status":"complete", "source_refs":[{"conversation_title":"原会话", "conversation_url":"https://chatgpt.com/c/example"}]})
        self.assertEqual(recipe["ingredients"][0]["group"], "腌料")
        self.assertEqual(recipe["ingredients"][0]["quantity_text"], "0.5–1")
        self.assertEqual(recipe["ingredients"][0]["amount_status"], "pending")
        self.assertEqual(recipe["ingredients"][1]["quantity_text"], "少许")
        self.assertEqual(recipe["ingredients"][2]["amount"], 220.0)
        self.assertEqual(recipe["method_status"], "complete")

    def test_direct_requires_explicit_complete_method_status(self):
        recipe = _direct_candidate({"title":"煲", "ingredients":[{"name":"水", "amount":220, "unit":"mL"}], "steps":["煮开。"], "source_refs":[{"conversation_title":"原会话"}]})
        self.assertEqual(recipe["method_status"], "pending")
        self.assertEqual(recipe["status"], "needs_completion")

    def test_nas_import_excludes_debug_and_test_rows_and_keeps_notes_out_of_steps(self):
        recipes = _nas_candidates()
        self.assertEqual(len(recipes), 33)
        self.assertNotIn("nas-recipe-2", {recipe["recipe_id"] for recipe in recipes})
        self.assertNotIn("nas-recipe-24", {recipe["recipe_id"] for recipe in recipes})
        target = next(recipe for recipe in recipes if recipe["recipe_id"] == "nas-recipe-35")
        self.assertEqual(target["source_refs"][0]["source_row_id"], 35)
        self.assertEqual(target["steps"], [])
        self.assertTrue(any(item["group"] == "虾仁腌料" and item["name"] == "盐" for item in target["ingredients"]))
        self.assertEqual(target["source_refs"][0]["source_path"], "raw/docs/nas-recipes/nas-recipes-import-20260910.json")
        self.assertEqual(len([item for item in target["ingredients"] if item["name"] == "生抽" and item["group"] == "煲汁"]), 1)
        soup = next(recipe for recipe in recipes if recipe["recipe_id"] == "nas-recipe-5")
        self.assertEqual(soup["method_status"], "partial")
        self.assertTrue(any("炒蛋" in step for step in soup["steps"]))

    def test_nas_annotation_formulae_are_curated_without_double_counting(self):
        recipes = {recipe["recipe_id"]: recipe for recipe in _nas_candidates()}
        noodles = recipes["nas-recipe-25"]
        self.assertEqual([(item["name"], item["quantity_text"]) for item in noodles["ingredients"]], [("鸡胸肉", "100"), ("面条", "150"), ("黄瓜", "100"), ("椒麻汁", "30")])
        sauce = recipes["nas-recipe-26"]
        self.assertEqual([item["name"] for item in sauce["ingredients"]], ["生抽", "蚝油", "醋", "蒜蓉酱", "油泼辣子", "糖", "白芝麻"])
        cucumber = recipes["nas-recipe-9"]
        self.assertEqual(sum(item["name"] in {"熟芝麻", "芝麻"} for item in cucumber["ingredients"]), 1)
        self.assertEqual(sum(item["name"] in {"白砂糖", "糖"} for item in cucumber["ingredients"]), 1)
        tofu = recipes["nas-recipe-35"]
        self.assertIn("来源用量存在版本差异", tofu["notes"][0])
        self.assertFalse(any(i["name"] in {"生抽", "蚝油", "糖", "老抽"} and not i["group"] for i in tofu["ingredients"]))

if __name__ == "__main__": unittest.main()
