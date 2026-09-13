#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import calendar
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from compiled_source_contract import (
    SourceContractError,
    extract_instrument_candidates,
    extract_structured_candidates,
    load_compiled_source,
    stable_candidate_id,
)
from registry_split_store import SplitStoreCoordinator


TZ = ZoneInfo("Asia/Shanghai")
LIVE_STATE_ENV = "CODEX_PROGRESS_BRIDGE_LIVE_STATE"
DEFAULT_LIVE_STATE = Path.home() / ".codex/progress-bridge/live-state.json"
LIVE_STATUSES = {"running", "waiting_approval", "completed", "failed", "stale"}

WORK_CATEGORIES = [
    {"id": "experimental_work", "label": "实验工作"},
    {"id": "industry_career", "label": "产业与职业认知"},
    {"id": "ai_automation", "label": "AI 与自动化前瞻"},
]

CATEGORY_LABELS = {item["id"]: item["label"] for item in WORK_CATEGORIES}
CATEGORY_LABELS.update(
    {
        "rd_case": "研发实践与问题解决（历史兼容）",
        "instrument_methodology": "仪器知识与应用方法论（历史兼容）",
        "life": "生活",
    }
)

STATUS_LABELS = {
    "in_progress": "进行中",
    "unknown": "待确认",
    "on_hold": "暂缓",
    "completed": "已完成",
    "archived": "已归档",
}

ACTIVE_STATUSES = {"in_progress", "unknown", "on_hold"}

CASE_CATEGORY_BY_ID = {
    "case-c15a153c9e934f8e": "rd_case",
    "case-ba9bd123633aca41": "instrument_methodology",
    "case-cbf44407df62fc24": "rd_case",
    "case-232ed8090e8c20a5": "industry_career",
    "case-e652ced6fd788ff2": "ai_automation",
    "case-db88755f08e823fe": "ai_automation",
    "case-3e47bd58f785c139": "ai_automation",
    "case-141f8976c72c3370": "life",
    "case-33cb33a5570f5d73": "life",
    "case-be3c9c425a6ca80f": "life",
    "case-5bb67e618275df9a": "life",
    "case-d25ff49419f62e22": "life",
    "case-fa6bb6808872d85c": "life",
    "case-d2fe54720e1da554": "life",
}

CASE_CATEGORY_TITLE_RULES = [
    ("Nutrition Tracker", "ai_automation"),
    ("Hermes", "ai_automation"),
    ("多肽", "rd_case"),
    ("色谱", "rd_case"),
    ("仪器", "instrument_methodology"),
    ("Q-TOF", "instrument_methodology"),
    ("CDMO", "industry_career"),
    ("CXO", "industry_career"),
    ("Codex", "ai_automation"),
    ("ChatGPT", "ai_automation"),
]

CASE_SUGGESTION_TERMS = {
    "case-c15a153c9e934f8e": ["多肽", "硫醚", "环肽", "解序", "unifi", "mse", "product ion", "mrm"],
    "case-62f956b227a365c7": ["caaa", "fdaa", "marfey", "dcl", "d/l", "构型", "氨基酸", "product ion", "mrm"],
    "case-ba9bd123633aca41": ["mrt", "q-tof", "新仪器", "lockmass", "calibrant", "waters_connect"],
    "case-cbf44407df62fc24": ["色谱", "基线", "压力", "滤膜", "卡尔费休", "样品前处理"],
    "case-232ed8090e8c20a5": ["cdmo", "cxo", "职业", "产业", "能力路线"],
    "case-e652ced6fd788ff2": ["codex", "路由器", "账号切换", "自动化治理"],
    "case-db88755f08e823fe": ["日报", "weekly review", "review cycle", "cognitive observatory"],
    "case-5bb67e618275df9a": ["饮食", "运动", "体重", "健康追踪"],
    "case-d2fe54720e1da554": ["智驾", "租车", "noa", "小鹏"],
}

GENERIC_CONVERGENCE_TERMS = {
    "codex", "chatgpt", "日报", "归档", "任务", "项目", "修复", "完成", "系统", "自动化",
    "开发", "部署", "验证", "更新", "进度", "问题", "工作流", "健康", "饮食", "运动",
    "账号", "账号路由", "账号切换", "固定账号", "门禁", "review", "职业", "产业", "体重",
}

# Only project-exclusive names and product/repository identifiers belong here.
# General subject words stay out even when a case's legacy routing_terms contains
# them: this projection must prefer an unresolved group over a false association.
DISTINCTIVE_PROJECT_TERMS = {
    "case-232ed8090e8c20a5": ["cdmo/cxo 产业判断与分析研发能力路线", "医药外包产业判断"],
    "case-e652ced6fd788ff2": ["codex-account-router", "codex account router", "codex 账号路由器与自动化治理", "routerctl"],
    "case-db88755f08e823fe": ["codex-project-dashboard", "codex-progress-bridge", "codex-memory-maintainer", "chatgpt-daily-report", "cognitive observatory"],
    "case-3e47bd58f785c139": ["nutrition tracker", "nutrition-tracker", "nutrition tracker 与 hermes 生产 skill 维护"],
    "case-141f8976c72c3370": ["deartime", "ai 相册整理与电子相框构想"],
    "case-33cb33a5570f5d73": ["2026 年 8 月云南父母同行旅行规划"],
    "case-be3c9c425a6ca80f": ["临时小猫照护与领养准备"],
    "case-5bb67e618275df9a": ["personal-health-analyst", "personal health analyst", "wearable-tracker-system"],
    "case-d25ff49419f62e22": ["家庭健康与探望判断"],
    "case-fa6bb6808872d85c": ["auto-song-list", "音乐拆解与视觉表达学习"],
    "case-d2fe54720e1da554": ["用车体验和买车考虑"],
    "project-sequencing": ["硫醚环肽氧化辅助解序"],
    "project-caaa": ["caaa"],
}

SUGGESTED_PROJECT_TERMS = {
    "case-232ed8090e8c20a5": ["cdmo", "cxo", "医药外包"],
    "case-e652ced6fd788ff2": ["auto-off", "no-switch", "routerctl", "账号路由器"],
    "case-db88755f08e823fe": ["项目看板", "知识沉淀系统", "codex memory"],
    "case-3e47bd58f785c139": ["hermes", "营养追踪", "食品识别", "食品目录"],
    "case-141f8976c72c3370": ["电子相框", "相册整理"],
    "case-33cb33a5570f5d73": ["云南旅行", "云南行程", "父母同行"],
    "case-be3c9c425a6ca80f": ["小猫照护", "猫咪领养", "临时小猫"],
    "case-5bb67e618275df9a": ["体重趋势", "饮食记录", "运动记录", "可穿戴健康"],
    "case-d25ff49419f62e22": ["家庭探望", "探望判断"],
    "case-fa6bb6808872d85c": ["网易云", "音乐拆解"],
    "case-d2fe54720e1da554": ["高速 noa", "车辆智驾", "小鹏租车"],
    "project-sequencing": ["硫醚环肽", "多肽解序", "氧化辅助解序"],
    "project-caaa": ["fdaa", "marfey", "氨基酸构型分析"],
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def source_created_at(source_date: str) -> str:
    """Use source-owned time so replaying the same daily evidence is deterministic."""
    return f"{source_date}T00:00:00+08:00"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def stable_hash(*parts: str, prefix: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:20]


def frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.M)
    return match.group(1).strip() if match else ""


def markdown_section(text: str, headings: list[str]) -> str:
    for heading in headings:
        match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if match:
            return match.group(1).strip()
    return ""


def compact_markdown_text(value: str) -> str:
    """Collapse a bounded Markdown field without inventing missing content."""
    value = re.sub(r"\s+", " ", value or "").strip()
    return value.strip(" -")


def _chatgpt_event(
    *,
    date: str,
    session_id: str,
    title: str,
    detail: str,
    source: str,
    source_status: str,
    coverage: str,
) -> dict[str, Any]:
    event_kind, state = classify_daily_event(title, detail)
    evidence_boundary = "formal_daily_source_summary"
    if looks_like_confidential_work(title, detail):
        title = "实验工作内容（待合规抽象）"
        detail = "检测到可能涉及实验工作；派生看板未保留具体问题、对象、参数或结果。"
        event_kind, state = "compliance_review", "compliance_review"
        evidence_boundary = "compliance_abstracted"
    return {
        "event_id": f"chatgpt-daily:{date}:{session_id}",
        "date": date,
        "title": title.strip(),
        "detail": detail.strip(),
        "source": source,
        "source_kind": "daily",
        "source_status": source_status,
        "coverage": coverage,
        "session_id": session_id,
        "evidence_type": "reported",
        "evidence_boundary": evidence_boundary,
        "event_kind": event_kind,
        "state": state,
        "case_id": None,
        "created_at": source_created_at(date),
    }


def _validated_chatgpt_raw(text: str, *, date: str, wiki_root: Path) -> str:
    """Read only the immutable raw explicitly attested by the source summary."""
    raw_source = frontmatter_value(text, "raw_source").strip("\"'")
    if not re.fullmatch(
        rf"raw/conversations/chatgpt-daily/{re.escape(date[:4])}/chatgpt-daily-report-{re.escape(date)}\.md",
        raw_source,
    ):
        return ""
    vault_root = wiki_root.parent.resolve()
    raw_path = (vault_root / raw_source).resolve()
    allowed_root = (vault_root / "raw/conversations/chatgpt-daily").resolve()
    try:
        raw_path.relative_to(allowed_root)
    except ValueError:
        return ""
    if not raw_path.is_file():
        return ""
    raw_text = raw_path.read_text(encoding="utf-8")
    if frontmatter_value(raw_text, "type") != "chatgpt_daily_report":
        return ""
    if frontmatter_value(raw_text, "date") != date:
        return ""
    return raw_text


def _raw_chatgpt_sessions(raw_text: str) -> dict[str, tuple[str, str]]:
    """Parse the current immutable `## 会话摘要` presentation format."""
    if not raw_text:
        return {}
    section = markdown_section(raw_text, ["会话摘要", "Session Summaries"])
    matches = list(re.finditer(r"^###\s+(S\d{2,})\s+[—-]\s+(.+?)\s*$", section, re.M))
    result: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        session_id, title = match.groups()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.end():end]

        def field(label: str) -> str:
            found = re.search(
                rf"\*\*{re.escape(label)}[：:]\*\*\s*(.*?)(?=\n\s*\*\*[^*]+[：:]\*\*|\n\s*<details|\Z)",
                block,
                re.S,
            )
            return compact_markdown_text(found.group(1)) if found else ""

        discussed = field("我们聊了什么")
        conclusion = field("结论")
        detail = " ".join(part for part in (discussed, conclusion) if part)
        if detail:
            result[session_id] = (compact_markdown_text(title), detail)
    return result


def _validated_codex_raw(text: str, *, date: str, wiki_root: Path) -> str:
    """Read only the exact immutable Codex raw named by the compiled source."""
    raw_source = frontmatter_value(text, "raw_source").strip("\"'")
    if not re.fullmatch(
        rf"raw/conversations/codex-daily/{re.escape(date[:4])}/codex-daily-report-{re.escape(date)}\.md",
        raw_source,
    ):
        return ""
    vault_root = wiki_root.parent.resolve()
    raw_path = (vault_root / raw_source).resolve()
    allowed_root = (vault_root / "raw/conversations/codex-daily").resolve()
    try:
        raw_path.relative_to(allowed_root)
    except ValueError:
        return ""
    if not raw_path.is_file():
        return ""
    raw_text = raw_path.read_text(encoding="utf-8")
    if frontmatter_value(raw_text, "type") != "codex_daily_report":
        return ""
    if frontmatter_value(raw_text, "date") != date:
        return ""
    return raw_text


def _normalized_candidate_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[\s\-_—–]+", " ", value)
    value = re.sub(r"[^\w\u4e00-\u9fff ]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _candidate_id(candidate_type: str, domain: str, claim: str) -> str:
    return stable_candidate_id(candidate_type, domain, claim)


def parse_explicit_value_candidates(
    text: str,
    *,
    date: str,
    source: str,
    source_kind: str,
    source_status: str,
    coverage: str,
) -> list[dict[str, Any]]:
    """Accept only formal, low-risk reusable knowledge/workflow candidates.

    Completion events, inferred project progress, decisions, actions, cognitive
    promotions, and prose sections never enter this path. Rejected records are
    not persisted, which also prevents unsafe source text from leaking into the
    registry.
    """
    try:
        raw_candidates = extract_structured_candidates(text, required=False)
    except SourceContractError:
        return []
    expected_prefix = "S" if source_kind == "daily" else "T"
    accepted: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_type = str(candidate.get("type") or "").strip()
        domain = str(candidate.get("domain") or "").strip()
        claim = str(candidate.get("normalized_claim") or "").strip()
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        boundary = str(candidate.get("evidence_boundary") or "").strip()
        sessions = sorted(set(str(item).strip() for item in candidate.get("sessions") or []))
        confidence = candidate.get("confidence")
        conflicts = candidate.get("conflicts_with") or []
        target = str(candidate.get("suggested_target") or "").strip()
        if candidate_type not in {"knowledge", "workflow"}:
            continue
        if target != candidate_type:
            continue
        if not domain or not claim or not boundary:
            continue
        if candidate_id != _candidate_id(candidate_type, domain, claim):
            continue
        if str(candidate.get("source_date") or "") != date:
            continue
        if not sessions or any(not re.fullmatch(rf"{expected_prefix}\d{{2,}}", item) for item in sessions):
            continue
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or float(confidence) < 0.8:
            continue
        if candidate.get("evidence_complete") is not True:
            continue
        if str(candidate.get("assertion") or "") != "explicit" or str(candidate.get("risk") or "") != "low":
            continue
        if str(candidate.get("status") or "") not in {"candidate", "promoted"}:
            continue
        if not isinstance(conflicts, list) or conflicts:
            continue
        if looks_like_confidential_work(domain, claim, boundary):
            continue
        accepted.append(
            {
                "candidate_id": candidate_id,
                "value_id": f"value-{candidate_id}",
                "type": candidate_type,
                "domain": domain,
                "detail": claim,
                "date": date,
                "sessions": sessions,
                "source": source,
                "source_kind": source_kind,
                "source_status": source_status,
                "coverage": coverage,
                "evidence_type": "explicit",
                "evidence_boundary": boundary,
                "confidence": float(confidence),
            }
        )
    return accepted


def parse_instrument_knowledge_candidates(
    text: str,
    *,
    date: str,
    source: str,
    source_status: str,
    coverage: str,
) -> list[dict[str, Any]]:
    try:
        candidates, _ = extract_instrument_candidates(text, source_date=date, required=False)
    except SourceContractError:
        return []
    events: list[dict[str, Any]] = []
    project_ids = {"sequencing": "project-sequencing", "解序": "project-sequencing", "caaa": "project-caaa"}
    for candidate in candidates:
        question = candidate["question"]
        answer = candidate["answer"]
        summary = candidate["summary"]
        topic = candidate["topic"]
        instrument_types = candidate["instrument_types"]
        # Retain the canonical private locator in the local registry for source
        # provenance.  The H5 exporter uses a separate public allowlist and never
        # includes this field.
        evidence_refs = [
            {key: raw_ref[key] for key in (
                "source_type", "source_date", "session_id", "session_title", "support_level", "excerpt"
            )} | (
                {"source_locator": raw_ref["source_locator"]}
                if raw_ref.get("source_locator") else {}
            )
            for raw_ref in candidate["evidence_refs"]
        ]
        related = [
            project_ids[key]
            for raw in candidate.get("related_projects") or []
            if (key := str(raw).strip().lower()) in project_ids
        ]
        supplied_id = str(candidate.get("knowledge_id") or "").strip()
        event_id = supplied_id if re.fullmatch(r"instrument-knowledge-[a-z0-9-]{8,80}", supplied_id) else stable_hash(date[:7], topic, question, answer, prefix="instrument-knowledge-")
        events.append({
            "event_id": event_id,
            "period": date[:7],
            "date": date,
            "title": "知识与结论",
            "detail": answer,
            "summary": summary,
            "question": question,
            "answer": answer,
            "topic": topic,
            "instrument_types": instrument_types,
            "answer_status": "source_grounded",
            "answer_origin": "chatgpt_conversation" if any(ref["source_type"] == "chatgpt_conversation" for ref in evidence_refs) else "daily_report",
            "evidence_refs": evidence_refs,
            "source": source,
            "source_kind": "daily_instrument_knowledge",
            "source_status": source_status,
            "coverage": coverage,
            "evidence_type": str(candidate.get("confidence") or "bounded_synthesis"),
            "evidence_boundary": str(candidate.get("evidence_boundary") or "来源为日报归纳；具体型号与数值仍需厂商文档复核。"),
            "event_kind": "instrument_knowledge",
            "state": "capability_item",
            "capability_domain_id": "instrument_methodology",
            "related_project_ids": list(dict.fromkeys(related)),
            "knowledge_kind": "exam_qa",
            "knowledge_status": "reusable",
            "privacy_mode": "non_reconstructable",
            "progress_node": False,
            "created_at": source_created_at(date),
            "verification_refs": [],
        })
    return events


def parse_daily_source(path: Path, wiki_root: Path) -> list[dict[str, Any]]:
    try:
        compiled = load_compiled_source(
            path,
            memory_root=wiki_root.parent,
            expected_family="chatgpt",
            legacy_compatible=True,
            require_structured=False,
            require_instrument=False,
        )
    except (OSError, SourceContractError):
        return []
    text = compiled.body
    date = compiled.source_date
    coverage = compiled.coverage
    source_status = compiled.status
    source = path.relative_to(wiki_root).as_posix()
    events_by_id: dict[str, dict[str, Any]] = {}

    def add_session(session_id: str, title: str, detail: str, *, replace: bool = False) -> None:
        event = _chatgpt_event(
            date=date,
            session_id=session_id,
            title=title,
            detail=detail,
            source=source,
            source_status=source_status,
            coverage=coverage,
        )
        if replace or event["event_id"] not in events_by_id:
            events_by_id[event["event_id"]] = event

    inventory = markdown_section(text, ["Session Inventory"])
    for line in inventory.splitlines():
        match = re.match(r"^-\s+(S\d+)\s+[—-]\s+`([^`]+)`\s*:\s*(.+)$", line.strip())
        if not match:
            continue
        session_id, title, detail = match.groups()
        add_session(session_id, title, detail)

    facts = markdown_section(text, ["已报告事实", "事实与推断"])
    for line in facts.splitlines():
        match = re.match(r"^-\s+(S\d+)\s+`([^`]+)`[：:]\s*(.+)$", line.strip())
        if not match:
            continue
        session_id, title, detail = match.groups()
        add_session(session_id, title, detail)

    # Current compiled summaries use one bounded `- Sxx：...` item per visible
    # session. Ignore aggregate/range bullets such as S01–S02 here; the validated
    # immutable raw fallback below restores their individual formal summaries.
    raw_sessions = _raw_chatgpt_sessions(compiled.raw_text)
    cross_session = markdown_section(text, ["跨会话摘要", "Cross-session Summary"])
    for line in cross_session.splitlines():
        match = re.match(r"^-\s+(S\d{2,})[：:]\s*(.+)$", line.strip())
        if not match:
            continue
        session_id, detail = match.groups()
        title = raw_sessions.get(session_id, (session_id, ""))[0]
        add_session(session_id, title, detail, replace=True)

    # Older valid source summaries in the current window contain only an
    # aggregate paragraph. Their `raw_source` is an exact, date-matched,
    # immutable formal report with structured Sxx summaries. Reading it is a
    # deterministic compatibility path, not a title/preview/transcript fallback.
    for session_id, (title, detail) in raw_sessions.items():
        add_session(session_id, title, detail)

    events = list(events_by_id.values())
    events.extend(parse_instrument_knowledge_candidates(
        compiled.body,
        date=date,
        source=source,
        source_status=source_status,
        coverage=coverage,
    ))
    return events


def parse_codex_daily_source(path: Path, wiki_root: Path) -> list[dict[str, Any]]:
    """Extract durable task outcomes from a Codex daily source summary.

    The source summary is already the privacy-preserving boundary: prompts,
    reasoning and raw tool output are intentionally not copied into the dashboard.
    """
    try:
        compiled = load_compiled_source(
            path,
            memory_root=wiki_root.parent,
            expected_family="codex",
            legacy_compatible=True,
            require_structured=False,
            require_instrument=False,
        )
    except (OSError, SourceContractError):
        return []
    text = compiled.body
    date = compiled.source_date
    coverage = compiled.coverage
    source_status = compiled.status
    source = path.relative_to(wiki_root).as_posix()
    events_by_id: dict[str, dict[str, Any]] = {}
    detailed_task_ids: set[str] = set()

    def add_task(
        task_id: str,
        title: str,
        *,
        goal: str,
        status: str,
        detail: str,
        next_step: str = "",
        evidence: str = "",
        boundary: str = "summary_and_evidence",
        source_thread_id: str = "",
        codex_project_id: str = "",
        codex_cwd: str = "",
        project_identity_source: str = "",
        replace: bool = False,
    ) -> None:
        verification_refs = re.findall(r"`([^`]+)`", evidence)
        state = "inbox"
        project_metadata_present = any(
            value.strip()
            for value in (source_thread_id, codex_project_id, codex_cwd, project_identity_source)
        )
        if looks_like_confidential_work(title, goal, detail, next_step, evidence, boundary):
            title = "实验工作内容（待合规抽象）"
            detail = "检测到可能涉及实验工作；派生看板未保留具体任务、文件或结果。"
            goal = "实验工作内容（待合规抽象）"
            next_step = ""
            verification_refs = []
            boundary = "compliance_abstracted"
            state = "compliance_review"
            source_thread_id = ""
            codex_project_id = ""
            codex_cwd = ""
            project_identity_source = "compliance_abstracted"
        event = {
            "event_id": f"codex-daily:{date}:{task_id}",
            "date": date,
            "title": title.strip(),
            "detail": detail.strip(),
            "source": source,
            "source_kind": "codex_daily",
            "source_status": source_status,
            "coverage": coverage,
            "task_id": task_id,
            "goal": goal.strip(),
            "task_status": status.strip() or "unknown",
            "next_step": next_step.strip(),
            "evidence_type": "reported",
            "event_kind": "codex_progress",
            "state": state,
            "case_id": None,
            "verification_refs": verification_refs,
            "evidence_boundary": boundary.strip() or "summary_and_evidence",
            "created_at": source_created_at(date),
        }
        if project_metadata_present:
            event.update({
                "thread_id": source_thread_id.strip(),
                "codex_project_id": codex_project_id.strip(),
                "codex_cwd": codex_cwd.strip(),
                "codex_project_identity_source": project_identity_source.strip(),
                "project_metadata_present": True,
            })
        if replace or event["event_id"] not in events_by_id:
            events_by_id[event["event_id"]] = event
        elif project_metadata_present:
            existing = events_by_id[event["event_id"]]
            for key in (
                "thread_id", "codex_project_id", "codex_cwd",
                "codex_project_identity_source", "project_metadata_present",
            ):
                existing[key] = event[key]

    task_section = markdown_section(text, ["任务摘要", "Task Summary", "Task Summaries"])
    matches = list(re.finditer(r"^###\s+(T\d+)\s+[—-]\s+(.+?)\s*$", task_section, re.M))
    for index, match in enumerate(matches):
        task_id, title = match.groups()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(task_section)
        block = task_section[match.end():end]

        def field(*labels: str) -> str:
            alternatives = "|".join(re.escape(label) for label in labels)
            found = re.search(rf"^-\s+(?:{alternatives})[：:]\s*(.+?)\s*$", block, re.M)
            return found.group(1).strip() if found else ""

        goal = field("目标", "Goal")
        status = field("状态", "Status") or "unknown"
        detail = field("结果", "Result", "Outcome") or "任务结果未提供摘要。"
        next_step = field("下一步", "Next step", "Next Step")
        evidence = field("证据", "Evidence")
        boundary = field("边界", "Boundary")
        add_task(
            task_id,
            title,
            goal=goal,
            status=status,
            detail=detail,
            next_step=next_step,
            evidence=evidence,
            boundary=boundary,
            source_thread_id=field("来源线程 ID", "Source thread ID", "Source Thread ID"),
            codex_project_id=field("Codex 项目 ID", "Codex project ID", "Codex Project ID"),
            codex_cwd=field("工作目录", "Working directory", "Working Directory"),
            project_identity_source=field("项目身份来源", "Project identity source", "Project Identity Source"),
        )
        detailed_task_ids.add(task_id)

    # Some current compiled summaries intentionally keep only aggregate prose,
    # while their exact, date-matched immutable raw still contains the formal
    # per-task Txx records. Recover those bounded records deterministically so
    # project routing remains tied to actual daily evidence. Never infer tasks
    # from the aggregate "project changes" prose.
    raw_text = _validated_codex_raw(compiled.text, date=date, wiki_root=wiki_root)
    raw_task_section = markdown_section(raw_text, ["任务摘要与证据", "Task Summary", "Task Summaries"])
    raw_matches = list(re.finditer(r"^###\s+(T\d+)\s+[—-]\s+(.+?)\s*$", raw_task_section, re.M))
    for index, match in enumerate(raw_matches):
        task_id, title = match.groups()
        end = raw_matches[index + 1].start() if index + 1 < len(raw_matches) else len(raw_task_section)
        block = raw_task_section[match.end():end]

        def raw_field(*labels: str) -> str:
            alternatives = "|".join(re.escape(label) for label in labels)
            found = re.search(
                rf"^\*\*(?:{alternatives})[：:]\*\*\s*(.+?)\s*$",
                block,
                re.M,
            )
            return found.group(1).strip() if found else ""

        add_task(
            task_id,
            title,
            goal=raw_field("目标", "Goal"),
            status=raw_field("状态", "Status") or "unknown",
            detail=raw_field("结果", "Result", "Outcome") or "任务结果未提供摘要。",
            next_step=raw_field("下一步", "Next step", "Next Step"),
            evidence=raw_field("文件证据", "File evidence", "File Evidence"),
            boundary=raw_field("证据边界", "Boundary") or "summary_and_evidence",
            source_thread_id=raw_field("来源线程 ID", "Source thread ID", "Source Thread ID"),
            codex_project_id=raw_field("Codex 项目 ID", "Codex project ID", "Codex Project ID"),
            codex_cwd=raw_field("工作目录", "Working directory", "Working Directory"),
            project_identity_source=raw_field("项目身份来源", "Project identity source", "Project Identity Source"),
        )
        detailed_task_ids.add(task_id)

    # Current Codex source summaries use a bounded one-line record:
    # `- Txx — title — status — actual result`. It is formal source evidence,
    # not a task title/preview fallback. Aggregate prose without Txx records is
    # deliberately ignored.
    action_by_task: dict[str, str] = {}
    actions = markdown_section(text, ["Actions and Open Items", "Action Items"])
    for line in actions.splitlines():
        action_match = re.match(r"^-\s+(T\d{2,})\s+[—-]\s+.+?[：:]\s*(.+)$", line.strip())
        if action_match:
            action_by_task[action_match.group(1)] = action_match.group(2).strip()
    cross_task = markdown_section(text, ["Cross-task Summary", "跨任务摘要"])
    status_values = "completed|blocked|in_progress|failed|unknown|on_hold|cancelled"
    for line in cross_task.splitlines():
        match = re.match(
            rf"^-\s+(T\d{{2,}})\s+[—-]\s+(.+?)\s+[—-]\s+({status_values})\s+[—-]\s+(.+)$",
            line.strip(),
            re.I,
        )
        if not match:
            continue
        task_id, title, status, detail = match.groups()
        add_task(
            task_id,
            title,
            goal=title,
            status=status.lower(),
            detail=detail,
            next_step=action_by_task.get(task_id, ""),
            boundary="summary_and_evidence",
            replace=task_id not in detailed_task_ids,
        )
    return list(events_by_id.values())


def source_family(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "").lower()
    source_kind = str(item.get("source_kind") or "").lower()
    if "codex-daily" in source or source_kind.startswith("codex"):
        return "codex"
    if "chatgpt-daily" in source or "weekly-review" in source or source_kind == "daily":
        return "chatgpt"
    if source_kind == "manual" or source == "manual":
        return "manual"
    return "memory"


def inbox_review_reason(event: dict[str, Any]) -> str:
    """Return only source-backed reasons that require project-routing review."""
    if event.get("source_kind") == "manual":
        return "manual_review"
    if (
        event.get("source_kind") in {"codex_daily", "codex_live"}
        and event.get("project_metadata_present") is True
        and event.get("state") == "inbox"
    ):
        # The source already supplied the parent-project identity.  A missing or
        # conflicting semantic Dashboard mapping is system bookkeeping, not a
        # project-classification question for the user.
        return ""
    if event.get("candidate_eligible") is True:
        return "explicit_project_candidate"
    if event.get("suggested_case_ids"):
        return "ambiguous_existing_project_match"
    reason = str(event.get("live_routing_reason") or event.get("routing_reason") or "")
    if reason in {"mapping_conflict", "review_required"}:
        return reason
    return ""


def _inbox_cluster_identity(event: dict[str, Any]) -> tuple[str, str]:
    source_kind = str(event.get("source_kind") or "unknown")
    if source_kind == "codex_live" and str(event.get("thread_id") or "").strip():
        return source_kind, f"thread:{str(event['thread_id']).strip()}"
    title = unicodedata.normalize("NFKC", str(event.get("title") or "")).lower()
    title = re.sub(r"[^\w\u3400-\u9fff]+", "", title)
    if title:
        return source_kind, f"topic:{title}"
    return source_kind, f"event:{str(event.get('event_id') or '')}"


def build_inbox_review_units(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project immutable inbox evidence into review units without changing it.

    Codex live turns cluster by thread. Daily evidence clusters by normalized
    task/topic title. A unit is actionable only when the source supplies an
    explicit candidate, ambiguous case suggestions, a mapping conflict, or a
    review-required marker. Everything else remains visible as reference-only
    evidence rather than inflating the pending-project count.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(_inbox_cluster_identity(event), []).append(event)
    review_units: list[dict[str, Any]] = []
    reference_units: list[dict[str, Any]] = []
    labels = {"chatgpt": "ChatGPT", "codex": "Codex", "memory": "Memory", "manual": "人工"}
    for (source_kind, identity), members in groups.items():
        ordered = sorted(
            members,
            key=lambda item: (
                str(item.get("date") or ""),
                str(item.get("created_at") or ""),
                str(item.get("event_id") or ""),
            ),
        )
        reasons = sorted({reason for item in ordered if (reason := inbox_review_reason(item))})
        actionable = [item for item in ordered if inbox_review_reason(item)]
        representative = dict((actionable or ordered)[-1])
        source_counts: dict[str, int] = {}
        for item in ordered:
            family = source_family(item)
            source_counts[family] = source_counts.get(family, 0) + 1
        dates = sorted({str(item.get("date") or "") for item in ordered if str(item.get("date") or "")})
        representative.update({
            "review_unit_id": stable_hash(source_kind, identity, prefix="inbox-unit-"),
            "representative_event_id": str(representative.get("event_id") or ""),
            "member_event_ids": [str(item.get("event_id") or "") for item in ordered],
            "member_count": len(ordered),
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "source_mix": [
                {"family": family, "label": labels.get(family, family), "count": count}
                for family, count in sorted(source_counts.items())
            ],
            "review_reasons": reasons,
            "reference_only": not reasons,
            "action_scope": "representative_only",
        })
        (review_units if reasons else reference_units).append(representative)
    sort_key = lambda item: (
        str(item.get("last_date") or ""),
        str(item.get("created_at") or ""),
        str(item.get("review_unit_id") or ""),
    )
    review_units.sort(key=sort_key, reverse=True)
    reference_units.sort(key=sort_key, reverse=True)
    return review_units, reference_units


def _convergence_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "detail", "goal", "next_step", "cwd", "project_key"):
        parts.append(str(event.get(key) or ""))
    for key in ("file_refs", "verification_refs"):
        for value in event.get(key) or []:
            if isinstance(value, dict):
                parts.extend(str(item) for item in value.values())
            else:
                parts.append(str(value))
    return unicodedata.normalize("NFKC", " ".join(parts)).lower()


def _contains_distinctive_term(text: str, term: str) -> bool:
    normalized = unicodedata.normalize("NFKC", term).lower().strip()
    if not normalized or normalized in GENERIC_CONVERGENCE_TERMS:
        return False
    if re.fullmatch(r"[a-z0-9_.-]+", normalized):
        return bool(re.search(rf"(?<![a-z0-9_.-]){re.escape(normalized)}(?![a-z0-9_.-])", text))
    return normalized in text


def plan_inbox_convergence(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan high-confidence project routing without mutating the registry."""
    cases = {
        str(case.get("case_id") or ""): case
        for case in registry.get("cases", [])
        if str(case.get("case_id") or "") and not case.get("hidden")
    }
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in registry.get("events", []):
        if event.get("state") == "inbox":
            groups.setdefault(_inbox_cluster_identity(event), []).append(event)

    thread_routes: dict[str, set[str]] = {}
    for event in registry.get("events", []):
        thread_id = str(event.get("thread_id") or "").strip()
        case_id = str(event.get("case_id") or "").strip()
        if (
            thread_id
            and case_id in cases
            and event.get("state") in {"routed", "linked"}
            and event.get("source_kind") != "manual"
        ):
            thread_routes.setdefault(thread_id, set()).add(case_id)

    path_mappings = [
        (unicodedata.normalize("NFKC", str(mapping.get("path") or "")).lower().rstrip("/"), str(mapping.get("case_id") or ""))
        for mapping in registry.get("project_mappings", [])
        if str(mapping.get("path") or "") and str(mapping.get("case_id") or "") in cases
    ]
    project_key_mappings: dict[str, set[str]] = {}
    for mapping in registry.get("project_mappings", []):
        project_key = str(mapping.get("project_key") or "").strip().lower()
        case_id = str(mapping.get("case_id") or "")
        if project_key and case_id in cases:
            project_key_mappings.setdefault(project_key, set()).add(case_id)

    project_terms: dict[str, list[str]] = {}
    for case_id in cases:
        terms = DISTINCTIVE_PROJECT_TERMS.get(case_id, [])
        project_terms[case_id] = list(dict.fromkeys(
            term for term in terms if term and term.lower().strip() not in GENERIC_CONVERGENCE_TERMS
        ))

    plan: list[dict[str, Any]] = []
    for (source_kind, identity), members in sorted(groups.items()):
        texts = [_convergence_text(event) for event in members]
        primary = unicodedata.normalize("NFKC", "\n".join(
            f"{event.get('title') or ''} {event.get('goal') or ''}"
            for event in members
        )).lower()
        direct: set[str] = set()
        for event, text in zip(members, texts):
            project_key = str(event.get("project_key") or "").strip().lower()
            if project_key in cases:
                direct.add(project_key)
            direct.update(project_key_mappings.get(project_key, set()))
            for path, case_id in path_mappings:
                relative = path[path.find("/projects/") + 1:] if "/projects/" in path else ""
                if path in text or (relative and relative in text):
                    direct.add(case_id)
        target = ""
        reason = "unmatched"
        candidates: set[str] = set()
        if any(inbox_review_reason(event) for event in members):
            reason = "actionable_review_required"
        elif len(direct) == 1:
            target, reason = next(iter(direct)), "exact_mapping"
        elif len(direct) > 1:
            reason, candidates = "ambiguous_exact_mapping", direct
        else:
            continuity = set()
            for event in members:
                continuity.update(thread_routes.get(str(event.get("thread_id") or "").strip(), set()))
            if len(continuity) == 1:
                target, reason = next(iter(continuity)), "thread_continuity"
            elif len(continuity) > 1:
                reason, candidates = "ambiguous_thread_continuity", continuity
            else:
                # A project identifier in a title/goal is dominant enough for a
                # derived association. Passing detail mentions are left unresolved.
                term_matches = {
                    case_id for case_id, terms in project_terms.items()
                    if any(_contains_distinctive_term(primary, term) for term in terms)
                }
                if len(term_matches) == 1:
                    target, reason = next(iter(term_matches)), "unique_distinctive_term"
                elif len(term_matches) > 1:
                    reason, candidates = "ambiguous_distinctive_terms", term_matches
        dates = sorted({str(event.get("date") or "") for event in members if str(event.get("date") or "")})
        plan.append({
            "source_kind": source_kind,
            "cluster_identity": identity,
            "event_ids": [str(event.get("event_id") or "") for event in members],
            "member_count": len(members),
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "case_id": target,
            "reason": reason,
            "candidate_case_ids": sorted(candidates),
        })
    return plan


def plan_inbox_suggestions(registry: dict[str, Any], *, excluded_event_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Suggest one existing project from title/goal signals without routing."""
    excluded = excluded_event_ids or set()
    cases = {
        str(case.get("case_id") or "")
        for case in registry.get("cases", [])
        if str(case.get("case_id") or "") and not case.get("hidden")
    }
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in registry.get("events", []):
        event_id = str(event.get("event_id") or "")
        if event.get("state") == "inbox" and event_id not in excluded:
            groups.setdefault(_inbox_cluster_identity(event), []).append(event)
    result: list[dict[str, Any]] = []
    for (source_kind, identity), members in sorted(groups.items()):
        primary = unicodedata.normalize("NFKC", "\n".join(
            f"{event.get('title') or ''} {event.get('goal') or ''}"
            for event in members
        )).lower()
        matches = {
            case_id for case_id, terms in SUGGESTED_PROJECT_TERMS.items()
            if case_id in cases and any(_contains_distinctive_term(primary, term) for term in terms)
        }
        if any(inbox_review_reason(event) for event in members):
            matches = set()
        result.append({
            "source_kind": source_kind,
            "cluster_identity": identity,
            "event_ids": [str(event.get("event_id") or "") for event in members],
            "member_count": len(members),
            "case_id": next(iter(matches)) if len(matches) == 1 else "",
            "reason": "single_project_title_signal" if len(matches) == 1 else ("ambiguous_project_signals" if matches else "unmatched"),
            "candidate_case_ids": sorted(matches),
        })
    return result


def build_unresolved_topic_groups(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create neutral topic folders that do not imply a project association."""
    topics = [
        ("account_access", "账号、权限与访问", ("账号", "门禁", "权限", "quota", "登录")),
        ("report_archive", "日报、归档与回查", ("日报", "归档", "补档", "回查", "review")),
        ("delivery_ops", "构建、部署与运行维护", ("部署", "构建", "服务", "发布", "刷新", "测试")),
        ("research_learning", "研究、分析与知识问询", ("分析", "研究", "解释", "方法", "知识", "对比")),
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for unit in units:
        text = _convergence_text(unit)
        topic_id, label = "other", "其他待判断证据"
        for candidate_id, candidate_label, markers in topics:
            if any(marker in text for marker in markers):
                topic_id, label = candidate_id, candidate_label
                break
        bucket = grouped.setdefault(topic_id, {"topic_id": topic_id, "label": label, "units": [], "member_count": 0})
        bucket["units"].append(unit)
        bucket["member_count"] += int(unit.get("member_count") or 1)
    return sorted(grouped.values(), key=lambda item: (-len(item["units"]), item["topic_id"]))


def build_project_evidence_units(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate routed evidence for project-folder display only."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        if event.get("source_kind") == "manual":
            key = ("manual", str(event.get("event_id") or ""))
        else:
            key = _inbox_cluster_identity(event)
        groups.setdefault(key, []).append(event)
    units: list[dict[str, Any]] = []
    for members in groups.values():
        ordered = sorted(members, key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or ""), str(item.get("event_id") or "")))
        unit = dict(ordered[-1])
        dates = sorted({str(item.get("date") or "") for item in ordered if str(item.get("date") or "")})
        families = sorted({source_family(item) for item in ordered})
        unit.update({
            "member_count": len(ordered),
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "source_families": families,
        })
        units.append(unit)
    units.sort(key=lambda item: (str(item.get("last_date") or ""), str(item.get("created_at") or ""), str(item.get("event_id") or "")), reverse=True)
    return units


def build_latest_daily_progress(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Project the newest confirmed daily progress without changing manual state.

    Project evidence units may merge a formal daily event with newer live-state
    records from the same task.  Derive the card summary from the complete routed
    event set so that this display field cannot be hidden by that aggregation.
    """
    eligible = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("state") == "routed"
        and event.get("source_kind") in {"daily", "codex_daily"}
        and str(event.get("event_id") or "")
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(event.get("date") or ""))
        and str(event.get("detail") or "").strip()
    ]
    if not eligible:
        return {"date": "", "items": []}
    # Refresh keeps event IDs unique. Retain that invariant defensively when a
    # caller passes replay input containing an exact duplicate.
    eligible = list({
        str(event["event_id"]): event
        for event in sorted(eligible, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    }.values())
    latest_date = max(str(event["date"]) for event in eligible)
    source_order = {"daily": 0, "codex_daily": 1}
    latest = sorted(
        (dict(event) for event in eligible if str(event["date"]) == latest_date),
        key=lambda event: (
            source_order.get(str(event.get("source_kind") or ""), 9),
            str(event.get("session_id") or event.get("task_id") or ""),
            str(event.get("event_id") or ""),
        ),
    )
    return {"date": latest_date, "items": latest}


def source_date(item: dict[str, Any]) -> str:
    if item.get("date"):
        return str(item["date"])
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(item.get("source") or ""))
    return match.group(1) if match else ""


def classify_daily_event(title: str, detail: str) -> tuple[str, str]:
    text = f"{title} {detail}".lower()
    reference_markers = (
        "解释", "是什么", "什么是", "什么意思", "区别", "介绍", "全名", "cas号", "换算",
        "查询", "路线", "库存", "推荐",
        "meaning", "difference", "introduction", "what is",
    )
    progress_markers = (
        "问题", "异常", "失败", "下降", "排查", "仍未", "重跑", "优化", "验证",
        "迁移", "开发", "部署", "修复", "推进", "工作流", "设置方法", "步骤",
        "problem", "failed", "failure", "troubleshoot", "optimize", "verify", "deploy", "fix",
    )
    if any(marker in text for marker in reference_markers):
        return "reference", "reference"
    if any(marker in text for marker in progress_markers):
        return "case_candidate", "inbox"
    return "event", "reference"


def looks_like_confidential_work(*parts: str) -> bool:
    raw_text = " ".join(str(part or "") for part in parts)
    # These dashboard implementation references are known non-experimental
    # workspace evidence. Remove only the exact allowlisted project paths before
    # generic `project-*` identifier detection; the original evidence references
    # remain available for routing and display.
    privacy_text = re.sub(
        r"projects/automation/codex-project-dashboard(?:-h5)?/[^\s`；;，。]+",
        " ",
        raw_text,
        flags=re.I,
    )
    # "Project Dashboard" is the public product/workflow name used by this
    # repository, not a reconstructable `project-<identifier>`.  Strip only
    # that exact generic phrase before applying the intentionally broad
    # project/customer/sample identifier patterns below.
    privacy_text = re.sub(r"\bproject dashboard\b", " ", privacy_text, flags=re.I)
    privacy_text = re.sub(
        r"(?:raw/conversations|wiki/sources/conversations)/(?:chatgpt-daily|codex-daily)/[^\s`；;，。]+",
        " ",
        privacy_text,
        flags=re.I,
    )
    privacy_text = re.sub(r"wiki/(?:projects|decisions)\.md", " ", privacy_text, flags=re.I)
    text = privacy_text.lower()
    markers = (
        "多肽", "硫醚", "环肽", "碎裂", "肽图", "解序", "氨基酸", "unifi", "mcpba", "fdaa", "marfey", "dcl",
        "q-tof", "mrt", "masshunter", "masslynx", "product ion", "mrm", "transition",
        "色谱", "基线", "uplc", "hplc", "样品前处理", "卡尔费休", "lc-ms", "ms/ms", "mse",
    )
    identifier_patterns = (
        r"(?<![A-Za-z0-9])(?:customer|client|sample|batch|project)[\s:_/#-]+[a-z0-9][a-z0-9._-]*(?![A-Za-z0-9._-])",
        r"(?:客户|样品|批次|项目)(?:编号|ID|代码|代号)?[：:\s_-]*[A-Za-z0-9][A-Za-z0-9._-]*",
        r"(?:^|[/\\])(?:customer|client|sample|batch|lab)(?:[/\\]|$)",
    )
    contiguous_identifier = re.search(
        r"(?<![A-Za-z0-9])(?:[Cc]ustomer|[Cc]lient|[Ss]ample|[Bb]atch|[Pp]roject)[A-Z0-9][A-Za-z0-9._-]*(?![A-Za-z0-9._-])",
        privacy_text,
    )
    internal_url = False
    for url in re.findall(r"(?:https?|smb|file|afp|nfs)://[^\s`]+", raw_text, re.I):
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if scheme in {"smb", "file", "afp", "nfs"}:
            internal_url = True
            break
        if not host or "." not in host or host == "localhost" or host.endswith((".local", ".internal", ".intranet", ".corp")):
            internal_url = True
            break
        try:
            address = ipaddress.ip_address(host)
            if address.is_private or address.is_loopback or address.is_link_local:
                internal_url = True
                break
        except ValueError:
            if any(label in {"internal", "intranet", "corp"} for label in host.split(".")):
                internal_url = True
                break
    return (
        any(marker in text for marker in markers)
        or bool(contiguous_identifier)
        or internal_url
        or any(re.search(pattern, privacy_text, re.I) for pattern in identifier_patterns)
    )


def category_for_manifest_case(case: dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    if case.get("line") == "life":
        return "life"
    if case_id in CASE_CATEGORY_BY_ID:
        return CASE_CATEGORY_BY_ID[case_id]
    title = str(case.get("title") or "")
    for marker, category in CASE_CATEGORY_TITLE_RULES:
        if marker.lower() in title.lower():
            return category
    return "rd_case"


def bootstrap_registry(manifest_path: Path | None, manifest: dict[str, Any]) -> dict[str, Any]:
    created_at = now_iso()
    manifest_month = str(manifest.get("month") or "")
    bootstrap_through = ""
    if re.fullmatch(r"\d{4}-\d{2}", manifest_month):
        year, month = (int(part) for part in manifest_month.split("-"))
        bootstrap_through = f"{manifest_month}-{calendar.monthrange(year, month)[1]:02d}"
    registry: dict[str, Any] = {
        "version": 2,
        "created_at": created_at,
        "updated_at": created_at,
        "source_policy": {
            "primary": "chatgpt_daily_source_summary",
            "monthly_manifest_role": "bootstrap_history_only",
            "bootstrap_manifest": str(manifest_path) if manifest_path else "",
            "bootstrap_through_date": bootstrap_through,
        },
        "categories": WORK_CATEGORIES,
        "cases": [],
        "events": [],
        "routing_rules": [],
    }
    seen_events: set[str] = set()
    for raw_case in manifest.get("cases", []):
        case_id = str(raw_case.get("case_id") or "").strip()
        if not case_id:
            continue
        title = str(raw_case.get("title") or "未命名 Case")
        if case_id == "case-c15a153c9e934f8e":
            title = "硫醚环肽氧化辅助解序"
        line = "life" if raw_case.get("line") == "life" else "work"
        category = category_for_manifest_case(raw_case)
        open_items = raw_case.get("open_items") or []
        next_step = "\n".join(
            str(item.get("text") or "").strip()
            for item in open_items
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        )
        case = {
            "case_id": case_id,
            "title": title,
            "line": line,
            "category": category,
            "status": raw_case.get("status") if raw_case.get("status") in STATUS_LABELS else "unknown",
            "current_summary": str(raw_case.get("current_progress") or raw_case.get("summary") or ""),
            "next_step": next_step,
            "created_at": created_at,
            "updated_at": created_at,
            "evidence_mode": str(raw_case.get("evidence_mode") or "historical"),
            "needs_review": case_id == "case-cbf44407df62fc24",
            "routing_terms": CASE_SUGGESTION_TERMS.get(case_id, []),
            "origin": {"kind": "monthly_manifest", "legacy_title": str(raw_case.get("title") or "")},
            "field_authority": {"title": "manual_seed", "category": "manual_seed", "status": "legacy"},
        }
        registry["cases"].append(case)
        for entry in raw_case.get("timeline") or []:
            if not isinstance(entry, dict):
                continue
            event_text = str(entry.get("event") or "").strip()
            if not event_text:
                continue
            source = str(entry.get("source") or "monthly-manifest")
            date = str(entry.get("date") or "")
            event_id = stable_hash(case_id, date, source, event_text, prefix="event-")
            if event_id in seen_events:
                continue
            seen_events.add(event_id)
            registry["events"].append(
                {
                    "event_id": event_id,
                    "date": date,
                    "title": title,
                    "detail": event_text,
                    "source": source,
                    "source_kind": "monthly_history",
                    "source_status": "historical",
                    "coverage": str(raw_case.get("evidence_mode") or "historical"),
                    "evidence_type": str(entry.get("evidence_type") or "reported"),
                    "state": "routed",
                    "case_id": case_id,
                    "created_at": created_at,
                }
            )
    return registry


class CaseRegistry:
    def __init__(
        self,
        *,
        wiki_root: Path,
        registry_path: Path,
        reviews_root: Path | None = None,
        daily_root: Path | None = None,
        codex_daily_root: Path | None = None,
        live_state_path: Path | None = None,
        require_deposition_receipts: bool = False,
        memory_root: Path | None = None,
        source_through_date: str = "",
        manual_state_path: Path | None = None,
        replay_state_path: Path | None = None,
        split_journal_path: Path | None = None,
        split_lock_path: Path | None = None,
    ) -> None:
        self.wiki_root = wiki_root
        self.registry_path = registry_path
        self.reviews_root = reviews_root or wiki_root / "reviews"
        self.daily_root = daily_root or wiki_root / "sources/conversations/chatgpt-daily"
        self.codex_daily_root = codex_daily_root or wiki_root / "sources/conversations/codex-daily"
        self.live_state_path = live_state_path or Path(os.environ.get(LIVE_STATE_ENV, str(DEFAULT_LIVE_STATE))).expanduser()
        self.require_deposition_receipts = require_deposition_receipts
        self.memory_root = memory_root or wiki_root.parent
        self.source_through_date = source_through_date
        coordinator_args: dict[str, Any] = {
            "manual_path": manual_state_path or registry_path.with_name("project-dashboard-manual-state.json"),
            "replay_path": replay_state_path or registry_path.with_name("project-dashboard-replay-state.json"),
            "compatibility_path": registry_path,
        }
        if split_journal_path is not None:
            coordinator_args["journal_path"] = split_journal_path
        if split_lock_path is not None:
            coordinator_args["lock_path"] = split_lock_path
        self.split_store = SplitStoreCoordinator(**coordinator_args)
        self._lock = threading.RLock()

    def latest_manifest(self) -> tuple[Path | None, dict[str, Any]]:
        paths = sorted(self.reviews_root.glob("monthly-case-manifest-*.json"))
        if not paths:
            return None, {"month": "", "cases": []}
        path = paths[-1]
        return path, read_json(path, {"month": "", "cases": []})

    def all_manifests(self) -> list[tuple[Path, dict[str, Any]]]:
        return [
            (path, read_json(path, {"month": "", "cases": []}))
            for path in sorted(self.reviews_root.glob("monthly-case-manifest-*.json"))
        ]

    def _source_metadata(self, source: str, fallback_coverage: str = "unknown") -> dict[str, str]:
        candidate = source.strip()
        if not candidate:
            return {"coverage": fallback_coverage or "unknown", "source_status": "unknown"}
        path = self.wiki_root.parent / candidate if candidate.startswith("wiki/") else self.wiki_root / candidate
        if not path.suffix:
            path = path.with_suffix(".md")
        if not path.exists() or not path.is_file():
            return {"coverage": fallback_coverage or "unknown", "source_status": "unknown"}
        try:
            text = path.read_text(encoding="utf-8")
            return {
                "coverage": frontmatter_value(text, "coverage") or fallback_coverage or "unknown",
                "source_status": frontmatter_value(text, "status") or "unknown",
            }
        except OSError:
            return {"coverage": fallback_coverage or "unknown", "source_status": "unknown"}

    def load(self) -> dict[str, Any]:
        if self.split_store.enabled:
            registry = self._migrate_registry(self.split_store.load())
            return self._bounded_daily_projection(registry)
        if self.registry_path.exists():
            raw = read_json(self.registry_path, {})
            old_version = int(raw.get("version") or 0)
            registry = self._migrate_registry(raw)
            if old_version < 4:
                registry["_migration_pending"] = True
            return self._bounded_daily_projection(registry)
        manifest_path, manifest = self.latest_manifest()
        return self._bounded_daily_projection(self._migrate_registry(bootstrap_registry(manifest_path, manifest)))

    def _bounded_daily_projection(self, registry: dict[str, Any]) -> dict[str, Any]:
        """Hide replayable daily evidence newer than a requested H5 checkpoint.

        ``source_through_date`` is used only while constructing a historical,
        contiguous H5 checkpoint.  Manual case state and manual events remain
        authoritative and visible; only source-replay records are bounded.
        The caller is responsible for using an isolated registry whenever the
        canonical replay store is already ahead, so this projection can never
        write a historical generation back over current canonical state.
        """
        if not self.source_through_date:
            return registry

        bounded = copy.deepcopy(registry)
        replay_kinds = {"daily", "daily_instrument_knowledge", "codex_daily", "explicit_daily_candidate"}

        def is_future_replay(record: dict[str, Any]) -> bool:
            source_kind = str(record.get("source_kind") or "")
            source_date = str(record.get("date") or record.get("period") or "")
            return source_kind in replay_kinds and bool(source_date) and source_date > self.source_through_date

        bounded["events"] = [
            record for record in bounded.get("events") or []
            if isinstance(record, dict) and not is_future_replay(record)
        ]
        bounded["value_candidates"] = [
            record for record in bounded.get("value_candidates") or []
            if isinstance(record, dict) and not is_future_replay(record)
        ]
        for case in bounded.get("cases") or []:
            if not isinstance(case, dict):
                continue
            if "value_items" not in case:
                continue
            case["value_items"] = [
                record for record in case.get("value_items") or []
                if isinstance(record, dict) and not is_future_replay(record)
            ]
        return bounded

    def _migrate_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
        """Normalize historic registries without dropping user-maintained fields."""
        version = int(registry.get("version") or 0)
        if version >= 4:
            registry.setdefault("value_candidates", [])
            legacy_reconciliation = registry.pop("capability_reconciliation", None)
            if legacy_reconciliation:
                registry.setdefault("migration_audit", []).extend(legacy_reconciliation)
                registry["_migration_pending"] = True
            removed_legacy_event_fields = False
            for event in registry.get("events", []):
                if not isinstance(event, dict):
                    continue
                removed_legacy_event_fields = event.pop("superseded_at", None) is not None or removed_legacy_event_fields
                removed_legacy_event_fields = event.pop("supersession_reason", None) is not None or removed_legacy_event_fields
            if removed_legacy_event_fields:
                registry["_migration_pending"] = True
            return registry
        registry.setdefault("categories", WORK_CATEGORIES)
        registry.setdefault("cases", [])
        registry.setdefault("events", [])
        registry.setdefault("branches", [])
        registry.setdefault("capability_domains", [])
        registry.setdefault("routing_rules", [])
        registry.setdefault("project_mappings", [])
        registry.setdefault("value_candidates", [])
        policy = registry.setdefault("source_policy", {})
        policy.setdefault("codex_live_state", "summary_and_evidence_only")
        policy.setdefault("codex_live_default_path", str(DEFAULT_LIVE_STATE))
        for event in registry["events"]:
            event.setdefault("source_kind", "daily")
            event.setdefault("verification_refs", [])
        registry["version"] = 4
        return registry

    def save(self, registry: dict[str, Any], *, writer_role: str = "manual_operation") -> None:
        registry["updated_at"] = now_iso()
        if self.split_store.enabled:
            self.split_store.write(registry, writer_role=writer_role)
        else:
            atomic_write_json(self.registry_path, registry)

    def _daily_paths(self, registry: dict[str, Any]) -> list[Path]:
        from daily_deposition_receipt import source_deposition_state

        bootstrap_through = str(registry.get("source_policy", {}).get("bootstrap_through_date") or "")
        result: list[Path] = []
        for path in sorted(self.daily_root.glob("*/chatgpt-daily-report-*.md")):
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            if not match:
                continue
            if bootstrap_through and match.group(1) <= bootstrap_through:
                continue
            if self.source_through_date and match.group(1) > self.source_through_date:
                continue
            if self.require_deposition_receipts and not source_deposition_state(path, self.memory_root)["ready"]:
                continue
            result.append(path)
        return result

    def _codex_daily_paths(self, registry: dict[str, Any]) -> list[Path]:
        from daily_deposition_receipt import source_deposition_state

        bootstrap_through = str(registry.get("source_policy", {}).get("bootstrap_through_date") or "")
        result: list[Path] = []
        for path in sorted(self.codex_daily_root.glob("*/codex-daily-report-*.md")):
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            if not match:
                continue
            if bootstrap_through and match.group(1) <= bootstrap_through:
                continue
            if self.source_through_date and match.group(1) > self.source_through_date:
                continue
            if self.require_deposition_receipts and not source_deposition_state(path, self.memory_root)["ready"]:
                continue
            result.append(path)
        return result

    def _suggest_cases(self, registry: dict[str, Any], event: dict[str, Any]) -> list[str]:
        identity_text = " ".join(
            str(event.get(field) or "") for field in ("title", "goal")
        ).lower()
        context_text = " ".join(
            str(event.get(field) or "")
            for field in ("detail", "next_step", "evidence_boundary")
        ).lower()
        scored: list[tuple[int, str]] = []
        for case in registry.get("cases", []):
            if case.get("status") == "archived":
                continue
            terms = [str(term).lower() for term in case.get("routing_terms") or [] if str(term).strip()]
            # A project name or goal is direct routing evidence. A term that
            # appears only in the outcome can be incidental (for example a
            # nutrition fix mentioning a daily-report query), so keep it as a
            # weaker hint. Return only top-scoring targets; equal direct matches
            # still preserve intentional cross-project routing.
            score = sum(
                4 if term in identity_text else 1 if term in context_text else 0
                for term in terms
            )
            if score:
                scored.append((score, str(case.get("case_id") or "")))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            return []
        top_score = scored[0][0]
        return [case_id for score, case_id in scored if score == top_score][:3]

    def _map_codex_evidence(self, registry: dict[str, Any], event: dict[str, Any]) -> tuple[str, str, str]:
        """Route a Codex daily outcome by its verification paths when exactly one project matches."""
        workspace_prefix = "/Users/shiba/Documents/codex/"

        def aliases(value: str) -> set[str]:
            normalized = os.path.normpath(value.strip()) if value.strip() else ""
            result = {normalized} if normalized else set()
            if normalized.startswith(workspace_prefix):
                result.add(normalized[len(workspace_prefix):])
            return result

        matches: list[tuple[int, str, str]] = []
        refs = [str(ref) for ref in event.get("verification_refs") or [] if str(ref).strip()]
        for mapping in registry.get("project_mappings", []):
            if not isinstance(mapping, dict):
                continue
            target_id = str(mapping.get("case_id") or mapping.get("branch_id") or "")
            target_kind = "case" if mapping.get("case_id") else "branch" if mapping.get("branch_id") else ""
            mapping_aliases = aliases(str(mapping.get("path") or mapping.get("cwd") or ""))
            if not target_id or not mapping_aliases:
                continue
            for ref in refs:
                ref_aliases = aliases(ref)
                lengths = [
                    len(base)
                    for ref_path in ref_aliases
                    for base in mapping_aliases
                    if ref_path == base or ref_path.startswith(base + os.sep)
                ]
                if lengths:
                    matches.append((max(lengths), target_kind, target_id))
                    break
        if not matches:
            return "", "", "no_evidence_mapping"
        longest = max(length for length, _, _ in matches)
        targets = {(kind, target) for length, kind, target in matches if length == longest}
        if len(targets) == 1:
            kind, target = targets.pop()
            return kind, target, "mapped_by_evidence"
        if len(targets) > 1:
            return "", "", "mapping_conflict"
        return "", "", "no_evidence_mapping"

    def _map_codex_parent_project(self, registry: dict[str, Any], event: dict[str, Any]) -> tuple[str, str, str]:
        """Map source-owned Codex project membership without semantic guessing."""
        project_id = str(event.get("codex_project_id") or "").strip()
        if project_id and project_id not in {"无", "已脱敏", "unavailable"}:
            targets = {
                (
                    "case" if mapping.get("case_id") else "branch",
                    str(mapping.get("case_id") or mapping.get("branch_id") or ""),
                )
                for mapping in registry.get("project_mappings", [])
                if isinstance(mapping, dict)
                and str(mapping.get("codex_project_id") or "").strip() == project_id
                and (mapping.get("case_id") or mapping.get("branch_id"))
            }
            if len(targets) == 1:
                kind, target = targets.pop()
                return kind, target, "mapped_by_codex_project_id"
            if len(targets) > 1:
                return "", "", "mapping_conflict"
            return "", "", "unmapped_codex_project_id"

        cwd = str(event.get("codex_cwd") or "").strip()
        if cwd and cwd not in {"无", "已脱敏", "unavailable"}:
            kind, target, reason = self._map_live_project(registry, cwd, [], "")
            if reason == "mapped_by_cwd":
                return kind, target, "mapped_by_codex_cwd"
            if reason == "mapped_by_saved_project_path":
                return kind, target, reason
            return kind, target, reason
        return "", "", "projectless"

    @staticmethod
    def _merge_source_event(existing: dict[str, Any], parsed: dict[str, Any]) -> bool:
        """Refresh source-owned fields without overwriting manual routing or review decisions."""
        protected = {
            "event_id", "state", "case_id", "branch_id", "capability_domain_id", "routing_reason",
            "routing_history", "route_undone_at", "ignored_at", "created_at",
        }
        changed = False
        for key, value in parsed.items():
            if key in protected:
                continue
            if existing.get(key) != value:
                existing[key] = value
                changed = True
        return changed

    @staticmethod
    def _matching_capability_event(registry: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any] | None:
        """Find the pre-stable-ID version of one source-grounded capability item.

        Older compiled daily sources did not always carry ``knowledge_id``.  A
        later evidence-only repair must update that same review unit rather than
        append a second capability card merely because a legacy hash algorithm
        produced a different event ID.  Source + question is the bounded
        identity here; it is used only for instrument knowledge and never for
        ordinary daily/project events.
        """
        if parsed.get("event_kind") != "instrument_knowledge":
            return None
        source = str(parsed.get("source") or "")
        question = str(parsed.get("question") or "")
        if not source or not question:
            return None
        return next(
            (
                existing
                for existing in registry.get("events", [])
                if existing.get("state") == "capability_item"
                and existing.get("event_kind") == "instrument_knowledge"
                and str(existing.get("source") or "") == source
                and str(existing.get("question") or "") == question
            ),
            None,
        )

    @staticmethod
    def _reconcile_capability_duplicates(registry: dict[str, Any]) -> list[tuple[str, str]]:
        """Retire duplicate current cards while retaining their derived history.

        This is intentionally a projection-state transition, not a deletion:
        the superseded event remains auditable in the registry but cannot enter
        the active capability-domain/H5 projection.
        """
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in registry.get("events", []):
            if event.get("state") != "capability_item" or event.get("event_kind") != "instrument_knowledge":
                continue
            source = str(event.get("source") or "")
            question = str(event.get("question") or "")
            if source and question:
                groups.setdefault((source, question), []).append(event)
        retired: list[tuple[str, str]] = []
        for items in groups.values():
            if len(items) < 2:
                continue
            canonical = min(items, key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")))
            for duplicate in items:
                if duplicate is canonical:
                    continue
                # Preserve the older canonical ID, while carrying forward the
                # richer source-owned evidence fields from its duplicate.
                CaseRegistry._merge_source_event(canonical, duplicate)
                duplicate.update({
                    "state": "superseded_capability_item",
                    "superseded_by": canonical.get("event_id"),
                })
                retired.append((str(duplicate.get("event_id") or ""), str(canonical.get("event_id") or "")))
        if retired:
            registry.setdefault("migration_audit", []).append({
                "at": now_iso(),
                "kind": "duplicate_source_question_superseded",
                "transitions": [{"superseded": old, "canonical": new} for old, new in retired],
            })
        return retired

    def _apply_chatgpt_routing(self, registry: dict[str, Any], event: dict[str, Any], *, preserve_manual: bool) -> None:
        if event.get("event_kind") == "instrument_knowledge":
            event.update({
                "state": "capability_item",
                "case_id": None,
                "branch_id": None,
                "capability_domain_id": "instrument_methodology",
                "progress_node": False,
                "routing_reason": "daily_reusable_instrument_knowledge",
                "route_origin": "source",
            })
            return
        suggestions = self._suggest_cases(registry, event)
        event["suggested_case_ids"] = suggestions
        if event.get("state") == "compliance_review":
            previous = {"state": event.get("state"), "case_id": event.get("case_id"), "branch_id": event.get("branch_id")}
            if previous["case_id"] or previous["branch_id"]:
                registry.setdefault("routing_audit", []).append({"action": "source_compliance_review", "event_id": event.get("event_id"), "at": now_iso(), "previous": previous})
            event.update({"state": "compliance_review", "case_id": None, "branch_id": None, "routing_reason": "source_compliance_review", "route_origin": "source"})
            return
        if preserve_manual or event.get("state") == "ignored":
            return
        parsed_state = classify_daily_event(str(event.get("title") or ""), str(event.get("detail") or ""))[1]
        event.update({"state": parsed_state, "case_id": None, "branch_id": None})
        event.pop("routing_reason", None)
        if parsed_state == "inbox" and len(suggestions) == 1:
            event.update({"state": "routed", "case_id": suggestions[0], "routing_reason": "single_keyword_match", "route_origin": "automatic"})

    def _apply_codex_routing(self, registry: dict[str, Any], event: dict[str, Any], *, preserve_manual: bool) -> None:
        has_project_metadata = event.get("project_metadata_present") is True
        suggestions = [] if has_project_metadata else self._suggest_cases(registry, event)
        event["suggested_case_ids"] = suggestions
        if event.get("state") == "compliance_review" or event.get("evidence_boundary") == "compliance_abstracted":
            previous = {"state": event.get("state"), "case_id": event.get("case_id"), "branch_id": event.get("branch_id")}
            if previous["case_id"] or previous["branch_id"]:
                registry.setdefault("routing_audit", []).append({"action": "source_compliance_review", "event_id": event.get("event_id"), "at": now_iso(), "previous": previous})
            event.update({"state": "compliance_review", "case_id": None, "branch_id": None, "routing_reason": "source_compliance_review", "route_origin": "source"})
            return
        if preserve_manual or event.get("state") == "ignored":
            return
        event.update({"state": "inbox", "case_id": None, "branch_id": None})
        event.pop("additional_case_ids", None)
        event.pop("routing_reason", None)
        kind, target, reason = (
            self._map_codex_parent_project(registry, event)
            if has_project_metadata
            else self._map_codex_evidence(registry, event)
        )
        if reason == "mapping_conflict":
            event["routing_reason"] = reason
        elif kind == "case":
            event.update({"state": "routed", "case_id": target, "routing_reason": reason, "route_origin": "automatic"})
        elif kind == "branch":
            event.update({"state": "branch_progress", "branch_id": target, "routing_reason": reason, "route_origin": "automatic"})
        elif has_project_metadata:
            event["routing_reason"] = reason
        elif len(suggestions) == 1:
            event.update({"state": "routed", "case_id": suggestions[0], "routing_reason": "single_keyword_match", "route_origin": "automatic"})
        elif len(suggestions) > 1 and registry.get("source_policy", {}).get("allow_multi_project_routing"):
            # A single Codex task can genuinely advance more than one maintained
            # project (for example daily deposition plus account-routing gates).
            # Keep one primary target for existing edit controls and expose the
            # same source-backed progress under each additional project.
            event.update({
                "state": "routed",
                "case_id": suggestions[0],
                "additional_case_ids": suggestions[1:],
                "routing_reason": "multiple_keyword_matches",
                "route_origin": "automatic",
            })

    def _deposit_explicit_values(
        self,
        registry: dict[str, Any],
        candidates: list[dict[str, Any]],
        known_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Attach an explicit value only when all cited events resolve to one target."""
        records = registry.setdefault("value_candidates", [])
        by_id = {
            str(item.get("candidate_id") or ""): item
            for item in records
            if isinstance(item, dict) and item.get("candidate_id")
        }
        for candidate in candidates:
            source_prefix = "chatgpt-daily" if candidate.get("source_kind") == "daily" else "codex-daily"
            targets: set[tuple[str, str]] = set()
            all_sessions_resolved = True
            for session_id in candidate.get("sessions") or []:
                event = known_by_id.get(f"{source_prefix}:{candidate['date']}:{session_id}")
                if not event:
                    all_sessions_resolved = False
                    break
                if event.get("state") == "routed" and event.get("case_id"):
                    case_ids = [str(event["case_id"]), *[
                        str(item) for item in event.get("additional_case_ids") or [] if str(item)
                    ]]
                    targets.update(("case", item) for item in case_ids)
                elif event.get("state") == "branch_progress" and event.get("branch_id"):
                    targets.add(("branch", str(event["branch_id"])))
                else:
                    all_sessions_resolved = False
                    break
            resolved_target = next(iter(targets)) if all_sessions_resolved and len(targets) == 1 else None
            evidence = {
                "source": candidate["source"],
                "date": candidate["date"],
                "sessions": list(candidate["sessions"]),
                "coverage": candidate["coverage"],
                "source_status": candidate["source_status"],
                "evidence_type": "explicit",
            }
            record = by_id.get(candidate["candidate_id"])
            if record is None:
                record = {
                    **candidate,
                    "state": "pending_routing",
                    "evidence_sources": [evidence],
                }
                records.append(record)
                by_id[candidate["candidate_id"]] = record
            elif evidence not in record.setdefault("evidence_sources", []):
                record["evidence_sources"].append(evidence)

            if not resolved_target:
                continue
            target_kind, target_id = resolved_target
            previous_target = (str(record.get("target_kind") or ""), str(record.get("target_id") or ""))
            if record.get("state") == "accepted" and previous_target != resolved_target:
                record["state"] = "manual_review"
                record["routing_conflict"] = True
                continue
            collection_name = "cases" if target_kind == "case" else "branches"
            id_key = "case_id" if target_kind == "case" else "branch_id"
            target = next(
                (item for item in registry.get(collection_name, []) if str(item.get(id_key) or "") == target_id),
                None,
            )
            if target is None:
                continue
            values = target.setdefault("value_items", [])
            value = next(
                (item for item in values if str(item.get("candidate_id") or "") == candidate["candidate_id"]),
                None,
            )
            if value is None:
                value = {
                    "value_id": candidate["value_id"],
                    "candidate_id": candidate["candidate_id"],
                    "date": candidate["date"],
                    "detail": candidate["detail"],
                    "source": candidate["source"],
                    "source_kind": "explicit_daily_candidate",
                    "evidence_type": "explicit",
                    "evidence_mode": candidate["coverage"],
                    "evidence_boundary": candidate["evidence_boundary"],
                    "evidence_sources": [evidence],
                }
                values.append(value)
            elif evidence not in value.setdefault("evidence_sources", []):
                value["evidence_sources"].append(evidence)
            record.update({"state": "accepted", "target_kind": target_kind, "target_id": target_id})

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            registry = self.load()
            known_by_id = {str(event.get("event_id")): event for event in registry.get("events", [])}
            known = set(known_by_id)
            changed = not self.registry_path.exists() or bool(registry.pop("_migration_pending", False))
            explicit_values: list[dict[str, Any]] = []
            for path in self._daily_paths(registry):
                source_text = path.read_text(encoding="utf-8")
                for event in parse_daily_source(path, self.wiki_root):
                    if event["event_id"] in known:
                        existing = known_by_id[event["event_id"]]
                        changed = self._merge_source_event(existing, event) or changed
                        before = json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        if event.get("state") == "compliance_review":
                            existing["state"] = "compliance_review"
                            existing["evidence_boundary"] = "compliance_abstracted"
                        self._apply_chatgpt_routing(registry, existing, preserve_manual=bool(existing.get("routing_history")))
                        changed = changed or before != json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        continue
                    existing = self._matching_capability_event(registry, event)
                    if existing is not None:
                        changed = self._merge_source_event(existing, event) or changed
                        before = json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        self._apply_chatgpt_routing(registry, existing, preserve_manual=bool(existing.get("routing_history")))
                        changed = changed or before != json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        continue
                    self._apply_chatgpt_routing(registry, event, preserve_manual=False)
                    registry.setdefault("events", []).append(event)
                    known.add(event["event_id"])
                    known_by_id[event["event_id"]] = event
                    changed = True
                explicit_values.extend(parse_explicit_value_candidates(
                    source_text,
                    date=frontmatter_value(source_text, "date"),
                    source=path.relative_to(self.wiki_root).as_posix(),
                    source_kind="daily",
                    source_status=frontmatter_value(source_text, "status") or "unknown",
                    coverage=frontmatter_value(source_text, "coverage") or "unknown",
                ))
            for path in self._codex_daily_paths(registry):
                source_text = path.read_text(encoding="utf-8")
                for event in parse_codex_daily_source(path, self.wiki_root):
                    if event["event_id"] in known:
                        existing = known_by_id[event["event_id"]]
                        changed = self._merge_source_event(existing, event) or changed
                        before = json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        if event.get("state") == "compliance_review":
                            existing["state"] = "compliance_review"
                            existing["evidence_boundary"] = "compliance_abstracted"
                        elif (
                            existing.get("state") == "compliance_review"
                            and existing.get("routing_reason") == "source_compliance_review"
                            and existing.get("route_origin") == "source"
                            and not existing.get("routing_history")
                        ):
                            # A source-owned privacy classification may be
                            # corrected by a stricter parser or source repair.
                            # Manual review/routing decisions remain protected.
                            existing["state"] = "inbox"
                            existing.pop("routing_reason", None)
                            existing.pop("route_origin", None)
                        self._apply_codex_routing(registry, existing, preserve_manual=bool(existing.get("routing_history")))
                        changed = changed or before != json.dumps(existing, ensure_ascii=False, sort_keys=True)
                        continue
                    self._apply_codex_routing(registry, event, preserve_manual=False)
                    registry.setdefault("events", []).append(event)
                    known.add(event["event_id"])
                    known_by_id[event["event_id"]] = event
                    changed = True
                explicit_values.extend(parse_explicit_value_candidates(
                    source_text,
                    date=frontmatter_value(source_text, "date"),
                    source=path.relative_to(self.wiki_root).as_posix(),
                    source_kind="codex_daily",
                    source_status=frontmatter_value(source_text, "status") or "unknown",
                    coverage=frontmatter_value(source_text, "coverage") or "unknown",
                ))
            changed = bool(self._reconcile_capability_duplicates(registry)) or changed
            value_before = json.dumps(
                {
                    "value_candidates": registry.get("value_candidates", []),
                    "case_values": [item.get("value_items", []) for item in registry.get("cases", [])],
                    "branch_values": [item.get("value_items", []) for item in registry.get("branches", [])],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            self._deposit_explicit_values(registry, explicit_values, known_by_id)
            value_after = json.dumps(
                {
                    "value_candidates": registry.get("value_candidates", []),
                    "case_values": [item.get("value_items", []) for item in registry.get("cases", [])],
                    "branch_values": [item.get("value_items", []) for item in registry.get("branches", [])],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            changed = changed or value_before != value_after
            if changed:
                self.save(registry, writer_role="source_refresh")
            return registry

    def _read_live_state(self) -> tuple[list[dict[str, Any]], str]:
        """Read a best-effort hook state file. A broken/missing bridge never breaks the UI."""
        try:
            if not self.live_state_path.exists():
                return [], "live state not initialized"
            raw = read_json(self.live_state_path, {})
            if raw.get("schema_version") != 1:
                return [], "unsupported live-state schema"
            items = raw.get("tasks") or []
            if not isinstance(items, list):
                return [], "live state has no tasks"
            return [item for item in items if isinstance(item, dict)], ""
        except Exception as exc:
            return [], f"live state unavailable: {exc}"

    @staticmethod
    def _normalise_live_status(item: dict[str, Any]) -> str:
        status = str(item.get("status") or item.get("state") or "running").lower()
        aliases = {"waiting": "waiting_approval", "approval": "waiting_approval", "error": "failed", "done": "completed"}
        status = aliases.get(status, status)
        if status not in LIVE_STATUSES:
            status = "running"
        return status

    def _map_live_project(
        self,
        registry: dict[str, Any],
        cwd: str,
        file_refs: list[str] | None = None,
        project_key: str = "",
        codex_project_id: str = "",
    ) -> tuple[str, str, str]:
        """Map trusted parent project first, then use file evidence as fallback."""
        def map_one(candidate: str, *, saved_project_only: bool = False) -> tuple[str, str, str]:
            normalized = os.path.normpath(candidate) if candidate else ""
            matches: list[tuple[int, str, str]] = []
            for mapping in registry.get("project_mappings", []):
                if not isinstance(mapping, dict):
                    continue
                if saved_project_only and not str(mapping.get("codex_project_id") or "").strip():
                    continue
                path = str(mapping.get("path") or mapping.get("cwd") or "")
                target_id = str(mapping.get("case_id") or mapping.get("branch_id") or "")
                target_kind = "case" if mapping.get("case_id") else "branch" if mapping.get("branch_id") else ""
                if path and target_id and normalized and (normalized == os.path.normpath(path) or normalized.startswith(os.path.normpath(path) + os.sep)):
                    matches.append((len(os.path.normpath(path)), target_kind, target_id))
            if not saved_project_only:
                for group, kind, key in ((registry.get("cases", []), "case", "case_id"), (registry.get("branches", []), "branch", "branch_id")):
                    for item in group:
                        for path in item.get("project_paths") or []:
                            if normalized and (normalized == os.path.normpath(str(path)) or normalized.startswith(os.path.normpath(str(path)) + os.sep)):
                                matches.append((len(os.path.normpath(str(path))), kind, str(item.get(key) or "")))
            if not matches:
                return "", "", "unknown_project"
            max_len = max(item[0] for item in matches)
            winners = {(kind, target) for length, kind, target in matches if length == max_len}
            if len(winners) != 1:
                return "", "", "mapping_conflict"
            kind, target = winners.pop()
            return kind, target, "mapped"

        if codex_project_id:
            project_matches = {
                ("case" if mapping.get("case_id") else "branch", str(mapping.get("case_id") or mapping.get("branch_id") or ""))
                for mapping in registry.get("project_mappings", [])
                if isinstance(mapping, dict)
                and str(mapping.get("codex_project_id") or "").strip() == codex_project_id
                and (mapping.get("case_id") or mapping.get("branch_id"))
            }
            if len(project_matches) == 1:
                kind, target = project_matches.pop()
                return kind, target, "mapped_by_codex_project_id"
            if len(project_matches) > 1:
                return "", "", "mapping_conflict"
            return "", "", "unmapped_codex_project_id"

        saved_project_match = map_one(cwd, saved_project_only=True)
        if saved_project_match[2] == "mapped":
            kind, target, _ = saved_project_match
            return kind, target, "mapped_by_saved_project_path"
        if saved_project_match[2] == "mapping_conflict":
            return saved_project_match

        cwd_match = map_one(cwd)
        if cwd_match[2] == "mapped":
            kind, target, _ = cwd_match
            return kind, target, "mapped_by_cwd"

        file_matches = [map_one(path) for path in (file_refs or []) if path]
        routed_files = [(kind, target) for kind, target, reason in file_matches if reason == "mapped"]
        if routed_files:
            targets = set(routed_files)
            if len(targets) == 1:
                kind, target = targets.pop()
                return kind, target, "mapped_by_file"
            return "", "", "mapping_conflict"
        if any(reason == "mapping_conflict" for _, _, reason in file_matches):
            return "", "", "mapping_conflict"
        project_matches = {
            ("case" if mapping.get("case_id") else "branch", str(mapping.get("case_id") or mapping.get("branch_id") or ""))
            for mapping in registry.get("project_mappings", [])
            if isinstance(mapping, dict) and project_key and str(mapping.get("project_key") or "") == project_key and (mapping.get("case_id") or mapping.get("branch_id"))
        }
        if len(project_matches) == 1:
            kind, target = project_matches.pop()
            return kind, target, "mapped_by_project_key"
        if len(project_matches) > 1:
            return "", "", "mapping_conflict"
        return cwd_match

    def _live_tasks(self, registry: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool]:
        raw_tasks, error = self._read_live_state()
        tasks: list[dict[str, Any]] = []
        changed = False
        known = {str(event.get("event_id") or "") for event in registry.get("events", [])}
        for raw in raw_tasks:
            thread_id = str(raw.get("thread_id") or raw.get("session_id") or "").strip()
            turn_id = str(raw.get("turn_id") or "").strip()
            task_id = str(raw.get("task_id") or (f"{thread_id}:{turn_id}" if thread_id and turn_id else thread_id) or raw.get("id") or "").strip()
            if not task_id:
                continue
            cwd = str(raw.get("cwd") or "")
            raw_files = raw.get("changed_files") or []
            file_refs = []
            for item in raw_files if isinstance(raw_files, list) else []:
                path = str(item.get("path") or "") if isinstance(item, dict) else ""
                if path:
                    file_refs.append(path if os.path.isabs(path) else os.path.normpath(os.path.join(cwd, path)))
            status = self._normalise_live_status(raw)
            codex_project_id = str(raw.get("projectId") or raw.get("project_id") or "").strip()
            kind, target, mapping = self._map_live_project(
                registry, cwd, file_refs, str(raw.get("project_key") or ""), codex_project_id
            )
            title = "Codex 任务"
            detail = str(raw.get("preview") or "任务未提供可展示摘要").strip()
            risk = str(raw.get("risk") or "low").lower()
            review_required = bool(raw.get("has_value") or raw.get("has_decision") or raw.get("needs_review") or risk not in {"low", "none", ""})
            target_item = next((item for item in registry.get("cases" if kind == "case" else "branches", []) if str(item.get(f"{kind}_id") or "") == target), {})
            target_text = f"{target_item.get('title', '')} {target_item.get('category', '')}".lower()
            path_text = " ".join([cwd, *file_refs])
            experimental_path = bool(re.search(r"(^|[\\/])lab([\\/]|$)", path_text, re.IGNORECASE))
            confidential_target = (
                (kind == "case" and target_item.get("category") == "rd_case")
                or any(marker in target_text for marker in ("解序", "caaa", "多肽", "肽"))
                or experimental_path
            )
            compliance_abstracted = bool(raw.get("compliance_abstracted"))
            redacted = confidential_target and not compliance_abstracted
            if redacted:
                title, detail, file_refs = "实验工作内容（待合规抽象）", "检测到实验工作内容；实时看板未保留具体任务、文件或结果。", []
                review_required = True
            task = {"task_id": task_id, "thread_id": thread_id, "turn_id": turn_id, "cwd": cwd, "file_refs": [] if confidential_target else file_refs, "title": title, "detail": detail, "status": status, "project_kind": kind, "project_id": target, "mapping": mapping, "review_required": review_required, "compliance_redacted": redacted, "updated_at": str(raw.get("updated_at") or ""), "verification_refs": [] if confidential_target else list(raw.get("verification_refs") or raw.get("evidence") or [])}
            tasks.append(task)
            if status != "completed":
                continue
            completed_at = str(raw.get("ended_at") or raw.get("updated_at") or datetime.now(TZ).date().isoformat())
            event_id = stable_hash(task_id, completed_at, prefix="codex-live-")
            if event_id in known:
                continue
            state = (
                "compliance_review" if redacted
                else "linked" if kind == "case" and review_required
                else "routed" if kind == "case"
                else "branch_question" if kind == "branch" and review_required
                else "branch_progress" if kind == "branch"
                else "inbox"
            )
            registry.setdefault("events", []).append({"event_id": event_id, "date": completed_at[:10], "title": title, "detail": detail, "source": "codex-live-state", "source_kind": "codex_live", "source_status": "completed", "coverage": "summary_and_evidence", "evidence_type": "reported", "state": state, "case_id": target if state in {"routed", "linked"} else None, "branch_id": target if state in {"branch_progress", "branch_question"} else None, "thread_id": task["thread_id"], "turn_id": task["turn_id"], "project_key": target, "codex_project_id": "" if redacted else codex_project_id, "codex_cwd": "" if redacted else cwd, "codex_project_identity_source": "compliance_abstracted" if redacted else "bridge_project_id" if codex_project_id else "bridge_cwd" if cwd else "projectless", "project_metadata_present": bool(codex_project_id or cwd), "verification_refs": task["verification_refs"], "live_routing_reason": mapping, "created_at": now_iso()})
            known.add(event_id)
            changed = True
        tasks.sort(key=lambda task: (task["status"] not in {"running", "waiting_approval"}, task["updated_at"]), reverse=False)
        return tasks, error, changed

    def _case(self, registry: dict[str, Any], case_id: str) -> dict[str, Any]:
        target = next((item for item in registry.get("cases", []) if item.get("case_id") == case_id), None)
        if target is None:
            raise ValueError("case not found")
        return target

    def _branch(self, registry: dict[str, Any], branch_id: str) -> dict[str, Any]:
        target = next((item for item in registry.get("branches", []) if item.get("branch_id") == branch_id), None)
        if target is None:
            raise ValueError("branch not found")
        return target

    def _event(self, registry: dict[str, Any], event_id: str) -> dict[str, Any]:
        target = next((item for item in registry.get("events", []) if item.get("event_id") == event_id), None)
        if target is None:
            raise ValueError("event not found")
        return target

    def update_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            case_id = str(payload.get("case_id") or "").strip()
            target = self._case(registry, case_id)
            title = str(payload.get("title") or "").strip()
            line = str(payload.get("line") or "").strip()
            category = str(payload.get("category") or "").strip()
            status = str(payload.get("status") or "").strip()
            if not title:
                raise ValueError("title is required")
            if line not in {"work", "life"}:
                raise ValueError("invalid line")
            if line == "work" and category not in CATEGORY_LABELS:
                raise ValueError("invalid category")
            if line == "life":
                category = "life"
            if status not in STATUS_LABELS:
                raise ValueError("invalid status")
            target.update(
                {
                    "title": title,
                    "line": line,
                    "category": category,
                    "status": status,
                    "current_summary": str(payload.get("current_summary") or "").strip(),
                    "next_step": str(payload.get("next_step") or "").strip(),
                    "needs_review": bool(payload.get("needs_review", False)),
                    "updated_at": now_iso(),
                }
            )
            self.save(registry)
            return target

    def create_case(self, payload: dict[str, Any], event_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            title = str(payload.get("title") or "").strip()
            line = str(payload.get("line") or "").strip()
            category = str(payload.get("category") or "").strip()
            if not title:
                raise ValueError("title is required")
            if line not in {"work", "life"}:
                raise ValueError("invalid line")
            if line == "life":
                category = "life"
            if line == "work" and category not in CATEGORY_LABELS:
                raise ValueError("invalid category")
            timestamp = now_iso()
            case_id = "case-" + secrets.token_hex(8)
            target = {
                "case_id": case_id,
                "title": title,
                "line": line,
                "category": category,
                "status": str(payload.get("status") or "in_progress"),
                "current_summary": str(payload.get("current_summary") or "").strip(),
                "next_step": str(payload.get("next_step") or "").strip(),
                "created_at": timestamp,
                "updated_at": timestamp,
                "evidence_mode": "manual",
                "needs_review": False,
                "routing_terms": [],
            }
            if target["status"] not in STATUS_LABELS:
                raise ValueError("invalid status")
            registry.setdefault("cases", []).append(target)
            if event_id:
                event = self._event(registry, event_id)
                if event.get("state") == "compliance_review":
                    raise ValueError("compliance-review work cannot create a case")
                event.setdefault("routing_history", []).append(
                    {
                        "action": "route",
                        "at": timestamp,
                        "from_state": event.get("state"),
                        "from_case_id": event.get("case_id"),
                        "to_case_id": case_id,
                        "set_current": not bool(target["current_summary"]),
                        "previous_current_summary": target["current_summary"],
                    }
                )
                event.update({"state": "routed", "case_id": case_id, "routed_at": timestamp, "route_origin": "manual"})
                if not target["current_summary"]:
                    target["current_summary"] = str(event.get("detail") or "")
            self.save(registry)
            return target

    def add_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            case_id = str(payload.get("case_id") or "").strip()
            target = self._case(registry, case_id)
            detail = str(payload.get("detail") or "").strip()
            if not detail:
                raise ValueError("log detail is required")
            date = str(payload.get("date") or datetime.now(TZ).strftime("%Y-%m-%d"))
            timestamp = now_iso()
            event = {
                "event_id": stable_hash(case_id, timestamp, detail, prefix="event-"),
                "date": date,
                "title": str(payload.get("title") or "人工更新").strip() or "人工更新",
                "detail": detail,
                "source": "wiki/project-dashboard-case-registry",
                "source_kind": "manual",
                "source_status": "reported",
                "coverage": "manual",
                "evidence_type": "reported",
                "state": "routed",
                "route_origin": "manual",
                "case_id": case_id,
                "created_at": timestamp,
            }
            registry.setdefault("events", []).append(event)
            if bool(payload.get("set_as_current", True)):
                target["current_summary"] = detail
            target["updated_at"] = timestamp
            self.save(registry)
            return event

    def manual_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply an owner-entered status and/or progress update as durable history."""
        with self._lock:
            registry = self.refresh()
            case_id = str(payload.get("case_id") or "").strip()
            target = self._case(registry, case_id)
            status = str(payload.get("status") or target.get("status") or "").strip()
            detail = str(payload.get("detail") or "").strip()
            if status not in STATUS_LABELS:
                raise ValueError("invalid status")
            previous_status = str(target.get("status") or "unknown")
            if status == previous_status and not detail:
                raise ValueError("progress or status change is required")

            timestamp = now_iso()
            status_changed = status != previous_status
            event_detail = detail
            if not event_detail:
                event_detail = f"项目状态：{STATUS_LABELS.get(previous_status, previous_status)} → {STATUS_LABELS[status]}"
            event = {
                "event_id": stable_hash(case_id, timestamp, event_detail, prefix="event-"),
                "date": str(payload.get("date") or datetime.now(TZ).strftime("%Y-%m-%d")),
                "title": "手动更新项目状态与进度",
                "detail": event_detail,
                "source": "wiki/project-dashboard-case-registry",
                "source_kind": "manual",
                "source_status": "reported",
                "coverage": "manual",
                "evidence_type": "reported",
                "state": "routed",
                "route_origin": "manual",
                "case_id": case_id,
                "created_at": timestamp,
                "status_changed": status_changed,
                "previous_status": previous_status,
                "new_status": status,
            }
            registry.setdefault("events", []).append(event)
            target["status"] = status
            if detail:
                target["current_summary"] = detail
            target["updated_at"] = timestamp
            self.save(registry)
            return {"case": target, "event": event}

    def route_event_to_branch(self, event_id: str, branch_id: str, *, as_progress: bool = False) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            event = self._event(registry, event_id)
            branch = self._branch(registry, branch_id)
            timestamp = now_iso()
            event.setdefault("routing_history", []).append(
                {
                    "action": "route_branch",
                    "at": timestamp,
                    "from_state": event.get("state"),
                    "from_case_id": event.get("case_id"),
                    "from_branch_id": event.get("branch_id"),
                    "to_branch_id": branch_id,
                    "as_progress": bool(as_progress),
                }
            )
            event.update(
                {
                    "state": "branch_progress" if as_progress else "branch_question",
                    "case_id": None,
                    "branch_id": branch_id,
                    "progress_node": bool(as_progress),
                    "routed_at": timestamp,
                    "route_origin": "manual",
                }
            )
            branch["updated_at"] = timestamp
            self.save(registry)
            return event

    def add_branch_value(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            branch = self._branch(registry, str(payload.get("branch_id") or "").strip())
            detail = str(payload.get("detail") or "").strip()
            if not detail:
                raise ValueError("value detail is required")
            timestamp = now_iso()
            item = {
                "value_id": stable_hash(str(branch["branch_id"]), timestamp, detail, prefix="value-"),
                "date": str(payload.get("date") or datetime.now(TZ).strftime("%Y-%m-%d")),
                "detail": detail,
                "source": str(payload.get("source") or "manual"),
                "evidence_mode": str(payload.get("evidence_mode") or "manual"),
                "created_at": timestamp,
            }
            branch.setdefault("value_items", []).append(item)
            branch["updated_at"] = timestamp
            self.save(registry)
            return item

    def _restore_previous_summary(self, registry: dict[str, Any], event: dict[str, Any], action: dict[str, Any]) -> None:
        old_case_id = str(action.get("to_case_id") or "")
        if not old_case_id or not action.get("set_current"):
            return
        try:
            old_case = self._case(registry, old_case_id)
        except ValueError:
            return
        if old_case.get("current_summary") == event.get("detail"):
            old_case["current_summary"] = str(action.get("previous_current_summary") or "")
            old_case["updated_at"] = now_iso()

    def route_event(
        self,
        event_id: str,
        case_id: str,
        *,
        set_current: bool = False,
        link_only: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            event = self._event(registry, event_id)
            if event.get("state") == "compliance_review":
                raise ValueError("compliance-review work cannot be routed to a case")
            target = self._case(registry, case_id)
            target_state = "linked" if link_only else "routed"
            if event.get("state") == target_state and event.get("case_id") == case_id:
                if set_current:
                    target["current_summary"] = str(event.get("detail") or "")
                    target["updated_at"] = now_iso()
                    self.save(registry)
                return event
            history = event.setdefault("routing_history", [])
            from_case_summary: str | None = None
            if event.get("state") == "routed" and event.get("case_id"):
                try:
                    from_case_summary = str(self._case(registry, str(event["case_id"])).get("current_summary") or "")
                except ValueError:
                    from_case_summary = None
            if history:
                self._restore_previous_summary(registry, event, history[-1])
            timestamp = now_iso()
            history.append(
                {
                    "action": "route",
                    "at": timestamp,
                    "from_state": event.get("state"),
                    "from_case_id": event.get("case_id"),
                    "from_case_current_summary": from_case_summary,
                    "to_case_id": case_id,
                    "set_current": bool(set_current),
                    "link_only": bool(link_only),
                    "previous_current_summary": str(target.get("current_summary") or ""),
                }
            )
            event.update({"state": target_state, "case_id": case_id, "routed_at": timestamp, "route_origin": "manual"})
            target["updated_at"] = timestamp
            if not link_only and (set_current or not target.get("current_summary")):
                target["current_summary"] = str(event.get("detail") or "")
            self.save(registry)
            return event

    def undo_event_route(self, event_id: str) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            event = self._event(registry, event_id)
            history = event.get("routing_history") or []
            if not history:
                raise ValueError("event has no route to undo")
            action = history.pop()
            self._restore_previous_summary(registry, event, action)
            event["state"] = action.get("from_state") or "inbox"
            event["case_id"] = action.get("from_case_id")
            if event.get("case_id") and action.get("from_case_current_summary") is not None:
                try:
                    previous_case = self._case(registry, str(event["case_id"]))
                    previous_case["current_summary"] = str(action.get("from_case_current_summary") or "")
                    previous_case["updated_at"] = now_iso()
                except ValueError:
                    pass
            event["route_undone_at"] = now_iso()
            registry.setdefault("routing_audit", []).append(
                {
                    "action": "undo_route",
                    "event_id": event_id,
                    "at": event["route_undone_at"],
                    "undone": action,
                }
            )
            self.save(registry)
            return event

    def ignore_event(self, event_id: str) -> dict[str, Any]:
        with self._lock:
            registry = self.refresh()
            event = self._event(registry, event_id)
            event.setdefault("routing_history", []).append(
                {
                    "action": "ignore",
                    "at": now_iso(),
                    "from_state": event.get("state"),
                    "from_case_id": event.get("case_id"),
                    "to_case_id": None,
                    "set_current": False,
                }
            )
            event.update({"state": "ignored", "case_id": None, "ignored_at": now_iso(), "route_origin": "manual"})
            self.save(registry)
            return event

    def snapshot(self) -> dict[str, Any]:
        registry = self.refresh()
        _, latest_manifest = self.latest_manifest()
        manifest_cases = {
            str(item.get("case_id") or ""): item
            for item in latest_manifest.get("cases", [])
            if isinstance(item, dict) and item.get("case_id")
        }
        lessons_by_case: dict[str, list[dict[str, Any]]] = {}
        lesson_index_by_case: dict[str, dict[str, dict[str, Any]]] = {}
        for _, manifest in self.all_manifests():
            for manifest_case in manifest.get("cases", []):
                if not isinstance(manifest_case, dict) or not manifest_case.get("case_id"):
                    continue
                case_id = str(manifest_case["case_id"])
                for lesson in manifest_case.get("lessons") or []:
                    if not isinstance(lesson, dict) or not str(lesson.get("text") or "").strip():
                        continue
                    text = str(lesson.get("text") or "").strip()
                    normalized_text = re.sub(r"[\s。.!！？]+", "", text).lower()
                    candidate_id = str(lesson.get("candidate_id") or lesson.get("stable_id") or "").strip()
                    semantic_key = candidate_id or stable_hash(case_id, normalized_text, prefix="lesson-semantic-")
                    source = str(lesson.get("source") or "")
                    metadata = self._source_metadata(source, str(manifest_case.get("evidence_mode") or "unknown"))
                    evidence = {
                        "source": source,
                        "evidence_type": str(lesson.get("evidence_type") or "reported"),
                        **metadata,
                    }
                    index = lesson_index_by_case.setdefault(case_id, {})
                    if semantic_key not in index:
                        merged = {**lesson, "text": text, "_semantic_key": semantic_key, "evidence_sources": [evidence]}
                        index[semantic_key] = merged
                        lessons_by_case.setdefault(case_id, []).append(merged)
                    elif evidence not in index[semantic_key]["evidence_sources"]:
                        index[semantic_key]["evidence_sources"].append(evidence)
        with self._lock:
            live_tasks, live_error, live_changed = self._live_tasks(registry)
            if live_changed:
                self.save(registry, writer_role="live_source_refresh")
        events_by_case: dict[str, list[dict[str, Any]]] = {}
        related_by_case: dict[str, list[dict[str, Any]]] = {}
        progress_by_branch: dict[str, list[dict[str, Any]]] = {}
        questions_by_branch: dict[str, list[dict[str, Any]]] = {}
        items_by_capability: dict[str, list[dict[str, Any]]] = {}
        inbox: list[dict[str, Any]] = []
        for event in registry.get("events", []):
            event["can_undo_route"] = bool(event.get("routing_history")) and event.get("routing_history", [])[-1].get("action") == "route"
            if event.get("state") == "inbox":
                inbox.append(event)
            elif event.get("state") == "routed" and event.get("case_id"):
                target_ids = [str(event["case_id"]), *[
                    str(case_id) for case_id in event.get("additional_case_ids") or [] if str(case_id)
                ]]
                for case_id in dict.fromkeys(target_ids):
                    events_by_case.setdefault(case_id, []).append(event)
            elif event.get("state") == "linked" and event.get("case_id"):
                related_by_case.setdefault(str(event["case_id"]), []).append(event)
            elif event.get("state") == "branch_progress" and event.get("branch_id"):
                progress_by_branch.setdefault(str(event["branch_id"]), []).append(event)
            elif event.get("state") == "branch_question" and event.get("branch_id"):
                questions_by_branch.setdefault(str(event["branch_id"]), []).append(event)
            elif event.get("state") == "capability_item" and event.get("capability_domain_id"):
                items_by_capability.setdefault(str(event["capability_domain_id"]), []).append(event)

        canonical_inbox_event_count = len(inbox)
        convergence_plan = plan_inbox_convergence(registry)
        association_by_event: dict[str, dict[str, Any]] = {}
        for decision in convergence_plan:
            case_id = str(decision.get("case_id") or "")
            if not case_id:
                continue
            for event_id in decision.get("event_ids") or []:
                association_by_event[str(event_id)] = decision
        remaining_inbox: list[dict[str, Any]] = []
        for event in inbox:
            decision = association_by_event.get(str(event.get("event_id") or ""))
            if not decision:
                remaining_inbox.append(event)
                continue
            associated = dict(event)
            associated.update({
                "association_kind": "high_confidence",
                "association_reason": str(decision.get("reason") or ""),
                "association_case_id": str(decision.get("case_id") or ""),
            })
            events_by_case.setdefault(str(decision.get("case_id") or ""), []).append(associated)
        inbox = remaining_inbox

        suggestion_plan = plan_inbox_suggestions(registry, excluded_event_ids=set(association_by_event))
        suggestion_by_event: dict[str, dict[str, Any]] = {}
        for decision in suggestion_plan:
            if not decision.get("case_id"):
                continue
            for event_id in decision.get("event_ids") or []:
                suggestion_by_event[str(event_id)] = decision
        suggested_by_case: dict[str, list[dict[str, Any]]] = {}
        remaining_inbox = []
        for event in inbox:
            decision = suggestion_by_event.get(str(event.get("event_id") or ""))
            if not decision:
                remaining_inbox.append(event)
                continue
            suggested = dict(event)
            suggested.update({
                "association_kind": "suggested",
                "association_reason": str(decision.get("reason") or ""),
                "association_case_id": str(decision.get("case_id") or ""),
            })
            suggested_by_case.setdefault(str(decision.get("case_id") or ""), []).append(suggested)
        inbox = remaining_inbox

        cases: list[dict[str, Any]] = []
        for raw_case in registry.get("cases", []):
            if raw_case.get("hidden"):
                continue
            case = dict(raw_case)
            logs = sorted(
                events_by_case.get(str(case.get("case_id") or ""), []),
                key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or "")),
                reverse=True,
            )
            case["logs"] = logs
            case["log_count"] = len(logs)
            case["evidence_units"] = build_project_evidence_units(logs)
            case["evidence_unit_count"] = len(case["evidence_units"])
            case["latest_daily_progress"] = build_latest_daily_progress(logs)
            case["associated_event_count"] = sum(1 for event in logs if event.get("association_kind") == "high_confidence")
            suggested_events = suggested_by_case.get(str(case.get("case_id") or ""), [])
            case["suggested_evidence_units"] = build_project_evidence_units(suggested_events)
            case["suggested_evidence_count"] = len(suggested_events)
            case["suggested_evidence_unit_count"] = len(case["suggested_evidence_units"])
            related_events = sorted(
                related_by_case.get(str(case.get("case_id") or ""), []),
                key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or "")),
                reverse=True,
            )
            case["related_events"] = related_events
            case["related_count"] = len(related_events)
            manifest_case = manifest_cases.get(str(case.get("case_id") or ""), {})
            values: list[dict[str, Any]] = []
            for raw_value in case.get("value_items") or []:
                if not isinstance(raw_value, dict) or not str(raw_value.get("detail") or "").strip():
                    continue
                value = dict(raw_value)
                value["detail"] = str(raw_value.get("detail") or "").strip()
                value["source_family"] = source_family(value)
                value["date"] = source_date(value)
                value.setdefault("evidence_type", str(raw_value.get("evidence_mode") or "reported"))
                value.setdefault("evidence_sources", [{
                    "source": str(value.get("source") or ""),
                    "evidence_type": str(value.get("evidence_type") or "reported"),
                    "coverage": str(value.get("evidence_mode") or "compliance_abstracted"),
                    "source_status": "historical",
                }])
                values.append(value)
            for lesson in lessons_by_case.get(str(case.get("case_id") or ""), []):
                if not isinstance(lesson, dict) or not str(lesson.get("text") or "").strip():
                    continue
                value = {
                    "value_id": stable_hash(str(case.get("case_id") or ""), str(lesson.get("_semantic_key") or ""), prefix="value-"),
                    "detail": str(lesson.get("text") or "").strip(),
                    "source": str((lesson.get("evidence_sources") or [{}])[0].get("source") or ""),
                    "evidence_type": str(lesson.get("evidence_type") or "reported"),
                    "evidence_sources": list(lesson.get("evidence_sources") or []),
                }
                value["source_family"] = source_family(value)
                value["date"] = source_date(value)
                values.append(value)
            case["value_items"] = values
            case["value_count"] = len(values)
            case["open_items"] = [
                dict(item)
                for item in manifest_case.get("open_items") or []
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            case["open_item_count"] = len(case["open_items"])
            source_rollup: dict[str, dict[str, Any]] = {}

            def rollup_for(family: str) -> dict[str, Any]:
                return source_rollup.setdefault(
                    family,
                    {"family": family, "progress_count": 0, "value_count": 0, "latest_date": "", "verification_count": 0, "incomplete_count": 0, "partial_event_count": 0, "coverage_states": set(), "_incomplete_sources": set()},
                )

            limited_states = {"partial", "unknown", "bounded_partial", "compliance_abstracted"}
            for item in [*logs, *related_events]:
                family = source_family(item)
                rollup = rollup_for(family)
                rollup["progress_count"] += 1
                rollup["verification_count"] += len(item.get("verification_refs") or [])
                coverage = str(item.get("coverage") or "unknown").lower()
                rollup["coverage_states"].add(coverage)
                if coverage in limited_states or str(item.get("source_status") or "").lower() == "access_incomplete":
                    rollup["_incomplete_sources"].add(str(item.get("source") or item.get("event_id") or "unknown"))
                    rollup["partial_event_count"] += 1
                rollup["latest_date"] = max(rollup["latest_date"], source_date(item))
            for value in values:
                evidence_by_family: dict[str, list[dict[str, Any]]] = {}
                for evidence in value.get("evidence_sources") or []:
                    evidence_by_family.setdefault(source_family(evidence), []).append(evidence)
                for family, evidence_items in evidence_by_family.items():
                    rollup = rollup_for(family)
                    rollup["value_count"] += 1
                    for evidence in evidence_items:
                        coverage = str(evidence.get("coverage") or "unknown").lower()
                        rollup["coverage_states"].add(coverage)
                        if coverage in limited_states or str(evidence.get("source_status") or "").lower() == "access_incomplete":
                            rollup["_incomplete_sources"].add(str(evidence.get("source") or "unknown"))
                            rollup["partial_event_count"] += 1
                        rollup["latest_date"] = max(rollup["latest_date"], source_date(evidence))
            for rollup in source_rollup.values():
                rollup["incomplete_count"] = len(rollup.pop("_incomplete_sources"))
                rollup["partial_source_count"] = rollup["incomplete_count"]
                rollup["coverage_states"] = sorted(rollup["coverage_states"])
            labels = {"chatgpt": "ChatGPT", "codex": "Codex", "memory": "Memory", "manual": "人工"}
            order = {"chatgpt": 0, "codex": 1, "memory": 2, "manual": 3}
            case["source_mix"] = [
                {**item, "label": labels.get(family, family)}
                for family, item in sorted(source_rollup.items(), key=lambda pair: order.get(pair[0], 9))
            ]
            case["category_label"] = CATEGORY_LABELS.get(str(case.get("category") or ""), "待分类")
            case["status_label"] = STATUS_LABELS.get(str(case.get("status") or ""), "待确认")
            cases.append(case)

        case_titles = {str(case.get("case_id")): str(case.get("title") or "") for case in cases}
        for event in inbox:
            event["suggestions"] = [
                {"case_id": case_id, "title": case_titles.get(case_id, case_id)}
                for case_id in event.get("suggested_case_ids") or []
                if case_id in case_titles
            ]
        work_groups = {item["id"]: [] for item in WORK_CATEGORIES}
        life_cases: list[dict[str, Any]] = []
        closed_cases: list[dict[str, Any]] = []
        for case in cases:
            if case.get("status") in {"completed", "archived"}:
                closed_cases.append(case)
            elif case.get("line") == "life":
                life_cases.append(case)
            else:
                work_groups.setdefault(str(case.get("category") or "rd_case"), []).append(case)
        for items in work_groups.values():
            items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        life_cases.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        closed_cases.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        inbox.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("session_id") or "")), reverse=True)
        inbox_review_units, inbox_reference_units = build_inbox_review_units(inbox)
        remaining_plan = {
            (str(item.get("source_kind") or ""), str(item.get("cluster_identity") or "")): item
            for item in convergence_plan
            if not item.get("case_id")
        }
        reason_labels = {
            "unmatched": "未找到项目独有标识或既有路径/线程证据",
            "actionable_review_required": "来源明确要求人工判断，未做自动关联",
            "ambiguous_exact_mapping": "同时匹配多个项目路径",
            "ambiguous_thread_continuity": "同一线程已有多个项目归属",
            "ambiguous_distinctive_terms": "同时出现多个项目独有标识",
        }
        for unit in [*inbox_review_units, *inbox_reference_units]:
            source_kind, identity = _inbox_cluster_identity(unit)
            decision = remaining_plan.get((source_kind, identity), {})
            unit["convergence_reason"] = str(decision.get("reason") or "unmatched")
            unit["convergence_reason_label"] = reason_labels.get(unit["convergence_reason"], "需要人工确认归属")
            unit["candidate_case_ids"] = list(decision.get("candidate_case_ids") or [])
        inbox_topic_groups = build_unresolved_topic_groups([*inbox_review_units, *inbox_reference_units])
        daily_dates = [str(event.get("date") or "") for event in registry.get("events", []) if event.get("source_kind") == "daily"]
        references = sorted(
            [event for event in registry.get("events", []) if event.get("state") == "reference"],
            key=lambda item: (str(item.get("date") or ""), str(item.get("session_id") or "")),
            reverse=True,
        )
        branches: list[dict[str, Any]] = []
        for raw_branch in registry.get("branches", []):
            if raw_branch.get("hidden"):
                continue
            branch = dict(raw_branch)
            progress = sorted(
                progress_by_branch.get(str(branch.get("branch_id") or ""), []),
                key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or "")),
                reverse=True,
            )
            questions = sorted(
                questions_by_branch.get(str(branch.get("branch_id") or ""), []),
                key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or "")),
                reverse=True,
            )
            branch["progress_events"] = progress
            branch["progress_count"] = len(progress)
            branch["related_questions"] = questions
            branch["related_count"] = len(questions)
            branch["value_items"] = sorted(branch.get("value_items") or [], key=lambda item: str(item.get("date") or ""), reverse=True)
            branch["value_count"] = len(branch["value_items"])
            branches.append(branch)
        branches.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        capability_domains: list[dict[str, Any]] = []
        for raw_domain in registry.get("capability_domains", []):
            domain = dict(raw_domain)
            items = sorted(
                items_by_capability.get(str(domain.get("capability_domain_id") or ""), []),
                key=lambda item: (str(item.get("period") or item.get("date") or ""), str(item.get("created_at") or "")),
                reverse=True,
            )
            domain["items"] = items
            domain["item_count"] = len(items)
            capability_domains.append(domain)
        active_cases = [case for case in cases if case.get("status") in ACTIVE_STATUSES]
        project_value_count = sum(int(case.get("value_count") or 0) for case in cases)
        branch_value_count = sum(int(branch.get("value_count") or 0) for branch in branches)
        capability_value_count = sum(int(domain.get("item_count") or 0) for domain in capability_domains)
        return {
            "generated_at": now_iso(),
            "daily_updated_through": max(daily_dates, default=""),
            "categories": WORK_CATEGORIES,
            "status_labels": STATUS_LABELS,
            "summary": {
                "branch_count": len(branches),
                "case_count": len(cases),
                "active_case_count": sum(1 for case in cases if case.get("status") in ACTIVE_STATUSES),
                "chatgpt_project_count": sum(1 for case in active_cases if any(source.get("family") == "chatgpt" for source in case.get("source_mix") or [])),
                "codex_project_count": sum(1 for case in active_cases if any(source.get("family") == "codex" for source in case.get("source_mix") or [])),
                "value_item_count": project_value_count + branch_value_count + capability_value_count,
                "project_value_count": project_value_count,
                "branch_value_count": branch_value_count,
                "capability_value_count": capability_value_count,
                "work_case_count": sum(1 for case in cases if case.get("line") == "work"),
                "life_case_count": sum(1 for case in cases if case.get("line") == "life"),
                "inbox_count": len(inbox_review_units),
                "inbox_raw_event_count": canonical_inbox_event_count,
                "inbox_associated_group_count": sum(1 for item in convergence_plan if item.get("case_id")),
                "inbox_associated_event_count": len(association_by_event),
                "inbox_suggested_group_count": sum(1 for item in suggestion_plan if item.get("case_id")),
                "inbox_suggested_event_count": len(suggestion_by_event),
                "inbox_remaining_event_count": len(inbox),
                "inbox_topic_group_count": len(inbox_topic_groups),
                "inbox_reference_unit_count": len(inbox_reference_units),
                "inbox_projected_event_count": sum(
                    int(unit.get("member_count") or 0)
                    for unit in [*inbox_review_units, *inbox_reference_units]
                ),
                "reference_count": len(references),
                "compliance_review_count": sum(1 for event in registry.get("events", []) if event.get("state") == "compliance_review"),
                "live_task_count": len(live_tasks),
                "live_inbox_count": sum(
                    1 for task in live_tasks
                    if task.get("review_required")
                ),
            },
            "inbox": inbox,
            "inbox_review_units": inbox_review_units,
            "inbox_reference_units": inbox_reference_units,
            "inbox_topic_groups": inbox_topic_groups,
            "branches": branches,
            "capability_domains": capability_domains,
            "daily_references": references,
            "work_groups": work_groups,
            "life_cases": life_cases,
            "closed_cases": closed_cases,
            "all_cases": cases,
            "source_policy": registry.get("source_policy", {}),
            "privacy_policy": registry.get("privacy_policy", {}),
            "live_tasks": live_tasks,
            "live_state_error": live_error,
        }
