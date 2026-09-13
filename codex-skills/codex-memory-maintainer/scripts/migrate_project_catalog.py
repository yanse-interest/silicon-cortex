#!/usr/bin/env python3
"""Losslessly migrate legacy wiki/projects.md H2 sections into catalog pages."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path


DEFAULT_VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
DEFAULT_INVENTORY = Path("/private/tmp/catalog-min-inventory.json")
SECTION_START = "<!-- BEGIN LOSSLESS LEGACY PROJECT SECTION -->"
SECTION_END = "<!-- END LOSSLESS LEGACY PROJECT SECTION -->"


def _load_maintainer():
    path = Path(__file__).with_name("maintain_memory.py")
    spec = importlib.util.spec_from_file_location("catalog_migration_maintainer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load maintain_memory.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalize_section(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def section_sha256(text: str) -> str:
    return hashlib.sha256(normalize_section(text).encode("utf-8")).hexdigest()


def stable_project_id(heading: str) -> str:
    basis = "project\0" + unicodedata.normalize("NFC", heading)
    return "project-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def split_h2_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\n", text))
    result: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1), text[match.start():end]))
    return result


def _source_inventory(inventory: dict) -> dict:
    matches = [item for item in inventory.get("files", []) if item.get("path") == "wiki/projects.md"]
    if len(matches) != 1:
        raise ValueError("inventory must contain exactly one wiki/projects.md record")
    return matches[0]


def _aliases(heading: str, section_meta: dict) -> list[dict[str, str]]:
    anchor = section_meta["anchor"]
    return [
        {"type": "legacy_heading", "value": heading},
        {"type": "obsidian_locator", "value": f"wiki/projects.md#{anchor['obsidian']}"},
        {"type": "markdown_locator", "value": f"wiki/projects.md#{anchor['markdown_slug']}"},
    ]


def render_project_page(entry: dict, section: str) -> str:
    lines = [
        f"# {entry['title']}（项目历史）",
        "",
        f"- stable ID：`{entry['id']}`",
        f"- subtype：`{entry['subtype']}`",
        f"- legacy locator：`wiki/projects.md#{entry['legacy_heading']}`",
        f"- source section SHA-256：`{entry['source_section_sha256']}`",
        "",
        "以下区块逐字符保留迁移前的完整 H2 section；当前 Dashboard 字段仍以 registry 为准。",
        "",
        SECTION_START,
    ]
    return "\n".join(lines) + "\n" + section + SECTION_END + "\n"


def preserved_section(page_text: str) -> str:
    prefix = SECTION_START + "\n"
    suffix = SECTION_END
    start = page_text.index(prefix) + len(prefix)
    end = page_text.index(suffix, start)
    return page_text[start:end]


def build_catalog_targets(vault: Path, inventory_path: Path) -> tuple[dict[Path, str], dict]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    source_meta = _source_inventory(inventory)
    source_path = vault / "wiki/projects.md"
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != source_meta["sha256"]:
        raise ValueError("wiki/projects.md file hash differs from immutable inventory")
    source_text = source_bytes.decode("utf-8")
    if normalize_section(source_text) != source_text:
        raise ValueError("wiki/projects.md must already be LF + NFC so exact text can be preserved")

    sections = split_h2_sections(source_text)
    section_meta = source_meta.get("sections", [])
    if len(sections) != 11 or len(section_meta) != 11:
        raise ValueError("project migration requires exactly 11 inventoried H2 sections")

    targets: dict[Path, str] = {}
    entries: list[dict] = []
    seen_ids: set[str] = set()
    for (heading, section), meta in zip(sections, section_meta, strict=True):
        if heading != meta["heading"]:
            raise ValueError(f"section heading mismatch: {heading!r}")
        digest = section_sha256(section)
        if digest != meta["normalized_sha256"]:
            raise ValueError(f"section hash mismatch: {heading}")
        stable_id = stable_project_id(heading)
        if stable_id in seen_ids:
            raise ValueError(f"duplicate stable ID: {stable_id}")
        seen_ids.add(stable_id)
        locator = f"wiki/catalog/projects/{stable_id}.md"
        entry = {
            "id": stable_id,
            "kind": "project",
            "subtype": "project_or_infrastructure_history",
            "title": heading,
            "legacy_heading": heading,
            "aliases": _aliases(heading, meta),
            "canonical_locator": locator,
            "source_section_sha256": digest,
            "source": {
                "path": "wiki/projects.md",
                "start_line": meta["start_line"],
                "end_line": meta["end_line"],
            },
        }
        entries.append(entry)
        page = render_project_page(entry, section)
        if preserved_section(page) != section or section_sha256(preserved_section(page)) != digest:
            raise AssertionError(f"rendered section was not lossless: {heading}")
        targets[vault / locator] = page

    catalog = {
        "schema_version": "1.0",
        "stable_id_basis": "project + NUL + NFC legacy heading; SHA-256 first 16 hex",
        "entries": entries,
    }
    targets[vault / "wiki/catalog/catalog.json"] = json.dumps(
        catalog, ensure_ascii=False, indent=2
    ) + "\n"
    return targets, catalog


def migrate(vault: Path, inventory_path: Path) -> dict:
    targets, catalog = build_catalog_targets(vault, inventory_path)
    maintainer = _load_maintainer()
    maintainer.atomic_replace_many(targets)
    for entry in catalog["entries"]:
        page = (vault / entry["canonical_locator"]).read_text(encoding="utf-8")
        if section_sha256(preserved_section(page)) != entry["source_section_sha256"]:
            raise RuntimeError(f"post-write section hash mismatch: {entry['id']}")
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args()
    catalog = migrate(args.vault.expanduser().resolve(), args.inventory.expanduser().resolve())
    print(f"migrated {len(catalog['entries'])} project sections; schema={catalog['schema_version']}")


if __name__ == "__main__":
    main()
