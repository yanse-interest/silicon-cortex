#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
REGISTRY = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/wiki/project-dashboard-case-registry.json")
BACKUP = Path(__file__).with_name("registry-before-lab-case-scope-20260802.json")
SEQUENCE_CASE = "case-c15a153c9e934f8e"
CAAA_CASE = "case-62f956b227a365c7"
RETIRED_GROUPS = {"case-ba9bd123633aca41", "case-cbf44407df62fc24"}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def upsert_event(registry: dict, event: dict) -> None:
    existing = next((item for item in registry["events"] if item.get("event_id") == event["event_id"]), None)
    if existing is None:
        registry["events"].append(event)
    else:
        existing.update(event)


def daily_event(date: str, session: str, title: str, detail: str, *, scope: str, case_id: str | None = None) -> dict:
    return {
        "event_id": f"chatgpt-daily:{date}:{session}",
        "date": date,
        "title": title,
        "detail": detail,
        "source": f"sources/conversations/chatgpt-daily/2026/chatgpt-daily-report-{date}.md",
        "source_kind": "daily",
        "source_status": "access_incomplete",
        "coverage": "partial",
        "session_id": session,
        "evidence_type": "reported",
        "event_kind": "related_question" if case_id else "reference",
        "state": "linked" if case_id else "reference",
        "case_id": case_id,
        "classification_scope": scope,
        "progress_node": False,
        "created_at": now_iso(),
        "classified_at": now_iso(),
        "classification_authority": "user_confirmed_2026-08-02",
    }


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if not BACKUP.exists():
        shutil.copy2(REGISTRY, BACKUP)

    cases = {item["case_id"]: item for item in registry["cases"]}
    sequence = cases[SEQUENCE_CASE]
    sequence.update(
        {
            "title": "解序",
            "routing_terms": ["多肽", "硫醚", "环肽", "解序", "unifi", "mse", "fragmentation viewer", "碎片覆盖"],
            "updated_at": now_iso(),
            "field_authority": {**sequence.get("field_authority", {}), "title": "user_confirmed"},
        }
    )

    if CAAA_CASE not in cases:
        registry["cases"].append(
            {
                "case_id": CAAA_CASE,
                "title": "CAAA",
                "line": "work",
                "category": "rd_case",
                "status": "in_progress",
                "current_summary": "已确认 CAAA 的问题范围；相关问询与真实进度节点分开记录，当前尚未从问询记录推断实验进度。",
                "next_step": "后续仅在出现实验行动、结果、关键判断或明确下一步时新增进度 Log。",
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "evidence_mode": "compliance_abstracted",
                "needs_review": False,
                "routing_terms": ["CAAA", "FDAA", "Marfey", "DCl", "D/L", "构型", "氨基酸", "Product Ion", "MRM"],
                "origin": {"kind": "manual_user_confirmed"},
                "field_authority": {"title": "user_confirmed", "category": "user_confirmed", "status": "user_confirmed"},
            }
        )

    for case_id in RETIRED_GROUPS:
        case = cases.get(case_id)
        if case:
            case.update(
                {
                    "status": "archived",
                    "hidden": True,
                    "retired_reason": "用户确认其内容属于仪器认知问询或独立范围，不构成真实实验工作 case。",
                    "updated_at": now_iso(),
                }
            )

    for event in registry["events"]:
        if event.get("case_id") in RETIRED_GROUPS:
            event.update(
                {
                    "state": "reference",
                    "case_id": None,
                    "classification_scope": "instrument_inquiry",
                    "progress_node": False,
                    "classified_at": now_iso(),
                    "classification_authority": "user_confirmed_2026-08-02",
                }
            )

    by_id = {event.get("event_id"): event for event in registry["events"]}
    edits = {
        "event-eeea27e73cb65948f545": "继续围绕 Waters Q-TOF、MS/MS/MSE、mCPBA 氧化和硫醚键环肽压缩解序实验矩阵与参数问题。",
        "event-33dd4800a0a9dd363aba": "连续推进 UNIFI component、Fragmentation Viewer、MSe/MS/MS、molfile 导入和氧化结构的解序处理。",
        "event-d6bc54a8b0d9fad0853a": "解序线推进到 C 端酰胺化设置和 waters_connect/MRT peptide sequencing 流程核对。",
    }
    for event_id, detail in edits.items():
        if event_id in by_id:
            by_id[event_id]["detail"] = detail
    for event_id in {"event-068e5449ea875674e7fe", "event-8b28a7aee3d09ebde771", "event-fc461c137c5fc9a51d7d"}:
        if event_id in by_id:
            by_id[event_id].update({"state": "superseded", "case_id": None, "superseded_by": "session_level_classification_2026-08-02"})

    caaa_items = [
        ("2026-07-13", "S04", "aeY氨基酸含义解析", "FDAA、Tyr、氘代水解、D/L 构型及 LC-MS/MS 诊断证据问询。"),
        ("2026-07-14", "S03", "组氨酸缩写", "组氨酸缩写及基础代码问询。"),
        ("2026-07-14", "S05", "多肽水解难题解析", "6N DCl 水解困难、特殊结构与分析条件问询。"),
        ("2026-07-14", "S07", "氨基酸种类分析", "常见氨基酸种类、代码与基础分类问询。"),
        ("2026-07-16", "S03", "FDAA样品TFA体系出峰", "FDAA 衍生样品在 TFA 体系下出峰及临时查看 full mass。"),
        ("2026-07-16", "S06", "FDAA分子量及衍生化", "FDAA 分子量及氘代后衍生化质量计算。"),
        ("2026-07-16", "S07", "DCl分子量查询", "DCl 分子量问询。"),
        ("2026-07-20", "S02", "MS/MS模式解析", "MS/MS 母离子选择、碰撞与子离子采集问询。"),
        ("2026-07-20", "S03", "SIM与MRM区别", "SIM 与 MRM 的对象和用途区别。"),
        ("2026-07-20", "S04", "Tyr典型子离子掉失", "Tyr 典型诊断子离子与中性丢失问询。"),
        ("2026-07-27", "S01", "FDAA衍生质量增加", "FDAA/Marfey 衍生后的质量增加问询。"),
        ("2026-07-27", "S03", "FDAA M+31可能性分析", "FDAA 衍生 LC-MS 中异常 M+31 信号排查。"),
        ("2026-07-31", "S01", "SIM与MSMS区别", "SIM 与 MS/MS 采集模式区别。"),
        ("2026-07-31", "S02", "QQQ MRM子离子选择", "QQQ Product Ion、MRM transition、CE 与跨平台参数理解。"),
        ("2026-07-31", "S05", "SIM SCAN 离子挑选", "Agilent 6475 从 SIM/SCAN、Product Ion 到 MRM transition 的选择流程。"),
    ]
    for date, session, title, detail in caaa_items:
        upsert_event(registry, daily_event(date, session, title, detail, scope="caaa", case_id=CAAA_CASE))

    instrument_items = [
        ("2026-07-02", "S04", "UNIFI M+1命中问题分析", "UNIFI 中 M+1、monoisotopic/average/neutral mass、m/z 与质量容差问询。"),
        ("2026-07-02", "S14", "mCPBA氧化电荷分析", "统计同一物质全部电荷态而非只看最大丰度 charge state。"),
        ("2026-07-27", "S04", "MRT与G3分辨率比较", "MRT/G3 的 resolving power、FWHM、peak width 与小数显示比较。"),
        ("2026-07-27", "S05", "Waters进样器最大量程", "依据硬件配置和手册确认 Waters autosampler 最大进样量。"),
    ]
    for date, session, title, detail in instrument_items:
        upsert_event(registry, daily_event(date, session, title, detail, scope="instrument_inquiry"))

    peptide_map_items = [
        ("2026-06-29", "S04", "Trypsin酶切原理", "trypsin 酶切原理、24 小时后酸化冷藏与稳定性测试。"),
        ("2026-07-01", "S10", "碳酸氢铵与冰醋酸反应", "trypsin 肽图体系中碳酸氢铵、酸化沉淀和 HPLC 前处理。"),
        ("2026-07-07", "S02", "酶切术语区分", "trypsin 酶切与 Asp-N cleavage 的术语边界。"),
        ("2026-07-07", "S06", "酶切后溶液絮凝原因", "trypsin 酶切后絮凝、酸化浑浊与上清变化排查。"),
    ]
    for date, session, title, detail in peptide_map_items:
        upsert_event(registry, daily_event(date, session, title, detail, scope="peptide_mapping"))

    registry.setdefault("migration_audit", []).append(
        {
            "at": now_iso(),
            "kind": "lab_case_scope_correction",
            "authority": "user_confirmed",
            "summary": "建立 CAAA；保留解序；仪器认知与肽图不建 case；相关问询不计入进度。",
            "backup": str(BACKUP),
        }
    )
    registry["updated_at"] = now_iso()
    temporary = REGISTRY.with_name(f".{REGISTRY.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, REGISTRY)


if __name__ == "__main__":
    main()
