#!/usr/bin/env python3
"""Read-only audit for the shared Obsidian Codex Memory vault.

This includes governed cold-asset receipts: action counts, locator state, tree
fingerprints, migration guards, and active canonical references are fail-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

_MAINTAIN_SPEC = importlib.util.spec_from_file_location(
    "codex_memory_maintain_memory", Path(__file__).with_name("maintain_memory.py")
)
if not _MAINTAIN_SPEC or not _MAINTAIN_SPEC.loader:
    raise RuntimeError("cannot load maintain_memory.py")
_MAINTAIN_MODULE = importlib.util.module_from_spec(_MAINTAIN_SPEC)
sys.modules[_MAINTAIN_SPEC.name] = _MAINTAIN_MODULE
_MAINTAIN_SPEC.loader.exec_module(_MAINTAIN_MODULE)
generation_issues = _MAINTAIN_MODULE.generation_issues

_COLD_SPEC = importlib.util.spec_from_file_location(
    "codex_memory_cold_assets", Path(__file__).with_name("converge_cold_assets.py")
)
if not _COLD_SPEC or not _COLD_SPEC.loader:
    raise RuntimeError("cannot load converge_cold_assets.py")
_COLD_MODULE = importlib.util.module_from_spec(_COLD_SPEC)
sys.modules[_COLD_SPEC.name] = _COLD_MODULE
_COLD_SPEC.loader.exec_module(_COLD_MODULE)
tree_fingerprint = _COLD_MODULE.tree_fingerprint

DEFAULT_VAULT = "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory"
REQUIRED_FILES = [
    "_index.md",
    "AGENTS.memory.md",
    "inbox.md",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/source-of-truth-manifest.json",
    "wiki/system-convergence-status.json",
    "wiki/workflows/codex-memory-system-map.md",
]
REQUIRED_WIKI_DIRS = [
    "wiki/concepts",
    "wiki/entities",
    "wiki/workflows",
    "wiki/claims",
    "wiki/sources",
]
IGNORED_DIR_NAMES = {".backups", ".obsidian", ".trash"}
MANIFEST_PATH = "wiki/source-of-truth-manifest.json"
HEALTH_JSON_PATH = "wiki/memory-system-health.json"
HEALTH_MARKDOWN_PATH = "wiki/memory-system-health.md"
COLD_ASSET_INVENTORY_PATH = "wiki/cold-asset-inventory.json"
SYSTEM_MAP_PATH = "wiki/workflows/codex-memory-system-map.md"
CONVERGENCE_STATUS_PATH = "wiki/system-convergence-status.json"
CONVERGENCE_ALLOWED_STATUSES = {"achieved", "waiting_external", "requires_approval", "incomplete"}
CONVERGENCE_EXTERNAL_ROLES = {"canonical_contract", "observed_derived_view"}
MANIFEST_CLASSIFICATIONS = {
    "canonical_evidence",
    "canonical_state",
    "curated_knowledge",
    "derived_view",
    "temporary",
    "cold_backup",
}
NONCANONICAL_CLASSES = {"derived_view", "temporary", "cold_backup"}
REQUIRED_RESOURCE_FIELDS = {
    "paths",
    "classification",
    "owner_role",
    "producer",
    "consumers",
    "mutability",
    "rebuildability",
    "authoritative_fields",
    "legacy_transition_notes",
    "canonical_use",
}
LIVE_SUFFIXES = {".md", ".json"}
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|.*?)?\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]+)?\)")
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "obsidian://")
LOG_HEADING_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] .+")
MALFORMED_LOG_HEADING_RE = re.compile(r"^#{1,6} \d{4}-\d{2}-\d{2}\b")
DATE_HEADING_RE = re.compile(r"^(#{1,6}) \[(\d{4}-\d{2}-\d{2})\]\b")
MALFORMED_DATE_HEADING_RE = re.compile(r"^(#{1,6}) \d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class LinkIssue:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class PageMetric:
    path: str
    lines: int
    bytes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="Codex Memory vault path")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    parser.add_argument(
        "--large-page-lines",
        type=int,
        default=220,
        help="Flag wiki pages longer than this many lines",
    )
    parser.add_argument(
        "--write-health",
        action="store_true",
        help="Atomically refresh the canonical machine and Markdown health artifacts",
    )
    parser.add_argument("--health-json", default=HEALTH_JSON_PATH, help="Vault-relative health JSON path")
    parser.add_argument(
        "--health-markdown",
        default=HEALTH_MARKDOWN_PATH,
        help="Vault-relative health Markdown path",
    )
    parser.add_argument(
        "--h5-url",
        default="http://127.0.0.1:8792/api/fresh-snapshot",
        help="Read-only H5 freshness endpoint",
    )
    parser.add_argument("--skip-h5", action="store_true", help="Record the H5 check as unavailable")
    parser.add_argument(
        "--observed-at",
        default="",
        help="Fixed ISO timestamp for deterministic replay/tests; defaults to current local time",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero for vault integrity or manifest validation errors",
    )
    return parser.parse_args()


def iter_markdown(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.md")):
        if any(part in IGNORED_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        yield path


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def target_without_suffix(value: str) -> str:
    return value[:-3] if value.endswith(".md") else value


def build_aliases(root: Path, markdown_files: list[Path]) -> tuple[dict[str, Path], dict[str, list[str]]]:
    aliases: dict[str, Path] = {}
    collisions: dict[str, list[str]] = {}

    def add(alias: str, path: Path) -> None:
        alias = target_without_suffix(alias.strip())
        if not alias:
            return
        existing = aliases.get(alias)
        if existing and existing != path:
            collisions.setdefault(alias, sorted({rel(root, existing), rel(root, path)}))
            return
        aliases[alias] = path

    for path in markdown_files:
        path_rel = rel(root, path)
        no_suffix = target_without_suffix(path_rel)
        add(no_suffix, path)
        if path.name != "_README.md":
            add(path.name[:-3], path)
        if no_suffix.startswith("wiki/"):
            add(no_suffix[5:], path)
        if no_suffix.startswith("raw/"):
            add(no_suffix[4:], path)
    return aliases, collisions


def resolve_wiki_target(root: Path, source: Path, target: str, aliases: dict[str, Path]) -> Path | None:
    cleaned = target_without_suffix(target.strip())
    if not cleaned or cleaned.startswith(EXTERNAL_LINK_PREFIXES):
        return None
    if "/" not in cleaned:
        sibling = source.parent / f"{cleaned}.md"
        if sibling.exists():
            try:
                sibling.relative_to(root)
            except ValueError:
                pass
            else:
                return sibling
    ancestor = source.parent
    while True:
        nested = ancestor / f"{cleaned}.md"
        if nested.exists():
            try:
                nested.relative_to(root)
            except ValueError:
                pass
            else:
                return nested
        if ancestor == root:
            break
        ancestor = ancestor.parent
    candidates = [
        cleaned,
        f"wiki/{cleaned}",
        f"raw/{cleaned}",
    ]
    for candidate in candidates:
        if candidate in aliases:
            return aliases[candidate]
    return None


def resolve_markdown_target(root: Path, source: Path, target: str) -> Path | None:
    if target.startswith(EXTERNAL_LINK_PREFIXES):
        return None
    raw = unquote(target.split("#", 1)[0])
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (source.parent / candidate).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def classify_legacy_link(root: Path, source: Path, target: str) -> str:
    """Classify immutable legacy absolute links without treating them as vault failures."""
    raw = target.split("#", 1)[0]
    decoded = unquote(raw)
    candidate = Path(decoded)
    if not candidate.is_absolute():
        return ""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return "legacy_external" if rel(root, source).startswith("raw/") else ""
    return "legacy_encoded_absolute" if decoded != raw else "legacy_absolute"


def iter_live_manifest_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LIVE_SUFFIXES:
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        result.append(path)
    return result


def _manifest_glob(pattern: str) -> str:
    return (
        pattern.replace("YYYY-MM-DD", "*")
        .replace("YYYY-MM", "*")
        .replace("YYYY", "*")
    )


def _safe_manifest_path(value: str) -> bool:
    if not value or value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    parts = Path(value).parts
    return ".." not in parts and "\\" not in value


def _matched_live_paths(root: Path, pattern: str, live_paths: set[Path]) -> set[Path]:
    return {
        path.resolve()
        for path in root.glob(_manifest_glob(pattern))
        if path.is_file() and path.resolve() in live_paths
    }


def audit_manifest(root: Path) -> dict:
    root = root.expanduser().resolve()
    manifest_path = root / MANIFEST_PATH
    errors: list[str] = []
    warnings: list[str] = []
    unmatched_patterns: list[str] = []
    duplicate_patterns: list[str] = []
    conflicting_paths: list[dict[str, object]] = []
    unmatched_live_paths: list[str] = []
    data: dict = {}
    if not manifest_path.is_file():
        return {
            "path": MANIFEST_PATH,
            "valid": False,
            "errors": ["manifest is missing"],
            "warnings": [],
            "unmatched_patterns": [],
            "duplicate_patterns": [],
            "conflicting_paths": [],
            "unmatched_live_paths": [],
            "resource_count": 0,
        }
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
        else:
            errors.append("manifest root must be an object")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest JSON is invalid: {exc}")

    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be `1.0`")
    if data.get("path_format") != "vault_relative":
        errors.append("path_format must be `vault_relative`")
    declared = set(data.get("classifications") or [])
    if declared != MANIFEST_CLASSIFICATIONS:
        errors.append("classifications must exactly match the supported set")
    policy = data.get("policies")
    if not isinstance(policy, dict) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", str(policy.get("daily_receipt_enforcement_start") or "")
    ):
        errors.append("policies.daily_receipt_enforcement_start must be an ISO date")

    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        errors.append("resources must be a non-empty array")
        resources = []

    live_files = iter_live_manifest_files(root)
    live_paths = {path.resolve() for path in live_files}
    owners_by_path: dict[Path, list[dict[str, str]]] = {}
    seen_patterns: dict[str, int] = {}
    for index, resource in enumerate(resources):
        label = f"resources[{index}]"
        if not isinstance(resource, dict):
            errors.append(f"{label} must be an object")
            continue
        missing = sorted(REQUIRED_RESOURCE_FIELDS - set(resource))
        if missing:
            errors.append(f"{label} missing required fields: {', '.join(missing)}")
        classification = str(resource.get("classification") or "")
        if classification not in MANIFEST_CLASSIFICATIONS:
            errors.append(f"{label}.classification is invalid: {classification!r}")
        canonical_use = str(resource.get("canonical_use") or "")
        expected_use = "forbidden" if classification in NONCANONICAL_CLASSES else "allowed"
        if canonical_use != expected_use:
            errors.append(
                f"{label}.canonical_use must be {expected_use!r} for {classification!r}"
            )
        for field in ("owner_role", "producer", "mutability", "legacy_transition_notes"):
            if not isinstance(resource.get(field), str) or not resource.get(field).strip():
                errors.append(f"{label}.{field} must be a non-empty string")
        for field in ("consumers", "authoritative_fields"):
            value = resource.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                errors.append(f"{label}.{field} must be a non-empty string array")
        rebuildability = resource.get("rebuildability")
        if not isinstance(rebuildability, dict) or "rebuildable" not in rebuildability or not str(
            rebuildability.get("method") or ""
        ).strip():
            errors.append(f"{label}.rebuildability must contain rebuildable and method")

        paths = resource.get("paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"{label}.paths must be a non-empty array")
            continue
        exclusions = resource.get("exclude_paths") or []
        if not isinstance(exclusions, list) or not all(isinstance(item, str) for item in exclusions):
            errors.append(f"{label}.exclude_paths must be a string array when present")
            exclusions = []
        excluded: set[Path] = set()
        for pattern in exclusions:
            if not _safe_manifest_path(pattern):
                errors.append(f"{label}.exclude_paths contains unsafe path: {pattern!r}")
                continue
            excluded.update(_matched_live_paths(root, pattern, live_paths))
        for pattern in paths:
            if not isinstance(pattern, str) or not _safe_manifest_path(pattern):
                errors.append(f"{label}.paths contains non-vault-relative path: {pattern!r}")
                continue
            if pattern in seen_patterns:
                duplicate_patterns.append(pattern)
            seen_patterns[pattern] = index
            matches = _matched_live_paths(root, pattern, live_paths) - excluded
            if not matches:
                unmatched_patterns.append(pattern)
            for path in matches:
                owners_by_path.setdefault(path, []).append(
                    {
                        "classification": classification,
                        "owner_role": str(resource.get("owner_role") or ""),
                        "pattern": pattern,
                    }
                )

    for path, owners in sorted(owners_by_path.items(), key=lambda item: str(item[0])):
        unique = {(item["classification"], item["owner_role"]) for item in owners}
        if len(owners) > 1:
            conflicting_paths.append({"path": rel(root, path), "owners": owners, "conflict": len(unique) > 1})
    unmatched_live_paths = [
        rel(root, path)
        for path in live_files
        if path.resolve() not in owners_by_path
    ]
    if duplicate_patterns:
        errors.append(f"duplicate path patterns: {', '.join(sorted(set(duplicate_patterns)))}")
    ownership_conflicts = [item for item in conflicting_paths if item["conflict"]]
    if ownership_conflicts:
        errors.append(f"{len(ownership_conflicts)} live paths have conflicting ownership")
    if unmatched_live_paths:
        errors.append(f"{len(unmatched_live_paths)} major live paths are unmatched")
    if unmatched_patterns:
        warnings.append(f"{len(unmatched_patterns)} manifest patterns currently match no live file")
    return {
        "path": MANIFEST_PATH,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "unmatched_patterns": sorted(set(unmatched_patterns)),
        "duplicate_patterns": sorted(set(duplicate_patterns)),
        "conflicting_paths": conflicting_paths,
        "unmatched_live_paths": unmatched_live_paths,
        "resource_count": len(resources),
        "data": data,
    }


def audit_system_convergence(root: Path, manifest: dict | None = None) -> list[str]:
    """Validate the governed convergence checklist and its human-readable map."""
    root = root.expanduser().resolve()
    issues: list[str] = []
    status_path = root / CONVERGENCE_STATUS_PATH
    map_path = root / SYSTEM_MAP_PATH
    if not status_path.is_file():
        return [f"missing convergence status: {CONVERGENCE_STATUS_PATH}"]
    if not map_path.is_file():
        return [f"missing system map: {SYSTEM_MAP_PATH}"]
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid convergence status JSON: {exc}"]
    if not isinstance(data, dict):
        return ["convergence status root must be an object"]
    if data.get("schema_version") != "1.0":
        issues.append("convergence status schema_version must be 1.0")
    if data.get("artifact_type") != "codex_memory_system_convergence_status":
        issues.append("convergence status artifact_type is invalid")
    allowed = data.get("allowed_statuses")
    if not isinstance(allowed, list) or set(allowed) != CONVERGENCE_ALLOWED_STATUSES or len(allowed) != len(set(allowed)):
        issues.append("convergence allowed_statuses must exactly match the supported set")

    external_paths: dict[str, dict] = {}
    external = data.get("external_evidence_paths")
    if not isinstance(external, list):
        issues.append("convergence external_evidence_paths must be an array")
        external = []
    for entry in external:
        if not isinstance(entry, dict):
            issues.append("convergence external evidence entry must be an object")
            continue
        value = str(entry.get("path") or "")
        role = str(entry.get("role") or "")
        path = Path(value).expanduser()
        if not value or not path.is_absolute():
            issues.append(f"convergence external evidence path must be absolute: {value!r}")
            continue
        if value in external_paths:
            issues.append(f"duplicate convergence external evidence path: {value}")
        external_paths[value] = entry
        if role not in CONVERGENCE_EXTERNAL_ROLES:
            issues.append(f"unsupported convergence external evidence role: {role!r}")
        expected_canonical = role == "canonical_contract"
        if entry.get("canonical") is not expected_canonical:
            issues.append(f"convergence external evidence canonical flag mismatches role: {value}")
        if not path.is_file():
            issues.append(f"convergence external evidence path is missing: {value}")

    checklist = data.get("checklist")
    if not isinstance(checklist, list) or not checklist:
        issues.append("convergence checklist must be a non-empty array")
        checklist = []
    seen_ids: set[str] = set()
    computed = {status: 0 for status in CONVERGENCE_ALLOWED_STATUSES}
    for index, item in enumerate(checklist):
        label = f"convergence checklist[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            issues.append(f"{label} needs a non-empty id")
        elif item_id in seen_ids:
            issues.append(f"duplicate convergence checklist id: {item_id}")
        seen_ids.add(item_id)
        status = str(item.get("status") or "")
        if status not in CONVERGENCE_ALLOWED_STATUSES:
            issues.append(f"{label} has unsupported status: {status!r}")
        else:
            computed[status] += 1
        if not str(item.get("label") or "").strip():
            issues.append(f"{label} needs a non-empty label")
        if not str(item.get("exact_next_action") or "").strip():
            issues.append(f"{label} needs a non-empty exact_next_action")
        evidence_paths = item.get("evidence_paths")
        if not isinstance(evidence_paths, list) or not evidence_paths:
            issues.append(f"{label} needs non-empty evidence_paths")
            evidence_paths = []
        for value in evidence_paths:
            if not isinstance(value, str) or not value:
                issues.append(f"{label} has invalid evidence path: {value!r}")
                continue
            if "memory-system-health" in value:
                issues.append(f"{label} uses derived health as convergence evidence: {value}")
                continue
            path = Path(value).expanduser()
            if path.is_absolute():
                if value not in external_paths:
                    issues.append(f"{label} uses undeclared external evidence path: {value}")
            elif not _safe_manifest_path(value):
                issues.append(f"{label} has unsafe vault evidence path: {value}")
            elif not (root / value).is_file():
                issues.append(f"{label} evidence path is missing: {value}")
        absent_paths = item.get("expected_absent_paths") or []
        if not isinstance(absent_paths, list):
            issues.append(f"{label} expected_absent_paths must be an array")
            absent_paths = []
        for value in absent_paths:
            if not isinstance(value, str) or not _safe_manifest_path(value):
                issues.append(f"{label} has invalid expected absent path: {value!r}")
            elif (root / value).exists():
                issues.append(f"{label} expected absent path now exists: {value}")

    counts = data.get("status_counts")
    expected_counts = {**computed, "total": len(checklist)}
    if counts != expected_counts:
        issues.append(
            "convergence status_counts drift: "
            f"expected {json.dumps(expected_counts, ensure_ascii=False, sort_keys=True)}"
        )

    if manifest is None:
        manifest = audit_manifest(root).get("data")
    resources = manifest.get("resources") if isinstance(manifest, dict) else []
    for path_value, expected_classification in (
        (CONVERGENCE_STATUS_PATH, "canonical_state"),
        (SYSTEM_MAP_PATH, "curated_knowledge"),
    ):
        owners = [
            resource
            for resource in resources or []
            if isinstance(resource, dict) and path_value in (resource.get("paths") or [])
        ]
        if len(owners) != 1 or owners[0].get("classification") != expected_classification:
            issues.append(
                f"{path_value} must have one explicit {expected_classification} manifest owner"
            )

    contract_entries = [entry for entry in external if isinstance(entry, dict) and entry.get("role") == "canonical_contract"]
    if len(contract_entries) != 1:
        issues.append("convergence status must declare exactly one canonical_contract")
    else:
        contract_path = Path(str(contract_entries[0].get("path") or ""))
        if contract_path.is_file():
            match = re.search(
                r'^RECEIPT_REQUIRED_FROM\s*=\s*["\'](\d{4}-\d{2}-\d{2})["\']',
                contract_path.read_text(encoding="utf-8"),
                re.M,
            )
            manifest_start = str(((manifest or {}).get("policies") or {}).get("daily_receipt_enforcement_start") or "")
            if not match:
                issues.append("canonical compiled source contract lacks RECEIPT_REQUIRED_FROM")
            elif match.group(1) != manifest_start:
                issues.append(
                    f"receipt enforcement drift: contract {match.group(1)} != manifest {manifest_start}"
                )

    for relative in ("_index.md", "wiki/index.md", "wiki/workflows/codex-memory-system-architecture.md"):
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "codex-memory-system-map" not in text:
            issues.append(f"system map link missing from {relative}")
    map_text = map_path.read_text(encoding="utf-8")
    if "../system-convergence-status.json" not in map_text:
        issues.append("system map must link the convergence status")
    if "../source-of-truth-manifest.json" not in map_text:
        issues.append("system map must link the source-of-truth manifest")
    return issues


def audit(root: Path, large_page_lines: int) -> dict:
    root = root.expanduser().resolve()
    markdown_files = list(iter_markdown(root))
    aliases, alias_collisions = build_aliases(root, markdown_files)

    missing_required_files = [item for item in REQUIRED_FILES if not (root / item).is_file()]
    missing_required_dirs = [item for item in REQUIRED_WIKI_DIRS if not (root / item).is_dir()]

    broken_links: list[LinkIssue] = []
    legacy_links: list[LinkIssue] = []
    inbound: dict[Path, int] = {path: 0 for path in markdown_files}
    linked_from_indexes: set[Path] = set()
    index_files = {
        root / "_index.md",
        root / "wiki/index.md",
        *sorted((root / "wiki").rglob("_index.md")),
        root / "wiki/cognitive-observatory/README.md",
        root / "wiki/cognitive-observatory/99_index/cognitive-index.md",
    }

    for source in markdown_files:
        text = source.read_text(encoding="utf-8")
        for target in WIKI_LINK_RE.findall(text):
            resolved = resolve_wiki_target(root, source, target, aliases)
            if resolved:
                inbound[resolved] = inbound.get(resolved, 0) + 1
                if source in index_files:
                    linked_from_indexes.add(resolved)
            elif not target.startswith(EXTERNAL_LINK_PREFIXES):
                broken_links.append(LinkIssue(rel(root, source), target, "wiki"))

        for target in MD_LINK_RE.findall(text):
            legacy_kind = classify_legacy_link(root, source, target)
            if legacy_kind:
                legacy_links.append(LinkIssue(rel(root, source), target, legacy_kind))
            resolved = resolve_markdown_target(root, source, target)
            if resolved:
                inbound[resolved] = inbound.get(resolved, 0) + 1
            elif not target.startswith(EXTERNAL_LINK_PREFIXES) and not legacy_kind:
                broken_links.append(LinkIssue(rel(root, source), target, "markdown"))

    wiki_files = [path for path in markdown_files if rel(root, path).startswith("wiki/")]
    index_exempt_names = {"index.md", "_index.md", "README.md", "_README.md", "log.md"}
    unindexed = [
        rel(root, path)
        for path in wiki_files
        if path not in linked_from_indexes and path.name not in index_exempt_names
    ]

    empty_or_tiny: list[PageMetric] = []
    large_pages: list[PageMetric] = []
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        metric = PageMetric(rel(root, path), len(text.splitlines()), len(text.encode("utf-8")))
        if len(text.strip()) < 80:
            empty_or_tiny.append(metric)
        if rel(root, path).startswith("wiki/") and metric.lines > large_page_lines:
            large_pages.append(metric)

    raw_files = [rel(root, path) for path in markdown_files if rel(root, path).startswith("raw/")]
    wiki_source_files = [
        rel(root, path)
        for path in markdown_files
        if rel(root, path).startswith("wiki/sources/") and path.name != "_index.md"
    ]
    log_order_issues = audit_log_timeline(root)
    maintenance_generation_issues = generation_issues(root)
    date_heading_issues = audit_date_headings(root, markdown_files)
    manifest_report = audit_manifest(root)
    cold_asset_inventory_issues = audit_cold_asset_inventory(root, manifest_report.get("data"))
    system_convergence_issues = audit_system_convergence(root, manifest_report.get("data"))

    return {
        "vault": str(root),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "counts": {
            "markdown_files": len(markdown_files),
            "wiki_files": len(wiki_files),
            "raw_files": len(raw_files),
            "wiki_source_files": len(wiki_source_files),
            "broken_links": len(broken_links),
            "legacy_links": len(legacy_links),
            "unindexed_wiki_pages": len(unindexed),
            "large_wiki_pages": len(large_pages),
            "empty_or_tiny_pages": len(empty_or_tiny),
            "alias_collisions": len(alias_collisions),
            "log_order_issues": len(log_order_issues),
            "maintenance_generation_issues": len(maintenance_generation_issues),
            "date_heading_issues": len(date_heading_issues),
            "manifest_errors": len(manifest_report["errors"]),
            "unmatched_manifest_paths": len(manifest_report["unmatched_live_paths"]),
            "cold_asset_inventory_issues": len(cold_asset_inventory_issues),
            "system_convergence_issues": len(system_convergence_issues),
        },
        "missing_required_files": missing_required_files,
        "missing_required_dirs": missing_required_dirs,
        "broken_links": [asdict(item) for item in broken_links],
        "legacy_links": [asdict(item) for item in legacy_links],
        "unindexed_wiki_pages": unindexed,
        "large_wiki_pages": [asdict(item) for item in large_pages],
        "empty_or_tiny_pages": [asdict(item) for item in empty_or_tiny],
        "alias_collisions": alias_collisions,
        "log_order_issues": log_order_issues,
        "maintenance_generation_issues": maintenance_generation_issues,
        "date_heading_issues": date_heading_issues,
        "manifest": manifest_report,
        "cold_asset_inventory_issues": cold_asset_inventory_issues,
        "system_convergence_issues": system_convergence_issues,
    }


def _canonical_active_text_paths(root: Path, manifest: dict | None) -> list[Path]:
    """Return manifest-owned canonical/curated text files outside backup storage."""
    if not isinstance(manifest, dict):
        report = audit_manifest(root)
        manifest = report.get("data") if isinstance(report, dict) else None
    if not isinstance(manifest, dict):
        return []
    live_files = iter_live_manifest_files(root)
    live_paths = {item.resolve() for item in live_files}
    result: set[Path] = set()
    for resource in manifest.get("resources") or []:
        if not isinstance(resource, dict) or resource.get("classification") in NONCANONICAL_CLASSES:
            continue
        excluded: set[Path] = set()
        for pattern in resource.get("exclude_paths") or []:
            if isinstance(pattern, str) and _safe_manifest_path(pattern):
                excluded.update(_matched_live_paths(root, pattern, live_paths))
        for pattern in resource.get("paths") or []:
            if isinstance(pattern, str) and _safe_manifest_path(pattern):
                result.update(_matched_live_paths(root, pattern, live_paths) - excluded)
    return sorted(
        path
        for path in result
        if path.suffix in {".md", ".json", ".py", ".toml", ".plist"}
        and rel(root, path) != COLD_ASSET_INVENTORY_PATH
    )


def _locator_strings(root: Path, path: Path) -> set[str]:
    values = {str(path)}
    if path.is_relative_to(root):
        values.add(path.relative_to(root).as_posix())
    return values


def audit_cold_asset_inventory(root: Path, manifest: dict | None = None) -> list[str]:
    root = root.expanduser().resolve()
    path = root / COLD_ASSET_INVENTORY_PATH
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"invalid cold asset inventory: {error}"]
    issues: list[str] = []
    if data.get("schema_version") != "1.0":
        issues.append("cold asset inventory must use schema_version 1.0")
    policy = data.get("policy") or {}
    if policy.get("cold_assets_are_canonical_inputs") is not False:
        issues.append("cold assets must be forbidden as canonical inputs")
    if policy.get("ambiguous_or_referenced_assets_move") is not False:
        issues.append("referenced or ambiguous cold assets must be retained")
    scope_roots = []
    for value in data.get("scope_roots") or []:
        try:
            scope_roots.append(Path(value).expanduser().resolve())
        except (OSError, TypeError):
            issues.append(f"invalid cold inventory scope root: {value!r}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        return issues + ["cold asset inventory entries must be an array"]
    seen_ids: set[str] = set()
    counts = {
        "moved_assets": 0,
        "retained_referenced_assets": 0,
        "deleted_reproducible_caches": 0,
        "retained_ambiguous_caches": 0,
    }
    active_text_paths = _canonical_active_text_paths(root, manifest)
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("cold asset inventory entry must be an object")
            continue
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            issues.append("cold asset inventory entry missing asset_id")
            continue
        if asset_id in seen_ids:
            issues.append(f"duplicate cold asset ID: {asset_id}")
        seen_ids.add(asset_id)
        action = entry.get("action")
        if action == "moved_to_cold_archive":
            counts["moved_assets"] += 1
        elif action == "retained_in_place":
            counts["retained_referenced_assets"] += 1
        elif action == "deleted_reproducible":
            counts["deleted_reproducible_caches"] += 1
            continue
        elif action == "retained_ambiguous":
            counts["retained_ambiguous_caches"] += 1
        else:
            issues.append(f"cold asset {asset_id} has unsupported action: {action!r}")
            continue
        current_value = entry.get("current_path")
        original_value = entry.get("original_path")
        if not isinstance(current_value, str) or not isinstance(original_value, str):
            issues.append(f"cold asset {asset_id} has invalid paths")
            continue
        current = Path(current_value).expanduser().resolve()
        original = Path(original_value).expanduser().resolve()
        if scope_roots and not any(current == scope or current.is_relative_to(scope) for scope in scope_roots):
            issues.append(f"cold asset {asset_id} current path is outside declared scope")
            continue
        if not current.exists():
            issues.append(f"cold asset {asset_id} current path is missing: {current}")
            continue
        if action == "moved_to_cold_archive" and original.exists():
            issues.append(f"cold asset {asset_id} original path reappeared: {original}")
        try:
            actual = tree_fingerprint(current)
        except OSError as error:
            issues.append(f"cold asset {asset_id} cannot be hashed: {error}")
            continue
        for field in ("tree_sha256", "file_count", "byte_count"):
            if actual[field] != entry.get(field):
                issues.append(f"cold asset {asset_id} inventory drift: {field}")
        if entry.get("classification") == "unreferenced_cold_backup":
            if entry.get("reference_evidence"):
                issues.append(f"unreferenced cold asset {asset_id} contains reference evidence")
            locator_strings = _locator_strings(root, current) | _locator_strings(root, original)
            for active in active_text_paths:
                text = active.read_text(encoding="utf-8", errors="replace")
                if any(value in text for value in locator_strings):
                    issues.append(
                        f"cold asset {asset_id} is referenced by active canonical file: {rel(root, active)}"
                    )
                    break
    summary = data.get("summary") or {}
    for key, expected in counts.items():
        if summary.get(key) != expected:
            issues.append(f"cold asset inventory summary drift: {key}")
    receipt = data.get("migration_receipt") or {}
    if receipt.get("protected_hashes_equal") is not True or receipt.get("protected_before") != receipt.get("protected_after"):
        issues.append("cold asset migration protected hashes are not equal")
    if receipt.get("moved_hashes_equal") is not True:
        issues.append("cold asset migration moved hash equality is not proven")
    return issues


def audit_log_timeline(root: Path) -> list[str]:
    """Check reverse chronology in the compatibility log and canonical shards."""
    log_paths = [root / "wiki/log.md", *sorted((root / "wiki/logs").glob("[0-9]*/[0-9]*.md"))]

    issues: list[str] = []
    for log_path in log_paths:
        if not log_path.exists():
            continue
        dates: list[tuple[int, str]] = []
        for lineno, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
            match = LOG_HEADING_RE.match(line)
            if match:
                dates.append((lineno, match.group(1)))
                continue
            if MALFORMED_LOG_HEADING_RE.match(line):
                issues.append(f"{rel(root, log_path)} line {lineno}: malformed log heading `{line}`")
        previous: tuple[int, str] | None = None
        for current in dates:
            if previous and current[1] > previous[1]:
                issues.append(
                    f"{rel(root, log_path)} line {current[0]}: log date {current[1]} appears after "
                    f"date {previous[1]} on line {previous[0]}"
                )
            previous = current
    return issues


def audit_date_headings(root: Path, markdown_files: list[Path]) -> list[str]:
    """Check dated Markdown sections for consistent heading syntax and order."""
    issues: list[str] = []
    for path in markdown_files:
        dates: list[tuple[int, str]] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = DATE_HEADING_RE.match(line)
            if match:
                dates.append((lineno, match.group(2)))
                continue
            if MALFORMED_DATE_HEADING_RE.match(line):
                issues.append(f"{rel(root, path)} line {lineno}: malformed date heading `{line}`")

        previous: tuple[int, str] | None = None
        for current in dates:
            if previous and current[1] > previous[1]:
                issues.append(
                    f"{rel(root, path)} line {current[0]}: date {current[1]} appears after "
                    f"date {previous[1]} on line {previous[0]}"
                )
            previous = current
    return issues


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.M)
    return match.group(1).strip() if match else ""


def structured_candidate_ids(text: str) -> list[str]:
    match = re.search(
        r"^## Structured Candidates\s*\n+```json\s*\n(.*?)\n```",
        text,
        re.M | re.S,
    )
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return sorted(
        {
            str(item.get("candidate_id"))
            for item in payload
            if isinstance(item, dict) and item.get("candidate_id")
        }
    )


def _family_paths(root: Path, family: str) -> tuple[Path, Path, str, str]:
    if family == "chatgpt":
        return (
            root / "raw/conversations/chatgpt-daily",
            root / "wiki/sources/conversations/chatgpt-daily",
            "chatgpt-daily-report-",
            "chatgpt-daily-deposition-",
        )
    return (
        root / "raw/conversations/codex-daily",
        root / "wiki/sources/conversations/codex-daily",
        "codex-daily-report-",
        "codex-daily-deposition-",
    )


def _dated_files(root: Path, prefix: str, suffix: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    for path in sorted(root.glob(f"*/*{suffix}")):
        match = re.search(rf"{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})", path.name)
        if match:
            result[match.group(1)] = path
    return result


def build_daily_health(root: Path, manifest: dict) -> tuple[dict[str, list[dict]], dict]:
    policy = manifest.get("policies") if isinstance(manifest, dict) else {}
    enforcement_start = str((policy or {}).get("daily_receipt_enforcement_start") or "")
    ledger_path = root / "wiki/review-cycles/promotion-ledger.json"
    ledger_data = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    ledger_candidates = ledger_data.get("candidates", {})
    if isinstance(ledger_candidates, dict):
        # The canonical promotion ledger is keyed by stable candidate ID.  Use
        # those keys so membership stays correct even for older entries that do
        # not repeat candidate_id inside the value object.
        ledger_ids = {str(item) for item in ledger_candidates}
    elif isinstance(ledger_candidates, list):
        # Retain read compatibility for the early list-shaped ledger schema.
        ledger_ids = {
            str(item.get("candidate_id"))
            for item in ledger_candidates
            if isinstance(item, dict) and item.get("candidate_id")
        }
    else:
        ledger_ids = set()
    receipt_root = root / "wiki/review-cycles/daily-deposition"
    families: dict[str, list[dict]] = {}
    all_receipt_candidate_ids: set[str] = set()
    state_counts: dict[str, int] = {}
    integrity_issue_count = 0

    for family in ("chatgpt", "codex"):
        raw_root, source_root, report_prefix, receipt_prefix = _family_paths(root, family)
        raw_files = _dated_files(raw_root, report_prefix, ".md")
        source_files = _dated_files(source_root, report_prefix, ".md")
        receipt_files = _dated_files(receipt_root, receipt_prefix, ".json")
        rows: list[dict] = []
        for date in sorted(set(raw_files) | set(source_files) | set(receipt_files)):
            raw_path = raw_files.get(date)
            source_path = source_files.get(date)
            receipt_path = receipt_files.get(date)
            source_text = source_path.read_text(encoding="utf-8") if source_path else ""
            candidate_ids = structured_candidate_ids(source_text)
            coverage = frontmatter_value(source_text, "coverage")
            source_status = frontmatter_value(source_text, "status")
            receipt_required = bool(enforcement_start and date >= enforcement_start)
            issues: list[str] = []
            receipt: dict[str, object] = {
                "required": receipt_required,
                "present": bool(receipt_path),
                "status": "",
                "hashes_match": None,
                "candidate_count": 0,
                "candidate_ids": [],
            }
            if not raw_path:
                issues.append("raw_missing")
            if not source_path:
                issues.append("source_missing")
            if receipt_path:
                try:
                    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
                    receipt_ids = sorted(str(item) for item in receipt_data.get("candidate_ids", []))
                    receipt.update(
                        {
                            "status": str(receipt_data.get("status") or ""),
                            "candidate_count": int(receipt_data.get("candidate_count") or 0),
                            "candidate_ids": receipt_ids,
                        }
                    )
                    hashes_match = bool(raw_path and source_path) and (
                        receipt_data.get("raw_sha256") == sha256_file(raw_path)
                        and receipt_data.get("source_sha256") == sha256_file(source_path)
                    )
                    receipt["hashes_match"] = hashes_match
                    if receipt_data.get("status") != "completed":
                        issues.append("receipt_not_completed")
                    if not hashes_match:
                        issues.append("receipt_hash_mismatch")
                    if int(receipt_data.get("candidate_count") or 0) != len(receipt_ids):
                        issues.append("receipt_candidate_count_mismatch")
                    if receipt_ids != candidate_ids:
                        issues.append("source_receipt_candidates_mismatch")
                    ledger_missing = sorted(set(receipt_ids) - ledger_ids)
                    if ledger_missing:
                        issues.append("receipt_candidates_missing_from_ledger")
                    all_receipt_candidate_ids.update(receipt_ids)
                except (OSError, ValueError, json.JSONDecodeError):
                    issues.append("receipt_invalid")
            elif receipt_required:
                issues.append("receipt_missing")

            if issues:
                if any(item.endswith("_missing") for item in issues):
                    state = "missing"
                else:
                    state = "integrity_error"
                integrity_issue_count += 1
            elif not receipt_required:
                state = "legacy_compatible"
            elif coverage == "complete" and source_status in {"ready", "no_tasks"}:
                state = "healthy"
            else:
                state = "partial_access_incomplete"
            state_counts[state] = state_counts.get(state, 0) + 1
            rows.append(
                {
                    "date": date,
                    "raw": {"present": bool(raw_path), "path": rel(root, raw_path) if raw_path else ""},
                    "source": {
                        "present": bool(source_path),
                        "path": rel(root, source_path) if source_path else "",
                        "coverage": coverage,
                        "status": source_status,
                        "candidate_ids": candidate_ids,
                    },
                    "receipt": receipt,
                    "ledger_missing_candidate_ids": sorted(set(receipt.get("candidate_ids", [])) - ledger_ids),
                    "state": state,
                    "issues": issues,
                }
            )
        families[family] = rows
    summary = {
        "family_date_count": sum(len(rows) for rows in families.values()),
        "state_counts": dict(sorted(state_counts.items())),
        "receipt_count": sum(1 for rows in families.values() for row in rows if row["receipt"]["present"]),
        "receipt_hash_mismatch_count": sum(
            1
            for rows in families.values()
            for row in rows
            if row["receipt"]["present"] and row["receipt"]["hashes_match"] is not True
        ),
        "receipt_candidate_id_count": len(all_receipt_candidate_ids),
        "receipt_candidate_ids_missing_from_ledger": sorted(all_receipt_candidate_ids - ledger_ids),
        "ledger_candidate_count": len(ledger_ids),
        "integrity_issue_count": integrity_issue_count,
        "daily_receipt_enforcement_start": enforcement_start,
    }
    return families, summary


def fetch_h5_health(url: str, *, skip: bool = False, timeout: float = 3.0) -> dict:
    if skip:
        return {"status": "unavailable", "reason": "external check disabled"}
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "status": "available",
                "http_status": int(getattr(response, "status", 200)),
                "schema_version": payload.get("schema_version"),
                "generated_at": payload.get("generated_at", ""),
                "daily_updated_through": payload.get("daily_updated_through", ""),
                "freshness_mode": (payload.get("freshness") or {}).get("mode", ""),
                "summary": payload.get("summary") or {},
                "cache_control": response.headers.get("Cache-Control", ""),
            }
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}


def build_health_model(
    root: Path,
    audit_report: dict,
    *,
    observed_at: str,
    h5_health: dict,
) -> dict:
    manifest = audit_report.get("manifest", {}).get("data") or {}
    families, daily_summary = build_daily_health(root, manifest)
    registry_path = root / "wiki/project-dashboard-case-registry.json"
    registry: dict = {}
    registry_error = ""
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        registry_error = f"{type(exc).__name__}: {exc}"
    dashboard_path = root / "wiki/project-dashboard.md"
    dashboard_text = dashboard_path.read_text(encoding="utf-8") if dashboard_path.is_file() else ""
    updated_match = re.search(r"^- 更新时间：(.+)$", dashboard_text, re.M)
    internal_error_count = (
        len(audit_report.get("missing_required_files", []))
        + len(audit_report.get("missing_required_dirs", []))
        + len(audit_report.get("broken_links", []))
        + len(audit_report.get("log_order_issues", []))
        + len(audit_report.get("maintenance_generation_issues", []))
        + len(audit_report.get("date_heading_issues", []))
        + len(audit_report.get("manifest", {}).get("errors", []))
        + len(audit_report.get("system_convergence_issues", []))
        + int(daily_summary["integrity_issue_count"])
        + (1 if registry_error else 0)
    )
    return {
        "schema_version": "1.0",
        "artifact_type": "codex_memory_health",
        "generated_at": observed_at,
        "generator": "codex-memory-maintainer/scripts/audit_codex_memory.py",
        "vault_integrity": {
            "status": "healthy" if internal_error_count == 0 else "issues",
            "error_count": internal_error_count,
            "audit_counts": audit_report.get("counts", {}),
            "manifest_valid": bool(audit_report.get("manifest", {}).get("valid")),
            "manifest_errors": audit_report.get("manifest", {}).get("errors", []),
        },
        "daily": {"summary": daily_summary, "families": families},
        "registry": {
            "status": "available" if registry and not registry_error else "unavailable",
            "path": "wiki/project-dashboard-case-registry.json",
            "error": registry_error,
            "sha256": sha256_file(registry_path) if registry_path.is_file() else "",
            "version": registry.get("version"),
            "created_at": registry.get("created_at", ""),
            "updated_at": registry.get("updated_at", ""),
            "counts": {
                "cases": len(registry.get("cases", [])),
                "events": len(registry.get("events", [])),
                "value_candidates": len(registry.get("value_candidates", [])),
            },
        },
        "derived_views": {
            "project_dashboard": {
                "status": "available" if dashboard_path.is_file() else "unavailable",
                "path": "wiki/project-dashboard.md",
                "reported_updated_at": updated_match.group(1) if updated_match else "",
                "compatibility_banner": "生成兼容视图" in dashboard_text,
            },
            "h5": h5_health,
        },
        "legacy_links": audit_report.get("legacy_links", []),
    }


def _health_state_label(value: str) -> str:
    return {
        "healthy": "healthy",
        "partial_access_incomplete": "partial/access_incomplete",
        "legacy_compatible": "legacy-compatible",
        "missing": "missing",
        "integrity_error": "integrity_error",
    }.get(value, value)


def render_health_markdown(model: dict) -> str:
    daily = model["daily"]
    summary = daily["summary"]
    registry = model["registry"]
    h5 = model["derived_views"]["h5"]
    lines = [
        "<!-- generated by codex-memory-maintainer/scripts/audit_codex_memory.py; do not edit manually -->",
        "# Codex Memory 端到端健康报告",
        "",
        f"- 审计时间：{model['generated_at']}",
        "- 数据模型：`wiki/memory-system-health.json`",
        "- 架构与权威边界：[[workflows/codex-memory-system-architecture]]",
        f"- Vault integrity：`{model['vault_integrity']['status']}`；external H5：`{h5.get('status', 'unavailable')}`",
        "",
        "## 状态口径",
        "",
        "- `missing`：该日期应有的 raw/source 或适用的新式 receipt 缺失。",
        "- `legacy-compatible`：raw/source 成对存在，但早于强制 receipt 窗口。",
        "- `partial/access_incomplete`：内部流水线闭环，但来源覆盖有明确边界。",
        "- `healthy`：raw/source/receipt/ledger 一致，且来源为 `complete/ready` 或 `complete/no_tasks`。",
        "- `integrity_error`：文件存在，但 receipt 哈希、候选或 ledger 对账失败。",
        "",
        "## 总结",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
        f"| family-date | {summary['family_date_count']} |",
        f"| 状态计数 | {json.dumps(summary['state_counts'], ensure_ascii=False, sort_keys=True)} |",
        f"| daily receipts | {summary['receipt_count']}；哈希异常 {summary['receipt_hash_mismatch_count']} |",
        f"| receipt candidates | {summary['receipt_candidate_id_count']}；ledger 缺失 {len(summary['receipt_candidate_ids_missing_from_ledger'])} |",
        f"| promotion ledger | {summary['ledger_candidate_count']} candidates |",
        f"| receipt enforcement | {summary['daily_receipt_enforcement_start']} |",
        "",
    ]
    for family, title in (("chatgpt", "ChatGPT"), ("codex", "Codex")):
        lines.extend(
            [
                f"## {title} 日期矩阵",
                "",
                "| 日期 | raw → source | coverage/status | receipt | 候选入账 | 状态 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in daily["families"][family]:
            raw_source = f"{'✓' if row['raw']['present'] else 'missing'} → {'✓' if row['source']['present'] else 'missing'}"
            source_state = f"{row['source']['coverage'] or 'unknown'}/{row['source']['status'] or 'unknown'}"
            receipt = row["receipt"]
            if receipt["present"]:
                receipt_label = f"{receipt['status']}; hash {'✓' if receipt['hashes_match'] else '×'}"
            else:
                receipt_label = "missing" if receipt["required"] else "legacy n/a"
            candidate_count = len(receipt.get("candidate_ids") or [])
            ledger_label = f"{candidate_count}; ledger {'✓' if not row['ledger_missing_candidate_ids'] else '×'}"
            lines.append(
                f"| {row['date']} | {raw_source} | `{source_state}` | {receipt_label} | {ledger_label} | `{_health_state_label(row['state'])}` |"
            )
        lines.append("")
    lines.extend(
        [
            "## Registry 与派生视图",
            "",
            "| 资产 | 观察 | 判定 |",
            "| --- | --- | --- |",
            f"| project registry | version {registry.get('version')}; updated {registry.get('updated_at')}; {registry['counts']['cases']} cases / {registry['counts']['events']} events / {registry['counts']['value_candidates']} candidates | canonical state `{registry['status']}` |",
            f"| Obsidian Dashboard | updated {model['derived_views']['project_dashboard'].get('reported_updated_at') or 'unknown'}; compatibility banner {model['derived_views']['project_dashboard'].get('compatibility_banner')} | derived view |",
        ]
    )
    if h5.get("status") == "available":
        lines.append(
            f"| H5 snapshot | schema {h5.get('schema_version')}; generated {h5.get('generated_at')}; through {h5.get('daily_updated_through')}; freshness {h5.get('freshness_mode')} | derived view available |"
        )
    else:
        lines.append(f"| H5 snapshot | {h5.get('reason', 'unavailable')} | external check unavailable；不降低 vault integrity |")
    lines.extend(
        [
            "",
            "## Manifest 与链接审计",
            "",
            f"- Manifest：`{'valid' if model['vault_integrity']['manifest_valid'] else 'invalid'}`。",
            f"- 普通 broken links：{model['vault_integrity']['audit_counts'].get('broken_links', 0)}；legacy absolute/external links：{len(model.get('legacy_links', []))}。",
            "- Legacy absolute/external links 保留在 immutable raw 中并单独分类，不作为普通 vault 断链，也不授权改写证据。",
            "",
        ]
    )
    return "\n".join(lines)


def _stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def write_health_artifacts(root: Path, model: dict, json_rel: str, markdown_rel: str) -> None:
    if not _safe_manifest_path(json_rel) or not _safe_manifest_path(markdown_rel):
        raise ValueError("health artifact paths must be vault-relative")
    targets = [
        (root / json_rel, json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n"),
        (root / markdown_rel, render_health_markdown(model)),
    ]
    staged: list[tuple[Path, Path]] = []
    try:
        for target, text in targets:
            staged.append((target, _stage_text(target, text)))
        for target, temporary in staged:
            os.replace(temporary, target)
    finally:
        for _, temporary in staged:
            if temporary.exists():
                temporary.unlink()


def render_markdown(report: dict) -> str:
    lines = [
        "# Codex Memory Audit",
        "",
        f"- Vault: `{report['vault']}`",
        f"- Generated: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- {key}: {value}")

    sections = [
        ("Missing Required Files", report["missing_required_files"]),
        ("Missing Required Directories", report["missing_required_dirs"]),
        ("Broken Links", report["broken_links"]),
        ("Legacy Absolute Or External Links", report["legacy_links"]),
        ("Unindexed Wiki Pages", report["unindexed_wiki_pages"]),
        ("Large Wiki Pages", report["large_wiki_pages"]),
        ("Empty Or Tiny Pages", report["empty_or_tiny_pages"]),
        ("Alias Collisions", report["alias_collisions"]),
        ("Log Order Issues", report["log_order_issues"]),
        ("Maintenance Generation Issues", report["maintenance_generation_issues"]),
        ("Date Heading Issues", report["date_heading_issues"]),
        ("Cold Asset Inventory Issues", report["cold_asset_inventory_issues"]),
        ("System Convergence Issues", report["system_convergence_issues"]),
        ("Manifest Errors", report["manifest"]["errors"]),
        ("Manifest Warnings", report["manifest"]["warnings"]),
        ("Unmatched Manifest Live Paths", report["manifest"]["unmatched_live_paths"]),
        ("Conflicting Manifest Paths", report["manifest"]["conflicting_paths"]),
    ]
    for title, items in sections:
        lines.extend(["", f"## {title}", ""])
        if not items:
            lines.append("_None._")
            continue
        if isinstance(items, dict):
            for key, value in items.items():
                lines.append(f"- `{key}`: {value}")
            continue
        for item in items:
            if isinstance(item, dict):
                fields = ", ".join(f"{k}={v!r}" for k, v in item.items())
                lines.append(f"- {fields}")
            else:
                lines.append(f"- `{item}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    root = Path(args.vault).expanduser().resolve()
    report = audit(root, args.large_page_lines)
    observed_at = args.observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    if args.write_health:
        h5_health = fetch_h5_health(args.h5_url, skip=args.skip_h5)
        model = build_health_model(root, report, observed_at=observed_at, h5_health=h5_health)
        write_health_artifacts(root, model, args.health_json, args.health_markdown)
        # Re-audit generated artifacts so manifest coverage and link counts describe final state.
        report = audit(root, args.large_page_lines)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report), end="")
    if args.fail_on_errors:
        error_count = (
            len(report["missing_required_files"])
            + len(report["missing_required_dirs"])
            + len(report["broken_links"])
            + len(report["log_order_issues"])
            + len(report["maintenance_generation_issues"])
            + len(report["date_heading_issues"])
            + len(report["cold_asset_inventory_issues"])
            + len(report["system_convergence_issues"])
            + len(report["manifest"]["errors"])
        )
        if error_count:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
