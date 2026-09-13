#!/usr/bin/env python3
"""Ground the last three legacy exam answers in original ChatGPT conversations."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_REGISTRY = Path(
    "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/"
    "wiki/project-dashboard-case-registry.json"
)
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"


def ref(
    date: str,
    session: str,
    title: str,
    excerpt: str,
    conversation_id: str,
) -> dict[str, str]:
    return {
        "source_type": "chatgpt_conversation",
        "source_date": date,
        "session_id": session,
        "session_title": title,
        "support_level": "direct_answer",
        "excerpt": excerpt,
        "source_locator": f"https://chatgpt.com/c/{conversation_id}",
    }


UPDATES: dict[str, dict[str, object]] = {
    "原始信号、峰、组分和软件计算结果有什么区别？": {
        "answer": (
            "原始信号或原始谱是仪器在保留时间与 m/z 维度记录的观测；峰是软件在 TIC、XIC 或谱图上经峰检测、积分后得到的信号特征。"
            "去卷积会再利用电荷态、同位素间隔、保留时间和峰形，把多个带电信号归并并估算中性质量或重建较干净的谱。"
            "Component 则是更下游的软件对象：在所回查的 UNIFI 会话里，它可理解为“色谱峰/保留时间 + 去卷积后的观测质量 + 对理论库的匹配”，并不等同于原始离子，也不等同于子离子清单。"
            "匹配分数、鉴定结果和定量值又是基于这些对象、阈值和方法参数计算出的结果。因此判断异常时要按“原始谱 → 峰 → 去卷积/Component → 匹配或定量结果”逐层回查，不能把软件命中反当成未经处理的原始证据。"
        ),
        "boundary": "原会话直接支持 TIC/XIC 峰、去卷积、UNIFI Component 与匹配结果的层级关系；不同软件对 Component 的具体字段和算法仍受模块与版本约束。",
        "refs": [
            ref(
                "2026-07-21",
                "S01",
                "Masshunter分析模式区别",
                "原回答说明 TIC Analysis 在总离子流上积分色谱峰；deconvolution 按各 m/z 的保留时间和峰形重建组分并拆分共流出化合物。",
                "6a5ee117-df80-83ec-bb9b-a2bcae39bf22",
            ),
            ref(
                "2026-07-02",
                "S05",
                "UNIFI Fragmentation Viewer解析",
                "原回答说明 Component Summary 不是子离子清单；Component 可由保留时间、去卷积观测质量和理论库匹配构成，并需回看色谱、质量误差、同位素与碎片证据。",
                "6a461568-a814-83ec-b105-9b426fd2a1aa",
            ),
        ],
    },
    "怎样才算公平地比较两台分析仪器的能力？": {
        "answer": (
            "先把比较问题和指标定义清楚，再统一样品、浓度与进样、离子源和 LC 条件、校准状态、m/z 区间、采集模式、扫描速度、数据处理和显示口径。"
            "以分辨本领为例，必须同时报告比较时的 m/z、FWHM 定义、模式和速度；视觉峰宽还会受强度、采样、平滑和坐标尺度影响，质量分辨率也不能与质量准确度混为一谈。"
            "最后还要按真实任务比较灵敏度、动态范围、稳定性、通量、软件工作流和适用样品，而不是只按宣传页上的单一最高参数排高低。"
            "公平比较的结论应写成“在什么条件、什么指标和什么应用上谁更合适”，而不是给平台做脱离场景的总排名。"
        ),
        "boundary": "原会话支持高分辨质谱的比较框架；具体型号的定量性能仍需同批样品、统一方法与厂商规定的测试条件实测。",
        "refs": [
            ref(
                "2026-07-14",
                "S08",
                "MRT Beta测试理解",
                "原回答强调平台比较应同时看定位、硬件架构、典型应用、软件成熟度、稳定性与通量，并指出分辨率数值只有在 m/z、FWHM、模式和扫描速度一致时才可比较。",
                "6a558b83-8a0c-83ec-b571-7686fce9c34d",
            ),
            ref(
                "2026-07-27",
                "S04",
                "MRT与G3分辨率比较",
                "原回答定义 R=m/Δm 和 FWHM，并说明视觉峰宽还会受信号强度、平滑、采样与显示尺度影响。",
                "6a67023b-bd44-83ec-8861-79e44ffc966c",
            ),
        ],
    },
    "monoisotopic mass、average mass、neutral mass 和 m/z 有什么区别？": {
        "answer": (
            "monoisotopic mass 是同位素包络中 M 峰所对应的精确质量口径；average mass 是平均分子量口径，两者数值不能混用。"
            "原会话以 Tyr 为例给出 monoisotopic/exact mass 181.07389321 Da、average molecular weight 181.19 Da。"
            "neutral mass 是去卷积后、不带电荷与加合物表达的分子质量；m/z 则是仪器实际观测的带电离子质量电荷比。"
            "例如中性质量为 M、带 z 个质子的正离子满足 m/z=(M+zH)/z；Tyr 的中性 M 为 181.07389，而 [M+H]⁺ 为 182.08117。"
            "因此软件匹配前要先确认输入的是单同位素还是平均质量、界面展示的是中性质量还是原始 m/z，以及电荷态和 H/Na/K 等加合物是否一致；M+1 成为最高峰也不代表它是新的化合物。"
        ),
        "boundary": "原会话直接给出 monoisotopic/exact mass、average molecular weight、neutral mass 与 m/z 的实例及换算关系；average mass 的同位素加权计算公式未在原会话展开，因此本答案不补写未被该会话直接支持的计算细节。",
        "refs": [
            ref(
                "2026-07-15",
                "S03",
                "Tyr MAFESY衍生化离子分析",
                "原回答把 Tyr 的 exact/monoisotopic mass 列为 181.07389321 Da、average molecular weight 列为 181.19 Da，并区分中性 M 181.07389、[M+H]+ 182.08117、[M-H]- 180.06662 与 [M+Na]+ 204.06311。",
                "6a57203f-fc70-83ec-9aad-27512eb8ec1d",
            ),
            ref(
                "2026-07-02",
                "S04",
                "UNIFI M+1命中问题分析",
                "原回答说明 Component 输入应使用中性 monoisotopic mass，软件按 m/z=(M+zH)/z 换算；原始 m/z 同位素包络与去卷积后的观测中性质量不能混看。",
                "6a461bf5-152c-83ec-a9df-1db7fe224960",
            ),
        ],
    },
}


def walk(node: object):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.registry.read_text(encoding="utf-8"))
    reviewed_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    found: set[str] = set()

    for item in walk(data):
        question = item.get("question")
        if item.get("answer_status") != "needs_source_review" or question not in UPDATES:
            continue
        update = UPDATES[question]
        answer = str(update["answer"])
        item["answer"] = answer
        item["detail"] = answer
        item["answer_status"] = "source_grounded"
        item["answer_origin"] = "chatgpt_conversation"
        item["evidence_boundary"] = update["boundary"]
        item["evidence_refs"] = update["refs"]
        item["provenance_reviewed_at"] = reviewed_at
        found.add(question)

    missing = sorted(set(UPDATES) - found)
    pending = [
        item.get("question")
        for item in walk(data)
        if item.get("answer_status") == "needs_source_review"
    ]
    print(
        json.dumps(
            {"matched": len(found), "missing": missing, "still_pending": pending},
            ensure_ascii=False,
            indent=2,
        )
    )

    if not args.apply:
        return 0 if not missing else 1
    if missing:
        raise SystemExit("Refusing to apply because expected questions were not found")

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    backup = args.backup_dir / f"project-dashboard-case-registry-before-final-three-{stamp}.json"
    shutil.copy2(args.registry, backup)
    data["updated_at"] = reviewed_at
    args.registry.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
