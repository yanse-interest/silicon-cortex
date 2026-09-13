#!/usr/bin/env python3
"""Record the completed knowledge backcheck in shared Codex Memory."""

from __future__ import annotations

from pathlib import Path

from memory_log_compat import replace_memory_log_text


ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}: {old[:80]}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    decisions = ROOT / "wiki/decisions.md"
    replace_once(
        decisions,
        "- 每条可展示答案必须携带 `evidence_refs`，标明日报或 ChatGPT 会话、session、支持层级与证据摘要。仅有题目、标题或关键词不能支撑答案。\n",
        "- 每条可展示答案必须携带 `evidence_refs`，标明日报或 ChatGPT 会话、session、支持层级与证据摘要。仅有题目、标题或关键词不能支撑答案。\n"
        "- 原 ChatGPT 会话可访问时，日报技术记录和私有证据引用必须保存 canonical `/c/<UUID>` URL；`Sxx` 只负责当日日报内定位。原会话不可访问时明确写 `unavailable`，不得省略或猜测。原会话 URL 不进入 H5。\n",
    )

    projects = ROOT / "wiki/projects.md"
    replace_once(
        projects,
        "- 2026-08-05 仪器考点回答证据化：能力域回答先检索 ChatGPT 日报，必要时回查固定来源账号的可访问原会话；H5 只展示带 `evidence_refs` 的答案。24 道历史题中 2 道已由日报/原会话核验，22 道旧迁移生成答案降级为待回查。日报模板和 07:05 实际自动化提示词同步升级为工作会话“简明摘要 + 细节保真技术记录”，保留可复用回答逻辑而不保留可还原实验工作的标识；模型和账号路由不变。",
        "- 2026-08-05 仪器考点回答证据化：能力域回答先检索 ChatGPT 日报，必要时回查固定来源账号的可访问原会话；H5 只展示带 `evidence_refs` 的答案。24 道历史题完成首轮回查后，21 道已有日报或原会话证据，3 道因证据仅覆盖局部概念而继续待回查；不得恢复无证据旧答案。日报模板和 07:05 实际自动化提示词同步升级为工作会话“简明摘要 + 细节保真技术记录”，并要求原会话可访问时保存 canonical `/c/<UUID>` URL、不可访问时明确写 `unavailable`；H5 不公开 URL。模型和账号路由不变。",
    )

    replace_memory_log_text(
        ROOT,
        "- 历史 24 道考点已完成来源状态迁移：`TOF/MRT` 由可访问原会话直接支撑，`SIM/MRM` 由 2026-07-20 日报 S03 支撑；其余 22 道旧迁移生成答案降级为“证据不足，待回查”，保留问题但不再展示旧答案。",
        "- 历史 24 道考点已完成首轮来源回查：在原有 2 道已核验题目基础上，从日报和 ChatGPT 站内历史定位并读取原回答，新增核验 19 道；当前共 21 道 `source_grounded`、3 道继续 `needs_source_review`。证据只覆盖局部概念的题目没有强行恢复。",
    )
    replace_memory_log_text(
        ROOT,
        "- ChatGPT 日报模板新增工作会话细节保真要求：在简明摘要之外保留原问题上下文、回答要点、推理/诊断链、关键区别、条件/版本、适用边界与未决证据；继续脱敏可还原实验工作的标识。",
        "- ChatGPT 日报模板新增工作会话细节保真要求：在简明摘要之外保留原问题上下文、回答要点、推理/诊断链、关键区别、条件/版本、适用边界与未决证据；继续脱敏可还原实验工作的标识。原会话可访问时必须记录 canonical `/c/<UUID>` URL，不可访问时写 `unavailable`。",
    )

    agents = ROOT / "AGENTS.memory.md"
    replace_once(
        agents,
        "- ChatGPT 日报中的相关工作会话采用“简明摘要 + 细节保真技术记录”双层结构。`lab`、`project`、`engineering`、`research` 会话必须保留可见的原问题上下文、回答要点、推理/诊断链、关键区别、条件、版本、适用边界和未决证据，不能压缩成只剩主题或一句结论；仍须移除客户、项目、样品、批次、内部链接、原始结果等可还原实验工作的标识。",
        "- ChatGPT 日报中的相关工作会话采用“简明摘要 + 细节保真技术记录”双层结构。`lab`、`project`、`engineering`、`research` 会话必须保留可见的原问题上下文、回答要点、推理/诊断链、关键区别、条件、版本、适用边界和未决证据，不能压缩成只剩主题或一句结论；原 ChatGPT 会话可访问时必须记录 canonical `/c/<UUID>` URL，确实不可访问时明确写 `unavailable`；仍须移除客户、项目、样品、批次、内部链接、原始结果等可还原实验工作的标识。",
    )
    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
