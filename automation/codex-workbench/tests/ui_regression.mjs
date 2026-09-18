import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const appJs = readFileSync(0, 'utf8');
const classList = () => ({ add() {}, remove() {}, toggle() {} });
const node = () => ({
  value: '', innerHTML: '', textContent: '', hidden: true, classList: classList(),
  setAttribute() {}, querySelectorAll: () => [], focus() {}, showModal() {}, close() {},
});
const nodes = new Map();
for (const id of [
  '#projects', '#outcomes', '#detail', '#search', '#status-filter', '#sort', '#refresh',
  '#manager', '#manager-close', '#manage', '#join-preview', '#project-refresh', '#detail-close',
  '#filters-toggle', '#filters-popover', '#quota-toggle', '#quota-popover', '#quota',
  '#quota-summary', '#runtime', '#project-count', '#active-members', '#archived-members',
]) nodes.set(id, node());
const pending = [];
const context = vm.createContext({
  document: {
    hidden: false,
    querySelector: id => nodes.get(id),
    querySelectorAll: () => [],
    addEventListener() {},
  },
  window: { addEventListener() {} },
  setInterval() {},
  fetch: url => new Promise(resolve => pending.push({ url, resolve })),
  navigator: { clipboard: { writeText: async () => {} } },
  URLSearchParams,
});
vm.runInContext(appJs, context);

const outcome = (id, title, status, depends_on = [], next_action = '继续检查') => ({
  id, title, status, depends_on, priority: 'normal',
  checkpoint: { last_result: '检查点', next_action, next_action_basis: 'existing_scope', remaining: [] },
  acceptance: [], evidence: [],
});
const first = { project_id: 'first-project', title: '甲项目', goal: '甲目标', revision: 1,
  updated_at: '2026-09-17T00:00:00Z', focus_outcome_id: 'first-outcome',
  outcomes: [outcome('first-outcome', '甲成果', 'in_progress')] };
const second = { project_id: 'second-project', title: '乙项目', goal: '乙目标', revision: 1,
  updated_at: '2026-09-17T00:00:00Z', focus_outcome_id: 'second-outcome',
  outcomes: [outcome('second-outcome', '乙成果', 'not_started')] };

context.fixtures = [first, second];
vm.runInContext("projects=fixtures;selectedProjectId='first-project';selectedOutcomeId='first-outcome';render()", context);
nodes.get('#detail').innerHTML = '<h2>甲成果</h2>';
vm.runInContext("selectProject('second-project')", context);
assert.equal(vm.runInContext('selectedOutcomeId', context), null);
assert.ok(!nodes.get('#detail').innerHTML.includes('甲成果'), 'switching projects must clear old detail');
assert.ok(nodes.get('#outcomes').innerHTML.includes('乙成果'));
assert.ok(nodes.get('#outcomes').innerHTML.includes('做到这里'));
assert.ok(nodes.get('#outcomes').innerHTML.includes('下一步'));
assert.ok(!nodes.get('#outcomes').innerHTML.includes('甲成果'));

vm.runInContext("selectedProjectId='first-project';selectedOutcomeId='first-outcome';projects=fixtures;render()", context);
nodes.get('#detail').innerHTML = '<h2>甲成果</h2>';
context.filtered = [{ ...first, outcomes: [] }, second];
vm.runInContext('projects=filtered;render()', context);
assert.equal(vm.runInContext('selectedOutcomeId', context), null);
assert.ok(!nodes.get('#detail').innerHTML.includes('甲成果'), 'filtering an outcome must clear its detail');

vm.runInContext("projects=fixtures;selectedProjectId='first-project';selectedOutcomeId=null;render()", context);
const detailPromise = vm.runInContext("detail('first-project','first-outcome')", context);
vm.runInContext("selectProject('second-project')", context);
const request = pending.find(item => String(item.url).includes('/api/projects/first-project/outcomes/first-outcome'));
assert.ok(request);
request.resolve({ ok: true, json: async () => ({ outcome: first.outcomes[0], resume_note: 'resume' }) });
await detailPromise;
assert.ok(!nodes.get('#detail').innerHTML.includes('甲成果'), 'late detail response must not restore the old project');

context.compactFixture = { ...first, focus_outcome_id: 'active', outcomes: [
  outcome('active', '当前成果', 'in_progress', [], '完成精简界面'),
  outcome('paused', '暂停成果', 'paused'),
  outcome('review', '待验收成果', 'pending_review'),
  outcome('done', '历史成果', 'done', [], null),
] };
const panel = vm.runInContext('taskPanel(compactFixture)', context);
assert.match(panel, /当前任务/);
assert.match(panel, /完成精简界面/);
assert.match(panel, /历史成果/);
assert.ok(!panel.includes('更早完成的成果'), 'one completed outcome stays visible as the recent result');
assert.match(panel, /其他成果/);
assert.ok(panel.indexOf('复制接续说明') < panel.indexOf('查看详情'), 'primary action follows the approved mockup');
context.compactHistory = { ...context.compactFixture, outcomes: [...context.compactFixture.outcomes, outcome('older', '更早成果', 'done', [], null)] };
const panelWithHistory = vm.runInContext('taskPanel(compactHistory)', context);
assert.match(panelWithHistory, /更早完成的成果/);
assert.match(panelWithHistory, /更早成果/);
for (const removed of ['推荐下一步', '可能需要你决定', '中断风险与健康', '最近可验证变化']) {
  assert.ok(!panel.includes(removed), `compact panel must omit ${removed}`);
}
assert.match(appJs, /setInterval\(checkRevisions,10000\)/);
assert.match(appJs, /not_started:'待开始'/);
assert.match(appJs, /silicon-cortex-workbench/);
console.log('UI regression passed: compact focus, scoped project state, drawer safety, and revision polling');
