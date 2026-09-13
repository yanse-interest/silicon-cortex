#!/usr/bin/env python3
"""Losslessly add legacy wiki/decisions.md H2 sections to the shared catalog."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path


DEFAULT_VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
DEFAULT_INVENTORY = Path("/private/tmp/catalog-min-inventory.json")
SECTION_START = "<!-- BEGIN LOSSLESS LEGACY DECISION SECTION -->"
SECTION_END = "<!-- END LOSSLESS LEGACY DECISION SECTION -->"


def _load_sibling(filename: str, name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stable_decision_id(heading: str) -> str:
    basis = "decision\0" + unicodedata.normalize("NFC", heading)
    return "decision-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _aliases(heading: str, section_meta: dict) -> list[dict[str, str]]:
    anchor = section_meta["anchor"]
    return [
        {"type": "legacy_heading", "value": heading},
        {"type": "obsidian_locator", "value": f"wiki/decisions.md#{anchor['obsidian']}"},
        {"type": "markdown_locator", "value": f"wiki/decisions.md#{anchor['markdown_slug']}"},
    ]


def render_decision_page(entry: dict, section: str) -> str:
    lines = [
        f"# {entry['title']}（决策历史）",
        "",
        f"- stable ID：`{entry['id']}`",
        f"- legacy locator：`wiki/decisions.md#{entry['legacy_heading']}`",
        f"- source section SHA-256：`{entry['source_section_sha256']}`",
        "",
        "以下区块逐字符保留迁移前的完整 H2 section。",
        "",
        SECTION_START,
    ]
    return "\n".join(lines) + "\n" + section + SECTION_END + "\n"


def preserved_section(page_text: str) -> str:
    prefix = SECTION_START + "\n"
    start = page_text.index(prefix) + len(prefix)
    end = page_text.index(SECTION_END, start)
    return page_text[start:end]


def build_catalog_targets(vault: Path, inventory_path: Path) -> tuple[dict[Path, str], dict, list[dict]]:
    project_module = _load_sibling("migrate_project_catalog.py", "decision_catalog_project_helpers")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    matches = [item for item in inventory.get("files", []) if item.get("path") == "wiki/decisions.md"]
    if len(matches) != 1:
        raise ValueError("inventory must contain exactly one wiki/decisions.md record")
    source_meta = matches[0]
    source_path = vault / "wiki/decisions.md"
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != source_meta["sha256"]:
        raise ValueError("wiki/decisions.md file hash differs from immutable inventory")
    source_text = source_bytes.decode("utf-8")
    if project_module.normalize_section(source_text) != source_text:
        raise ValueError("wiki/decisions.md must already be LF + NFC so exact text can be preserved")
    sections = project_module.split_h2_sections(source_text)
    metadata = source_meta.get("sections", [])
    if len(sections) != 45 or len(metadata) != 45:
        raise ValueError("decision migration requires exactly 45 inventoried H2 sections")

    catalog_path = vault / "wiki/catalog/catalog.json"
    existing_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    existing_entries = existing_catalog.get("entries", [])
    if existing_catalog.get("schema_version") != "1.0" or len(existing_entries) != 11:
        raise ValueError("decision migration requires the existing schema 1.0 11-entry project catalog")
    if any(item.get("kind") != "project" for item in existing_entries):
        raise ValueError("existing catalog must contain only the 11 project entries")
    preserved_projects = json.loads(json.dumps(existing_entries, ensure_ascii=False))

    targets: dict[Path, str] = {}
    decisions: list[dict] = []
    for (heading, section), meta in zip(sections, metadata, strict=True):
        if heading != meta["heading"]:
            raise ValueError(f"section heading mismatch: {heading!r}")
        digest = project_module.section_sha256(section)
        if digest != meta["normalized_sha256"]:
            raise ValueError(f"section hash mismatch: {heading}")
        stable_id = stable_decision_id(heading)
        locator = f"wiki/catalog/decisions/{stable_id}.md"
        entry = {
            "id": stable_id,
            "kind": "decision",
            "title": heading,
            "legacy_heading": heading,
            "aliases": _aliases(heading, meta),
            "canonical_locator": locator,
            "source_section_sha256": digest,
            "source": {
                "path": "wiki/decisions.md",
                "start_line": meta["start_line"],
                "end_line": meta["end_line"],
            },
        }
        page = render_decision_page(entry, section)
        if preserved_section(page) != section or project_module.section_sha256(preserved_section(page)) != digest:
            raise AssertionError(f"rendered section was not lossless: {heading}")
        decisions.append(entry)
        targets[vault / locator] = page

    combined = existing_entries + decisions
    ids = [item["id"] for item in combined]
    locators = [item["canonical_locator"] for item in combined]
    alias_keys = [
        (alias["type"], alias["value"])
        for item in combined
        for alias in item.get("aliases", [])
    ]
    if len(ids) != 56 or len(set(ids)) != 56:
        raise ValueError("combined catalog stable IDs must be unique 56/56")
    if len(set(locators)) != 56:
        raise ValueError("combined catalog locators must be unique 56/56")
    if len(set(alias_keys)) != len(alias_keys):
        raise ValueError("combined catalog typed aliases must be unique")
    catalog = {
        "schema_version": "1.0",
        "stable_id_basis": "kind + NUL + NFC legacy heading; SHA-256 first 16 hex",
        "entries": combined,
    }
    if catalog["entries"][:11] != preserved_projects:
        raise AssertionError("project catalog entries changed while building decision catalog")
    targets[catalog_path] = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    return targets, catalog, preserved_projects


def migrate(vault: Path, inventory_path: Path) -> dict:
    targets, catalog, preserved_projects = build_catalog_targets(vault, inventory_path)
    maintainer = _load_sibling("maintain_memory.py", "decision_catalog_maintainer")
    maintainer.atomic_replace_many(targets)
    written = json.loads((vault / "wiki/catalog/catalog.json").read_text(encoding="utf-8"))
    if written["entries"][:11] != preserved_projects:
        raise RuntimeError("post-write project catalog entries changed")
    for entry in written["entries"][11:]:
        page = (vault / entry["canonical_locator"]).read_text(encoding="utf-8")
        normalized = unicodedata.normalize("NFC", preserved_section(page))
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != entry["source_section_sha256"]:
            raise RuntimeError(f"post-write decision section hash mismatch: {entry['id']}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args()
    catalog = migrate(args.vault.expanduser().resolve(), args.inventory.expanduser().resolve())
    print(f"migrated 45 decision sections; combined_entries={len(catalog['entries'])}; schema={catalog['schema_version']}")


if __name__ == "__main__":
    main()
