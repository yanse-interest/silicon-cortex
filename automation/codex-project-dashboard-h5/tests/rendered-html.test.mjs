import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} });
}

test("renders the private project dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store, max-age=0");
  const html = await response.text();
  assert.match(html, /ChatGPT \+ Codex 项目看板/);
  assert.match(html, /项目进度与/);
  assert.match(html, /价值沉淀/);
  assert.match(html, /项目文件夹/);
  assert.match(html, /已落库证据的只读整合/);
  assert.match(html, /正在读取最新落库数据/);
  assert.match(html, /日报处理至/);
  assert.match(html, /不代表底层证据完整/);
  assert.match(html, /本次日报带来的变化/);
  assert.match(html, /已处理/);
  assert.match(html, /ChatGPT 来源固定 account‑1 · Codex 独立/);
  assert.doesNotMatch(html, /\/Users\//);
  assert.doesNotMatch(html, /dashboard-token|api\/case|api\/event/);
});

test("publishes only source-grounded capability answers", async () => {
  const snapshot = JSON.parse(await readFile(new URL("../public/dashboard-snapshot.json", import.meta.url), "utf8"));
  const items = snapshot.capability_domains.flatMap((domain) => domain.items);
  const grounded = items.filter((item) => item.answer_status === "source_grounded");
  const pending = items.filter((item) => item.answer_status === "needs_source_review");
  assert.equal(grounded.length + pending.length, items.length);
  assert.ok(grounded.length >= 1);
  assert.ok(grounded.every((item) => item.answer && item.evidence_refs.length));
  assert.ok(pending.every((item) => !item.answer && !item.detail && item.evidence_refs.length === 0));
  assert.doesNotMatch(JSON.stringify(snapshot), /chatgpt\.com\/c\/|\/Users\/|raw\/conversations|source_locator|excerpt/);
  assert.equal(snapshot.schema_version, 3);
  assert.ok(Array.isArray(snapshot.work_sections));
  assert.equal("work_groups" in snapshot, false);
  assert.equal("categories" in snapshot, false);
});

test("client requests only the same-origin read-only fresh snapshot", async () => {
  const client = await readFile(new URL("../app/dashboard-client.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(client, /fetch\("\/api\/fresh-snapshot"/);
  assert.match(client, /cache: "no-store"/);
  assert.match(client, /日报与任务证据/);
  assert.match(client, /log\.title/);
  assert.match(client, /log\.source === "Codex" \|\| log\.source === "ChatGPT"/);
  assert.match(client, /人工记录/);
  assert.match(client, /日报进展/);
  assert.match(client, /人工维护摘要/);
  assert.match(client, /实时生成失败 · 正在使用上次验证快照/);
  assert.doesNotMatch(client, /8791|dashboard-token|Authorization|POST|PUT|PATCH|DELETE/);
  assert.match(css, /overflow-x: hidden/);
  assert.match(css, /overflow-wrap: anywhere/);
});

test("recipe cards use the fixed ingredients-before-method template", async () => {
  const client = await readFile(new URL("../app/dashboard-client.tsx", import.meta.url), "utf8");
  assert.match(client, /食材用量/);
  assert.match(client, /具体做法/);
  assert.ok(client.indexOf("食材用量") < client.indexOf("具体做法"));
  assert.match(client, /用量待补充/);
  assert.match(client, /package_spec/);
});

test("global value feed includes closed cases and remains newest-first", async () => {
  const client = await readFile(new URL("../app/dashboard-client.tsx", import.meta.url), "utf8");
  assert.match(client, /currentSnapshot\.closed_cases\.forEach/);
  assert.match(client, /b\.value\.date\.localeCompare\(a\.value\.date\)/);
});
