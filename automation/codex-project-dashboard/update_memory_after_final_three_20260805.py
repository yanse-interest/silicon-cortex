#!/usr/bin/env python3
"""Record completion of the 24-question evidence backcheck in shared Memory."""

from __future__ import annotations

from pathlib import Path

from memory_log_compat import replace_memory_log_text


ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {old[:100]}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    projects = ROOT / "wiki/projects.md"
    replace_once(
        projects,
        "24 道历史题完成首轮回查后，21 道已有日报或原会话证据，3 道因证据仅覆盖局部概念而继续待回查；不得恢复无证据旧答案。",
        "24 道历史题均已完成回查并取得日报或原会话证据，当前 24 道全部为 `source_grounded`、0 道待回查；其中最后三道分别由 MassHunter/UNIFI 数据处理、MRT/G3 比较及 Tyr/UNIFI 质量口径原会话支撑。",
    )

    replace_memory_log_text(
        ROOT,
        "- 历史 24 道考点已完成首轮来源回查：在原有 2 道已核验题目基础上，从日报和 ChatGPT 站内历史定位并读取原回答，新增核验 19 道；当前共 21 道 `source_grounded`、3 道继续 `needs_source_review`。证据只覆盖局部概念的题目没有强行恢复。",
        "- 历史 24 道考点已完成全部来源回查：最后 3 道通过原 ChatGPT 会话 ID 读取 MassHunter/UNIFI 数据层级、MRT/G3 公平比较及 Tyr/UNIFI 质量口径原回答后补齐；当前 24 道均为 `source_grounded`，0 道 `needs_source_review`。第三道原会话未展开 average mass 的同位素加权公式，因此答案明确保留该证据边界，没有用通用模型知识补写。",
    )

    agents = ROOT / "AGENTS.memory.md"
    replace_once(
        agents,
        "- 每份新日报都必须从所有相关会话中提取可复用的仪器知识，并生成机器可读的 `Instrument Knowledge Candidates`。回答考点前先检索已归档日报；日报证据不足且固定来源账号中的原 ChatGPT 会话仍可访问时，再回查具体会话。",
        "- 每份新日报都必须从所有相关会话中提取每一个有证据支撑、彼此独立的可复用仪器考点，并生成机器可读的 `Instrument Knowledge Candidates`；同一会话可有多道，不得只保留最概括的一道。回答考点前先检索已归档日报；日报证据不足且固定来源账号中的原 ChatGPT 会话仍可访问时，再回查具体会话。",
    )

    decisions = ROOT / "wiki/decisions.md"
    replace_once(
        decisions,
        "- 历史迁移脚本重新生成、但没有来源证据的答案停止作为已核验答案展示，统一降级为“证据不足，待回查”；找不到证据时不得用模型常识补写。",
        "- 历史迁移脚本重新生成、但没有来源证据的答案停止作为已核验答案展示，统一降级为“证据不足，待回查”；找不到证据时不得用模型常识补写。\n"
        "- 日报应从每个相关工作会话中提取全部彼此独立且有证据支撑的可复用考点；同一会话允许多道，Dashboard 聚合完整的“问题 + 带证据答案”，不以宽泛项目摘要替代。",
    )

    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
