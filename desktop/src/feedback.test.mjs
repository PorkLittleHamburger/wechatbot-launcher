import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import ts from 'typescript';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url);

async function loadPure(name) {
  const source = await readFile(new URL(name, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
const { applyProgress, finishFeedback } = await loadPure('operation-feedback.ts');
const { confirmationBlockReason } = await loadPure('presentation.ts');
const { validateResult } = await loadPure('api-contract.ts');
const batch = key => `00000000-0000-4000-8000-${Buffer.from(key).toString('hex').slice(0, 12).padEnd(12, '0')}`;
const row = (key, operation, id, operationId = batch(key)) => ({ key, operationId, operation, id, label: key, phase: 'start', pending: true, message: '正在处理' });

test('parallel instance progress is isolated by instance ID', () => {
  const rows = [row('a', 'start', 'a'), row('b', 'start', 'b'), row('download', 'version_download', '0.9')];
  const updated = applyProgress(rows, { operation_id: batch('b'), operation: 'start', id: 'b', phase: 'progress', message: 'B启动中', progress: 30 });
  assert.equal(updated[0], rows[0]);
  assert.equal(updated[1].message, 'B启动中');
  assert.equal(updated[2], rows[2]);
});
test('version progress is matched using version ID', () => {
  const rows = [row('v1', 'version_download', '1'), row('v2', 'version_download', '2')];
  const updated = applyProgress(rows, { operation_id: batch('v2'), operation: 'version_download', id: '2', message: '校验中' });
  assert.equal(updated[0], rows[0]);
  assert.equal(updated[1].message, '校验中');
});
test('ambiguous legacy progress never overwrites another concurrent operation', () => {
  const rows = [row('a', 'stop', 'a'), row('b', 'stop', 'b')];
  assert.equal(applyProgress(rows, { operation: 'stop', message: '处理中' }), rows);
});
test('legacy events without operation_id cannot update even a singleton new operation', () => {
  const rows = [row('create', 'create')];
  assert.equal(applyProgress(rows, { operation: 'create', id: null, message: '旧事件' }), rows);
  assert.equal(applyProgress(rows, { operation_id: batch('create'), operation: 'create', id: null, message: '创建中' })[0].message, '创建中');
});
test('backend success waits for complete RPC including Windows autostart transaction', () => {
  const updated = applyProgress([row('settings', 'settings_set')], { operation_id: batch('settings'), operation: 'settings_set', phase: 'success', message: 'Python成功', progress: 100 })[0];
  assert.equal(updated.phase, 'progress');
  assert.equal(updated.pending, true);
  const failed = finishFeedback(updated, 'error', 'Windows自启失败，已回滚');
  assert.equal(failed.phase, 'error');
  assert.equal(failed.progress, undefined);
  assert.equal(failed.pending, false);
});
test('failure and cancellation cannot regress into processing on late events', () => {
  for (const phase of ['error', 'cancelled']) {
    const failed = finishFeedback(row('a', 'start', 'a'), phase, phase);
    assert.equal(applyProgress([failed], { operation_id: batch('a'), operation: 'start', id: 'a', message: '迟到', progress: 40 })[0], failed);
    assert.equal(finishFeedback(failed, 'success', '完成').phase, phase);
  }
});
test('a failed operation does not terminate an unrelated download', () => {
  let rows = [row('stop', 'stop', 'a'), row('download', 'version_download', '1')];
  rows = applyProgress(rows, { operation_id: batch('stop'), operation: 'stop', id: 'a', phase: 'error', message: '失败' });
  rows = rows.map(r => r.key === 'stop' ? finishFeedback(r, 'error', '失败') : r);
  rows = applyProgress(rows, { operation_id: batch('download'), operation: 'version_download', id: '1', phase: 'progress', message: '仍在下载', progress: 60 });
  assert.equal(rows[0].phase, 'error');
  assert.equal(rows[0].progress, undefined);
  assert.equal(rows[1].message, '仍在下载');
});
test('empty RPC success is rejected for each mutation and update operation', () => {
  for (const method of ['start', 'stop', 'restart', 'create', 'update', 'archive', 'backup', 'restore', 'restore_archive', 'upgrade', 'version_remove', 'version_import', 'version_download', 'settings_set', 'launcher_update_check', 'launcher_update_download']) {
    for (const empty of [null, undefined, '', '{}', {}, false]) assert.throws(() => validateResult(method, empty), method);
  }
  validateResult('stop', { ok: true });
  assert.throws(() => validateResult('stop', { ok: false }));
});
test('an explicitly empty release list and no update are valid', () => {
  validateResult('releases', { releases: [] });
  validateResult('launcher_update_check', { current_version: '0.8.7', release: null });
});

// Render-only fixture: no Tauri invocation, process, dialog, registry or network access.
const bundled = await build({
  stdin: { contents: `export { createElement } from 'react'; export { renderToStaticMarkup } from 'react-dom/server'; export { SettingsPage } from './components/SettingsPage'; export { RecoveryPage } from './components/RecoveryPage'; export { InstancesPage } from './components/InstancesPage'; export { OperationFeedback } from './components/OperationFeedback';`, resolveDir: fileURLToPath(new URL('.', import.meta.url)), loader: 'tsx' },
  plugins: [{ name: 'react-node-runtime', setup(build) { build.onResolve({ filter: /^react(?:-dom)?(?:\/|$)/ }, args => ({ path: pathToFileURL(require.resolve(args.path)).href, external: true })); } }],
  bundle: true, write: false, platform: 'node', format: 'esm', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' },
});
const ui = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
const settings = { close_to_tray: false, autostart: false, autostart_hidden: false, restore_instances: false, restore_bridges: false };
const snapshot = { instances: [], versions: [], backups: [], archives: [], settings, diagnostics: [], root: 'D:/fixture', version: '0.8.7' };
const workspace = (patch = {}, pending = new Set()) => ({ snapshot: { ...snapshot, ...patch }, connected: true, busy: pending, mounted: { current: true } });
const render = (component, props) => ui.renderToStaticMarkup(ui.createElement(component, props));

test('damaged backup and archive remain visible with diagnostics and disabled restore', () => {
  const html = render(ui.RecoveryPage, { workspace: workspace({
    backups: [{ name: 'bad.zip', size: 0, invalid: true, error: '备份ZIP损坏' }],
    archives: [{ archive_name: 'bad-folder', name: '损坏归档', id: '', version: '未知', invalid: true, error: '归档JSON损坏' }],
  }) });
  assert.match(html, /bad.zip/); assert.match(html, /bad-folder/);
  assert.match(html, /备份ZIP损坏/); assert.match(html, /归档JSON损坏/);
  assert.equal((html.match(/disabled=""/g) || []).length, 2);
  assert.doesNotMatch(html, /暂无备份|暂无已归档/);
});
test('failed catalog and instance reads are distinct from empty collections', () => {
  const w = workspace({ diagnostics: [{ section: 'backups', message: '读取失败' }, { section: 'instances', message: '读取失败' }] });
  assert.match(render(ui.RecoveryPage, { workspace: w }), /此分区读取存在错误/);
  const html = render(ui.InstancesPage, { workspace: w });
  assert.match(html, /实例列表读取失败/); assert.doesNotMatch(html, /从第一个实例开始/);
});
test('settings corruption disables saving and explains settings.json repair', () => {
  const html = render(ui.SettingsPage, { workspace: workspace({ diagnostics: [{ section: 'settings', message: 'JSON错误' }] }) });
  assert.match(html, /请先修复数据目录中的 settings.json/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>保存设置<\/button>/);
});
test('instance diagnostic disables directory changes despite an empty instance array', () => {
  const html = render(ui.SettingsPage, { workspace: workspace({ diagnostics: [{ section: 'instances', message: '无法读取实例' }] }) });
  assert.match(html, /已禁用数据目录切换和自动更新/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>更改<\/button>/);
});
test('terminal feedback has no stale progress bar; pending feedback cannot be dismissed', () => {
  const failed = finishFeedback(row('a', 'start', 'a'), 'error', '失败');
  const pending = applyProgress([row('b', 'settings_set')], { operation_id: batch('b'), operation: 'settings_set', phase: 'success', message: '后端完成' })[0];
  const html = render(ui.OperationFeedback, { rows: [failed, pending], dismiss() {} });
  assert.doesNotMatch(html, /<progress/);
  assert.equal((html.match(/aria-label="关闭/g) || []).length, 1);
});

test('backend success detail appears only after complete native RPC confirmation', () => {
  const pending = applyProgress([row('settings', 'settings_set')], { operation_id: batch('settings'), operation: 'settings_set', phase: 'success', message: '设置已写入，保留任务管理器禁用选择' })[0];
  assert.equal(pending.detail, '设置已写入，保留任务管理器禁用选择');
  assert.match(pending.message, /等待完整操作确认/);
  const completed = finishFeedback(pending, 'success', '已完成');
  assert.equal(completed.phase, 'success');
  assert.equal(completed.message, pending.detail);
  assert.equal(completed.pending, false);
});
test('cancelled file selection visibly reports cancellation rather than completion', () => {
  const cancelled = finishFeedback(row('version:import', 'version_import'), 'cancelled', '已取消，未执行变更');
  const html = render(ui.OperationFeedback, { rows: [cancelled], dismiss() {} });
  assert.match(html, /已取消/);
  assert.match(html, /未执行变更/);
  assert.doesNotMatch(html, /已完成|· 成功|<progress/);
});

test('late events from every old phase cannot contaminate a successful same-resource retry', () => {
  for (const previousPhase of ['error', 'cancelled']) {
    const previous = finishFeedback(row('restore:backup.zip', 'restore', undefined, batch('old')), previousPhase, '旧请求结束');
    const retry = row(previous.key, 'restore', undefined, batch('new'));
    for (const phase of ['start', 'progress', 'success', 'error', 'cancelled']) {
      const rows = [retry];
      assert.equal(applyProgress(rows, { operation_id: previous.operationId, operation: 'restore', id: null, phase, message: '旧批次晚到事件' }), rows);
      assert.equal(applyProgress(rows, { operation: 'restore', phase, message: '无ID旧事件' }), rows);
    }
    const pending = applyProgress([retry], { operation_id: retry.operationId, operation: 'restore', phase: 'success', message: '本次恢复为新实例' })[0];
    const completed = finishFeedback(pending, 'success', '已完成');
    assert.equal(completed.phase, 'success');
    assert.equal(completed.message, '本次恢复为新实例');
  }
});
test('identical method and resource still require exact operation_id', () => {
  const rows = [row('first', 'start', 'same-instance'), row('second', 'start', 'same-instance')];
  const result = applyProgress(rows, { operation_id: rows[1].operationId, operation: 'start', id: 'same-instance', phase: 'error', message: '仅第二批次失败' });
  assert.equal(result[0], rows[0]);
  assert.equal(result[1].phase, 'error');
  assert.equal(result[1].message, '仅第二批次失败');
});
test('correct batch still waits for native settings failure and ignores late backend success', () => {
  const pending = row('settings', 'settings_set');
  const saved = applyProgress([pending], { operation_id: pending.operationId, operation: 'settings_set', phase: 'success', message: 'Python已写入' })[0];
  assert.equal(saved.phase, 'progress');
  const failure = finishFeedback(saved, 'error', 'native事务失败并已回滚');
  assert.equal(applyProgress([failure], { operation_id: pending.operationId, operation: 'settings_set', phase: 'success', message: '迟到成功' })[0], failure);
});
test('open confirmation reevaluates active states and unblocks only after all instances stop', () => {
  for (const key of ['workspace:change', 'launcher:install']) {
    assert.equal(confirmationBlockReason(snapshot, true, key), '');
    for (const state of ['starting', 'running', 'stopping', 'restarting']) {
      const changed = { ...snapshot, instances: [{ id: 'pending-instance', state }] };
      assert.match(confirmationBlockReason(changed, true, key), /实例状态已变化.*先停止所有实例/);
    }
    assert.equal(confirmationBlockReason({ ...snapshot, instances: [{ id: 'pending-instance', state: 'stopped' }] }, true, key), '');
  }
});
test('confirmation preserves diagnostics and corrupt-item guards while state changes', () => {
  assert.match(confirmationBlockReason({ ...snapshot, diagnostics: [{ section: 'instances', message: '损坏' }] }, true, 'launcher:install'), /实例列表读取失败/);
  assert.match(confirmationBlockReason({ ...snapshot, instances: [{ state: 'unknown' }] }, true, 'workspace:change'), /状态无法确认/);
  assert.match(confirmationBlockReason(snapshot, false, 'workspace:change'), /未连接/);
  assert.match(confirmationBlockReason({ ...snapshot, archives: [{ archive_name: 'a', invalid: true }] }, true, 'archive:a', 'restore_archive'), /归档已损坏/);
  assert.match(confirmationBlockReason({ ...snapshot, backups: [{ name: 'a.zip', invalid: true }] }, true, 'restore:a.zip', 'restore'), /备份已损坏/);
});

const apiBundle = await build({
  stdin: { contents: `export { rpc } from './api'; export { calls } from '@tauri-apps/api/core';`, resolveDir: fileURLToPath(new URL('.', import.meta.url)) },
  plugins: [{ name: 'native-rpc-stub', setup(build) {
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: 'native-stub', namespace: 'test' }));
    build.onLoad({ filter: /.*/, namespace: 'test' }, () => ({ contents: `export const calls = []; export async function invoke(command, args) { calls.push({command, args}); return {ok: true}; }` }));
  } }], bundle: true, write: false, platform: 'node', format: 'esm',
});
const api = await import(`data:text/javascript;base64,${Buffer.from(apiBundle.outputFiles[0].text).toString('base64')}`);
test('RPC presentation metadata is passed explicitly without mutating business params', async () => {
  const params = Object.freeze({ id: 'instance', _operation_id: 'stale' });
  const operationId = batch('rpc');
  await api.rpc('stop', params, operationId);
  assert.deepEqual(api.calls.at(-1), { command: 'rpc', args: { method: 'stop', params: { id: 'instance', _operation_id: operationId } } });
  assert.equal(params._operation_id, 'stale');
  await api.rpc('stop', { id: 'read-or-untracked' });
  assert.deepEqual(api.calls.at(-1).args.params, { id: 'read-or-untracked' });
});


const instance = { id: 'bridge-fixture', name: '恢复测试', mode: 'helper', port: 18080, state: 'running', pid: 123, error: '', url: '', version: '0.8.6' };
const resume = { supported: true, version: 1, enabled: true, account: 'fixture-account', desired: 'running', state: 'waiting_login', error: null };
const renderInstance = bridge_resume => render(ui.InstancesPage, { workspace: workspace({ instances: [{ ...instance, ...(bridge_resume === undefined ? {} : { bridge_resume }) }] }) });

test('settings contract requires a boolean restore_bridges for both reads and saves', () => {
  for (const enabled of [false, true]) {
    const value = { ...settings, restore_bridges: enabled };
    validateResult('settings_set', value);
    validateResult('snapshot', { ...snapshot, settings: value });
  }
  for (const invalid of [undefined, null, 0, 'false', {}, []]) {
    const value = { ...settings, restore_bridges: invalid };
    assert.throws(() => validateResult('settings_set', value));
    assert.throws(() => validateResult('snapshot', { ...snapshot, settings: value }));
  }
});

test('restore bridges requires both prerequisites without changing its saved value', () => {
  for (const autostart of [false, true]) for (const restore_instances of [false, true]) for (const restore_bridges of [false, true]) {
    const html = render(ui.SettingsPage, { workspace: workspace({ settings: { ...settings, autostart, restore_instances, restore_bridges } }) });
    const input = html.match(/<input[^>]*aria-label="开机自启时恢复上次桥接"[^>]*>/)?.[0];
    assert.ok(input);
    assert.equal(input.includes('disabled=""'), !(autostart && restore_instances));
    assert.equal(input.includes('checked=""'), restore_bridges);
    assert.equal(html.includes('当前选项已禁用，已保存的值保持不变'), !(autostart && restore_instances));
  }
  const html = render(ui.SettingsPage, { workspace: { ...workspace(), snapshot: null } });
  assert.doesNotMatch(html.match(/<input[^>]*aria-label="开机自启时恢复上次桥接"[^>]*>/)[0], /checked=""/);
});

test('bridge resume is optional, but present DTOs must be complete and typed', () => {
  const check = value => validateResult('snapshot', { ...snapshot, instances: [{ ...instance, ...value }] });
  check({});
  check({ bridge_resume: resume });
  check({ bridge_resume: { ...resume, version: null, account: null, state: 'future-state', error: 'future diagnostic' } });
  for (const invalid of [null, {}, false, { ...resume, supported: 1 }, { ...resume, enabled: 'true' }, { ...resume, version: '1' }, { ...resume, version: NaN }, { ...resume, account: 123 }, { ...resume, desired: null }, { ...resume, state: null }, { ...resume, error: 123 }]) {
    assert.throws(() => check({ bridge_resume: invalid }));
  }
});

test('management running without a bridge request never claims bridge readiness or unsupported capability', () => {
  const html = renderInstance(undefined);
  assert.match(html, /管理服务：运行中/);
  assert.match(html, /桥接恢复：未请求/);
  assert.match(html, /WebUI 手动启动桥接/);
  assert.doesNotMatch(html, /桥接运行中|不支持桥接恢复|后端已确认桥接/);
});

test('all bridge states have distinct feedback and errors remain visible', () => {
  for (const [state, label] of Object.entries({ disabled: '桥接恢复：未请求', waiting_login: '等待微信登录', no_target: '没有可恢复的桥接', starting: '桥接启动中', stopping: '桥接停止中', running: '桥接运行中', stopped: '桥接已停止', failed: '桥接恢复失败', unsupported: '不支持桥接恢复' })) {
    const html = renderInstance({ ...resume, state, error: state === 'failed' ? '认证 health 读取失败' : null });
    assert.match(html, new RegExp(label));
    assert.match(html, /管理服务：运行中/);
    if (state === 'failed') assert.match(html, /认证 health 读取失败/);
    if (state !== 'running') assert.doesNotMatch(html, /桥接运行中|后端已确认桥接正在运行/);
  }
  const unknown = renderInstance({ ...resume, state: 'future-state', error: '未知协议版本' });
  assert.match(unknown, /桥接状态未知/);
  assert.match(unknown, /future-state/);
  assert.match(unknown, /未知协议版本/);
  assert.match(renderInstance({ ...resume, state: 'failed' }), /后端未提供失败原因/);
  assert.match(renderInstance({ ...resume, state: 'future-state' }), /后端未提供错误说明/);
  const unsupported = renderInstance({ ...resume, supported: false, state: 'unsupported', enabled: false, error: '请升级运行版本' });
  assert.match(unsupported, /不支持桥接恢复/);
  assert.match(unsupported, /请升级运行版本/);
  assert.match(unsupported, /未启用桥接自动恢复/);
  assert.doesNotMatch(unsupported, /桥接运行中/);
});

test('success feedback for start and restart still requires manually starting the bridge', () => {
  for (const operation of ['start', 'restart']) {
    const pending = row('instance', operation, 'instance');
    for (const detail of [undefined, '实例管理服务已就绪；微信桥接需在 WebUI 手动启动']) {
      const completed = finishFeedback({ ...pending, detail }, 'success', '已完成');
      assert.match(completed.message, /管理服务已就绪/);
      assert.match(completed.message, /WebUI 手动启动/);
      assert.doesNotMatch(completed.message, /桥接已就绪|桥接运行中/);
    }
    assert.equal(finishFeedback(pending, 'error', 'health失败').message, 'health失败');
    assert.equal(finishFeedback(pending, 'cancelled', '已取消').message, '已取消');
  }
});


// Exercise component handlers and rerenders with local hook state and a native RPC stub.
const interactionBundle = await build({
  stdin: { contents: `export { SettingsPage } from './components/SettingsPage'; export { InstancesPage } from './components/InstancesPage'; export { beginRender, reset } from 'react'; export { calls, setFailure } from '@tauri-apps/api/core';`, resolveDir: fileURLToPath(new URL('.', import.meta.url)) },
  plugins: [{ name: 'isolated-component-state', setup(build) {
    build.onResolve({ filter: /^react$/ }, () => ({ path: 'hooks', namespace: 'fixture' }));
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: 'native', namespace: 'fixture' }));
    build.onResolve({ filter: /^react\// }, args => ({ path: pathToFileURL(require.resolve(args.path)).href, external: true }));
    build.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents: args.path === 'hooks' ? `
      let values = [], cursor = 0;
      export function beginRender() { cursor = 0; }
      export function reset() { values = []; cursor = 0; }
      export function useState(initial) { const index = cursor++; if (!(index in values)) values[index] = initial; return [values[index], next => { values[index] = typeof next === 'function' ? next(values[index]) : next; }]; }
    ` : `
      export const calls = []; let failure = false;
      export function setFailure(value) { failure = value; }
      export async function invoke(command, args) { calls.push({command, args}); if (failure) throw new Error('settings write failed'); return args.params; }
    ` }));
  } }], bundle: true, write: false, platform: 'node', format: 'esm', jsx: 'automatic',
});
const interactive = await import(`data:text/javascript;base64,${Buffer.from(interactionBundle.outputFiles[0].text).toString('base64')}`);
function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...elements(tree.props?.children)];
}
const paint = (component, props) => { interactive.beginRender(); return component(props); };
const control = (tree, title) => elements(tree).find(e => e.type === 'input' && e.props['aria-label'] === title);

test('settings interactions preserve restore choice across prerequisite changes, polling, failed save and retry', async () => {
  interactive.reset();
  interactive.setFailure(false);
  let work;
  const w = workspace({ settings: { ...settings, autostart: true, restore_instances: true, restore_bridges: true } });
  w.setSnapshot = change => { w.snapshot = change(w.snapshot); };
  w.run = (_key, _label, job) => { work = job('fixture-operation').catch(() => false); return work; };
  const props = { workspace: w };
  let tree = paint(interactive.SettingsPage, props);
  control(tree, '开机启动').props.onChange({ target: { checked: false } });
  tree = paint(interactive.SettingsPage, props);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.disabled, true);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.checked, true);
  // A background snapshot must not discard the user's unsaved draft.
  w.snapshot = { ...w.snapshot, settings: { ...settings } };
  tree = paint(interactive.SettingsPage, props);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.checked, true);
  control(tree, '开机启动').props.onChange({ target: { checked: true } });
  tree = paint(interactive.SettingsPage, props);
  control(tree, '恢复上次运行的实例').props.onChange({ target: { checked: false } });
  tree = paint(interactive.SettingsPage, props);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.disabled, true);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.checked, true);
  const save = tree => elements(tree).find(e => e.type === 'button' && e.props.children === '保存设置');
  interactive.setFailure(true);
  save(tree).props.onClick(); await work;
  tree = paint(interactive.SettingsPage, props);
  assert.equal(control(tree, '开机自启时恢复上次桥接').props.checked, true);
  assert.equal(save(tree).props.disabled, false);
  interactive.setFailure(false);
  save(tree).props.onClick(); await work;
  const params = interactive.calls.at(-1).args.params;
  assert.equal(params.restore_bridges, true);
  assert.equal(params.restore_instances, false);
  assert.equal(params.autostart, true);
  assert.equal(params.autostart_explicit, true);
  tree = paint(interactive.SettingsPage, props);
  assert.equal(save(tree).props.disabled, true);
  assert.equal(w.snapshot.settings.restore_bridges, true);
});

test('new snapshots replace bridge state and errors for the same instance without a restart', () => {
  interactive.reset();
  const w = workspace();
  for (const [state, error, expected] of [['waiting_login', null, '等待微信登录'], ['starting', null, '桥接启动中'], ['running', null, '桥接运行中'], ['failed', 'health认证失败', '桥接恢复失败'], ['running', null, '桥接运行中']]) {
    w.snapshot = { ...w.snapshot, instances: [{ ...instance, bridge_resume: { ...resume, state, error } }] };
    const html = ui.renderToStaticMarkup(paint(interactive.InstancesPage, { workspace: w }));
    assert.match(html, new RegExp(expected));
    if (error) assert.match(html, /health认证失败/);
    else assert.doesNotMatch(html, /health认证失败/);
    if (state !== 'waiting_login') assert.doesNotMatch(html, /等待微信登录/);
    if (state !== 'running') assert.doesNotMatch(html, /桥接运行中/);
  }
  w.snapshot = { ...w.snapshot, instances: [instance] };
  const html = ui.renderToStaticMarkup(paint(interactive.InstancesPage, { workspace: w }));
  assert.match(html, /桥接恢复：未请求/);
  assert.doesNotMatch(html, /桥接运行中|health认证失败/);
});


test('disabled means no resume request, while manual bridge running remains visible', () => {
  const disabled = renderInstance({ ...resume, enabled: false, state: 'disabled' });
  assert.match(disabled, /未请求自动恢复桥接/);
  assert.doesNotMatch(disabled, /桥接状态未知|不支持桥接恢复|桥接运行中/);
  const running = renderInstance({ ...resume, enabled: false, state: 'running' });
  assert.match(running, /桥接运行中/);
  assert.doesNotMatch(running, /未请求自动恢复桥接|桥接状态未知/);
});

test('temporary health read failure stays failed even when support is unconfirmed', () => {
  const error = '健康读取失败：认证请求超时，请刷新后重试';
  const bridge_resume = { ...resume, supported: false, version: null, state: 'failed', error };
  validateResult('snapshot', { ...snapshot, instances: [{ ...instance, bridge_resume }] });
  const html = renderInstance(bridge_resume);
  assert.match(html, /桥接恢复失败/);
  assert.ok(html.includes(error));
  assert.doesNotMatch(html, /不支持桥接恢复|桥接运行中/);
});

test('unsupported displays backend upgrade and instance switch advice without competing instructions', () => {
  const error = '所选版本不支持恢复；请导入支持此能力的新 Bot 版本并切换实例';
  const html = renderInstance({ ...resume, supported: false, version: null, state: 'unsupported', error });
  assert.match(html, /不支持桥接恢复/);
  assert.ok(html.includes(error));
  assert.doesNotMatch(html, /当前运行版本不支持自动恢复，请打开 WebUI/);
});
