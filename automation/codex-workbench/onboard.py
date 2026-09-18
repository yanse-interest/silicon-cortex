#!/usr/bin/env python3
"""Narrow, preview-first onboarding for one already-selected project root.

It intentionally does not scan directories, infer work, or bulk-import.  The
caller supplies every business field and receives a compact metadata-only plan
before ``--apply`` can create a fresh map and registry binding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from workbench import Store, WorkbenchError, atomic_write, new_map, parse_map, render_map, sha256


def git_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def safe_file_digest(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise WorkbenchError("path_outside_project", f"required metadata file is unavailable: {relative}")
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def plan(args: argparse.Namespace) -> tuple[Path, dict[str, object]]:
    root = args.root
    if root.is_symlink() or not root.is_dir():
        raise WorkbenchError("path_outside_project", "project root must be an existing non-symlink directory")
    root = root.resolve(strict=True)
    map_path = root / "PROJECT_MAP.md"
    if map_path.exists():
        raise WorkbenchError("invalid_reference", "onboarding only creates a fresh map; existing maps must be registered directly")
    metadata = [safe_file_digest(root, path) for path in args.metadata]
    return root, {
        "doc_type": "onboarding_preview",
        "project_id": args.project,
        "root_path": str(root),
        "map_path": "PROJECT_MAP.md",
        "git_head": git_head(root),
        "metadata": metadata,
        "backup": {"status": "not_applicable", "reason": "no existing business authority exists to overwrite"},
        "apply_effect": "create one new PROJECT_MAP.md, then register only ID/root/map path",
    }


def apply(args: argparse.Namespace, root: Path) -> dict[str, object]:
    """Create, validate, back up initial bytes, then atomically register.

    If registry promotion fails, the just-created map is retained (never
    deleted automatically); the caller has a valid, inspectable authority and
    can retry registration without losing it.
    """
    store = Store(args.data_dir)
    target = root / "PROJECT_MAP.md"
    value = new_map(args.project, args.title, args.goal, args.outcome, args.outcome_title)
    raw = render_map("", value)
    parse_map(raw)
    atomic_write(target, raw)
    # A private immutable initial backup means rollback is available even before
    # the first later CAS update creates its own predecessor backup.
    atomic_write(store.backup_dir / f"{args.project}.initial.md", raw)
    try:
        store.register(args.project, root)
    except Exception:
        raise
    return {"doc_type": "onboarding_result", "project_id": args.project,
            "map_sha256": sha256(raw), "backup": f"{args.project}.initial.md",
            "registry": "ready"}


def main() -> int:
    parser = argparse.ArgumentParser(description="single-project, preview-first workbench onboarding")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--outcome-title", required=True)
    parser.add_argument("--metadata", action="append", required=True,
                        help="required README/manifest-like metadata path; may repeat")
    parser.add_argument("--apply", action="store_true", help="perform the reviewed creation and registration")
    args = parser.parse_args()
    try:
        root, preview = plan(args)
        if not args.apply:
            print(json.dumps(preview, ensure_ascii=False, indent=2)); return 0
        result = apply(args, root)
        print(json.dumps({"preview": preview, "result": result}, ensure_ascii=False, indent=2)); return 0
    except WorkbenchError as exc:
        print(json.dumps(exc.response(), ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
