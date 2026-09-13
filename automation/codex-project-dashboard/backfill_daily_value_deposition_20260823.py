#!/usr/bin/env python3
"""Evidence-gated value-deposition backfill for 2026-08-05 through 2026-08-22."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


MEMORY_ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
COGNITIVE_ROOT = MEMORY_ROOT / "wiki/cognitive-observatory"
FINALIZER_PATH = (
    Path(__file__).resolve().parents[2]
    / "codex-skills/chatgpt-daily-report/scripts/daily_value_deposition.py"
)
SPEC = importlib.util.spec_from_file_location("daily_value_deposition_backfill", FINALIZER_PATH)
FINALIZER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(FINALIZER)


def candidate(
    *,
    candidate_type: str,
    domain: str,
    claim: str,
    sessions: list[str],
    boundary: str,
    evidence: list[tuple[str, str]],
    target: str | None = None,
    privacy_mode: str = "non_reconstructable",
    confidence: float = 0.95,
) -> dict[str, Any]:
    return {
        "type": candidate_type,
        "domain": domain,
        "normalized_claim": claim,
        "sessions": sessions,
        "evidence_boundary": boundary,
        "evidence_complete": True,
        "assertion": "explicit",
        "confidence": confidence,
        "risk": "low",
        "suggested_target": target or candidate_type,
        "status": "candidate",
        "conflicts_with": [],
        "privacy_mode": privacy_mode,
        "evidence_refs": [
            {"session_id": unit, "support_level": "direct_answer", "excerpt": excerpt}
            for unit, excerpt in evidence
        ],
    }


CANDIDATES: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("codex", "2026-08-05"): [
        candidate(
            candidate_type="workflow",
            domain="project dashboard manual state",
            claim="项目人工进度与状态更新应写入独立时间线且不被日报刷新覆盖，移动 H5 保持只读",
            sessions=["T01"],
            boundary="仅证明已完成并验证的 Dashboard 人工更新与只读移动端边界。",
            evidence=[("T01", "本机管理端增加人工进度和生命周期状态入口，变更进入独立时间线且不被日报覆盖；手机 H5 保持只读。")],
        ),
        candidate(
            candidate_type="workflow",
            domain="source grounded knowledge",
            claim="知识答案应先检索详细日报，再回查可访问原会话；证据不足时保留待回查，不用模型常识补写",
            sessions=["T04"],
            boundary="只沉淀证据治理流程，不包含具体实验项目、参数、结果或会话定位。",
            evidence=[("T04", "建立详细日报优先、原会话回查、证据不足待回查的答案流程，并完成私有 H5 的脱敏展示与本地防缓存刷新修复。")],
            privacy_mode="compliance_abstracted",
        ),
        candidate(
            candidate_type="workflow",
            domain="dashboard delivery",
            claim="本地 H5 与私有 Sites 使用独立调度并复用通用账号、日报和隐私门禁，飞书同步链路不再参与刷新",
            sessions=["T07"],
            boundary="仅证明当日已完成的 Dashboard 集成退役与门禁迁移；不涉及线上历史数据删除。",
            evidence=[("T07", "统一本地 H5 07:20 与 Sites 07:30，移除本地飞书同步链路，将通用门禁迁入独立支持模块并重建本地 H5。")],
        ),
        candidate(
            candidate_type="workflow",
            domain="adaptive reasoning routing",
            claim="自适应路由必须落实精确 target model 与 effort；不匹配时委派，目标不可用时显式失败",
            sessions=["T08"],
            boundary="仅证明当日可见的核心实现与契约验证，不重建缺失 turn 的中间讨论。",
            evidence=[("T08", "实现精确 target model/effort、不匹配即委派和不可用时显式失败，并同步全局规则与契约验证器。")],
        ),
        candidate(
            candidate_type="workflow",
            domain="adaptive reasoning user experience",
            claim="简单自包含请求走 Direct，其余请求自动路由；同任务追问复用 worker，并只对独立工作使用有界并行",
            sessions=["T09"],
            boundary="仅证明最终路由规则和主要验证结论；缺失代理过程未被重建。",
            evidence=[("T09", "增加全局 Direct fast path、自动 Everyday/Deep 路由、follow-up worker 复用和更严格的并行门槛，使用户无需手动切换模式。")],
        ),
        candidate(
            candidate_type="knowledge",
            domain="deployment verification",
            claim="部署成功、运行态读回和区域访问状态是不同证据，必须分别报告",
            sessions=["T10"],
            boundary="仅适用于可见的 Dashboard 发布验证经验，不证明任意部署当前可访问。",
            evidence=[("T10", "Dashboard 的重大修改以可验证发布为目标；部署成功、运行态读回和区域访问状态必须分开报告。")],
        ),
    ],
    ("codex", "2026-08-06"): [
        candidate(
            candidate_type="workflow",
            domain="codex daily evidence collection",
            claim="Codex 日报应从 bridge 精确引用开始，先做 thread read canary，再以有界并发逐线程分类结果，并让 ChatGPT 与 Codex 两分支独立完成",
            sessions=["T08", "T09"],
            boundary="该日证据证明确定性控制器的设计与主体实现；后续具体运行状态仍以各日来源为准。",
            evidence=[
                ("T08", "- 先解析 bridge，生成去重的线程清单； - 用单个线程做 `thread/read` canary； - 处理器缺失时立即失败并明确报警； - 使用稳定的 app-server `thread/read` RPC； - 有界并发读取，逐线程保存成功/失败状态； - ChatGPT 与 Codex 日报独立产出，不再互相阻塞；"),
                ("T09", "支持 canary、错误分类、有界并发、逐线程结果和敏感信息过滤。"),
            ],
        ),
        candidate(
            candidate_type="workflow",
            domain="monthly review and dashboard",
            claim="Dashboard 维护当前项目状态，月度复盘只展开当月变化并引用 Dashboard，同时保留认知审计与历史快照",
            sessions=["T12"],
            boundary="只证明用户当日采纳的月度去重方向；未修改历史月报或 raw。",
            evidence=[("T12", "- Dashboard：持续更新的当前项目状态、进度、价值与开放项。 - 月度沉淀：保留当月证据覆盖、认知变化、跨周期模式、候选晋升、冲突/漂移及历史快照。")],
        ),
        candidate(
            candidate_type="workflow",
            domain="weekly review and dashboard",
            claim="项目状态以 Dashboard 为准，周报只保留认知增量、判断变化、跨任务模式、冲突、待验证假设和晋升候选",
            sessions=["T14"],
            boundary="该日证据证明周报去重已完成并通过测试；月报后续未完成部分不计入本候选。",
            evidence=[("T14", "周报去重已完成：项目状态统一以 Dashboard 为准，周报只保留认知增量、判断变化、跨任务模式、冲突、待验证假设和晋升候选。相关测试 31/31 通过，Memory 与 Observatory 审计无错误。")],
        ),
    ],
    ("codex", "2026-08-21"): [
        candidate(
            candidate_type="workflow",
            domain="codex controller host execution",
            claim="官方 Codex app-server 在受限沙箱无法初始化时，日报控制器应使用受控 host 取证并保留 canary 容错与无替代来源边界",
            sessions=["T04"],
            boundary="仅证明该 host 取证修复、canary 容错与补档验证已完成；不暴露命令、日志或认证信息。",
            evidence=[("T04", "确认 official app-server 在受限沙箱初始化失败；host 取证路径与 canary 容错修复已验证，缺失日期的 Codex raw/source 已补齐并完成幂等验证。")],
        ),
        candidate(
            candidate_type="workflow",
            domain="automation execution accounts",
            claim="自动化可由当前已登录的受支持账号执行，但不得自动切换、登录、绑定、回退或修复认证",
            sessions=["T06"],
            boundary="只证明当日已验证的执行账号边界，不包含身份、认证数据或配置载荷。",
            evidence=[("T06", "host 取证、canary 容错与双账号执行门禁完成验证；当前已登录且受支持的账号可执行，仍不自动切换、登录、绑定或回退。")],
        ),
    ],
}


def source_path(family: str, day: str) -> Path:
    return (
        MEMORY_ROOT
        / "wiki/sources/conversations"
        / f"{family}-daily"
        / day[:4]
        / f"{family}-daily-report-{day}.md"
    )


def run(*, apply: bool) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="daily-value-backfill-") as temporary:
        temp_root = Path(temporary)
        for day_number in range(5, 23):
            day = f"2026-08-{day_number:02d}"
            for family in ("chatgpt", "codex"):
                source = source_path(family, day)
                extraction = temp_root / f"{family}-{day}.json"
                FINALIZER.prepare_manifest(source, MEMORY_ROOT, extraction)
                manifest = json.loads(extraction.read_text(encoding="utf-8"))
                candidates = CANDIDATES.get((family, day), [])
                manifest.update({
                    "reviewed_units": manifest["unit_ids"],
                    "result": "candidates" if candidates else "no_relevant_content",
                    "candidates": candidates,
                })
                extraction.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                # Always run the exact same grounding checks in preview and apply.
                source_info = FINALIZER.read_source(source, MEMORY_ROOT)
                grounded = FINALIZER.ground_candidates(manifest, source_info, source)
                item: dict[str, Any] = {
                    "family": family,
                    "date": day,
                    "reviewed_unit_count": len(manifest["unit_ids"]),
                    "candidate_count": len(grounded),
                    "candidate_ids": [record["candidate_id"] for record in grounded],
                    "result": manifest["result"],
                }
                if apply:
                    item["finalize"] = FINALIZER.finalize(
                        source, extraction, MEMORY_ROOT, COGNITIVE_ROOT
                    )
                results.append(item)
    return {
        "ok": True,
        "mode": "apply" if apply else "check",
        "source_count": len(results),
        "candidate_count": sum(item["candidate_count"] for item in results),
        "candidate_dates": sorted({item["date"] for item in results if item["candidate_count"]}),
        "receipted_count": sum("finalize" in item for item in results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
