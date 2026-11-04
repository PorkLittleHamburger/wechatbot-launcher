import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { rpc } from '../api';
import { active, statusNames } from '../presentation';
import type { Instance } from '../types';
import type { Workspace } from '../useWorkspace';
import type { Confirm } from '../ui-types';
interface Props {
  workspace: Workspace; confirm: Confirm;
  showEditor: (instance: Instance) => void; credentials: (instance: Instance) => Promise<void>;
  instanceAction: (instance: Instance, method: string, label: string) => Promise<boolean>;
  onLogs: (id: string) => void; onUpgrade: (instance: Instance) => void; onVersions: () => void;
}
export function InstancesPage({ workspace, confirm, showEditor, credentials, instanceAction, onLogs, onUpgrade, onVersions }: Props) {
  const { snapshot, connected, busy, run } = workspace;
  const instances = snapshot?.instances ?? [], versions = snapshot?.versions ?? [];
  const instancesReadable = !!snapshot && !snapshot.diagnostics?.some(d => d.section === 'instances');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  return <>
          <section className="metrics" aria-label="工作空间概况">{[['全部实例', instancesReadable ? instances.length : '—', '▦'], ['运行中', instancesReadable ? instances.filter(i => i.state === 'running').length : '—', '▷'], ['已安装版本', snapshot ? versions.length : '—', '◇']].map(([label, value, icon]) => <div className="metric" key={label}><div><small>{label}</small><b>{value}</b></div><span aria-hidden="true">{icon}</span></div>)}</section>
          <div className="section-heading"><h2>我的实例</h2><span className="muted">{instancesReadable ? `${instances.length} 个独立工作空间` : '实例数量暂不可用'}</span></div>
          {snapshot && !versions.length && <div className="alert info"><span>先安装一个运行版本，再创建你的第一个实例。</span><button onClick={onVersions}>前往版本管理</button></div>}
          <div className="filters"><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索实例名称或端口…" aria-label="搜索实例" /><select aria-label="筛选实例状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部状态</option><option value="running">运行中</option><option value="stopped">已停止</option><option value="error">异常</option></select></div>
          <div className="instance-grid">{instances.filter(i => (i.name.toLowerCase().includes(search.toLowerCase()) || String(i.port).includes(search)) && (filter === 'all' || (filter === 'error' ? !!i.error || ['error', 'failed'].includes(i.state) : i.state === filter))).map(i => {
            const locked = !connected || busy.has(i.id), stoppedOnly = locked || active(i);
            return <article className="instance-card" key={i.id} aria-busy={locked}>
              <div className="card-body"><div className="card-heading"><div className="identity"><span className="instance-icon" aria-hidden="true">{i.mode === 'mock' ? '◇' : '◈'}</span><div><h3>{i.name}</h3><small>{i.mode === 'mock' ? 'MOCK · 模拟模式' : 'HELPER · 微信桥接'}</small></div></div><span className={`badge ${i.state === 'running' ? 'running' : i.error || ['error', 'failed'].includes(i.state) ? 'failed' : ''}`}>{busy.has(i.id) ? '处理中…' : statusNames[i.state] ?? i.state}</span></div>
              <dl className="facts"><div><dt>WebUI 端口</dt><dd>{i.port}</dd></div><div><dt>运行版本</dt><dd>{i.version || '—'}</dd></div><div><dt>进程 PID</dt><dd>{i.pid ?? '—'}</dd></div><div><dt>运行模式</dt><dd>{i.mode}</dd></div></dl>
              {(busy.has(i.id) || active(i)) && <p className="muted">{busy.has(i.id) ? '实例操作正在执行，请等待上方操作反馈。' : '编辑、备份、升级和归档需要先停止实例。'}</p>}
              {i.error && <p className="instance-error">{i.error}</p>}
              <div className="card-actions"><button className="primary" title={i.state !== 'running' ? '请先启动实例' : locked ? '等待连接或实例操作完成' : '打开实例管理页面'} disabled={locked || i.state !== 'running'} onClick={() => void run(i.id, '打开实例 WebUI', () => invoke('open_instance', { instanceId: i.id }))}>打开 WebUI ↗</button><button title={locked ? '等待连接或此实例操作完成' : ['starting', 'stopping', 'restarting'].includes(i.state) ? '实例正在切换状态，请稍候' : '执行实例操作'} disabled={locked || ['starting', 'stopping', 'restarting'].includes(i.state)} onClick={() => void instanceAction(i, i.state === 'running' ? 'stop' : 'start', i.state === 'running' ? '停止' : '启动')}>{i.state === 'running' ? '停止' : '启动'}</button><button title={locked ? '等待连接或此实例操作完成' : ['starting', 'stopping', 'restarting'].includes(i.state) ? '实例正在切换状态，请稍候' : '执行实例操作'} disabled={locked || ['starting', 'stopping', 'restarting'].includes(i.state)} onClick={() => void instanceAction(i, 'restart', '重启')}>重启</button></div></div>
              <div className="card-footer"><button disabled={locked} onClick={() => { onLogs(i.id); }}>日志</button><button disabled={stoppedOnly} title={active(i) ? '请先停止实例' : undefined} onClick={() => showEditor(i)}>编辑</button><button title={active(i) ? '请先停止实例再备份' : locked ? '等待连接或实例操作完成' : '创建实例备份'} disabled={stoppedOnly} onClick={() => void instanceAction(i, 'backup', '备份')}>备份</button><button title={active(i) ? '请先停止实例再升级' : !versions.some(v => v.version !== i.version) ? '请先安装其他运行版本' : '切换运行版本'} disabled={stoppedOnly || !versions.some(v => v.version !== i.version)} onClick={() => { onUpgrade(i); }}>升级</button><button disabled={locked} onClick={() => void credentials(i)}>首次登录凭据</button><button className="danger" disabled={stoppedOnly} onClick={() => confirm('归档实例', `确定归档“${i.name}”吗？归档后可在“备份恢复”中恢复。`, i.id, operationId => rpc('archive', { id: i.id }, operationId), { operation: 'archive', id: i.id })}>归档</button></div>
            </article>;
          })}</div>
          {!instances.length && <div className="empty"><span>◇</span><h3>{snapshot?.diagnostics?.some(d => d.section === 'instances') ? '实例列表读取失败' : !connected ? '正在连接工作空间…' : '从第一个实例开始'}</h3><p>{snapshot?.diagnostics?.some(d => d.section === 'instances') ? '当前列表不可用，请查看上方诊断并修复后刷新。' : !connected ? '连接成功后将在此显示实例。' : '安装版本后，创建一个 Helper 实例开始体验。'}</p></div>}
          {!!instances.length && !instances.some(i => (i.name.toLowerCase().includes(search.toLowerCase()) || String(i.port).includes(search)) && (filter === 'all' || (filter === 'error' ? !!i.error || ['error', 'failed'].includes(i.state) : i.state === filter))) && <div className="empty">没有匹配的实例，请调整搜索条件。</div>}
        </>;
}