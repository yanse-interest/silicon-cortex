#!/usr/bin/env python3
"""Reformat legacy ChatGPT daily reports into a plain-language-first layout."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SESSION_RE = re.compile(r"^### S\d{2,}\s+[—-]\s+.+$", re.MULTILINE)
FIELD_RE = re.compile(r"^- \*\*(?P<name>[^*]+):\*\*\s*(?P<value>.*)$")


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML frontmatter")
    return text[: end + 5], text[end + 5 :]


def parse_fields(block: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.strip("\n").splitlines():
        match = FIELD_RE.match(line)
        if match:
            current = match.group("name")
            fields[current] = [match.group("value")]
        elif current is not None:
            fields[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in fields.items()}


def compact(value: str, fallback: str) -> str:
    if not value:
        return fallback
    parts: list[str] = []
    for line in value.splitlines():
        item = line.strip()
        if not item:
            continue
        item = re.sub(r"^-\s+", "", item)
        parts.append(item)
    return " ".join(parts) or fallback


def technical_line(label: str, value: str, fallback: str = "None") -> str:
    return f"- {label}: {compact(value, fallback)}"


def render_session(heading: str, fields: dict[str, str]) -> str:
    objective = compact(fields.get("Objective", ""), "现有记录不足以确认这次具体聊了什么。")
    outcome = compact(
        fields.get("Outcome or current conclusion", ""),
        "没有可靠结论",
    )
    follow_up = compact(fields.get("Follow-ups", ""), "无")
    lines = [
        heading,
        "",
        f"**我们聊了什么：** {objective}",
        "",
        f"**结论：** {outcome}",
        "",
        f"**下一步：** {follow_up}",
        "",
        "<details>",
        "<summary>技术记录（需要时展开）</summary>",
        "",
        technical_line("领域", fields.get("Domain", ""), "other"),
    ]
    if fields.get("Source URL"):
        lines.append(technical_line("来源 URL", fields["Source URL"]))
    if fields.get("Access time"):
        lines.append(technical_line("访问时间", fields["Access time"]))
    lines.extend(
        [
            technical_line("关键事实", fields.get("Key context and observations", "")),
            technical_line("明确决定", fields.get("Decisions", "")),
            technical_line("未解决", fields.get("Open questions", "")),
            technical_line("可复用洞察", fields.get("Reusable knowledge", "")),
            technical_line("证据边界", fields.get("Evidence and uncertainty", "")),
            "",
            "</details>",
        ]
    )
    return "\n".join(lines)


def add_regeneration_metadata(frontmatter: str, regenerated_at: str) -> str:
    if "\nregenerated_by:" in frontmatter:
        return frontmatter
    insertion = (
        f"regenerated_at: {regenerated_at}\n"
        "regenerated_by: codex\n"
        "regeneration_basis: archived_chatgpt_report\n"
    )
    return frontmatter.replace("\nsource: chatgpt\n", f"\nsource: chatgpt\n{insertion}")


def reformat(text: str, regenerated_at: str) -> str:
    if "**我们聊了什么：**" in text:
        return text
    frontmatter, body = split_frontmatter(text)
    digest_marker = "## 会话摘要" if "## 会话摘要" in body else "## Session Digest"
    synthesis_marker = "## 跨会话综合" if "## 跨会话综合" in body else "## Cross-session Synthesis"
    if digest_marker not in body or synthesis_marker not in body:
        raise ValueError("missing Session Digest/会话摘要 or Cross-session Synthesis/跨会话综合")
    before, rest = body.split(digest_marker, 1)
    digest, after = rest.split(synthesis_marker, 1)
    matches = list(SESSION_RE.finditer(digest))
    rendered: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(digest)
        rendered.append(render_session(match.group(0), parse_fields(digest[match.end() : end])))
    if not rendered:
        raise ValueError("no session entries found")
    before = before.replace("## Executive Summary", "## 今天用人话说")
    before = before.replace("## Today in Plain Language", "## 今天用人话说")
    before = before.replace("## Coverage & Limitations", "## 覆盖范围与限制")
    after = after.replace("## Session Index", "## 会话索引")
    note = (
        "- Codex 已基于先前归档的 ChatGPT 报告重新生成可读展示层；没有新增 session 事实，也没有补写不可访问的 assistant 回复。\n"
    )
    coverage = "## 覆盖范围与限制\n"
    if note not in before and coverage in before:
        before = before.replace(coverage, coverage + "\n" + note, 1)
    updated = (
        add_regeneration_metadata(frontmatter, regenerated_at)
        + before.rstrip()
        + "\n\n## 会话摘要\n\n"
        + "\n\n".join(rendered)
        + "\n\n## 跨会话综合"
        + after
    )
    return updated.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.in_place == bool(args.output_dir):
        parser.error("choose exactly one of --in-place or --output-dir")
    if args.in_place and not args.backup_dir:
        parser.error("--in-place requires --backup-dir")
    regenerated_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    for report in args.reports:
        original = report.read_text(encoding="utf-8")
        updated = reformat(original, regenerated_at)
        if args.in_place:
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            backup = args.backup_dir / report.name
            if backup.exists() and backup.read_text(encoding="utf-8") != original:
                raise FileExistsError(f"different backup already exists: {backup}")
            if not backup.exists():
                shutil.copy2(report, backup)
            report.write_text(updated, encoding="utf-8")
            print(f"updated {report} (backup: {backup})")
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            target = args.output_dir / report.name
            target.write_text(updated, encoding="utf-8")
            print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
