#!/usr/bin/env python3
"""Audit and index a curated Cognitive Observatory vault.

The audit itself is non-generative and writes only maintenance files in 99_index/.
Official auto-promotions may exist, but must carry complete deposition metadata.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

CONTENT_DIRS = [
    "00_conversations",
    "01_profile",
    "02_observations",
    "03_frameworks",
    "04_decisions",
    "05_projects",
    "06_reviews",
]
OFFICIAL_DIRS = ["02_observations", "03_frameworks", "04_decisions", "05_projects", "06_reviews"]
INDEX_DIR = "99_index"
REQUIRED_STATUS_TYPES = {"observation", "framework", "decision", "review", "profile", "project"}
CANDIDATE_HEADINGS = ["新概念", "新模式", "新决策", "认知演化链条", "核心发现", "后续行动"]
WEEKLY_SECTION_HEADINGS = {
    "本周核心主题",
    "本周重要思考",
    "本周关键决策",
    "被否决方案",
    "可复用概念",
    "行为模式观察",
    "认知变化",
    "项目进展",
    "下周行动项",
    "Open Questions",
    "本周一句话总结",
}


@dataclass(frozen=True)
class Note:
    path: Path
    rel: str
    frontmatter: dict[str, str]
    title: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Cognitive Observatory project root")
    return parser.parse_args()


def assert_inside(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise SystemExit(f"Refusing to access path outside project root: {path}")


def ensure_root(root: Path) -> Path:
    root = root.resolve()
    for rel in ["00_conversations", INDEX_DIR]:
        target = root / rel
        if not target.is_dir():
            raise SystemExit(f"Missing required directory: {target}")
    return root


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, flags=re.S)
    if not match:
        return {}, text
    raw, body = match.groups()
    data: dict[str, str] = {}
    current_key = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data[current_key] = data.get(current_key, "") + "\n" + line.strip()[2:].strip()
            continue
        if ":" in line and not line.lstrip().startswith("-"):
            key, value = line.split(":", 1)
            current_key = key.strip()
            data[current_key] = value.strip().strip('"')
    return data, body


def first_heading(body: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", body, flags=re.M)
    return match.group(1).strip() if match else ""


def title_from_path(path: Path) -> str:
    stem = path.name[:-3] if path.name.endswith(".md") else path.stem
    return stem.replace("-", " ").replace("_", " ").title()


def read_note(path: Path, root: Path) -> Note:
    assert_inside(root, path)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    title = frontmatter.get("title") or first_heading(body) or title_from_path(path)
    return Note(path=path, rel=path.relative_to(root).as_posix(), frontmatter=frontmatter, title=title)


def iter_markdown(root: Path, dirs: Iterable[str]) -> Iterable[Path]:
    for directory in dirs:
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            if path.name.startswith("_"):
                continue
            yield path


def yaml_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def obsidian_target(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def first_frontmatter_path(value: str) -> str:
    for line in value.splitlines():
        cleaned = line.strip().strip('"')
        if cleaned:
            return cleaned
    return ""


def wiki_link(note: Note) -> str:
    label = note.title
    return f"[[{obsidian_target(note.rel)}|{label}]]"


def group_notes(notes: list[Note]) -> dict[str, list[Note]]:
    groups = {directory: [] for directory in CONTENT_DIRS}
    for note in notes:
        top = note.rel.split("/", 1)[0]
        if top in groups:
            groups[top].append(note)
    return groups


def render_index(root: Path, notes: list[Note], issues: list[str], extracted_at: str) -> str:
    groups = group_notes(notes)
    lines = [
        "---",
        "type: cognitive_index",
        "status: active",
        f"updated_at: {yaml_quote(extracted_at)}",
        "---",
        "",
        "# 认知索引",
        "",
        "Cognitive Observatory 整理流程导航：对话 → 观察 → 框架 → 决策 → 复盘。",
        "",
        "## 摘要",
        "",
        f"- 对话：{len(groups['00_conversations'])}",
        f"- 画像记录：{len(groups['01_profile'])}",
        f"- 观察：{len(groups['02_observations'])}",
        f"- 框架：{len(groups['03_frameworks'])}",
        f"- 决策：{len(groups['04_decisions'])}",
        f"- 项目：{len(groups['05_projects'])}",
        f"- 复盘：{len(groups['06_reviews'])}",
        f"- 审计问题：{len(issues)}",
        "",
        "## 入口",
        "",
        "- [[README|Cognitive Observatory 根目录]]",
        "- [[99_index/extraction-candidates|提取候选]]",
        "- [[03_frameworks/thinking-as-data|思考即数据]]",
        "- [[03_frameworks/cognitive-observatory|认知观察站]]",
        "- [[03_frameworks/ai-system-role-separation-framework|AI 系统角色分离框架]]",
        "",
    ]

    section_labels = [
        ("00_conversations", "对话"),
        ("01_profile", "画像"),
        ("02_observations", "观察"),
        ("03_frameworks", "框架"),
        ("04_decisions", "决策"),
        ("05_projects", "项目"),
        ("06_reviews", "复盘"),
    ]
    for directory, heading in section_labels:
        lines.extend([f"## {heading}", ""])
        items = sorted(groups[directory], key=lambda note: note.title.lower())
        if not items:
            lines.extend(["_暂无。_", ""])
            continue
        for note in items:
            source = first_frontmatter_path(
                note.frontmatter.get("source")
                or note.frontmatter.get("related")
                or note.frontmatter.get("source_links")
                or ""
            )
            if source.startswith("obsidian://"):
                suffix = f" - 来源：[原始来源]({source})"
            else:
                suffix = f" - 来源：[[{obsidian_target(source)}|原始来源]]" if source and ".md" in source else ""
            lines.append(f"- {wiki_link(note)}{suffix}")
        lines.append("")

    lines.extend(["## 审计", ""])
    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- 未发现活动知识层审计问题。")
    lines.append("")
    return "\n".join(lines)


def extract_section_titles(text: str, heading: str) -> list[str]:
    pattern = re.compile(rf"^#\s+{re.escape(heading)}\s*$", flags=re.M)
    match = pattern.search(text)
    if not match:
        return []
    start = match.end()
    next_heading = re.search(r"^#\s+", text[start:], flags=re.M)
    end = start + next_heading.start() if next_heading else len(text)
    section = text[start:end]
    titles = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", section, flags=re.M)]
    return titles or [heading]


def render_candidates(root: Path, extracted_at: str) -> str:
    lines = [
        "---",
        "type: extraction_candidates",
        "status: active",
        f"updated_at: {yaml_quote(extracted_at)}",
        "---",
        "",
        "# 提取候选",
        "",
        "自动化会列出 `00_conversations` 中可能值得提取的材料。经人工确认并晋升到整理层之前，它们不属于正式知识。",
        "",
    ]
    found = False
    for path in sorted((root / "00_conversations").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        rel = path.relative_to(root).as_posix()
        file_lines: list[str] = []
        if frontmatter.get("type") == "weekly_review":
            file_lines.extend(render_weekly_review_candidate_lines(body))
        else:
            for heading in CANDIDATE_HEADINGS:
                titles = extract_section_titles(text, heading)
                if titles:
                    for title in titles:
                        file_lines.append(f"- {heading}: {title}")
        if file_lines:
            found = True
            lines.extend([f"## [[{obsidian_target(rel)}|{path.name}]]", ""])
            lines.extend(file_lines)
            lines.append("")
    if not found:
        lines.append("_未发现候选。_")
        lines.append("")
    return "\n".join(lines)


def split_blocks(body: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip() == "---":
            if any(item.strip() for item in current):
                blocks.append(current)
            current = []
            continue
        current.append(line.rstrip())
    if any(item.strip() for item in current):
        blocks.append(current)
    return blocks


def first_nonempty(lines: list[str]) -> str:
    for line in lines:
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def clean_candidate_title(value: str) -> str:
    return value.strip().strip("«»").strip()


def weekly_segments(body: str) -> list[tuple[str, list[str]]]:
    segments: list[tuple[str, list[str]]] = []
    current_section = ""
    current_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line == "---":
            if current_section and any(item.strip() for item in current_lines):
                segments.append((current_section, current_lines))
            current_lines = []
            continue
        if line in WEEKLY_SECTION_HEADINGS:
            if current_section and any(item.strip() for item in current_lines):
                segments.append((current_section, current_lines))
            current_section = line
            current_lines = []
            continue
        if current_section:
            current_lines.append(raw_line)
    if current_section and any(item.strip() for item in current_lines):
        segments.append((current_section, current_lines))
    return segments


def render_weekly_review_candidate_lines(body: str) -> list[str]:
    results: list[str] = []
    for current_section, block in weekly_segments(body):
        lines = [line.strip() for line in block if line.strip()]
        if not lines:
            continue
        joined = "\n".join(lines)
        first = lines[0]
        marker = re.search(r"^(决策|Pattern|Cognitive Shift)[：:]\s*(.+?)\s*$", joined, flags=re.M)
        if marker:
            label = {
                "决策": "决策",
                "Pattern": "模式",
                "Cognitive Shift": "认知转变",
            }[marker.group(1)]
            results.append(f"- {label}: {clean_candidate_title(marker.group(2))}")
            continue

        if current_section == "可复用概念":
            results.append(f"- 概念：{clean_candidate_title(first)}")
        elif current_section == "被否决方案":
            results.append(f"- 已否决方案：{clean_candidate_title(first)}")
        elif current_section == "本周重要思考" and re.match(r"^思考[一二三四五六七八九十\d]+[：:]", first):
            results.append(f"- 思考：{clean_candidate_title(first)}")
        elif current_section == "本周核心主题":
            core = [line for line in lines if line.startswith("«") and line.endswith("»")]
            if core:
                results.append(f"- 观察：{clean_candidate_title(core[0])}")
        elif current_section == "本周一句话总结":
            summary = [line for line in lines if line.startswith("«") and line.endswith("»")]
            if summary:
                results.append(f"- 周度总结：{clean_candidate_title(summary[0])}")
    return results


def link_targets(text: str) -> Iterable[str]:
    for match in re.finditer(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", text):
        target = match.group(1).strip()
        if target:
            yield target


def target_exists(root: Path, target: str) -> bool:
    candidates = [root / target]
    if not target.endswith(".md"):
        candidates.append(root / f"{target}.md")
    return any(candidate.exists() for candidate in candidates)


def audit(root: Path, notes: list[Note]) -> list[str]:
    issues: list[str] = []
    forbidden_dirs = ["01_concepts", "02_patterns", "03_decisions", "04_cognitive_shifts"]
    for directory in forbidden_dirs:
        if (root / directory).exists():
            issues.append(f"Legacy directory still exists: `{directory}`")

    for note in notes:
        top = note.rel.split("/", 1)[0]
        if top in OFFICIAL_DIRS:
            fm = note.frontmatter
            if not fm.get("type"):
                issues.append(f"Missing `type` frontmatter: [[{note.rel[:-3]}|{note.title}]]")
            if fm.get("type") in REQUIRED_STATUS_TYPES and not fm.get("status"):
                issues.append(f"Missing `status` frontmatter: [[{note.rel[:-3]}|{note.title}]]")
            if not (fm.get("source") or fm.get("related") or fm.get("source_links")):
                issues.append(f"Missing `source` or `related` frontmatter: [[{note.rel[:-3]}|{note.title}]]")
            if fm.get("promotion") == "auto":
                required = [
                    "candidate_id",
                    "evidence_count",
                    "first_seen",
                    "last_seen",
                    "confidence",
                    "source_links",
                ]
                for field in required:
                    if not fm.get(field):
                        issues.append(
                            f"Auto-promotion missing `{field}`: [[{note.rel[:-3]}|{note.title}]]"
                        )
        text = note.path.read_text(encoding="utf-8")
        for target in link_targets(text):
            if target.startswith("http"):
                continue
            if not target_exists(root, target):
                issues.append(f"Broken link in `{note.rel}`: `[[{target}]]`")
    return issues


def main() -> None:
    root = ensure_root(Path(parse_args().root))
    extracted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    notes = [read_note(path, root) for path in iter_markdown(root, CONTENT_DIRS)]
    issues = audit(root, notes)

    index_path = root / INDEX_DIR / "cognitive-index.md"
    candidates_path = root / INDEX_DIR / "extraction-candidates.md"
    assert_inside(root, index_path)
    assert_inside(root, candidates_path)
    index_path.write_text(render_index(root, notes, issues, extracted_at), encoding="utf-8")
    candidates_path.write_text(render_candidates(root, extracted_at), encoding="utf-8")

    print(f"Notes indexed: {len(notes)}")
    print(f"Audit issues: {len(issues)}")
    print(f"Index written: {index_path.relative_to(root)}")
    print(f"Candidates written: {candidates_path.relative_to(root)}")


if __name__ == "__main__":
    main()
