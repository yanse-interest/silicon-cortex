"use client";

import { useEffect, useMemo, useState } from "react";

type EvidenceRef = { source_type: string; source_date: string; session_ref: string; session_title?: string; support_level: string };
type ValueItem = {
  date: string; topic?: string; question?: string; answer?: string; answer_status?: string; answer_origin?: string;
  evidence_refs?: EvidenceRef[]; evidence_boundary?: string; capability_tags?: string[];
  detail: string; source: string; evidence_type: string;
};
type LogItem = ValueItem & { title: string; first_date?: string; member_count?: number };
type SourceMix = { label: string; progress_count: number; value_count: number; latest_date: string };
type LatestDailyProgress = { date: string; items: LogItem[] };
type Project = {
  title: string; category_label: string; status: string; status_label: string;
  current_summary: string; latest_daily_progress?: LatestDailyProgress; next_step: string; updated_at: string; evidence_mode: string;
  log_count: number; raw_log_count?: number; suggested_count?: number; value_count: number; open_item_count: number; related_count: number;
  logs: LogItem[]; suggested_items?: LogItem[]; value_items: ValueItem[]; open_items: string[]; related_items?: LogItem[]; source_mix: SourceMix[];
};
type Branch = {
  title: string; status: string; current_summary: string; progress_count: number; related_count: number; value_count: number;
  progress_events: LogItem[]; related_questions: LogItem[]; value_items: ValueItem[];
};
type Capability = { title: string; summary: string; tag_label?: string; item_count: number; items: ValueItem[] };
type DailyUpdate = LogItem & { scope: string };
type Ingredient = { name: string; role: "主料" | "调料" | string; group?: string; amount: number | null; quantity_text?: string; unit: string; package_spec: string | null; amount_status: string };
type RecipeSource = { type: string; name: string; url: string; row_id: number | null };
type Recipe = { title: string; variant?: string; ingredients: Ingredient[]; steps: string[]; method_status: string; status: string; source_dates: string[]; sources?: RecipeSource[]; notes?: string[] };
type Snapshot = {
  schema_version: number; generated_at: string; daily_updated_through: string;
  daily_digest: { date: string; source_counts: Record<string, number>; items: DailyUpdate[] };
  summary: {
    branch_count: number; case_count: number; active_case_count: number; chatgpt_project_count: number;
    codex_project_count: number; value_item_count: number; work_case_count: number; life_case_count: number;
  };
  branches: Branch[]; capability_domains: Capability[];
  recipes?: Recipe[];
  work_sections: { label: string; projects: Project[] }[]; life_cases: Project[]; closed_cases: Project[];
};
type Freshness = "refreshing" | "fresh" | "cached" | "fallback";
type Tab = "overview" | "projects" | "values" | "capabilities" | "recipes" | "archive";

const tabItems: { id: Tab; label: string }[] = [
  { id: "overview", label: "总览" }, { id: "projects", label: "项目" }, { id: "values", label: "价值沉淀" },
  { id: "capabilities", label: "能力域" }, { id: "recipes", label: "菜谱库" }, { id: "archive", label: "归档" },
];

function compactDate(value: string) { return value ? value.slice(0, 10) : "—"; }

function SourceBadge({ source }: { source: string }) {
  if (source === "合规抽象") return null;
  const lower = source.toLowerCase();
  const type = lower.includes("codex") ? "codex" : lower.includes("chatgpt") ? "chatgpt" : "memory";
  return <span className={`source-badge source-${type}`}>{source}</span>;
}

function evidenceLabel(log: LogItem) {
  if (log.source === "Codex" || log.source === "ChatGPT") {
    return log.evidence_type === "reported" ? "日报证据" : log.evidence_type || "已落库证据";
  }
  if (log.source === "人工") return "人工记录";
  return log.evidence_type || "已落库证据";
}

function ValueRow({ value }: { value: ValueItem }) {
  if (value.question) {
    const grounded = value.answer_status === "source_grounded" && Boolean(value.answer);
    const originLabel = value.answer_origin === "chatgpt_conversation" ? "基于 ChatGPT 具体会话" : "基于日报证据";
    return <article className={`value-row qa-row ${grounded ? "is-grounded" : "needs-source-review"}`}><div className="value-marker" aria-hidden="true" /><details>
    <summary><div className="meta-row"><span>{compactDate(value.date)}</span>{value.topic ? <span className="knowledge-topic">{value.topic}</span> : null}{value.capability_tags?.map((tag) => <span className="capability-tag" key={tag}>{tag}</span>)}<SourceBadge source={value.source} /></div><h4>{value.question}</h4></summary>
    {grounded ? <div className="qa-answer"><strong>{originLabel}</strong><p>{value.answer}</p>
      {value.evidence_refs?.length ? <div className="qa-evidence" aria-label="回答依据">回答依据：{value.evidence_refs.map((ref) => `${ref.source_date} · ${ref.session_ref}${ref.session_title ? ` ${ref.session_title}` : ""}`).join("；")}</div> : null}
      {value.evidence_boundary ? <small>边界：{value.evidence_boundary}</small> : null}</div>
      : <div className="qa-answer qa-answer-pending"><strong>证据不足，待回查</strong><p>当前归档没有足以核验答案的日报或会话证据。旧迁移生成的回答已停用；回查到原日报或可访问的 ChatGPT 会话后才会补充答案。</p></div>}
  </details></article>;
  }
  return <article className="value-row"><div className="value-marker" aria-hidden="true" /><div>
    <div className="meta-row"><span>{compactDate(value.date)}</span>{value.topic ? <span className="knowledge-topic">{value.topic}</span> : null}<SourceBadge source={value.source} /></div>
    <p>{value.detail}</p>
  </div></article>;
}

function ProjectCard({ project }: { project: Project }) {
  const [expanded, setExpanded] = useState(false);
  const status = project.status_label || (project.status === "in_progress" ? "进行中" : "待推进");
  const latestDaily = project.latest_daily_progress?.items?.length ? project.latest_daily_progress : null;
  return <article className={`project-card ${expanded ? "is-expanded" : ""}`}>
    <button className="card-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
      <div className="project-card-top"><span className="folder-mark" aria-hidden="true" />
        <span className={`status-dot status-${project.status}`} />
        <span className="eyebrow">项目文件夹 · {project.category_label || "待分类"}</span>
        <span className="status-pill">{status}</span></div>
      <h3>{project.title}</h3>{latestDaily ? <div className="daily-progress-preview">
        <div className="daily-progress-label">日报进展 · {compactDate(latestDaily.date)}</div>
        {latestDaily.items.map((item, index) => <div className="daily-progress-item" key={`${item.date}-${item.title}-${index}`}>
          <div>{item.title ? <strong>{item.title}</strong> : null}<SourceBadge source={item.source} /></div><span>{item.detail}</span></div>)}
      </div> : <p className="summary-text">{project.current_summary || "尚未形成当前摘要"}</p>}
      <div className="count-row"><span>{project.log_count} 个证据组{project.raw_log_count ? ` / ${project.raw_log_count} 条记录` : ""}</span><span>{project.value_count} 条价值</span>
        {project.suggested_count ? <span>{project.suggested_count} 个待确认关联</span> : null}
        <span>{project.open_item_count} 个开放项</span>{project.related_count ? <span>{project.related_count} 条相关问询</span> : null}
        <span className="expand-label">{expanded ? "收起" : "详情"}⌄</span></div>
    </button>
    {expanded ? <div className="card-detail">
      {latestDaily && project.current_summary ? <section className="detail-block"><h4>人工维护摘要</h4><p>{project.current_summary}</p></section> : null}
      {project.next_step ? <section className="detail-block next-block"><h4>下一步</h4><p>{project.next_step}</p></section> : null}
      {project.open_items.length ? <section className="detail-block"><h4>开放项</h4><ul>
        {project.open_items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section> : null}
      {project.related_items?.length ? <section className="detail-block"><h4>相关问询</h4><div className="timeline">
        {project.related_items.map((item, index) => <div className="timeline-item" key={`${item.date}-${index}`}>
          <span>{compactDate(item.date)}</span><strong>{item.title}</strong><p>{item.detail}</p><SourceBadge source={item.source} /></div>)}</div></section> : null}
      {project.logs.length ? <section className="detail-block"><h4>日报与任务证据</h4><div className="timeline">
        {project.logs.map((log, index) => <div className="timeline-item" key={`${log.date}-${index}`}>
          <span>{log.first_date && log.first_date !== log.date ? `${compactDate(log.first_date)} → ` : ""}{compactDate(log.date)} · {evidenceLabel(log)}{(log.member_count || 1) > 1 ? ` · ${log.member_count} 条记录` : ""}</span>
          <strong>{log.title || "项目进展"}</strong><p>{log.detail}</p><SourceBadge source={log.source} /></div>)}</div></section> : null}
      {project.suggested_items?.length ? <section className="detail-block"><h4>待确认关联 / 可能相关证据</h4><p>这些条目未计入项目进展。</p><div className="timeline">
        {project.suggested_items.map((log, index) => <div className="timeline-item" key={`suggested-${log.date}-${index}`}><span>{compactDate(log.date)}</span><strong>{log.title}</strong><p>{log.detail}</p><SourceBadge source={log.source} /></div>)}</div></section> : null}
      {project.value_items.length ? <section className="detail-block"><h4>价值沉淀</h4>
        {project.value_items.map((value, index) => <ValueRow value={value} key={`${value.date}-${index}`} />)}</section> : null}
      <footer className="card-footer"><span>更新于 {compactDate(project.updated_at)}</span><div>
        {project.source_mix.map((source) => <SourceBadge key={source.label} source={source.label} />)}</div></footer>
    </div> : null}
  </article>;
}

function BranchCard({ branch }: { branch: Branch }) {
  const [expanded, setExpanded] = useState(false);
  return <article className="branch-card"><button className="card-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
    <div className="branch-kicker">长期需求分支</div><h3>{branch.title}</h3><p>{branch.current_summary}</p>
    <div className="branch-counts"><span><b>{branch.progress_count}</b>抽象进展</span><span><b>{branch.related_count}</b>相关问询</span>
      <span><b>{branch.value_count}</b>价值沉淀</span></div></button>
    {expanded ? <div className="branch-detail">
      {branch.progress_events.map((log, index) => <div className="timeline-item" key={`progress-${index}`}><span>{log.date}</span><p>{log.detail}</p></div>)}
      {branch.value_items.map((value, index) => <ValueRow value={value} key={`value-${index}`} />)}
    </div> : null}</article>;
}

function SectionTitle({ eyebrow, title, count }: { eyebrow: string; title: string; count?: number }) {
  return <div className="section-title"><div><span>{eyebrow}</span><h2>{title}</h2></div>{typeof count === "number" ? <b>{count}</b> : null}</div>;
}

function CapabilityCard({ domain }: { domain: Capability }) {
  const [activeTag, setActiveTag] = useState("全部");
  const tags = useMemo(() => Array.from(new Set(domain.items.flatMap((item) => item.capability_tags || []))), [domain.items]);
  const visibleItems = domain.items.filter((item) => activeTag === "全部" || item.capability_tags?.includes(activeTag));
  const groundedCount = domain.items.filter((item) => item.answer_status === "source_grounded" && item.answer).length;
  const pendingCount = domain.items.length - groundedCount;
  return <article className="capability-card">
    <div className="capability-icon">◇</div><h3>{domain.title}</h3><p>{domain.summary}</p><div className="capability-count">{groundedCount} 道已有来源回答{pendingCount ? ` · ${pendingCount} 道待回查` : ""}</div>
    {tags.length ? <div className="capability-filter-block"><div className="capability-filter-label">{domain.tag_label || "分类标签"}</div>
      <div className="capability-filter" aria-label={`按${domain.tag_label || "标签"}筛选`}>{["全部", ...tags].map((tag) => <button className={activeTag === tag ? "active" : ""} key={tag} onClick={() => setActiveTag(tag)}>{tag}</button>)}</div></div> : null}
    <div className="capability-values">{visibleItems.map((value, index) => <ValueRow value={value} key={index} />)}</div>
  </article>;
}

function DailyDigest({ digest }: { digest: Snapshot["daily_digest"] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? digest.items : digest.items.slice(0, 4);
  return <section className="dashboard-section daily-digest">
    <SectionTitle eyebrow="DAILY DELTA" title="本次日报带来的变化" count={digest.items.length} />
    <div className="digest-source-row">
      <span><SourceBadge source="ChatGPT" />已处理 · {digest.source_counts.ChatGPT || 0} 条项目增量</span>
      <span><SourceBadge source="Codex" />已处理 · {digest.source_counts.Codex || 0} 条项目增量</span>
    </div>
    {visible.length ? <div className="digest-list">{visible.map((item, index) => <article className="digest-item" key={`${item.scope}-${item.title}-${index}`}>
      <div className="meta-row"><span>{compactDate(item.date)}</span><SourceBadge source={item.source} /><span className="digest-scope">{item.scope}</span></div>
      <h3>{item.title || "项目进展"}</h3><p>{item.detail}</p>
    </article>)}</div> : <div className="digest-empty">两份日报均已完成处理，本次没有形成可路由到项目的新增内容。</div>}
    {digest.items.length > 4 ? <button className="digest-more" onClick={() => setExpanded((value) => !value)}>
      {expanded ? "收起" : `查看全部 ${digest.items.length} 条`}
    </button> : null}
  </section>;
}

function RecipeLibrary({ recipes }: { recipes: Recipe[] }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("全部");
  const visible = recipes.filter((recipe) => (status === "全部" || recipe.status === status) && `${recipe.title} ${recipe.variant || ""} ${recipe.ingredients.map((item) => item.name).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  const ingredientLine = (item: Ingredient) => <li key={`${item.group || item.role}-${item.name}-${item.quantity_text || item.amount}`}><span>{item.name}</span><strong>{item.amount_status === "recorded" ? `${item.quantity_text || item.amount} ${item.unit}` : item.quantity_text || "用量待补充"}</strong>{item.package_spec ? <small>（{item.package_spec}）</small> : null}</li>;
  const ingredientGroups = (recipe: Recipe) => [...new Set(recipe.ingredients.map((item) => item.group || item.role || "食材"))];
  return <section className="dashboard-section first-section recipe-library"><SectionTitle eyebrow="RECIPES" title="菜谱库" count={visible.length} /><p className="recipe-note">每道菜固定先列食材用量，再列具体做法；范围、约量和少许按原文保留。</p><div className="recipe-controls"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索菜名或食材" aria-label="搜索菜谱" /><div className="recipe-filter" aria-label="按菜谱状态筛选">{[["全部", "全部"], ["complete", "完整配方"], ["needs_completion", "待补充"]].map(([id, label]) => <button key={id} className={status === id ? "active" : ""} aria-pressed={status === id} onClick={() => setStatus(id)}>{label}</button>)}</div></div>{visible.length ? <div className="recipe-list">{visible.map((recipe) => <details className="recipe-card" key={`${recipe.title}-${recipe.variant || ""}`}><summary><div><span className={`recipe-status ${recipe.status}`}>{recipe.status === "complete" ? "完整配方" : "待补充"}</span><h3>{recipe.title}{recipe.variant ? ` · ${recipe.variant}` : ""}</h3></div><span>详情⌄</span></summary><div className="recipe-detail"><p className="recipe-date">来源日期：{recipe.source_dates.join("、") || "—"}</p><section><h4>食材用量</h4>{recipe.ingredients.length ? ingredientGroups(recipe).map((group) => <div key={group}><h5>{group}</h5><ul>{recipe.ingredients.filter((item) => (item.group || item.role || "食材") === group).map(ingredientLine)}</ul></div>) : <p>食材用量待补充。</p>}</section><section><h4>具体做法</h4>{recipe.method_status === "complete" ? <ol>{recipe.steps.map((item) => <li key={item}>{item}</li>)}</ol> : recipe.steps.length ? <><p className="recipe-incomplete">已有步骤记录，待补全后才作为完整配方：</p><ol>{recipe.steps.map((item) => <li key={item}>{item}</li>)}</ol><p>具体做法仍待补充。</p></> : <p>具体做法待补充。</p>}</section>{recipe.notes?.length ? <section><h4>来源备注</h4>{recipe.notes.map((note) => <p key={note}>{note}</p>)}</section> : null}{recipe.sources?.length ? <section><h4>来源</h4><ul>{recipe.sources.map((source, index) => <li key={`${source.type}-${source.row_id || index}`}>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.name || source.type}</a> : <span>{source.name || source.type}</span>}{source.row_id ? <small>NAS 行 {source.row_id}</small> : null}</li>)}</ul></section> : null}</div></details>)}</div> : <div className="digest-empty">没有匹配的菜谱。</div>}</section>;
}

export function DashboardClient({ snapshot }: { snapshot: Snapshot }) {
  const [activeSnapshot, setActiveSnapshot] = useState(snapshot);
  const [freshness, setFreshness] = useState<Freshness>("refreshing");
  const [tab, setTab] = useState<Tab>("overview");
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 6500);
    fetch("/api/fresh-snapshot", { cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) throw new Error("fresh snapshot unavailable");
        const candidate = await response.json() as Snapshot & { freshness?: { mode?: string } };
        if (candidate.schema_version !== 3 || !candidate.generated_at || !candidate.daily_updated_through) throw new Error("invalid snapshot");
        setActiveSnapshot(candidate);
        setFreshness(candidate.freshness?.mode === "cached" ? "cached" : "fresh");
      })
      .catch(() => setFreshness("fallback"))
      .finally(() => window.clearTimeout(timeout));
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, []);
  const currentSnapshot = activeSnapshot;
  const activeWork = useMemo(() => currentSnapshot.work_sections.flatMap((section) => section.projects), [currentSnapshot.work_sections]);
  const activeProjects = useMemo(() => [...activeWork, ...currentSnapshot.life_cases]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at)), [activeWork, currentSnapshot.life_cases]);
  const values = useMemo(() => {
    const rows: { scope: string; value: ValueItem }[] = [];
    currentSnapshot.branches.forEach((branch) => branch.value_items.forEach((value) => rows.push({ scope: branch.title, value })));
    activeProjects.forEach((project) => project.value_items.forEach((value) => rows.push({ scope: project.title, value })));
    currentSnapshot.closed_cases.forEach((project) => project.value_items.forEach((value) => rows.push({ scope: project.title, value })));
    currentSnapshot.capability_domains.forEach((domain) => domain.items.forEach((value) => rows.push({ scope: domain.title, value })));
    return rows.sort((a, b) => b.value.date.localeCompare(a.value.date));
  }, [activeProjects, currentSnapshot]);

  return <main className="app-shell">
    <header className="hero"><div className="hero-orb hero-orb-one" /><div className="hero-orb hero-orb-two" />
      <div className="hero-label">PERSONAL PROJECT INTELLIGENCE</div><h1>项目进度与<br />价值沉淀</h1>
      <p>ChatGPT × Codex · 已落库证据的只读整合</p>
      <div className={`sync-panel sync-${freshness}`} aria-live="polite">
        <div className="sync-line"><span />{freshness === "refreshing" ? "正在读取最新落库数据" : freshness === "fallback" ? "实时生成失败 · 正在使用上次验证快照" : freshness === "cached" ? "已读取本机短时缓存" : "已读取最新落库数据"}</div>
        <div className="sync-meta"><span>生成于 {currentSnapshot.generated_at ? new Date(currentSnapshot.generated_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</span><span>日报处理至 {currentSnapshot.daily_updated_through || "—"}</span></div>
        <small>“处理至”仅表示已落库双来源日期，不代表底层证据完整。</small>
      </div>
    </header>
    <nav className="tabbar" aria-label="看板导航">{tabItems.map((item) => <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>
    <div className="content">
      {tab === "overview" ? <>
        <section className="metrics-grid"><article><span>正在推进</span><strong>{currentSnapshot.summary.active_case_count}</strong><small>个项目</small></article>
          <article><span>价值沉淀</span><strong>{currentSnapshot.summary.value_item_count}</strong><small>条复用价值</small></article>
          <article><span>ChatGPT 覆盖</span><strong>{currentSnapshot.summary.chatgpt_project_count}</strong><small>个项目</small></article>
          <article><span>Codex 覆盖</span><strong>{currentSnapshot.summary.codex_project_count}</strong><small>个项目</small></article></section>
        <DailyDigest digest={currentSnapshot.daily_digest} />
        {currentSnapshot.branches.length ? <section className="dashboard-section"><SectionTitle eyebrow="LONG-RUNNING" title="长期需求分支" count={currentSnapshot.branches.length} />
          <div className="branch-grid">{currentSnapshot.branches.map((branch) => <BranchCard branch={branch} key={branch.title} />)}</div></section> : null}
        <section className="dashboard-section"><SectionTitle eyebrow="IN FOCUS" title="近期项目" count={activeProjects.length} />
          <div className="project-list">{activeProjects.slice(0, 5).map((project) => <ProjectCard project={project} key={project.title} />)}</div></section>
      </> : null}
      {tab === "projects" ? <>{currentSnapshot.work_sections.map((section) => <section className="dashboard-section first-section" key={section.label}><SectionTitle eyebrow="WORK" title={section.label} count={section.projects.length} />
        <div className="project-list">{section.projects.map((project) => <ProjectCard project={project} key={project.title} />)}</div></section>)}
        <section className="dashboard-section"><SectionTitle eyebrow="LIFE" title="生活项目" count={currentSnapshot.life_cases.length} />
          <div className="project-list">{currentSnapshot.life_cases.map((project) => <ProjectCard project={project} key={project.title} />)}</div></section></> : null}
      {tab === "values" ? <section className="dashboard-section first-section"><SectionTitle eyebrow="REUSABLE VALUE" title="价值沉淀" count={values.length} />
        <div className="value-feed">{values.map(({ scope, value }, index) => <article className="value-card" key={`${scope}-${value.date}-${index}`}>
          <div className="scope-label">{scope}</div><ValueRow value={value} /></article>)}</div></section> : null}
      {tab === "capabilities" ? <section className="dashboard-section first-section"><SectionTitle eyebrow="CROSS-PROJECT" title="能力域" count={currentSnapshot.capability_domains.length} />
        <div className="capability-grid">{currentSnapshot.capability_domains.map((domain) => <CapabilityCard domain={domain} key={domain.title} />)}</div></section> : null}
      {tab === "recipes" ? <RecipeLibrary recipes={currentSnapshot.recipes || []} /> : null}
      {tab === "archive" ? <section className="dashboard-section first-section"><SectionTitle eyebrow="COMPLETED" title="已完成与归档" count={currentSnapshot.closed_cases.length} />
        <div className="project-list">{currentSnapshot.closed_cases.map((project) => <ProjectCard project={project} key={project.title} />)}</div></section> : null}
      <footer className="page-footer"><span>只读展示 · ChatGPT 来源固定 account‑1 · Codex 独立</span><span>生成时间与日报处理日期分开显示</span></footer>
    </div>
  </main>;
}
