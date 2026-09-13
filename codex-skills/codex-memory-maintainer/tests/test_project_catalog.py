import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/migrate_project_catalog.py"
SPEC = importlib.util.spec_from_file_location("project_catalog_test_module", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class ProjectCatalogMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "wiki").mkdir()
        self.source = (
            "# 项目\n\nIntro.\n\n"
            "## Alpha 项目\n\n- Exact alpha.\n\n"
            "## 基础设施\n\n- Exact infra.\n"
        )
        (self.root / "wiki/projects.md").write_text(self.source, encoding="utf-8")
        self.decisions = b"# Decisions\n\nMust remain unchanged.\n"
        (self.root / "wiki/decisions.md").write_bytes(self.decisions)
        sections = module.split_h2_sections(self.source)
        self.inventory_path = self.root / "inventory.json"
        inventory = {
            "files": [
                {
                    "path": "wiki/projects.md",
                    "sha256": hashlib.sha256(self.source.encode()).hexdigest(),
                    "sections": [
                        {
                            "heading": heading,
                            "anchor": {"obsidian": heading, "markdown_slug": f"slug-{index}"},
                            "start_line": index * 10,
                            "end_line": index * 10 + 9,
                            "normalized_sha256": module.section_sha256(section),
                        }
                        for index, (heading, section) in enumerate(sections, start=1)
                    ],
                }
            ]
        }
        self.inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_stable_ids_aliases_paths_hashes_and_lossless_sections(self):
        with self.assertRaisesRegex(ValueError, "exactly 11"):
            module.build_catalog_targets(self.root, self.inventory_path)

        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        first = inventory["files"][0]["sections"][0]
        inventory["files"][0]["sections"] = [first] * 11
        self.inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly 11|heading mismatch|duplicate"):
            module.build_catalog_targets(self.root, self.inventory_path)

    def test_full_eleven_section_migration_is_deterministic_and_does_not_touch_decisions(self):
        headings = [f"Project {index}" for index in range(1, 12)]
        source = "# 项目\n\nIntro.\n\n" + "".join(
            f"## {heading}\n\n- Body {index}.\n\n" for index, heading in enumerate(headings, start=1)
        )
        (self.root / "wiki/projects.md").write_text(source, encoding="utf-8")
        sections = module.split_h2_sections(source)
        inventory = {
            "files": [
                {
                    "path": "wiki/projects.md",
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "sections": [
                        {
                            "heading": heading,
                            "anchor": {"obsidian": heading, "markdown_slug": f"project-{index}"},
                            "start_line": index * 4,
                            "end_line": index * 4 + 3,
                            "normalized_sha256": module.section_sha256(section),
                        }
                        for index, ((heading, section)) in enumerate(sections, start=1)
                    ],
                }
            ]
        }
        self.inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")

        catalog = module.migrate(self.root, self.inventory_path)

        self.assertEqual(len(catalog["entries"]), 11)
        self.assertEqual((self.root / "wiki/decisions.md").read_bytes(), self.decisions)
        for (heading, original), entry in zip(sections, catalog["entries"], strict=True):
            self.assertEqual(entry["id"], module.stable_project_id(heading))
            self.assertEqual(entry["canonical_locator"], f"wiki/catalog/projects/{entry['id']}.md")
            self.assertEqual(
                entry["aliases"],
                [
                    {"type": "legacy_heading", "value": heading},
                    {"type": "obsidian_locator", "value": f"wiki/projects.md#{heading}"},
                    {"type": "markdown_locator", "value": f"wiki/projects.md#project-{sections.index((heading, original)) + 1}"},
                ],
            )
            page = (self.root / entry["canonical_locator"]).read_text(encoding="utf-8")
            self.assertEqual(module.preserved_section(page), original)
            self.assertEqual(module.section_sha256(original), entry["source_section_sha256"])

        targets_a, catalog_a = module.build_catalog_targets(self.root, self.inventory_path)
        targets_b, catalog_b = module.build_catalog_targets(self.root, self.inventory_path)
        self.assertEqual(catalog_a, catalog_b)
        self.assertEqual(targets_a, targets_b)


if __name__ == "__main__":
    unittest.main()
