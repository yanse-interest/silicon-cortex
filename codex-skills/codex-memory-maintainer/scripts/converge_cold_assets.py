#!/usr/bin/env python3
"""Inventory and reversibly converge proven-unreferenced Codex Memory cold assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


DEFAULT_VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
CHATGPT_SKILL = Path("/Users/shiba/Documents/codex/projects/codex-skills/chatgpt-daily-report")
MAINTAINER_SKILL = Path("/Users/shiba/Documents/codex/projects/codex-skills/codex-memory-maintainer")
DASHBOARD = Path("/Users/shiba/Documents/codex/projects/automation/codex-project-dashboard")
H5_SNAPSHOT = DASHBOARD.parent / "codex-project-dashboard-h5/public/dashboard-snapshot.json"
INVENTORY_PATH = "wiki/cold-asset-inventory.json"

VAULT_MOVE_NAMES = (
    "2026-05-30-karpathy-framework",
    "2026-06-06-feishu-codex-removed",
    "2026-06-25-chatgpt-vs-codex-deposition-boundary",
    "2026-06-25-codex-memory-maintainer",
    "2026-06-25-date-heading-normalization",
    "2026-06-25-low-token-retrieval-protocol",
    "2026-06-25-source-layer-classification",
    "2026-06-26-empty-w25-cycle",
    "2026-08-05-codex-daily-automation",
    "chinese-primary-2026-06-29",
    "cognitive-observatory-pre-migration-2026-06-27",
    "full-chinese-review-2026-06-29",
)

VAULT_RETAINED_REFERENCED = {
    "2026-06-25-log-timeline-normalization": ["wiki/logs/2026/2026-06.md"],
    "2026-06-25-retract-chatgpt-2026-06-24-recovery": ["wiki/logs/2026/2026-06.md"],
    "2026-06-25-retract-chatgpt-2026-06-24-subset-v2": ["wiki/logs/2026/2026-06.md"],
    "chatgpt-daily-chinese-2026-06-29": ["wiki/logs/2026/2026-06.md"],
    "chatgpt-daily-plain-language-2026-06-27": ["wiki/logs/2026/2026-06.md"],
}

DASHBOARD_MOVE_NAMES = (
    "archive-chatgpt-daily-report-automation-before-all-exam-points-20260805T200432.toml",
    "archive-chatgpt-daily-report-automation-before-conversation-id-20260805T195029.toml",
    "archive-chatgpt-daily-report-automation-before-evidence-20260805T193257.toml",
    "archive-chatgpt-daily-report-before-daily-value-deposition-20260823T203101.toml",
    "archive-chatgpt-daily-report-before-daily-value-deposition-20260823T203144.toml",
    "archive-codex-daily-report-before-daily-value-deposition-20260823T203101.toml",
    "archive-codex-daily-report-before-daily-value-deposition-20260823T203144.toml",
    "com.shiba.codex-project-dashboard-h5-refresh-before-20260805T201940.plist",
    "com.shiba.codex-project-dashboard-h5-refresh-before-20260805T230011.plist",
    "com.shiba.codex-project-dashboard-h5-refresh-before-20260805T231424.plist",
    "project-dashboard-case-registry-before-answer-provenance-20260805T192717.json",
    "project-dashboard-case-registry-before-backcheck-20260805T194926.json",
    "project-dashboard-case-registry-before-final-three-20260805T200208.json",
    "project-dashboard-case-registry-v3-20260802T2242.json",
    "publish-project-dashboard-sites-before-20260805T230011.toml",
    "publish-project-dashboard-sites-before-20260805T231424.toml",
)

DASHBOARD_REFERENCED = {
    "registry-before-compliance-branches-20260802.json": [
        "migrate_compliance_branches_20260802.py",
        "wiki/project-dashboard-case-registry.json",
    ],
    "registry-before-lab-case-scope-20260802.json": [
        "migrate_lab_case_scopes_20260802.py",
        "wiki/project-dashboard-case-registry.json",
    ],
}

CACHE_DIRS = (
    DASHBOARD / "__pycache__",
    CHATGPT_SKILL / "scripts/__pycache__",
    CHATGPT_SKILL / "tests/__pycache__",
    MAINTAINER_SKILL / "scripts/__pycache__",
    MAINTAINER_SKILL / "tests/__pycache__",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_fingerprint(path: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(path)
    records: list[str] = []
    file_count = 0
    byte_count = 0
    if path.is_file():
        size = path.stat().st_size
        records.append(f"F\t.\t{size}\t{sha256_file(path)}")
        file_count = 1
        byte_count = size
    elif path.is_symlink():
        records.append(f"L\t.\t{os.readlink(path)}")
    else:
        records.append("D\t.")
        for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                records.append(f"L\t{relative}\t{os.readlink(item)}")
            elif item.is_dir():
                records.append(f"D\t{relative}")
            elif item.is_file():
                size = item.stat().st_size
                records.append(f"F\t{relative}\t{size}\t{sha256_file(item)}")
                file_count += 1
                byte_count += size
    payload = "\n".join(records).encode("utf-8")
    return {
        "tree_sha256": hashlib.sha256(payload).hexdigest(),
        "file_count": file_count,
        "byte_count": byte_count,
    }


def protected_fingerprints(vault: Path) -> dict[str, dict[str, object]]:
    paths = {
        "immutable_raw": vault / "raw",
        "compiled_sources": vault / "wiki/sources",
        "canonical_registry": vault / "wiki/project-dashboard-case-registry.json",
        "review_receipts_ledgers_and_stage_state": vault / "wiki/review-cycles",
        "stage2_migration_plan": vault / "wiki/project-dashboard-registry-split-migration-plan.json",
        "live_h5_snapshot": H5_SNAPSHOT,
        "compiled_source_contract": DASHBOARD / "compiled_source_contract.py",
    }
    return {
        name: {"path": str(path), **tree_fingerprint(path)}
        for name, path in paths.items()
    }


def _entry(
    asset_id: str,
    classification: str,
    action: str,
    original: Path,
    current: Path | None,
    fingerprint: dict[str, object],
    reason: str,
    references: list[str] | None = None,
) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "classification": classification,
        "action": action,
        "original_path": str(original),
        "current_path": str(current) if current else None,
        **fingerprint,
        "reason": reason,
        "reference_evidence": references or [],
    }


def _move_many(pairs: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    completed: list[tuple[Path, Path]] = []
    try:
        for source, target in pairs:
            if not source.exists():
                if target.exists():
                    continue
                raise FileNotFoundError(source)
            if target.exists():
                raise FileExistsError(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            completed.append((source, target))
    except Exception:
        for source, target in reversed(completed):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
        raise
    return completed


def _remove_reproducible_cache(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    fingerprint = tree_fingerprint(path)
    files = [item for item in path.rglob("*") if item.is_file() or item.is_symlink()]
    if any(item.is_file() and item.suffix != ".pyc" for item in files):
        return _entry(
            "cache-" + hashlib.sha256(str(path).encode()).hexdigest()[:12],
            "temporary_cache",
            "retained_ambiguous",
            path,
            path,
            fingerprint,
            "Cache directory contained a non-.pyc file; fail-closed retention.",
        )
    for item in sorted(files, key=lambda value: len(value.parts), reverse=True):
        item.unlink()
    for directory in sorted(
        [item for item in path.rglob("*") if item.is_dir()],
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        directory.rmdir()
    path.rmdir()
    return _entry(
        "cache-" + hashlib.sha256(str(path).encode()).hexdigest()[:12],
        "temporary_cache",
        "deleted_reproducible",
        path,
        None,
        fingerprint,
        "Python bytecode cache only; reproducible by import or test execution.",
    )


def converge(vault: Path) -> dict[str, object]:
    protected_before = protected_fingerprints(vault)
    pairs: list[tuple[Path, Path]] = []
    for name in VAULT_MOVE_NAMES:
        pairs.append(
            (
                vault / ".backups" / name,
                vault / ".backups/cold-archive/vault-legacy" / name,
            )
        )
    dashboard_backup_root = DASHBOARD / "migration-backups"
    for name in DASHBOARD_MOVE_NAMES:
        pairs.append(
            (
                dashboard_backup_root / name,
                dashboard_backup_root / "cold-archive/legacy-2026" / name,
            )
        )

    before = {str(source): tree_fingerprint(source) for source, _ in pairs}
    _move_many(pairs)
    entries: list[dict[str, object]] = []
    for index, (source, target) in enumerate(pairs, start=1):
        after = tree_fingerprint(target)
        if before[str(source)] != after:
            raise RuntimeError(f"moved asset hash mismatch: {source}")
        entries.append(
            _entry(
                f"cold-{index:03d}",
                "unreferenced_cold_backup",
                "moved_to_cold_archive",
                source,
                target,
                after,
                "Exact-name reader/link scan outside backup assets returned no hits; retained for explicit recovery only.",
            )
        )

    for name, references in VAULT_RETAINED_REFERENCED.items():
        path = vault / ".backups" / name
        entries.append(
            _entry(
                "referenced-vault-" + hashlib.sha256(name.encode()).hexdigest()[:12],
                "referenced_recovery_asset",
                "retained_in_place",
                path,
                path,
                tree_fingerprint(path),
                "Canonical maintenance history cites this recovery locator; referenced assets are not moved.",
                references,
            )
        )
    for name, references in DASHBOARD_REFERENCED.items():
        path = DASHBOARD / name
        entries.append(
            _entry(
                "referenced-dashboard-" + hashlib.sha256(name.encode()).hexdigest()[:12],
                "referenced_recovery_asset",
                "retained_in_place",
                path,
                path,
                tree_fingerprint(path),
                "The live registry and historical migration writer reference this exact recovery path.",
                references,
            )
        )

    for cache in CACHE_DIRS:
        cache_entry = _remove_reproducible_cache(cache)
        if cache_entry:
            entries.append(cache_entry)

    protected_after = protected_fingerprints(vault)
    for name in protected_before:
        if protected_before[name] != protected_after[name]:
            raise RuntimeError(f"protected asset changed during convergence: {name}")

    entries.sort(key=lambda item: str(item["asset_id"]))
    inventory = {
        "schema_version": "1.0",
        "inventory_id": "codex-memory-cold-assets-2026-08-28",
        "scope_roots": [
            str(vault),
            str(CHATGPT_SKILL),
            str(MAINTAINER_SKILL),
            str(DASHBOARD),
        ],
        "policy": {
            "cold_assets_are_canonical_inputs": False,
            "ambiguous_or_referenced_assets_move": False,
            "raw_registry_h5_receipts_ledgers_stage_assets_move": False,
        },
        "owner_backup_roots": [
            str(vault / ".backups"),
            str(DASHBOARD / "migration-backups"),
        ],
        "summary": {
            "moved_assets": sum(item["action"] == "moved_to_cold_archive" for item in entries),
            "retained_referenced_assets": sum(item["action"] == "retained_in_place" for item in entries),
            "deleted_reproducible_caches": sum(item["action"] == "deleted_reproducible" for item in entries),
            "retained_ambiguous_caches": sum(item["action"] == "retained_ambiguous" for item in entries),
        },
        "entries": entries,
        "migration_receipt": {
            "protected_before": protected_before,
            "protected_after": protected_after,
            "protected_hashes_equal": True,
            "moved_hashes_equal": True,
            "recoverability": {
                "moved_cold_assets": "Restore with the inventory's explicit current_path -> original_path mapping after verifying tree_sha256.",
                "deleted_caches": "Regenerate by importing the owning Python modules or running their tests.",
            },
        },
    }
    output = vault / INVENTORY_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    args = parser.parse_args()
    inventory = converge(args.vault.expanduser().resolve())
    print(json.dumps(inventory["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
