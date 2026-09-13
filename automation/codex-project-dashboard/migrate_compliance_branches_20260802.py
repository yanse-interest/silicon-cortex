#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
REGISTRY = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/wiki/project-dashboard-case-registry.json")
BACKUP = Path(__file__).with_name("registry-before-compliance-branches-20260802.json")
LAB_CASE_IDS = {
    "case-c15a153c9e934f8e",
    "case-62f956b227a365c7",
    "case-ba9bd123633aca41",
    "case-cbf44407df62fc24",
}
LAB_SCOPES = {"caaa", "instrument_inquiry", "peptide_mapping"}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:20]


def abstract_event(branch_id: str, period: str, kind: str, detail: str, capability_ids: list[str]) -> dict:
    return {
        "event_id": stable_id("abstract-", branch_id, period, kind, detail),
        "period": period,
        "title": "合规抽象工作记录",
        "detail": detail,
        "source": "compliance_abstracted_from_prior_memory",
        "source_kind": "compliance_abstracted",
        "source_status": "abstracted",
        "coverage": "bounded_partial",
        "evidence_type": "reported_or_bounded_inference",
        "state": "branch_progress" if kind == "progress" else "branch_question",
        "branch_id": branch_id,
        "case_id": None,
        "capability_domain_ids": capability_ids,
        "progress_node": kind == "progress",
        "privacy_mode": "non_reconstructable",
        "created_at": now_iso(),
    }


def capability_item(domain_id: str, period: str, detail: str, branch_ids: list[str]) -> dict:
    return {
        "event_id": stable_id("capability-", domain_id, period, detail),
        "period": period,
        "title": "能力方法沉淀",
        "detail": detail,
        "source": "compliance_abstracted_from_prior_memory",
        "source_kind": "compliance_abstracted",
        "source_status": "abstracted",
        "coverage": "bounded_partial",
        "evidence_type": "bounded_synthesis",
        "state": "capability_item",
        "capability_domain_id": domain_id,
        "branch_ids": branch_ids,
        "case_id": None,
        "progress_node": False,
        "privacy_mode": "non_reconstructable",
        "created_at": now_iso(),
    }


def value(branch_id: str, detail: str) -> dict:
    return {
        "value_id": stable_id("value-", branch_id, detail),
        "date": "2026-07",
        "detail": detail,
        "source": "compliance_abstracted_from_prior_memory",
        "evidence_mode": "compliance_abstracted",
        "privacy_mode": "non_reconstructable",
        "created_at": now_iso(),
    }


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if not BACKUP.exists():
        shutil.copy2(REGISTRY, BACKUP)

    registry["version"] = 3
    registry["cases"] = [case for case in registry.get("cases", []) if case.get("case_id") not in LAB_CASE_IDS]
    registry["events"] = [
        event
        for event in registry.get("events", [])
        if event.get("case_id") not in LAB_CASE_IDS
        and event.get("classification_scope") not in LAB_SCOPES
        and event.get("branch_id") not in {"branch-sequencing", "branch-caaa"}
    ]

    registry["branches"] = [
        {
            "branch_id": "branch-sequencing",
            "title": "解序",
            "status": "active",
            "current_summary": "已围绕结构表示、碎片证据和跨平台验证形成持续工作方向；具体项目、对象、参数与结果不进入派生看板。",
            "privacy_mode": "compliance_abstracted",
            "time_granularity": "month",
            "capability_domain_ids": ["instrument_methodology"],
            "value_items": [
                value("branch-sequencing", "结构表示与可解释对象建模应先于参数强化；表示层错误不能靠继续提高能量补偿。"),
                value("branch-sequencing", "序列确认应从单一母离子匹配转向碎片证据与覆盖边界，避免把软件命中直接当作闭环。"),
            ],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
        {
            "branch_id": "branch-caaa",
            "title": "CAAA",
            "status": "active",
            "current_summary": "已围绕水解、衍生化、构型判定与质谱验证建立持续需求方向；不保存可识别具体项目的信息。",
            "privacy_mode": "compliance_abstracted",
            "time_granularity": "month",
            "capability_domain_ids": ["instrument_methodology"],
            "value_items": [
                value("branch-caaa", "衍生化质量解释应先冻结反应位点、理论变化和诊断证据，再排查加合、同位素或处理层异常。"),
                value("branch-caaa", "跨平台验证应区分采集对象、子离子选择和碰撞过程，不直接照搬不同厂商的参数名称或数值。"),
            ],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    ]

    registry["capability_domains"] = [
        {
            "capability_domain_id": "instrument_methodology",
            "title": "仪器知识与应用方法论",
            "summary": "横向支撑不同需求分支，沉淀测量对象、采集模式、软件处理层、硬件状态和跨平台边界。",
        },
        {
            "capability_domain_id": "peptide_mapping",
            "title": "肽图范围",
            "summary": "当前作为独立知识范围保存，不建立具体 case，也不自动升级为需求分支。",
        },
    ]

    registry["events"].extend(
        [
            abstract_event("branch-sequencing", "2026-06", "progress", "开始围绕复杂结构的表示、辅助处理与碎裂证据建立解序路径。", ["instrument_methodology"]),
            abstract_event("branch-sequencing", "2026-07", "progress", "工作重点推进到特殊对象建模、碎片覆盖证据和软件处理边界。", ["instrument_methodology"]),
            abstract_event("branch-sequencing", "2026-07", "progress", "开始梳理跨平台采集与验证流程；未保存具体对象、参数或实验结果。", ["instrument_methodology"]),
            abstract_event("branch-sequencing", "2026-07", "question", "如何区分结构表示问题、碎裂不足和软件解释问题仍需持续沉淀。", ["instrument_methodology"]),
            abstract_event("branch-sequencing", "2026-07", "question", "碎片覆盖与序列确认的证据阈值仍需按可访问资料建立边界。", ["instrument_methodology"]),
            abstract_event("branch-caaa", "2026-07", "progress", "围绕水解、衍生化、构型判断和诊断证据建立分析问题框架。", ["instrument_methodology"]),
            abstract_event("branch-caaa", "2026-07", "progress", "开始梳理从扫描、子离子采集到验证路径的跨平台方法关系。", ["instrument_methodology"]),
            abstract_event("branch-caaa", "2026-07", "question", "水解与衍生化造成的质量变化和异常信号解释仍需保持结构先行。", ["instrument_methodology"]),
            abstract_event("branch-caaa", "2026-07", "question", "构型判断所需的诊断证据、对照和适用边界仍需继续积累。", ["instrument_methodology"]),
            abstract_event("branch-caaa", "2026-07", "question", "跨平台采集模式与验证步骤的对应关系仍需文档化。", ["instrument_methodology"]),
            capability_item("instrument_methodology", "2026-07", "区分 Full scan、SIM、MS/MS、Product Ion 与 MRM 的测量对象和证据用途。", ["branch-sequencing", "branch-caaa"]),
            capability_item("instrument_methodology", "2026-07", "跨厂商比较应区分功能相似、实现等价与数值可迁移，参数名相近不代表可直接照搬。", ["branch-sequencing", "branch-caaa"]),
            capability_item("instrument_methodology", "2026-07", "数据解释需区分原始信号、峰、组分和计算结果，避免跨处理层混用口径。", ["branch-sequencing", "branch-caaa"]),
            capability_item("instrument_methodology", "2026-07", "比较仪器能力前应固定测量定义、硬件配置、软件版本和处理条件。", ["branch-sequencing", "branch-caaa"]),
            capability_item("instrument_methodology", "2026-07", "样品前处理与耗材影响应通过空白、回收、重复性和材料对照验证，不从单次现象直接归因。", ["branch-sequencing", "branch-caaa"]),
            capability_item("peptide_mapping", "2026-07", "蛋白酶特异性与 cleavage 术语必须对应，不能把不同酶切体系混称。", []),
            capability_item("peptide_mapping", "2026-07", "酸化、沉淀和稳定性问题应依靠时间点、空白和处理对照判断。", []),
        ]
    )

    registry["privacy_policy"] = {
        "mode": "compliance_abstracted",
        "work_case_storage": "disabled",
        "time_granularity": "month",
        "forbidden": ["customer", "project", "sample", "batch", "sequence_or_structure", "raw_data", "exact_parameters", "internal_links", "reconstructable_timeline"],
        "allowed": ["demand_branch", "abstract_progress", "knowledge_gap", "capability_method", "reusable_value", "evidence_boundary"],
    }
    registry.setdefault("migration_audit", []).append(
        {
            "at": now_iso(),
            "kind": "compliance_abstracted_work_value_model",
            "authority": "user_confirmed",
            "summary": "实验工作不保存具体 case；解序与 CAAA 转为需求分支；仪器方法为横向能力域。",
            "backup": str(BACKUP),
        }
    )
    registry["updated_at"] = now_iso()
    temporary = REGISTRY.with_name(f".{REGISTRY.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, REGISTRY)


if __name__ == "__main__":
    main()
