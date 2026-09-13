#!/usr/bin/env python3
"""Backfill source-grounded evidence for legacy instrument exam questions."""

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
    "Full scan、SIM、MS/MS、Product Ion 和 MRM 分别测量什么？": {
        "refs": [
            ref(
                "2026-07-31",
                "S01",
                "SIM与MSMS区别",
                "原回答明确区分 SIM 只监测指定母离子 m/z，而 MS/MS 选择母离子后经碰撞采集子离子；MRM 是固定母离子到子离子的跃迁。",
                "6a6c16f8-c0f0-83ec-8e3d-92264260d8cc",
            ),
            ref(
                "2026-07-31",
                "S05",
                "SIM SCAN 离子挑选",
                "原回答说明 Product Ion 模式由 Q1 固定前体、碰撞池碎裂、Q3 扫描产物离子质量范围，用于筛选 MRM 子离子。",
                "6a6c694d-b1e4-83ec-b12a-1e7d4e9ebc92",
            ),
        ]
    },
    "为什么不同厂商仪器的同名或近似参数不能直接照搬？": {
        "refs": [
            ref(
                "2026-07-31",
                "S02",
                "QQQ MRM子离子选择",
                "原回答逐项说明 Waters 与 Agilent 参数只能按功能对照；碰撞池、气压和离子光学不同，CE 与源区电压都不能按数值直接照搬。",
                "6a6c0bf8-40ac-83ec-a05a-040c1800068e",
            )
        ]
    },
    "如何判断滤膜、样品瓶或其他耗材是否影响分析结果？": {
        "question": "如何判断滤膜是否影响分析结果？",
        "answer": "应比较未过滤对照与不同膜材、不同弃液体积和重复过滤条件，重点观察目标物回收率或峰面积、峰形、杂质谱和重复性。峰形改善但峰面积明显下降更支持目标物被膜吸附；峰形改善且峰面积基本不变，更可能是去除了微粒或聚集体。还应设置用初始流动相等倍稀释的对照，以区分过滤效应与进样溶剂效应。",
        "refs": [
            ref(
                "2026-07-30",
                "S04",
                "多肽尼龙滤膜效应",
                "原回答提出过滤前后峰面积、峰形、弃液体积及 PVDF、RC、PES 等膜材对照，并用回收率变化区分吸附与去除聚集体。",
                "6a6ae568-6014-83ec-baed-8cc6eb4e1aae",
            )
        ],
    },
    "为什么提高碰撞能量不能解决所有碎片不足或匹配失败？": {
        "refs": [
            ref(
                "2026-07-08",
                "S04",
                "UNIFI解线肽",
                "原回答说明提高 CE 只能增强碎裂，不能保证提高序列覆盖；过高会增加二级小碎片，而错误前体、特殊残基规则或理论库缺失仍需单独修正。",
                "6a4e607e-a138-83ec-9fcc-0b8882d36746",
            )
        ]
    },
    "质谱分辨本领 R=m/Δm 应该怎样理解？": {
        "refs": [
            ref(
                "2026-07-27",
                "S04",
                "MRT与G3分辨率比较",
                "原回答定义 FWHM 为半高处峰宽，并说明 R=m/Δm；分子和分母使用相同质量坐标单位，因此分辨本领本身无量纲。",
                "6a67023b-bd44-83ec-8861-79e44ffc966c",
            )
        ]
    },
    "比较两台高分辨质谱的分辨能力时要控制哪些条件？": {
        "refs": [
            ref(
                "2026-07-27",
                "S04",
                "MRT与G3分辨率比较",
                "原回答强调分辨率需在同一或相近 m/z、同一 FWHM 口径下比较；视觉峰宽还会受强度、采样、平滑和显示尺度影响。",
                "6a67023b-bd44-83ec-8861-79e44ffc966c",
            )
        ]
    },
    "怎样判断质谱仪的真空状态是否正常？": {
        "refs": [
            ref(
                "2026-06-29",
                "S03",
                "MRT抽真空判断标准",
                "原回答要求结合对应真空区的读数及稳定趋势、泵状态、软件 Ready/Vacuum OK 状态和报警判断，不能只按抽气时间或单一颜色下结论。",
                "6a422bc0-11dc-83ea-a668-37d96b88fc2f",
            )
        ]
    },
    "仪器安装、校准、IQ 和 OQ 有什么区别？": {
        "refs": [
            ref(
                "2026-06-30",
                "S10",
                "Waters MRT 安装流程",
                "原回答区分安装与抽真空、质量校准及性能优化、IQ 的安装和文档确认、OQ 的规定范围性能验证，并指出校准不等于已经进入 IQ/OQ。",
                "6a436b31-9ed0-83ea-ad23-bcace0e5205c",
            )
        ]
    },
    "WASH、样品流路、CALIBRANT 和 LOCKMASS 分别有什么作用？": {
        "question": "在 MRT 流体系统中，WASH、SPL/CALIBRANT 和 REF/LOCKMASS 分别有什么作用？",
        "answer": "WASH 为样品与参考液路提供清洗并将冲洗液排向废液；SPL/Sample 流路通过样品泵和样品喷针输送样品或校准液，在当前配置中 NaI 校准液走该路；REF/Reference 流路通过参考泵和参考喷针输送 LE 等 LockMass 参考液。挡板只决定 Sample 或 Reference 喷雾哪一路进入质谱，不负责选择瓶子；实际瓶位和阀口仍以本机标签及手册为准。",
        "refs": [
            ref(
                "2026-08-04",
                "S06",
                "LE和NaI的选择",
                "原回答给出两套独立液路：SPL 经 Sample pump/sprayer 输送 NaI 校准液，REF 经 Reference pump/sprayer 输送 LE，WASH 用于清洗两套液路。",
                "6a71bf7d-9f88-83ec-88cd-5bf625a1d366",
            )
        ],
    },
    "自动进样器的最大进样量由什么决定？": {
        "refs": [
            ref(
                "2026-07-27",
                "S05",
                "Waters进样器最大量程",
                "原回答要求从 Console 的 Volume Configuration 核对 needle、extension loop 和 syringe 配置，并说明软件允许值不能代替实际硬件容量。",
                "6a670741-9760-83ec-9e93-0c222f219d4f",
            )
        ]
    },
    "MSe 与 targeted MS/MS 的核心区别是什么？": {
        "refs": [
            ref(
                "2026-07-20",
                "S02",
                "MS/MS模式解析",
                "原回答说明 targeted MS/MS 由四极杆选择指定前体后碎裂；MSe 不隔离单一前体，而以低能和高能交替采集并靠 RT、峰形等将碎片归属前体。",
                "6a5db90a-81bc-83ec-a02b-e78b9967c43a",
            )
        ]
    },
    "三重四极杆从寻找母离子到建立 MRM 方法的一般流程是什么？": {
        "refs": [
            ref(
                "2026-07-31",
                "S05",
                "SIM SCAN 离子挑选",
                "原回答给出先以 Scan/SIM 确认前体，再做 Product Ion 扫描选择子离子，随后逐 transition 优化 CE 并建立 MRM 的流程。",
                "6a6c694d-b1e4-83ec-b12a-1e7d4e9ebc92",
            ),
            ref(
                "2026-07-31",
                "S02",
                "QQQ MRM子离子选择",
                "原回答补充最终应在 QQQ 上重新优化每条 transition 的 CE，并以峰形、信噪比、离子比、基质干扰和重复性验证。",
                "6a6c0bf8-40ac-83ec-a05a-040c1800068e",
            ),
        ]
    },
    "离子源或接口电压与碰撞能量有什么本质区别？": {
        "refs": [
            ref(
                "2026-07-31",
                "S02",
                "QQQ MRM子离子选择",
                "原回答明确区分 Cone/Fragmentor 的源后传输和源内碎裂作用，与碰撞池内 CE 的 CID 碎裂作用；两类参数不能等同。",
                "6a6c0bf8-40ac-83ec-a05a-040c1800068e",
            )
        ]
    },
    "TIC、色谱峰检测和 deconvolution 分别解决什么问题？": {
        "refs": [
            ref(
                "2026-07-21",
                "S01",
                "Masshunter分析模式区别",
                "原回答说明 TIC Analysis 在总离子流上积分找色谱峰；deconvolution 按各 m/z 的保留时间和峰形重建组分，可拆分共流出化合物。",
                "6a5ee117-df80-83ec-bb9b-a2bcae39bf22",
            )
        ]
    },
    "质谱中的 M+1 峰通常表示什么，为什么不能直接当成新组分？": {
        "refs": [
            ref(
                "2026-07-02",
                "S04",
                "UNIFI M+1命中问题分析",
                "原回答解释约 5000 Da 多肽的 M+1 可因天然 13C 同位素而成为包络中最强峰；软件应按单同位素质量和同位素模型匹配，不能把最高峰直接当成新组分。",
                "6a461bf5-152c-83ec-a9df-1db7fe224960",
            )
        ]
    },
    "为什么序列软件中 component、修饰和自定义残基不能混用？": {
        "refs": [
            ref(
                "2026-07-08",
                "S04",
                "UNIFI解线肽",
                "原会话明确指出特殊片段应作为自定义氨基酸进入序列逻辑，而不是只建为 component；否则理论碎片和序列覆盖无法正确解释。",
                "6a4e607e-a138-83ec-9fcc-0b8882d36746",
            )
        ]
    },
    "为什么仪器软件的操作步骤必须注明版本和模块？": {
        "refs": [
            ref(
                "2026-08-04",
                "S01",
                "MassHunter Bio vs Qual",
                "原回答按 MassHunter Qual、BioConfirm 与 OpenLab 的不同模块、数据对象和入口解释功能边界，并指出具体能力取决于安装模块和版本。",
                "6a713e19-d9e8-83ec-9787-270baa7f8ade",
            )
        ]
    },
    "为什么切换旁路后压力下降不能直接证明某个部件堵塞？": {
        "refs": [
            ref(
                "2026-07-30",
                "S03",
                "切旁路压力下降原因",
                "原回答依据 Port 1、Port 6 及废液路径说明：压力下降只定位到被隔离的下游区段；必须确认阀位和出液位置才能区分柱后、阀体、针座或样品环。",
                "6a6aba1c-b944-83ec-808e-3c75684eef95",
            )
        ]
    },
    "出现色谱基线噪声或分离度下降时应怎样系统排查？": {
        "refs": [
            ref(
                "2026-07-30",
                "S01",
                "UPLC基线下降问题分析",
                "原回答建议以重复空白梯度、旁路色谱柱、多波长比较和 reference wavelength 检查，区分流动相梯度背景、色谱柱与检测器因素。",
                "6a6aa858-8b14-83ec-9064-80c8fbd3abe6",
            )
        ]
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
        item["question"] = update.get("question", question)
        if "answer" in update:
            item["answer"] = update["answer"]
            item["detail"] = update["answer"]
        item["answer_status"] = "source_grounded"
        item["answer_origin"] = "chatgpt_conversation"
        item["evidence_refs"] = update["refs"]
        item["provenance_reviewed_at"] = reviewed_at
        found.add(question)

    missing = sorted(set(UPDATES) - found)
    summary = {
        "matched": len(found),
        "missing": missing,
        "still_pending": sum(
            1
            for item in walk(data)
            if item.get("answer_status") == "needs_source_review"
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not args.apply:
        return 0 if not missing else 1

    if missing:
        raise SystemExit("Refusing to apply because expected questions were not found")
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    backup = args.backup_dir / f"project-dashboard-case-registry-before-backcheck-{stamp}.json"
    shutil.copy2(args.registry, backup)
    args.registry.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
