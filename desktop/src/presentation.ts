import type { Instance, Snapshot } from './types';
export const statusNames: Record<string, string> = { running: '运行中', stopped: '已停止', starting: '启动中', stopping: '停止中', restarting: '重启中', failed: '异常', error: '异常', created: '待启动' };
export const active = (i: Instance) => ['running', 'starting', 'stopping', 'restarting'].includes(i.state);
export const sizeOf = (size: number) => !Number.isFinite(size) ? '大小未知' : size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;

export function confirmationBlockReason(snapshot: Snapshot | null, connected: boolean, key: string, operation?: string): string {
  if (!connected || !snapshot) return '本地服务未连接，请刷新后重试。';
  if (key === 'workspace:change' || key === 'launcher:install') {
    if (snapshot.diagnostics?.some(d => d.section === 'instances')) return '实例列表读取失败，无法确认活动实例状态。请先修复并刷新。';
    if (snapshot.instances.some(active)) return '实例状态已变化：请先停止所有实例，再确认目录切换或自动更新。';
    if (snapshot.instances.some(i => !['stopped', 'created', 'error', 'failed'].includes(i.state))) return '实例状态无法确认，请刷新并确认所有实例已停止。';
  }
  if (operation === 'restore_archive' && snapshot.archives.some(a => `archive:${a.archive_name}` === key && a.invalid)) return '此归档已损坏，禁止恢复。请查看归档诊断。';
  if (operation === 'restore' && snapshot.backups.some(b => `restore:${b.name}` === key && b.invalid)) return '此备份已损坏，禁止恢复。请查看备份诊断。';
  return '';
}
