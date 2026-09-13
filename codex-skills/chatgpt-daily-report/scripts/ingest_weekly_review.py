#!/usr/bin/env python3
"""Ingest a Feishu weekly_review Markdown message into Codex Memory raw sources."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", help="Codex Memory vault root for the canonical raw archive")
    parser.add_argument("--cognitive-root", help="Optional Cognitive Observatory root to audit after ingest")
    parser.add_argument("--root", help="Legacy Cognitive Observatory project root")
    parser.add_argument("--input-file", help="Read Markdown from a file instead of stdin")
    return parser.parse_args()


def assert_inside(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise SystemExit(f"Refusing to access path outside project root: {path}")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, flags=re.S)
    if not match:
        return {}, text
    raw, body = match.groups()
    data: dict[str, str] = {}
    current_key = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if current_key:
                value = line.split("-", 1)[1].strip()
                data[current_key] = f"{data.get(current_key, '')}\n{value}".strip()
            continue
        if ":" in line and not line.lstrip().startswith("-"):
            key, value = line.split(":", 1)
            current_key = key.strip()
            data[current_key] = value.strip().strip('"')
    return data, body


def is_cognitive_weekly_review(frontmatter: dict[str, str]) -> bool:
    tags = frontmatter.get("tags", "")
    return frontmatter.get("type") == "weekly_review" and "cognitive-observatory" in tags.splitlines()


def validate_review_state(frontmatter: dict[str, str]) -> str:
    status = frontmatter.get("status", "")
    if status not in {"draft", "published"}:
        raise SystemExit("weekly_review status must be draft or published")
    if status == "published" and not frontmatter.get("published_at"):
        raise SystemExit("published weekly_review requires published_at")
    if not frontmatter.get("review_cycle") and not frontmatter.get("week"):
        raise SystemExit("weekly_review requires review_cycle or week")
    return status


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")
    return slug or "weekly-review"


def first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and cleaned != "---":
            return cleaned
    return ""


def audit_script(root: Path) -> Path:
    candidates = [
        root
        / ".codex"
        / "skills"
        / "chatgpt-daily-report"
        / "scripts"
        / "audit_cognitive_observatory.py",
        root
        / ".codex"
        / "skills"
        / "cognitive-observatory"
        / "scripts"
        / "audit_observatory.py",
        Path(__file__).resolve().with_name("audit_cognitive_observatory.py"),
        Path(__file__).resolve().with_name("audit_observatory.py"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Missing Cognitive Observatory audit script")


def infer_year(frontmatter: dict[str, str]) -> str:
    for key in ["date", "published_at", "created", "generated_at"]:
        value = frontmatter.get(key, "")
        match = re.search(r"(20\d{2})", value)
        if match:
            return match.group(1)
    for key in ["week", "review_cycle", "based_on"]:
        value = frontmatter.get(key, "")
        match = re.search(r"(20\d{2})", value)
        if match:
            return match.group(1)
    return str(datetime.now(timezone.utc).year)


def review_slug(frontmatter: dict[str, str]) -> str:
    cycle = frontmatter.get("week") or frontmatter.get("review_cycle") or "weekly-review"
    year = infer_year(frontmatter)
    if re.fullmatch(r"W\d{1,2}", cycle, flags=re.I):
        cycle = f"{year}-{cycle.upper()}"
    canonical_week = re.fullmatch(r"(20\d{2})-W(\d{1,2})", cycle, flags=re.I)
    if canonical_week:
        return f"weekly-review-{canonical_week.group(1)}-W{int(canonical_week.group(2)):02d}"
    slug = slugify(cycle)
    return f"weekly-review-{slug}"


def write_immutable(path: Path, text: str) -> str:
    encoded = text + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise SystemExit(f"different weekly review already exists: {path}")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    return "created"


def ingest_to_memory_vault(frontmatter: dict[str, str], text: str, vault: Path) -> tuple[Path, str]:
    raw_dir = vault / "raw" / "reviews" / "weekly" / infer_year(frontmatter)
    output_path = raw_dir / f"{review_slug(frontmatter)}.md"
    assert_inside(vault, output_path)
    return output_path, write_immutable(output_path, text)


def audit_cognitive_root(root: Path | None) -> str:
    if root is None:
        return ""
    if not (root / "00_conversations").is_dir() or not (root / "99_index").is_dir():
        raise SystemExit("Cognitive root must contain 00_conversations/ and 99_index/")
    audit = subprocess.run(
        [sys.executable, str(audit_script(root)), "--root", str(root)],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )
    return audit.stdout.strip()


def read_input(args: argparse.Namespace) -> str:
    if args.input_file:
        return Path(args.input_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> None:
    args = parse_args()
    text = read_input(args).strip()
    frontmatter, body = split_frontmatter(text)
    if not is_cognitive_weekly_review(frontmatter):
        print(json.dumps({"handled": False, "reason": "not cognitive weekly_review"}, ensure_ascii=False))
        return

    status = validate_review_state(frontmatter)
    cycle_label = frontmatter.get("review_cycle") or frontmatter.get("week") or "weekly"
    title = first_meaningful_line(body) or f"{cycle_label} 周度思维复盘"

    if status == "draft":
        print(
            json.dumps(
                {
                    "handled": True,
                    "archived": False,
                    "path": None,
                    "title": title,
                    "status": status,
                    "write_result": "skipped_draft",
                    "reason": "only published Weekly Reviews are archived as canonical raw evidence",
                    "audit_stdout": "",
                },
                ensure_ascii=False,
            )
        )
        return

    if args.vault:
        vault = Path(args.vault).resolve()
        output_path, write_result = ingest_to_memory_vault(frontmatter, text, vault)
        cognitive_root = Path(args.cognitive_root).resolve() if args.cognitive_root else None
        audit_stdout = audit_cognitive_root(cognitive_root)
        rel = output_path.relative_to(vault).as_posix()
        mode = "memory_raw"
    elif args.root:
        root = Path(args.root).resolve()
        conversations = root / "00_conversations"
        index = root / "99_index"
        if not conversations.is_dir() or not index.is_dir():
            raise SystemExit("Root must contain 00_conversations/ and 99_index/")
        week = frontmatter.get("review_cycle") or frontmatter.get("week", "")
        date = frontmatter.get("date", "")
        filename = f"{slugify('-'.join(part for part in [date, week, status, 'weekly-review'] if part))}.md"
        output_path = conversations / filename
        assert_inside(root, output_path)
        write_result = write_immutable(output_path, text)
        audit_stdout = audit_cognitive_root(root)
        rel = output_path.relative_to(root).as_posix()
        mode = "legacy_cognitive_root"
    else:
        raise SystemExit("provide --vault for canonical ingest or --root for legacy compatibility")
    print(
        json.dumps(
            {
                "handled": True,
                "archived": True,
                "mode": mode,
                "path": rel,
                "title": title,
                "status": status,
                "write_result": write_result,
                "audit_stdout": audit_stdout,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
