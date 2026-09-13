#!/usr/bin/env python3
from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dashboard_model import CaseRegistry


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
WORKSPACE = Path("/Users/shiba/Documents/codex")
APP_ROOT = WORKSPACE / "projects/automation/codex-project-dashboard"
TOKEN_FILE = APP_ROOT / ".dashboard-token"
REGISTRY_FILE = WIKI / "project-dashboard-case-registry.json"

STORE = CaseRegistry(wiki_root=WIKI, registry_path=REGISTRY_FILE)


def is_loopback_client(host: str) -> bool:
    """Return True only for a direct connection from this Mac."""
    return host.split("%", 1)[0] in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def access_token() -> str:
    if TOKEN_FILE.exists():
        return read_text(TOKEN_FILE).strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return token


def page_html() -> bytes:
    return r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>项目进度与价值沉淀</title>
  <style>
    :root{color-scheme:light;--bg:#f3f5f7;--panel:#fff;--ink:#202631;--muted:#687386;--line:#dce1e8;--blue:#215b7a;--blue-soft:#edf5f8;--amber:#a56812;--red:#a63f46;--green:#2d6e50;--purple:#72516c}
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.48}
    button,input,select,textarea{font:inherit} button{min-height:40px;border:1px solid #9ba8b8;border-radius:8px;background:#fff;color:var(--ink);padding:0 13px;font-weight:650;cursor:pointer} button.primary{border-color:var(--blue);background:var(--blue);color:#fff} button.danger{color:var(--red);border-color:#c99} button.small{min-height:34px;padding:0 10px;font-size:12px}
    .topbar{position:sticky;top:0;z-index:10;background:rgba(243,245,247,.94);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)} .wrap{width:min(1120px,calc(100vw - 32px));margin:0 auto}.toprow{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:16px}.brand h1{margin:0;font-size:22px}.subtitle,.muted{color:var(--muted);font-size:13px}.top-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
    main{padding:20px 0 48px}.error{display:none;padding:12px 14px;margin-bottom:14px;border:1px solid #d5a0a0;border-radius:9px;background:#fff5f5;color:#8a2f35}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.metric,.panel,.case-card,.event-card{background:var(--panel);border:1px solid var(--line);border-radius:10px}.metric{padding:14px}.metric strong{display:block;font-size:26px}.metric span{color:var(--muted);font-size:12px}
    section{margin-top:24px}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:10px}.section-head h2{margin:0;font-size:18px}.badge{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;background:#f8fafb;padding:2px 8px;font-size:12px;color:#526072}.badge.attention{border-color:#deb66e;background:#fff7e7;color:#80530f}.badge.done{border-color:#9fc7af;background:#eff8f2;color:#275e42}.badge.hold{border-color:#c4a5b8;background:#f8f0f5;color:#68415d}
    .inbox-list,.case-grid{display:grid;gap:10px}.inbox-list{grid-template-columns:repeat(2,minmax(0,1fr))}.event-card,.case-card{padding:13px}.event-title,.case-title{font-weight:740}.event-meta,.case-meta{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}.event-detail,.case-summary{font-size:13px;color:#394455}.event-actions,.case-actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:10px}.case-actions{justify-content:flex-end;padding-top:9px;border-top:1px solid var(--line)}.event-actions select{flex:1 1 220px;min-height:36px}.source-strip{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0}.source-chatgpt{border-color:#a9bfdf;background:#f1f6fd;color:#315a8d}.source-codex{border-color:#a8c8ba;background:#eff8f3;color:#276349}.source-memory{border-color:#c4b6d3;background:#f7f2fb;color:#664d78}.value-list{margin-top:10px;padding:9px 10px;background:#f2f7f3;border-left:3px solid var(--green);font-size:12px}.value-list strong{display:block;margin-bottom:4px}.value-line{margin-top:4px;color:#405448}.open-count{color:var(--amber)}
    .category{margin-top:14px}.category-head{display:flex;align-items:center;justify-content:space-between;padding:9px 2px;border-bottom:1px solid var(--line);margin-bottom:9px}.category-head h3{margin:0;font-size:15px}.case-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.case-card,.branch-card{cursor:pointer}.case-card:hover,.branch-card:hover{border-color:#9aa8b8;box-shadow:0 2px 10px rgba(28,39,55,.07)}.case-summary{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.next-step{margin-top:8px;padding:8px 10px;border-left:3px solid var(--blue);background:var(--blue-soft);font-size:12px}.logs-preview{margin-top:10px;padding-top:9px;border-top:1px solid var(--line)}.logs-label{font-size:12px;font-weight:720;color:#596679;margin-bottom:5px}.log-mini{font-size:12px;color:#495568;margin-top:5px}.log-mini time{color:var(--blue);font-weight:700}.no-log{color:var(--muted);font-size:12px}.value-box{margin-top:9px;padding:9px 10px;background:#f2f7f3;border-left:3px solid var(--green);font-size:12px}
    .project-folder{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}.project-folder+ .project-folder{margin-top:10px}.project-folder>summary{cursor:pointer;list-style:none;padding:13px}.project-folder>summary::-webkit-details-marker{display:none}.project-folder>summary:hover{background:#fafbfd}.project-folder-title{display:flex;align-items:center;gap:8px;font-weight:760}.folder-mark{width:17px;height:12px;border:1.5px solid #b98324;border-radius:2px;background:#fff4cf;position:relative;flex:0 0 auto}.folder-mark:before{content:"";position:absolute;left:1px;top:-5px;width:8px;height:5px;border:1.5px solid #b98324;border-bottom:0;border-radius:2px 2px 0 0;background:#fff4cf}.project-folder-body{padding:0 13px 13px;border-top:1px solid var(--line)}.project-folder-body>.case-summary{margin-top:12px}.folder-section{margin-top:14px}.folder-section>h4{margin:0 0 8px}.project-folder .panel{margin-top:8px}.folder-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:10px;border-top:1px solid var(--line)}
    details.panel{padding:0} details.panel>summary{cursor:pointer;padding:13px 15px;font-weight:700}.details-body{padding:0 14px 14px}.reference-row{padding:9px 0;border-top:1px solid var(--line);font-size:13px}.empty{padding:18px;color:var(--muted);font-size:13px;text-align:center;border:1px dashed #cbd3dd;border-radius:9px}
    .modal-backdrop{position:fixed;inset:0;z-index:30;display:none;align-items:flex-end;justify-content:center;background:rgba(21,28,38,.46);padding:16px}.modal{width:min(760px,100%);max-height:90vh;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 22px 60px rgba(20,29,42,.28)}.modal-head{position:sticky;top:0;z-index:1;display:flex;justify-content:space-between;gap:12px;padding:14px 16px;background:#fff;border-bottom:1px solid var(--line)}.modal-head h3{margin:0}.modal-body{padding:16px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.full{grid-column:1/-1}label{display:grid;gap:5px;color:var(--muted);font-size:12px;font-weight:650}input,select,textarea{width:100%;min-height:42px;border:1px solid #aab5c4;border-radius:7px;background:#fff;color:var(--ink);padding:9px}textarea{min-height:92px;resize:vertical}.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px;flex-wrap:wrap}.timeline{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}.timeline h4{margin:0 0 8px}.log-item{padding:10px 0;border-bottom:1px solid var(--line)}.log-date{font-size:12px;font-weight:720;color:var(--blue)}.log-source{font-size:11px;color:var(--muted);word-break:break-all}.log-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.log-actions select{flex:1 1 190px;min-height:34px;font-size:12px}.new-log{margin-top:16px;padding:12px;background:#f7f9fb;border-radius:9px}
    @media(max-width:760px){.wrap{width:min(100vw - 20px,1120px)}.toprow{align-items:flex-start;flex-direction:column;padding:11px 0}.top-actions{justify-content:flex-start;width:100%}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.inbox-list,.case-grid{grid-template-columns:1fr}.form-grid{grid-template-columns:1fr}.full{grid-column:auto}.modal-backdrop{padding:0}.modal{max-height:100vh;height:100vh;border-radius:0}.event-actions select{flex-basis:100%}}
  </style>
</head>
<body>
  <header class="topbar"><div class="wrap toprow"><div class="brand"><h1>项目进度与价值沉淀</h1><div class="subtitle" id="sourceStamp">ChatGPT + Codex 跨来源整合视图</div></div><div class="top-actions"><button class="primary" id="refreshBtn">刷新</button></div></div></header>
  <main class="wrap">
    <div class="error" id="errorBox"></div><div class="metrics" id="metrics"></div>
    <section><div class="section-head"><h2>活跃需求分支</h2><span class="muted">只保存合规抽象的进展、问询与价值</span></div><div class="case-grid" id="branches"></div></section>
    <section><div class="section-head"><h2>项目资料夹</h2><span class="muted">一个项目一个资料夹，汇总 ChatGPT 日报、Codex 任务证据、开放项与下一步</span></div><div id="workGroups"></div></section>
    <section><div class="section-head"><h2>生活项目</h2><span class="muted">同样使用跨来源证据</span></div><div class="case-grid" id="lifeCases"></div></section>
    <section><details class="panel" id="capabilityFolder"><summary>横向能力与知识域 <span class="badge" id="capabilityCount">0</span></summary><div class="details-body"><div class="muted">可同时支持多个项目</div><div class="case-grid" id="capabilityDomains"></div></div></details></section>
    <section id="inboxSection"><details class="panel" id="inboxFolder"><summary>待归入项目 <span class="badge attention" id="inboxCount">0</span></summary><div class="details-body"><div class="muted">这里只显示有明确候选、映射冲突或复核标记的项目归属审阅单元。</div><div class="inbox-list" id="inboxList"></div></div></details></section>
    <section><details class="panel" id="inboxReferenceFolder"><summary>未归项目证据 <span class="badge" id="inboxReferenceCount">0</span> <span class="muted" id="inboxRawCount"></span></summary><div class="details-body"><div class="muted">Codex 任务、日报条目和中间状态按线程或任务主题合并展示；它们保留为证据，不自动建立项目。</div><div class="inbox-list" id="inboxReferenceList"></div></div></details></section>
    <section><details class="panel"><summary>已完成与已归档 <span class="badge" id="closedCount">0</span></summary><div class="details-body"><div class="case-grid" id="closedCases"></div></div></details></section>
    <section><details class="panel"><summary>跨来源知识流水（不自动建立项目） <span class="badge" id="referenceCount">0</span></summary><div class="details-body"><div class="inbox-list" id="references"></div></div></details></section>
  </main>
  <div class="modal-backdrop" id="caseBackdrop"><div class="modal"><div class="modal-head"><div><h3 id="caseModalTitle">项目</h3><div class="muted" id="caseModalHint"></div></div><button id="closeCaseBtn">关闭</button></div><div class="modal-body">
    <form id="caseForm"><input type="hidden" id="caseId"><input type="hidden" id="sourceEventId"><div class="form-grid">
      <label class="full">项目名称<input id="caseTitle" required></label>
      <label>归属<select id="caseLine"><option value="work">工作</option><option value="life">生活</option></select></label>
      <label id="categoryLabel">工作分类<select id="caseCategory"></select></label>
      <label>状态<select id="caseStatus"><option value="in_progress">进行中</option><option value="unknown">待确认</option><option value="on_hold">暂缓</option><option value="completed">已完成</option><option value="archived">已归档</option></select></label>
      <label class="full">当前进度<textarea id="caseSummary"></textarea></label>
      <label class="full">下一步<textarea id="caseNext"></textarea></label>
    </div><div class="form-actions"><button class="primary" type="submit">保存项目</button></div></form>
    <div id="existingCaseExtras"><div class="timeline"><h4>完整进展记录</h4><div id="timelineList"></div></div><div class="new-log"><label>新增进展<textarea id="newLogDetail" placeholder="记录这次尝试、结果、判断或阻塞。"></textarea></label><div class="form-actions"><button class="primary" id="addLogBtn" type="button">添加进展</button></div></div></div>
  </div></div></div>
  <div class="modal-backdrop" id="progressBackdrop"><div class="modal"><div class="modal-head"><div><h3 id="progressModalTitle">更新进度 / 状态</h3><div class="muted">人工输入会保留为进展记录；状态和当前进度不会被日报刷新覆盖。</div></div><button id="closeProgressBtn">关闭</button></div><div class="modal-body">
    <div class="muted">当前进度</div><div class="case-summary" id="previousProgress"></div>
    <div class="new-log"><div class="form-grid"><label>项目状态<select id="manualProjectStatus"><option value="in_progress">进行中</option><option value="unknown">待确认</option><option value="on_hold">暂缓</option><option value="completed">已完成</option><option value="archived">已归档</option></select></label><label class="full">最新进度（可选）<textarea id="manualProgressDetail" placeholder="写下目前做到哪里、已确认的结果、阻塞或阶段判断；只改状态时可以留空。"></textarea></label></div><div class="form-actions"><button class="primary" id="saveProgressBtn" type="button">保存更新</button></div></div>
  </div></div></div>
  <div class="modal-backdrop" id="branchBackdrop"><div class="modal"><div class="modal-head"><div><h3 id="branchModalTitle">需求分支</h3><div class="muted">仅展示合规抽象信息</div></div><button id="closeBranchBtn">关闭</button></div><div class="modal-body"><div id="branchBody"></div><div class="new-log"><label>新增价值沉淀<textarea id="newValueDetail" placeholder="只写可复用判断、方法边界或经验，不写项目、样品、参数和原始结果。"></textarea></label><div class="form-actions"><button class="primary" id="addValueBtn" type="button">添加价值</button></div></div></div></div></div>
  <script>
    const token = new URLSearchParams(location.search).get("token") || localStorage.getItem("caseDashboardToken") || ""; if(token)localStorage.setItem("caseDashboardToken",token);
    let data=null; const $=id=>document.getElementById(id); const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const api=async(path,options={})=>{const join=path.includes("?")?"&":"?";const response=await fetch(`${path}${join}token=${encodeURIComponent(token)}`,{cache:"no-store",...options});const result=await response.json();if(!response.ok||result.ok===false)throw new Error(result.error||`HTTP ${response.status}`);return result};
    function badgeStatus(item){const cls=item.status==="completed"?"done":item.status==="on_hold"?"hold":"";return `<span class="badge ${cls}">${esc(item.status_label||item.status)}</span>`}
    function sourceStrip(item){const sources=item.source_mix||[];return `<div class="source-strip">${sources.length?sources.map(source=>`<span class="badge source-${esc(source.family)}">${esc(source.label)} · 进展 ${source.progress_count||0}${source.value_count?` · 价值 ${source.value_count}`:''}${source.verification_count?` · 验证 ${source.verification_count}`:''}${source.partial_source_count?` · 部分来源 ${source.partial_source_count}${source.partial_event_count?` / 证据 ${source.partial_event_count}`:''}`:''}</span>`).join(''):'<span class="badge">来源待补充</span>'}</div>`}
    function logPreview(item){const logs=(item.logs||[]).slice(0,2);return `<div class="logs-preview"><div class="logs-label">最近进展 · ${item.log_count||0} 条${item.related_count?` · 相关问询 ${item.related_count} 条`:''}</div>${logs.length?logs.map(log=>`<div class="log-mini"><time>${esc(log.date)}</time> · ${esc(log.detail)}</div>`).join(""):'<div class="no-log">暂无新增进展证据</div>'}</div>`}
    function valuePreview(item){const values=(item.value_items||[]).slice(0,2);return values.length?`<div class="value-list"><strong>价值沉淀 · ${item.value_count||0}</strong>${values.map(value=>`<div class="value-line">${esc(value.detail)}</div>`).join('')}</div>`:''}
    function caseFolder(item){const opens=(item.open_items||[]).length?`<div class="folder-section"><h4>开放项 · ${item.open_items.length}</h4>${item.open_items.map(open=>`<div class="log-item"><div>${esc(open.text)}</div><div class="log-source">${esc(open.source||'')}</div></div>`).join('')}</div>`:'';const related=(item.related_events||[]).length?`<div class="folder-section"><h4>相关问询 · ${item.related_events.length}</h4>${item.related_events.map(log=>`<div class="log-item"><div class="log-date">${esc(log.date)} · ${esc(sourceName(log))}</div><strong>${esc(log.title||'相关问询')}</strong><div>${esc(log.detail)}</div><div class="log-source">${esc(log.source)} · ${esc(evidenceLabel(log))}</div>${routeControls(log,item.case_id)}</div>`).join('')}</div>`:'';const suggested=(item.suggested_evidence_units||[]).length?`<div class="folder-section"><details class="panel"><summary>待确认关联 / 可能相关证据 · ${item.suggested_evidence_unit_count} 组</summary><div class="details-body"><div class="muted">这些证据只按标题或目标中的单一项目信号归到这里，尚未计入项目进展；可用下方控件确认或改归。</div>${item.suggested_evidence_units.map(log=>projectLog(log,item.case_id)).join('')}</div></details></div>`:'';return `<details class="project-folder" data-project-folder="${esc(item.case_id)}"><summary><div class="project-folder-title"><span class="folder-mark" aria-hidden="true"></span>${esc(item.title)}</div><div class="case-meta"><span class="badge">${esc(item.category_label)}</span>${badgeStatus(item)}<span class="badge">证据组 ${item.evidence_unit_count||0} · 原始 ${item.log_count||0}</span>${item.associated_event_count?`<span class="badge">高置信关联 ${item.associated_event_count}</span>`:''}${item.suggested_evidence_unit_count?`<span class="badge attention">待确认 ${item.suggested_evidence_unit_count}</span>`:''}${item.open_item_count?`<span class="badge open-count">开放项 ${item.open_item_count}</span>`:''}${item.needs_review?'<span class="badge attention">需要拆分整理</span>':''}</div><div class="case-summary">${esc(item.current_summary||"暂无当前进度")}</div></summary><div class="project-folder-body">${sourceStrip(item)}${item.next_step?`<div class="next-step"><strong>下一步：</strong>${esc(item.next_step)}</div>`:""}${opens}<div class="folder-section"><h4>日报与任务证据 · ${item.evidence_unit_count||0} 组</h4>${groupedProjectLogs(item)}</div>${suggested}${related}${valuePreview(item)}<div class="folder-actions"><button class="small" data-open-case="${esc(item.case_id)}">编辑项目与完整记录</button><button class="small primary" data-edit-progress="${esc(item.case_id)}">更新进度 / 状态</button></div></div></details>`}
    function branchCard(item){const value=item.value_items?.[0];return `<article class="case-card branch-card" data-branch-id="${esc(item.branch_id)}"><div class="case-title">${esc(item.title)}</div><div class="case-meta"><span class="badge">长期需求分支</span><span class="badge">抽象进展 ${item.progress_count||0}</span><span class="badge">问询 ${item.related_count||0}</span><span class="badge done">价值 ${item.value_count||0}</span></div><div class="case-summary">${esc(item.current_summary||"")}</div>${value?`<div class="value-box"><strong>最近价值：</strong>${esc(value.detail)}</div>`:''}</article>`}
    function capabilityCard(item){return `<article class="case-card"><div class="case-title">${esc(item.title)}</div><div class="case-meta"><span class="badge">横向能力域</span><span class="badge">${item.item_count||0} 条</span></div><div class="case-summary">${esc(item.summary||"")}</div>${(item.items||[]).slice(0,2).map(x=>`<div class="log-mini"><time>${esc(x.period||x.date||'')}</time> · ${esc(x.detail)}</div>`).join('')}</article>`}
    function renderCases(target,items){$(target).innerHTML=items.length?items.map(caseFolder).join(""):'<div class="empty">暂无项目</div>'}
    function caseOptions(currentCaseId=''){return (data?.all_cases||[]).filter(c=>c.status!=="archived"&&c.case_id!==currentCaseId).map(c=>`<option value="${esc(c.case_id)}">${esc(c.title)}</option>`).join("")}
    function routeControls(event,currentCaseId=''){return `<div class="event-actions log-actions"><select id="route-${esc(event.event_id)}"><option value="">选择目标项目</option>${caseOptions(currentCaseId)}</select><select id="route-mode-${esc(event.event_id)}"><option value="related">关联但不计入进度</option><option value="log">归入进展</option><option value="current">归入并设为当前进度</option></select><button class="small" data-route="${esc(event.event_id)}">${currentCaseId?'移动':'归入'}</button>${event.can_undo_route?`<button class="small" data-undo="${esc(event.event_id)}">撤销上次归入</button>`:''}</div>`}
    function eventCard(event,kind){const clustered=Boolean(event.review_unit_id);const range=event.first_date&&event.last_date&&event.first_date!==event.last_date?`${event.first_date} → ${event.last_date}`:event.last_date||event.date;const mix=(event.source_mix||[]).map(source=>`${source.label} ${source.count}`).join(' · ');const reason=event.convergence_reason_label?`<div class="muted">暂留原因：${esc(event.convergence_reason_label)}</div>`:'';const actionNote=clustered?'<div class="muted">路由或忽略只处理当前代表记录；同组其余证据继续保留。</div>':'';const create=kind==='candidate'?`<button class="small primary" data-create="${esc(event.event_id)}">从代表记录新建项目</button>`:'';return `<article class="event-card"><div class="event-title">${esc(event.title)}</div><div class="event-meta"><span class="badge">${esc(range)}</span><span class="badge ${kind==='candidate'?'attention':''}">${kind==='candidate'?'项目审阅单元':'参考证据组'}</span>${clustered?`<span class="badge">${event.member_count||1} 条证据</span>`:''}</div>${mix?`<div class="muted">${esc(mix)}</div>`:''}${reason}<div class="event-detail">${esc(event.detail)}</div><div class="muted">${esc(event.source)}</div>${routeControls(event)}${actionNote}<div class="event-actions">${create}<button class="small" data-ignore="${esc(event.event_id)}">忽略代表记录</button></div></article>`}
    function render(snapshot){data=snapshot;$('sourceStamp').textContent=`ChatGPT + Codex 跨来源整合 · ${new Date(snapshot.generated_at).toLocaleString()}`;const s=snapshot.summary;$('metrics').innerHTML=[["活跃项目",s.active_case_count,"持续推进"],["ChatGPT 覆盖",s.chatgpt_project_count||0,"个活跃项目"],["Codex 覆盖",s.codex_project_count||0,"个活跃项目"],["价值沉淀",s.value_item_count||0,"条可复用判断"]].map(x=>`<div class="metric"><strong>${esc(x[1])}</strong><span>${esc(x[0])} · ${esc(x[2])}</span></div>`).join("");
      $('branches').innerHTML=snapshot.branches.length?snapshot.branches.map(branchCard).join(''):'<div class="empty">暂无需求分支</div>';$('capabilityCount').textContent=snapshot.capability_domains.length;$('capabilityDomains').innerHTML=snapshot.capability_domains.length?snapshot.capability_domains.map(capabilityCard).join(''):'<div class="empty">暂无能力域</div>';
      $('inboxCount').textContent=s.inbox_count;$('inboxList').innerHTML=snapshot.inbox_review_units.length?snapshot.inbox_review_units.map(event=>eventCard(event,'candidate')).join(""):'<div class="empty">当前没有需要人工决定项目归属的记录</div>';$('inboxReferenceCount').textContent=`${s.inbox_topic_group_count||0} 类 / ${s.inbox_reference_unit_count} 组`;$('inboxRawCount').textContent=`原始 ${s.inbox_raw_event_count||0} 条；高置信关联 ${s.inbox_associated_event_count||0} 条；待确认关联 ${s.inbox_suggested_event_count||0} 条`;$('inboxReferenceList').innerHTML=(snapshot.inbox_topic_groups||[]).length?snapshot.inbox_topic_groups.map(topic=>`<details class="panel"><summary>${esc(topic.label)} · ${topic.units.length} 组 / ${topic.member_count} 条</summary><div class="details-body">${topic.units.map(event=>eventCard(event,'reference')).join('')}</div></details>`).join(''):'<div class="empty">没有未归项目证据</div>';
      $('workGroups').innerHTML=snapshot.categories.map(cat=>{const items=snapshot.work_groups[cat.id]||[];return `<div class="category"><div class="category-head"><h3>${esc(cat.label)}</h3><span class="badge">${items.length}</span></div><div>${items.length?items.map(caseFolder).join(""):'<div class="empty">暂无进行中的项目</div>'}</div></div>`}).join("");renderCases('lifeCases',snapshot.life_cases);renderCases('closedCases',snapshot.closed_cases);$('closedCount').textContent=snapshot.closed_cases.length;$('referenceCount').textContent=s.reference_count;$('references').innerHTML=snapshot.daily_references.length?snapshot.daily_references.slice(0,80).map(e=>eventCard(e,'reference')).join(""):'<div class="empty">暂无日报知识事件</div>';bindDynamic()}
    function bindDynamic(){document.querySelectorAll('[data-branch-id]').forEach(el=>el.onclick=()=>openBranch(el.dataset.branchId));document.querySelectorAll('[data-open-case]').forEach(el=>el.onclick=e=>{e.stopPropagation();openCase(el.dataset.openCase)});document.querySelectorAll('[data-edit-progress]').forEach(el=>el.onclick=e=>{e.stopPropagation();openProgressEditor(el.dataset.editProgress)});document.querySelectorAll('[data-route]').forEach(el=>el.onclick=e=>{e.stopPropagation();routeEvent(el.dataset.route)});document.querySelectorAll('[data-undo]').forEach(el=>el.onclick=e=>{e.stopPropagation();undoEvent(el.dataset.undo)});document.querySelectorAll('[data-create]').forEach(el=>el.onclick=()=>openCreateFromEvent(el.dataset.create));document.querySelectorAll('[data-ignore]').forEach(el=>el.onclick=()=>ignoreEvent(el.dataset.ignore))}
    async function refresh(){try{$('errorBox').style.display='none';render(await api('/api/dashboard'))}catch(e){$('errorBox').textContent=`刷新失败：${e.message}`;$('errorBox').style.display='block'}}
    function categories(){return (data?.categories||[]).map(c=>`<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("")}
    function setLineVisibility(){$('categoryLabel').style.display=$('caseLine').value==='work'?'grid':'none'}
    function showCaseModal(){$('caseBackdrop').style.display='flex'} function hideCaseModal(){$('caseBackdrop').style.display='none'}
    function hideProgressModal(){$('progressBackdrop').style.display='none'}
    function openProgressEditor(caseId){const item=data.all_cases.find(c=>c.case_id===caseId);if(!item)return;$('progressBackdrop').dataset.caseId=caseId;$('progressBackdrop').dataset.originalStatus=item.status;$('progressModalTitle').textContent=`更新进度 / 状态 · ${item.title}`;$('previousProgress').textContent=item.current_summary||'暂无当前进度';$('manualProjectStatus').value=item.status;$('manualProgressDetail').value='';$('progressBackdrop').style.display='flex';setTimeout(()=>$('manualProgressDetail').focus(),0)}
    function hideBranchModal(){$('branchBackdrop').style.display='none'}
    function openBranch(branchId){const b=data.branches.find(x=>x.branch_id===branchId);if(!b)return;$('branchBackdrop').dataset.branchId=branchId;$('branchModalTitle').textContent=b.title;const section=(title,items,renderItem)=>`<div class="timeline"><h4>${title} · ${items.length}</h4>${items.length?items.map(renderItem).join(''):'<div class="no-log">暂无</div>'}</div>`;$('branchBody').innerHTML=`<div class="case-summary">${esc(b.current_summary||'')}</div>${section('抽象进展',b.progress_events||[],x=>`<div class="log-item"><div class="log-date">${esc(x.period||x.date||'')}</div><div>${esc(x.detail)}</div></div>`)}${section('相关问询',b.related_questions||[],x=>`<div class="log-item"><div class="log-date">${esc(x.period||x.date||'')}</div><div>${esc(x.detail)}</div></div>`)}${section('价值沉淀',b.value_items||[],x=>`<div class="log-item"><div class="log-date">${esc(x.date||'')}</div><div>${esc(x.detail)}</div><div class="log-source">${esc(x.evidence_mode||'compliance_abstracted')}</div></div>`)}`;$('newValueDetail').value='';$('branchBackdrop').style.display='flex'}
    async function addBranchValue(){const detail=$('newValueDetail').value.trim();if(!detail)return alert('请填写合规抽象后的价值');const branchId=$('branchBackdrop').dataset.branchId;try{await api('/api/branch/value',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:branchId,detail,evidence_mode:'compliance_abstracted'})});hideBranchModal();await refresh()}catch(e){alert(`添加失败：${e.message}`)}}
    function fillForm(item,eventId=''){$('caseId').value=item?.case_id||'';$('sourceEventId').value=eventId;$('caseTitle').value=item?.title||'';$('caseLine').value=item?.line||'work';$('caseCategory').innerHTML=categories();$('caseCategory').value=item?.category||data.categories[0].id;$('caseStatus').value=item?.status||'in_progress';$('caseSummary').value=item?.current_summary||'';$('caseNext').value=item?.next_step||'';setLineVisibility()}
    function evidenceMeta(log){const refs=(log.verification_refs||[]).length?` · 证据 ${log.verification_refs.map(esc).join('；')}`:'';const boundary=log.evidence_boundary?` · 边界 ${esc(log.evidence_boundary)}`:'';const status=log.task_status?` · 状态 ${esc(log.task_status)}`:'';return status+refs+boundary}
    function sourceName(log){return log.source_kind==='codex_daily'||log.source_kind==='codex_live'?'Codex 任务与日报':log.source_kind==='manual'?'人工更新':'ChatGPT 日报 / Memory'}
    function evidenceLabel(log){return log.source_kind==='manual'?'人工记录':log.source_kind==='codex_daily'||log.source_kind==='codex_live'||String(log.source||'').includes('chatgpt-daily')?(log.evidence_type==='reported'?'日报证据':log.evidence_type||'已落库证据'):log.evidence_type||'已落库证据'}
    function projectLog(log,caseId){const range=log.first_date&&log.last_date&&log.first_date!==log.last_date?`${log.first_date} → ${log.last_date}`:log.last_date||log.date;const grouped=log.member_count>1?` · ${log.member_count} 条记录`:'';const associated=log.association_kind==='high_confidence'?' · 高置信关联':'';return `<div class="log-item"><div class="log-date">${esc(range)} · ${esc(sourceName(log))}${grouped}${associated}</div><strong>${esc(log.title||'项目进展')}</strong>${log.goal?`<div><strong>目标：</strong>${esc(log.goal)}</div>`:''}<div>${esc(log.detail)}</div>${log.next_step?`<div><strong>下一步：</strong>${esc(log.next_step)}</div>`:''}<div class="log-source">${esc(log.source)} · ${esc(evidenceLabel(log))}${evidenceMeta(log)}</div>${routeControls(log,caseId)}</div>`}
    function groupedProjectLogs(item,full=false){const groups={};(full?item.logs||[]:item.evidence_units||item.logs||[]).forEach(log=>(groups[sourceName(log)]??=[]).push(log));const order=['Codex 任务与日报','ChatGPT 日报 / Memory','人工更新'];const sections=order.filter(name=>groups[name]?.length).map(name=>`<details class="panel" open><summary>${esc(name)} · ${groups[name].length} ${full?'条':'组'}</summary><div class="details-body">${groups[name].map(log=>projectLog(log,item.case_id)).join('')}</div></details>`).join('');return sections||'<div class="no-log">暂无进展证据</div>'}
    function openCase(caseId){const item=data.all_cases.find(c=>c.case_id===caseId);if(!item)return;$('caseModalTitle').textContent=`📁 ${item.title}`;$('caseModalHint').textContent=`项目资料夹 · ${item.log_count} 条原始进展 · ${item.value_count||0} 条价值 · ${item.open_item_count||0} 个开放项`;fillForm(item);$('existingCaseExtras').style.display='block';const logs=groupedProjectLogs(item,true);const values=(item.value_items||[]).length?`<div class="timeline"><h4>价值沉淀</h4>${item.value_items.map(value=>`<div class="log-item"><div class="log-date">${esc(value.source_family==='chatgpt'?'ChatGPT':value.source_family==='codex'?'Codex':'Memory')} · ${esc(value.evidence_type)}</div><div>${esc(value.detail)}</div><div class="log-source">${esc(value.source)}</div></div>`).join('')}</div>`:'';const opens=(item.open_items||[]).length?`<div class="timeline"><h4>开放项</h4>${item.open_items.map(open=>`<div class="log-item"><div>${esc(open.text)}</div><div class="log-source">${esc(open.source||'')}</div></div>`).join('')}</div>`:'';const related=(item.related_events||[]).length?`<div class="timeline"><h4>相关问询（不计入进度）</h4>${item.related_events.map(log=>`<div class="log-item"><div class="log-date">${esc(log.date)}</div><div>${esc(log.title)} · ${esc(log.detail)}</div><div class="log-source">${esc(log.source)}</div>${routeControls(log,item.case_id)}</div>`).join('')}</div>`:'';$('timelineList').innerHTML=logs+values+opens+related;$('newLogDetail').value='';showCaseModal();bindDynamic()}
    function openNewCase(){$('caseModalTitle').textContent='新建项目';$('caseModalHint').textContent='只为具体、可推进并能关闭的问题建立项目';fillForm(null);$('existingCaseExtras').style.display='none';showCaseModal()}
    function findEvent(eventId){return [...data.inbox,...data.daily_references,...data.all_cases.flatMap(c=>c.logs||[])].find(x=>x.event_id===eventId)}
    function openCreateFromEvent(eventId){const e=findEvent(eventId);if(!e)return;$('caseModalTitle').textContent='从日报建立项目';$('caseModalHint').textContent=`首条进展：${e.date} · ${e.title}`;fillForm({title:e.title,line:'work',category:data.categories[0].id,status:'in_progress',current_summary:e.detail},eventId);$('existingCaseExtras').style.display='none';showCaseModal()}
    async function saveCase(ev){ev.preventDefault();const id=$('caseId').value;const payload={case_id:id,title:$('caseTitle').value.trim(),line:$('caseLine').value,category:$('caseCategory').value,status:$('caseStatus').value,current_summary:$('caseSummary').value.trim(),next_step:$('caseNext').value.trim()};if(!payload.title)return alert('请填写项目名称');const eventId=$('sourceEventId').value;const path=id?'/api/case/update':'/api/case/create';if(eventId)payload.event_id=eventId;try{await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});hideCaseModal();await refresh()}catch(e){alert(`保存失败：${e.message}`)}}
    async function addLog(){const detail=$('newLogDetail').value.trim();if(!detail)return alert('请填写 Log');try{await api('/api/case/log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:$('caseId').value,detail,set_as_current:true})});hideCaseModal();await refresh()}catch(e){alert(`添加失败：${e.message}`)}}
    async function saveProgress(){const detail=$('manualProgressDetail').value.trim();const status=$('manualProjectStatus').value;if(!detail&&status===$('progressBackdrop').dataset.originalStatus)return alert('请填写当前进度或改变项目状态');try{await api('/api/case/manual-update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:$('progressBackdrop').dataset.caseId,detail,status})});hideProgressModal();await refresh()}catch(e){alert(`保存失败：${e.message}`)}}
    async function routeEvent(eventId){const caseId=$(`route-${eventId}`).value;if(!caseId)return alert('先选择目标项目');const mode=$(`route-mode-${eventId}`).value;try{await api('/api/event/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:eventId,case_id:caseId,set_current:mode==='current',link_only:mode==='related'})});hideCaseModal();await refresh()}catch(e){alert(`归入失败：${e.message}`)}}
    async function undoEvent(eventId){try{await api('/api/event/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:eventId})});hideCaseModal();await refresh()}catch(e){alert(`撤销失败：${e.message}`)}}
    async function ignoreEvent(eventId){if(!confirm('将这条日报事件标记为忽略？'))return;try{await api('/api/event/ignore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:eventId})});await refresh()}catch(e){alert(`忽略失败：${e.message}`)}}
    $('refreshBtn').onclick=refresh;$('closeCaseBtn').onclick=hideCaseModal;$('caseBackdrop').onclick=e=>{if(e.target.id==='caseBackdrop')hideCaseModal()};$('closeProgressBtn').onclick=hideProgressModal;$('progressBackdrop').onclick=e=>{if(e.target.id==='progressBackdrop')hideProgressModal()};$('saveProgressBtn').onclick=saveProgress;$('closeBranchBtn').onclick=hideBranchModal;$('branchBackdrop').onclick=e=>{if(e.target.id==='branchBackdrop')hideBranchModal()};$('addValueBtn').onclick=addBranchValue;$('caseLine').onchange=setLineVisibility;$('caseForm').onsubmit=saveCase;$('addLogBtn').onclick=addLog;refresh();setInterval(refresh,300000);
  </script>
</body></html>'''.encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    def token_from_request(self) -> str:
        parsed = urlparse(self.path)
        return parse_qs(parsed.query).get("token", [""])[0] or self.headers.get("X-Dashboard-Token", "")

    def authorized(self) -> bool:
        client_host = str(self.client_address[0] or "")
        return is_loopback_client(client_host) or secrets.compare_digest(
            self.token_from_request(), access_token()
        )

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def reject(self) -> None:
        self.send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.FORBIDDEN)

    def body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/health":
                self.send_bytes(b"ok\n", "text/plain; charset=utf-8")
                return
            if not self.authorized():
                self.reject()
                return
            if parsed.path in {"/", "/dashboard"}:
                self.send_bytes(page_html(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/dashboard":
                self.send_json(STORE.snapshot())
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not self.authorized():
                self.reject()
                return
            payload = self.body_json()
            if parsed.path == "/api/case/update":
                result = STORE.update_case(payload)
            elif parsed.path == "/api/case/create":
                result = STORE.create_case(payload, str(payload.get("event_id") or "") or None)
            elif parsed.path == "/api/case/manual-update":
                result = STORE.manual_update(payload)
            elif parsed.path == "/api/case/log":
                result = STORE.add_log(payload)
            elif parsed.path == "/api/event/route":
                result = STORE.route_event(
                    str(payload.get("event_id") or ""),
                    str(payload.get("case_id") or ""),
                    set_current=bool(payload.get("set_current", False)),
                    link_only=bool(payload.get("link_only", False)),
                )
            elif parsed.path == "/api/event/route-branch":
                result = STORE.route_event_to_branch(
                    str(payload.get("event_id") or ""),
                    str(payload.get("branch_id") or ""),
                    as_progress=bool(payload.get("as_progress", False)),
                )
            elif parsed.path == "/api/branch/value":
                result = STORE.add_branch_value(payload)
            elif parsed.path == "/api/event/undo":
                result = STORE.undo_event_route(str(payload.get("event_id") or ""))
            elif parsed.path == "/api/event/ignore":
                result = STORE.ignore_event(str(payload.get("event_id") or ""))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "result": result})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Serve the ChatGPT + Codex project progress and value dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8791, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Serving project progress dashboard at http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
