const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown) => typeof value === 'string' && value.length > 0;
const settingsValid = (value: Record<string, unknown>) => ['close_to_tray', 'autostart', 'autostart_hidden', 'restore_instances', 'restore_bridges'].every(key => typeof value[key] === 'boolean');

const bridgeResumeValid = (value: unknown) => record(value) &&
  typeof value.supported === 'boolean' && typeof value.enabled === 'boolean' &&
  (value.version === null || typeof value.version === 'number' && Number.isFinite(value.version)) &&
  (value.account === null || typeof value.account === 'string') &&
  typeof value.desired === 'string' && typeof value.state === 'string' &&
  (value.error === null || typeof value.error === 'string');

// Rust returns serde_json::Value. Validate at this boundary, without decoding strings again.
export function validateResult(method: string, result: unknown): void {
  let valid = false;
  if (method === 'versions_list') valid = Array.isArray(result) && result.every(v => record(v) && text(v.version) && text(v.path));
  else if (record(result)) {
    switch (method) {
      case 'snapshot': valid = (result.diagnostics === undefined || Array.isArray(result.diagnostics) && result.diagnostics.every(d => record(d) && text(d.section) && typeof d.message === 'string')) && ['instances', 'versions', 'backups', 'archives'].every(key => Array.isArray(result[key])) && Array.isArray(result.instances) && result.instances.every(i => record(i) && (i.bridge_resume === undefined || bridgeResumeValid(i.bridge_resume))) && record(result.settings) && settingsValid(result.settings) && text(result.root) && text(result.version); break;
      case 'settings_set': valid = settingsValid(result); break;
      case 'releases': valid = Array.isArray(result.releases); break;
      case 'logs': valid = typeof result.text === 'string'; break;
      case 'credentials': valid = text(result.token); break;
      case 'create': case 'restore': case 'restore_archive': valid = text(result.id) && text(result.name) && text(result.version); break;
      case 'backup': valid = text(result.name); break;
      case 'version_import': case 'version_download': valid = text(result.version) && text(result.path) && result.compatible === true; break;
      case 'launcher_update_check': valid = text(result.current_version) && (result.release === null || record(result.release) && text(result.release.version) && typeof result.release.notes === 'string' && text(result.release.url)); break;
      case 'launcher_update_download': valid = text(result.version) && text(result.path) && typeof result.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(result.sha256) && typeof result.size === 'number' && result.size > 0; break;
      case 'upgrade': valid = result.ok === true && text(result.version); break;
      case 'start': case 'stop': case 'restart': case 'update': case 'archive': case 'version_remove': valid = result.ok === true; break;
    }
  }
  if (!valid) throw new Error(`${method} 返回结果不完整或格式错误，无法确认操作成功；请刷新状态后重试。`);
}
