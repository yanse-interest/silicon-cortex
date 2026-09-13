import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


decision_module = load("decision_catalog_test_module", ROOT / "scripts/migrate_decision_catalog.py")
project_module = load("decision_catalog_project_test_helpers", ROOT / "scripts/migrate_project_catalog.py")
maintainer = load("decision_catalog_maintainer_test_module", ROOT / "scripts/maintain_memory.py")


class DecisionCatalogMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "wiki/catalog/projects").mkdir(parents=True)
        self.project_entries = []
        self.project_bytes = {}
        for index in range(1, 12):
            heading = f"Project {index}"
            stable_id = project_module.stable_project_id(heading)
            section = f"## {heading}\n\n- Project body {index}.\n"
            entry = {
                "id": stable_id,
                "kind": "project",
                "subtype": "project_or_infrastructure_history",
                "title": heading,
                "legacy_heading": heading,
                "aliases": [
                    {"type": "legacy_heading", "value": heading},
                    {"type": "obsidian_locator", "value": f"wiki/projects.md#{heading}"},
                    {"type": "markdown_locator", "value": f"wiki/projects.md#project-{index}"},
                ],
                "canonical_locator": f"wiki/catalog/projects/{stable_id}.md",
                "source_section_sha256": project_module.section_sha256(section),
                "source": {"path": "wiki/projects.md", "start_line": index, "end_line": index + 1},
            }
            page = project_module.render_project_page(entry, section)
            path = self.vault / entry["canonical_locator"]
            path.write_text(page, encoding="utf-8")
            self.project_entries.append(entry)
            self.project_bytes[path] = path.read_bytes()
        catalog = {
            "schema_version": "1.0",
            "stable_id_basis": "project + NUL + NFC legacy heading; SHA-256 first 16 hex",
            "entries": self.project_entries,
        }
        (self.vault / "wiki/catalog/catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.project_compatibility = maintainer.render_catalog_compatibility(self.vault, "project")

        self.headings = [f"[2026-08-{index:02d}] Decision {index}" for index in range(1, 46)]
        source = "# 决策\n\n" + "".join(
            f"## {heading}\n\n- Decision body {index}.\n\n"
            for index, heading in enumerate(self.headings, start=1)
        )
        (self.vault / "wiki/decisions.md").write_text(source, encoding="utf-8")
        sections = project_module.split_h2_sections(source)
        inventory = {
            "files": [
                {
                    "path": "wiki/decisions.md",
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "sections": [
                        {
                            "heading": heading,
                            "anchor": {"obsidian": heading, "markdown_slug": f"decision-{index}"},
                            "start_line": index * 4,
                            "end_line": index * 4 + 3,
                            "normalized_sha256": project_module.section_sha256(section),
                        }
                        for index, (heading, section) in enumerate(sections, start=1)
                    ],
                }
            ]
        }
        self.inventory_path = self.vault / "inventory.json"
        self.inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
        self.sections = sections

    def tearDown(self):
        self.temp.cleanup()

    def test_combined_catalog_has_56_unique_entries_and_lossless_decision_coverage(self):
        catalog = decision_module.migrate(self.vault, self.inventory_path)

        self.assertEqual(len(catalog["entries"]), 56)
        self.assertEqual(catalog["entries"][:11], self.project_entries)
        self.assertEqual(
            {path: path.read_bytes() for path in self.project_bytes}, self.project_bytes
        )
        self.assertEqual(maintainer.render_catalog_compatibility(self.vault, "project"), self.project_compatibility)
        decisions = catalog["entries"][11:]
        self.assertEqual(len(decisions), 45)
        self.assertEqual(len({item["id"] for item in catalog["entries"]}), 56)
        self.assertEqual(len({item["canonical_locator"] for item in catalog["entries"]}), 56)
        aliases = [
            (alias["type"], alias["value"])
            for item in catalog["entries"]
            for alias in item["aliases"]
        ]
        self.assertEqual(len(aliases), len(set(aliases)))
        for (heading, original), entry in zip(self.sections, decisions, strict=True):
            self.assertEqual(entry["id"], decision_module.stable_decision_id(heading))
            self.assertEqual(entry["canonical_locator"], f"wiki/catalog/decisions/{entry['id']}.md")
            page = (self.vault / entry["canonical_locator"]).read_text(encoding="utf-8")
            self.assertEqual(decision_module.preserved_section(page), original)
            self.assertEqual(project_module.section_sha256(original), entry["source_section_sha256"])
        compatibility = maintainer.render_catalog_compatibility(self.vault, "decision")
        compatibility_headings = [line[3:] for line in compatibility.splitlines() if line.startswith("## ")]
        self.assertEqual(compatibility_headings, self.headings)
        self.assertEqual(maintainer.catalog_issues(self.vault), [])


if __name__ == "__main__":
    unittest.main()
