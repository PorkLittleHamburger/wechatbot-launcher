import { useState } from 'react';
import { rpc } from '../api';
import { displayPath } from '../path-display';
import { sizeOf } from '../presentation';
import type { InstalledVersion, Release } from '../types';
import type { Workspace } from '../useWorkspace';
import type { Confirm } from '../ui-types';
export function VersionsPage({ workspace, confirm }: { workspace: Workspace; confirm: Confirm }) {
  const { snapshot, busy, run, setSnapshot, mounted, connected, feedback } = workspace;
  const [releases, setReleases] = useState<Release[] | null>(null);
  const versions = snapshot?.versions ?? [], instances = snapshot?.instances ?? [];
  const versionBusy = !connected || [...busy].some(k => k.startsWith('version:'));
  const loadReleases = (): Promise<boolean> => run('releases', '获取可用版本', async () => {
    const data = await rpc<{ releases: Release[] }>('releases');
    if (mounted.current) setReleases(data.releases);
  }, () => { void loadReleases(); });
  const downloadVersion = (release: Release): Promise<boolean> => run(`version:${release.version}`, `下载 ${release.version}`, operationId => rpc('version_download', { version: release.version }, operationId), () => { void downloadVersion(release); }, { operation: 'version_download', id: release.version });
  const listVersions = () => run('version:list', '刷新已安装版本', async () => {
    const installed = await rpc<InstalledVersion[]>('versions_list');
    if (mounted.current) setSnapshot(old => old ? { ...old, versions: installed } : old);
  });
  return <>
          <section className="panel"><div className="section-heading"><div><h2>已安装版本 <span className="badge">{versions.length}</span></h2><p className="muted">创建实例时可选择不同版本；升级前请先停止实例。</p></div><button disabled={versionBusy} onClick={() => void listVersions()}>{busy.has('version:list') ? '读取中…' : '↻ 刷新已安装'}</button></div>{!versions.length ? <div className="empty compact">{!snapshot ? '正在读取已安装版本…' : snapshot.diagnostics?.some(d => d.section === 'versions') ? '版本读取失败，请查看诊断并修复后刷新。' : !connected ? '连接中断，无法确认已安装版本。' : '尚未安装版本。可导入本地 ZIP，或从下方下载可用版本。'}</div> : versions.map(v => <div className="list-row" key={v.version}><div className="row-copy"><strong>{v.version}</strong><small className="mono" title={displayPath(v.path)}>{displayPath(v.path)}</small><small>{instances.filter(i => i.version === v.version).length} 个实例正在使用此版本</small></div><button className="danger" disabled={versionBusy || instances.some(i => i.version === v.version) || (snapshot?.archives ?? []).some(i => i.version === v.version)} title="实例或归档仍使用的版本不能移除" onClick={() => confirm('移除版本', `确定移除已安装版本 ${v.version} 吗？`, `version:${v.version}`, operationId => rpc('version_remove', { version: v.version }, operationId), { operation: 'version_remove', id: v.version })}>移除</button></div>)}</section>
          <section className="panel"><div className="section-heading"><div><h2>可用发布版本</h2><p className="muted">兼容性由本地服务检查，下载失败后可以重试。</p></div><button disabled={!connected || busy.has('releases')} onClick={() => void loadReleases()}>{busy.has('releases') ? '获取中…' : releases ? '↻ 重新获取' : '获取版本列表'}</button></div>{releases === null ? <div className="empty compact">{busy.has('releases') ? '正在获取发布版本列表…' : feedback.some(row => row.key === 'releases' && row.phase === 'error') ? '获取失败，请查看操作诊断并重新获取。' : '点击“获取版本列表”查看可安装的发布版本。'}</div> : !releases.length ? <div className="empty compact">暂未找到可用发布版本，可稍后重试或导入本地 ZIP。</div> : releases.map(r => <div className="list-row" key={r.version}><div className="row-copy"><strong>{r.name || r.version} <span className={`badge ${r.compatible ? 'running' : 'failed'}`}>{r.compatible ? '兼容' : '不兼容'}</span></strong><small>{r.version} · {r.published_at ? r.published_at.slice(0, 10) : '发布日期未知'} · {sizeOf(r.size)}</small>{!r.compatible && <small className="danger">{r.reason || '当前系统或版本不满足安装条件。'}</small>}</div><button className="primary" title={!r.compatible ? r.reason || '此版本不兼容' : versions.some(v => v.version === r.version) ? '已安装此版本' : versionBusy ? '等待版本操作完成或服务连接' : '下载、校验并安装此版本'} disabled={!r.compatible || versionBusy || versions.some(v => v.version === r.version)} onClick={() => void downloadVersion(r)}>{versions.some(v => v.version === r.version) ? '已安装' : busy.has(`version:${r.version}`) ? '下载中…' : '下载并安装'}</button></div>)}</section>
        </>;
}