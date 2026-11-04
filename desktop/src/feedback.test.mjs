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
const settings = { close_to_tray: false, autostart: false, autostart_hidden: false, restore_instances: false };
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
