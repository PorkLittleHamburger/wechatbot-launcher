import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { open } from '@tauri-apps/plugin-dialog';
import { version as desktopVersion } from '../package.json';
import { messageOf, rpc } from './api';
import { displayPath } from './path-display';
import type { Archive, InstalledVersion, Instance, OperationProgress, Release, Settings, Snapshot } from './types';

type Page = '实例管理' | '版本管理' | '备份恢复' | '日志' | '设置';
type ErrorEntry = { id: number; label: string; message: string; retry?: () => void };
type ModalState = { kind: 'editor'; instance?: Instance } | { kind: 'upgrade'; instance: Instance }
  | { kind: 'confirm'; title: string; text: string; key: string; action: () => Promise<unknown> }
  | { kind: 'credentials'; name: string; token: string };
const pages: [Page, string][] = [['实例管理', '▦'], ['版本管理', '◇'], ['备份恢复', '▤'], ['日志', '≡'], ['设置', '⚙']];
const defaultSettings: Settings = { close_to_tray: true, autostart: false, autostart_hidden: false, restore_instances: false };
const statusNames: Record<string, string> = { running: '运行中', stopped: '已停止', starting: '启动中', stopping: '停止中', restarting: '重启中', failed: '异常', error: '异常', created: '待启动' };
const active = (i: Instance) => ['running', 'starting', 'stopping', 'restarting'].includes(i.state);
const sizeOf = (size: number) => !Number.isFinite(size) ? '大小未知' : size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;

function Modal({ title, busy, close, children }: { title: string; busy: boolean; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; d?.showModal(); return () => { d?.close(); }; }, []);
  return <dialog ref={ref} onCancel={e => { e.preventDefault(); if (!busy) close(); }} aria-labelledby="modal-title">
    <div className="modal-head"><h2 id="modal-title">{title}</h2><button type="button" className="icon-button" disabled={busy} onClick={close} aria-label="关闭">×</button></div>{children}
  </dialog>;
}

export default function App() {
  const [page, setPage] = useState<Page>('实例管理');
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const locks = useRef(new Set<string>());
  const [errors, setErrors] = useState<ErrorEntry[]>([]);
  const errorId = useRef(0);
  const [notice, setNotice] = useState('');
  const [progress, setProgress] = useState<OperationProgress | null>(null);
  const [modal, setModal] = useState<ModalState | null>(null);
  const [modalError, setModalError] = useState('');
  const [name, setName] = useState('');
  const [port, setPort] = useState('');
  const [version, setVersion] = useState('');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [launcherUpdate, setLauncherUpdate] = useState<{ current_version: string; release: {version: string; name: string; notes: string; url: string} | null } | null>(null);
  const [installerVersion, setInstallerVersion] = useState('');
  const [settingsDraft, setSettingsDraft] = useState<Settings | null>(null);
  const [logId, setLogId] = useState('');
  const [logText, setLogText] = useState('请选择一个实例查看日志。');
  const [logAuto, setLogAuto] = useState(true);
  const [logLoading, setLogLoading] = useState(false);
  const [tokenVisible, setTokenVisible] = useState(false);
  const snapshotRef = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const logRequest = useRef<Promise<void> | null>(null);
  const selectedLog = useRef('');
  selectedLog.current = logId;

  const report = useCallback((label: string, error: unknown, retry?: () => void) => {
    const message = messageOf(error);
    setErrors(old => old.some(e => e.label === label && e.message === message) ? old : [...old, { id: ++errorId.current, label, message, retry }]);
  }, []);
  const refresh = useCallback((): Promise<void> => {
    if (snapshotRef.current) { refreshAgain.current = true; return snapshotRef.current; }
    setRefreshing(true);
    snapshotRef.current = (async () => {
      do {
        refreshAgain.current = false;
        const epoch = generation.current;
        try {
          const data = await rpc<Snapshot>('snapshot');
          if (!Array.isArray(data.instances) || !Array.isArray(data.versions) || !Array.isArray(data.backups) || !Array.isArray(data.archives)) throw new Error('启动器快照格式不正确');
          if (epoch !== generation.current) { refreshAgain.current = true; continue; }
          if (mounted.current) { setSnapshot(data); setConnected(true); }
        } catch (e) { if (mounted.current) { setConnected(false); report('读取工作空间', e); } }
      } while (refreshAgain.current && mounted.current);
    })().finally(() => { snapshotRef.current = null; if (mounted.current) setRefreshing(false); });
    return snapshotRef.current;
  }, [report]);

  const run = useCallback(async (key: string, label: string, job: () => Promise<unknown>, retry?: () => void): Promise<boolean> => {
    if (locks.current.has(key)) return false;
    locks.current.add(key); generation.current++; setBusy(new Set(locks.current));
    try { const result = await job(); if (result === false) return false; setNotice(`${label}已完成`); return true; }
    catch (e) { report(label, e, retry); setModalError(messageOf(e)); return false; }
    finally { locks.current.delete(key); generation.current++; setBusy(new Set(locks.current)); void refresh(); }
  }, [refresh, report]);

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => { await refresh(); if (!cancelled) timer = setTimeout(tick, 2000); };
    void tick();
    return () => { mounted.current = false; cancelled = true; clearTimeout(timer); };
  }, [refresh]);
  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<OperationProgress>('operation-progress', event => { if (!disposed) setProgress(event.payload); })
      .then(fn => { if (disposed) fn(); else unlisten = fn; }).catch(() => { /* Progress events are optional. */ });
    return () => { disposed = true; unlisten?.(); };
  }, []);

  const loadLogs = useCallback((): Promise<void> => {
    if (logRequest.current || !selectedLog.current) return logRequest.current ?? Promise.resolve();
    const id = selectedLog.current;
    setLogLoading(true);
    logRequest.current = (async () => {
      try { const data = await rpc<{ text: string }>('logs', { id }); if (mounted.current && selectedLog.current === id) setLogText(data.text || '暂无日志。'); }
      catch (e) { if (selectedLog.current === id) report('读取日志', e); }
      finally { logRequest.current = null; if (mounted.current) setLogLoading(false); }
    })();
    return logRequest.current;
  }, [report]);
  useEffect(() => {
    if (page !== '日志' || !logId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setLogText('正在读取日志…');
    const tick = async () => {
      if (logRequest.current) await logRequest.current;
      if (cancelled) return;
      await loadLogs();
      if (!cancelled && logAuto) timer = setTimeout(tick, 2000);
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [page, logId, logAuto, loadLogs]);

  const instances = snapshot?.instances ?? [];
  const versions = snapshot?.versions ?? [];
  const settings = settingsDraft ?? snapshot?.settings ?? defaultSettings;
  const versionBusy = [...busy].some(k => k.startsWith('version:'));
  const updateBusy = [...busy].some(k => k.startsWith('launcher:'));
  const anyActive = instances.some(active);
  const modalBusy = !!modal && (modal.kind === 'editor' ? busy.has(modal.instance?.id ?? 'create') : modal.kind === 'upgrade' ? busy.has(modal.instance.id) : modal.kind === 'confirm' ? busy.has(modal.key) : false);
  function closeModal() { if (!modalBusy) { setModal(null); setModalError(''); setTokenVisible(false); } }
  function showEditor(i?: Instance) { setName(i?.name ?? ''); setPort(i ? String(i.port) : ''); setVersion(i?.version ?? versions[0]?.version ?? ''); setModalError(''); setModal({ kind: 'editor', instance: i }); }
  function confirm(title: string, text: string, key: string, action: () => Promise<unknown>) { setModalError(''); setModal({ kind: 'confirm', title, text, key, action }); }
  const instanceAction = (i: Instance, method: string, label: string) => run(i.id, `${i.name} · ${label}`, () => rpc(method, { id: i.id }));

  async function saveInstance(e: FormEvent) {
    e.preventDefault();
    if (modal?.kind !== 'editor') return;
    const i = modal.instance, parsedPort = port.trim() === '' ? null : Number(port);
    if (!name.trim() || (parsedPort !== null && (!Number.isInteger(parsedPort) || parsedPort < 1024 || parsedPort > 65535)) || (i && parsedPort === null)) { setModalError('请输入名称和有效端口（1024–65535）。'); return; }
    if (!i && !versions.some(v => v.version === version)) { setModalError('请先安装并选择一个版本。'); return; }
    if (i && active(instances.find(row => row.id === i.id) ?? i)) { setModalError('请先停止实例再编辑。'); return; }
    const ok = await run(i?.id ?? 'create', i ? '保存实例' : '创建实例', () => rpc(i ? 'update' : 'create', i ? { id: i.id, name: name.trim(), port: parsedPort } : { name: name.trim(), mode: 'helper', port: parsedPort, version }));
    if (ok) setModal(null);
  }
  async function credentials(i: Instance) {
    setModalError('');
    await run(i.id, '读取首次登录凭据', async () => {
      const data = await rpc<{ token: string }>('credentials', { id: i.id });
      if (!data.token) throw new Error('该实例未提供可用的首次登录令牌。');
      setTokenVisible(false); setModal({ kind: 'credentials', name: i.name, token: data.token });
    });
  }
  const loadReleases = (): Promise<boolean> => run('releases', '获取可用版本', async () => {
    const data = await rpc<{ releases: Release[] }>('releases');
    if (!Array.isArray(data.releases)) throw new Error('版本列表格式不正确');
    setReleases(data.releases);
  }, () => { void loadReleases(); });
  const checkLauncherUpdate = (): Promise<boolean> => run('launcher:check', '检查启动器更新', async () => {
    setInstallerVersion('');
    setLauncherUpdate(await rpc('launcher_update_check'));
  }, () => { void checkLauncherUpdate(); });
  const downloadLauncherUpdate = (next: string): Promise<boolean> => run('launcher:download', '下载并校验启动器', async () => {
    setInstallerVersion('');
    await rpc('launcher_update_download', { version: next });
    setInstallerVersion(next);
  }, () => { void downloadLauncherUpdate(next); });
  const downloadVersion = (release: Release): Promise<boolean> => run(`version:${release.version}`, `下载 ${release.version}`, () => rpc('version_download', { version: release.version }), () => { void downloadVersion(release); });
  const listVersions = () => run('version:list', '刷新已安装版本', async () => {
    const installed = await rpc<InstalledVersion[]>('versions_list');
    if (!Array.isArray(installed)) throw new Error('已安装版本列表格式不正确');
    setSnapshot(old => old ? { ...old, versions: installed } : old);
  });
  async function chooseZip(method: 'version_import' | 'restore') {
    const key = method === 'version_import' ? 'version:import' : 'restore:local';
    await run(key, method === 'version_import' ? '导入版本' : '恢复本地备份', async () => {
      const path = await open({ title: method === 'version_import' ? '选择版本 ZIP 安装包' : '选择本地 ZIP 备份', multiple: false, directory: false, filters: [{ name: 'ZIP 压缩包', extensions: ['zip'] }] });
      if (typeof path !== 'string') return false;
      if (method === 'restore') {
        confirm('恢复本地备份', `确定从“${displayPath(path)}”恢复实例吗？`, key, () => rpc('restore', { path }));
        return false;
      } else await rpc('version_import', { path });
    });
  }

  async function chooseWorkspace() {
    if (!snapshot || anyActive) return;
    try {
      const path = await open({ title: '选择数据目录', multiple: false, directory: true });
      if (typeof path !== 'string') return;
      confirm('更改数据目录', `将切换到“${displayPath(path)}”。启动器会自动重启；当前实例、版本和备份不会自动迁移。`, 'workspace:change', () => invoke('change_workspace', { path }));
    } catch (e) {
      report('选择数据目录', e);
    }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="logo" aria-hidden="true">◈</span><div><strong>WeChat OneBot</strong><small>DESKTOP WORKSPACE</small></div></div>
      <nav aria-label="主导航">{pages.map(([title, icon]) => <button key={title} className={page === title ? 'nav-item selected' : 'nav-item'} aria-current={page === title ? 'page' : undefined} onClick={() => setPage(title)}><span aria-hidden="true">{icon}</span>{title}</button>)}</nav>
      <div className="sidebar-bottom"><span className="version-label">桌面启动器 · {snapshot?.version ?? desktopVersion}</span><button className="text-button" onClick={() => void run('workspace', '打开工作空间', () => invoke('reveal_workspace'))}>打开工作空间 ↗</button></div>
    </aside>
    <main>
      <header className="topbar"><span className="muted">工作空间 <b>/ {page}</b></span><span className={`connection ${connected ? 'online' : ''}`} role="status"><i />{connected ? '本地服务已连接' : '等待本地服务'}</span></header>
      <div className="content">
        <div className="page-heading"><div><div className="eyebrow">YOUR BOT WORKSPACE</div><h1>{page}</h1><p>{({ 实例管理: '让每一个机器人，都有自己的工作空间。', 版本管理: '按需安装版本，让每次升级都更从容。', 备份恢复: '保存工作进度，随时找回熟悉的配置。', 日志: '查看实例的运行记录与诊断信息。', 设置: '按你的习惯，安排工作空间的启动与退出。' })[page]}</p></div><div className="actions"><button disabled={refreshing} onClick={() => void refresh()}>↻ 刷新</button>{page === '实例管理' && <button className="primary" disabled={!snapshot || !versions.length || busy.has('create')} onClick={() => showEditor()}>＋ 创建实例</button>}{page === '版本管理' && <button className="primary" disabled={versionBusy} onClick={() => void chooseZip('version_import')}>导入 ZIP 版本</button>}{page === '备份恢复' && <button className="primary" disabled={busy.has('restore:local')} onClick={() => void chooseZip('restore')}>从本地 ZIP 恢复</button>}</div></div>
        <div className="alerts" aria-live="polite">{errors.map(e => <div className="alert error" key={e.id}><span><strong>{e.label}</strong> · {e.message}</span>{e.retry && <button onClick={e.retry}>重试</button>}<button aria-label="关闭错误" onClick={() => setErrors(old => old.filter(row => row.id !== e.id))}>×</button></div>)}</div>
        {notice && <div className="alert info" role="status"><span>{notice}</span><button onClick={() => setNotice('')} aria-label="关闭消息">×</button></div>}
        {progress && <div className="progress-card" role="status"><div><strong>{progress.operation}</strong><span>{progress.message}</span><button className="text-button" aria-label="关闭进度提示" onClick={() => setProgress(null)}>×</button></div>{typeof progress.progress === 'number' && <progress max={100} value={Math.max(0, Math.min(100, progress.progress))} />}</div>}

        {page === '实例管理' && <>
          <section className="metrics" aria-label="工作空间概况">{[['全部实例', snapshot ? instances.length : '—', '▦'], ['运行中', snapshot ? instances.filter(i => i.state === 'running').length : '—', '▷'], ['已安装版本', snapshot ? versions.length : '—', '◇']].map(([label, value, icon]) => <div className="metric" key={label}><div><small>{label}</small><b>{value}</b></div><span aria-hidden="true">{icon}</span></div>)}</section>
          <div className="section-heading"><h2>我的实例</h2><span className="muted">{instances.length} 个独立工作空间</span></div>
          {snapshot && !versions.length && <div className="alert info"><span>先安装一个运行版本，再创建你的第一个实例。</span><button onClick={() => setPage('版本管理')}>前往版本管理</button></div>}
          <div className="filters"><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索实例名称或端口…" aria-label="搜索实例" /><select aria-label="筛选实例状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部状态</option><option value="running">运行中</option><option value="stopped">已停止</option><option value="error">异常</option></select></div>
          <div className="instance-grid">{instances.filter(i => (i.name.toLowerCase().includes(search.toLowerCase()) || String(i.port).includes(search)) && (filter === 'all' || (filter === 'error' ? !!i.error || ['error', 'failed'].includes(i.state) : i.state === filter))).map(i => {
            const locked = busy.has(i.id), stoppedOnly = locked || active(i);
            return <article className="instance-card" key={i.id} aria-busy={locked}>
              <div className="card-body"><div className="card-heading"><div className="identity"><span className="instance-icon" aria-hidden="true">{i.mode === 'mock' ? '◇' : '◈'}</span><div><h3>{i.name}</h3><small>{i.mode === 'mock' ? 'MOCK · 模拟模式' : 'HELPER · 微信桥接'}</small></div></div><span className={`badge ${i.state === 'running' ? 'running' : i.error || ['error', 'failed'].includes(i.state) ? 'failed' : ''}`}>{locked ? '处理中…' : statusNames[i.state] ?? i.state}</span></div>
              <dl className="facts"><div><dt>WebUI 端口</dt><dd>{i.port}</dd></div><div><dt>运行版本</dt><dd>{i.version || '—'}</dd></div><div><dt>进程 PID</dt><dd>{i.pid ?? '—'}</dd></div><div><dt>运行模式</dt><dd>{i.mode}</dd></div></dl>
              {i.error && <p className="instance-error">{i.error}</p>}
              <div className="card-actions"><button className="primary" disabled={locked || i.state !== 'running'} onClick={() => void run(i.id, '打开实例 WebUI', () => invoke('open_instance', { instanceId: i.id }))}>打开 WebUI ↗</button><button disabled={locked || ['starting', 'stopping', 'restarting'].includes(i.state)} onClick={() => void instanceAction(i, i.state === 'running' ? 'stop' : 'start', i.state === 'running' ? '停止' : '启动')}>{i.state === 'running' ? '停止' : '启动'}</button><button disabled={locked || ['starting', 'stopping', 'restarting'].includes(i.state)} onClick={() => void instanceAction(i, 'restart', '重启')}>重启</button></div></div>
              <div className="card-footer"><button disabled={locked} onClick={() => { setLogId(i.id); setPage('日志'); }}>日志</button><button disabled={stoppedOnly} title={active(i) ? '请先停止实例' : undefined} onClick={() => showEditor(i)}>编辑</button><button disabled={stoppedOnly} onClick={() => void instanceAction(i, 'backup', '备份')}>备份</button><button disabled={stoppedOnly || !versions.some(v => v.version !== i.version)} onClick={() => { setVersion(versions.find(v => v.version !== i.version)?.version ?? ''); setModalError(''); setModal({ kind: 'upgrade', instance: i }); }}>升级</button><button disabled={locked} onClick={() => void credentials(i)}>首次登录凭据</button><button className="danger" disabled={stoppedOnly} onClick={() => confirm('归档实例', `确定归档“${i.name}”吗？归档后可在“备份恢复”中恢复。`, i.id, () => rpc('archive', { id: i.id }))}>归档</button></div>
            </article>;
          })}</div>
          {!instances.length && <div className="empty"><span>◇</span><h3>{snapshot ? '从第一个实例开始' : '正在连接工作空间…'}</h3><p>{snapshot ? '安装版本后，创建一个 Helper 实例开始体验。' : '本地服务连接成功后将在此显示实例。'}</p></div>}
          {!!instances.length && !instances.some(i => (i.name.toLowerCase().includes(search.toLowerCase()) || String(i.port).includes(search)) && (filter === 'all' || (filter === 'error' ? !!i.error || ['error', 'failed'].includes(i.state) : i.state === filter))) && <div className="empty">没有匹配的实例，请调整搜索条件。</div>}
        </>}

        {page === '版本管理' && <>
          <section className="panel"><div className="section-heading"><div><h2>已安装版本 <span className="badge">{versions.length}</span></h2><p className="muted">创建实例时可选择不同版本；升级前请先停止实例。</p></div><button disabled={versionBusy} onClick={() => void listVersions()}>↻ 刷新已安装</button></div>{!versions.length ? <div className="empty compact">尚未安装版本。可导入本地 ZIP，或从下方下载可用版本。</div> : versions.map(v => <div className="list-row" key={v.version}><div className="row-copy"><strong>{v.version}</strong><small className="mono" title={displayPath(v.path)}>{displayPath(v.path)}</small><small>{instances.filter(i => i.version === v.version).length} 个实例正在使用此版本</small></div><button className="danger" disabled={versionBusy || instances.some(i => i.version === v.version) || (snapshot?.archives ?? []).some(i => i.version === v.version)} title="实例或归档仍使用的版本不能移除" onClick={() => confirm('移除版本', `确定移除已安装版本 ${v.version} 吗？`, `version:${v.version}`, () => rpc('version_remove', { version: v.version }))}>移除</button></div>)}</section>
          <section className="panel"><div className="section-heading"><div><h2>可用发布版本</h2><p className="muted">兼容性由本地服务检查，下载失败后可以重试。</p></div><button disabled={busy.has('releases')} onClick={() => void loadReleases()}>{busy.has('releases') ? '获取中…' : releases ? '↻ 重新获取' : '获取版本列表'}</button></div>{releases === null ? <div className="empty compact">点击“获取版本列表”查看可安装的发布版本。</div> : !releases.length ? <div className="empty compact">暂未找到可用发布版本，可稍后重试或导入本地 ZIP。</div> : releases.map(r => <div className="list-row" key={r.version}><div className="row-copy"><strong>{r.name || r.version} <span className={`badge ${r.compatible ? 'running' : 'failed'}`}>{r.compatible ? '兼容' : '不兼容'}</span></strong><small>{r.version} · {r.published_at ? r.published_at.slice(0, 10) : '发布日期未知'} · {sizeOf(r.size)}</small>{!r.compatible && <small className="danger">{r.reason || '当前系统或版本不满足安装条件。'}</small>}</div><button className="primary" disabled={!r.compatible || versionBusy || versions.some(v => v.version === r.version)} onClick={() => void downloadVersion(r)}>{versions.some(v => v.version === r.version) ? '已安装' : busy.has(`version:${r.version}`) ? '下载中…' : '下载并安装'}</button></div>)}</section>
        </>}

        {page === '备份恢复' && <>
          <section className="panel"><div className="section-heading"><h2>实例备份</h2><span className="muted">{snapshot?.backups.length ?? 0} 份备份</span></div>{snapshot?.backups.map(b => <div className="list-row" key={b.name}><div className="row-copy"><strong>{b.instance_name || b.name}</strong><small className="mono">{b.name}</small><small>{b.version || '版本未知'} · {sizeOf(b.size)}</small></div><button disabled={busy.has(`restore:${b.name}`)} onClick={() => confirm('恢复备份', `确定从“${b.name}”恢复实例吗？`, `restore:${b.name}`, () => rpc('restore', { name: b.name }))}>恢复实例</button></div>)}{!snapshot?.backups.length && <div className="empty compact">暂无备份。停止实例后，可在实例卡片中创建备份。</div>}</section>
          <section className="panel"><div className="section-heading"><h2>已归档实例</h2><span className="muted">归档的工作空间仍可恢复</span></div>{snapshot?.archives.map((a: Archive) => <div className="list-row" key={a.archive_name}><div className="row-copy"><strong>{a.name}</strong><small>{a.version} · {a.archive_name}</small></div><button disabled={busy.has(`archive:${a.archive_name}`)} onClick={() => confirm('恢复归档', `确定恢复归档实例“${a.name}”吗？`, `archive:${a.archive_name}`, () => rpc('restore_archive', { archive_name: a.archive_name }))}>恢复归档</button></div>)}{!snapshot?.archives.length && <div className="empty compact">暂无已归档实例。</div>}</section>
        </>}

        {page === '日志' && <section className="panel"><div className="log-toolbar"><label className="log-select">实例<select value={logId} onChange={e => setLogId(e.target.value)}><option value="">请选择实例</option>{instances.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}</select></label><label className="checkbox"><input type="checkbox" checked={logAuto} onChange={e => setLogAuto(e.target.checked)} />自动刷新</label><button disabled={!logId || logLoading} onClick={() => void loadLogs()}>{logLoading ? '读取中…' : '↻ 刷新日志'}</button></div>{logId && !instances.some(i => i.id === logId) && <p className="instance-error">该实例已不在当前列表中，请重新选择。</p>}<pre className="log-output" tabIndex={0} aria-label="实例日志">{logId ? logText : '请选择一个实例查看日志。'}</pre></section>}

        {page === '设置' && <>
          <section className="panel">
            <div className="section-heading"><div><h2>启动器更新</h2><p className="muted">当前桌面版本 {snapshot?.version ?? desktopVersion}。更新启动器本体，已有实例的网关运行版本保持不变。</p></div><button disabled={!connected || updateBusy} onClick={() => void checkLauncherUpdate()}>{busy.has('launcher:check') ? '检查中…' : '检查更新'}</button></div>
            {launcherUpdate && !launcherUpdate.release && <p>当前已是最新可用正式版。</p>}
            {launcherUpdate?.release && <><p><strong>{launcherUpdate.release.name}</strong> · {launcherUpdate.release.version}</p><pre className="log-output" style={{ maxHeight: 260, whiteSpace: 'pre-wrap' }}>{launcherUpdate.release.notes || '此版本未提供更新说明。'}</pre><div className="panel-actions"><button className="primary" disabled={updateBusy} onClick={() => void downloadLauncherUpdate(launcherUpdate.release!.version)}>{busy.has('launcher:download') ? '下载并校验中…' : installerVersion ? '重新下载' : '下载并校验安装器'}</button><button disabled={updateBusy || installerVersion !== launcherUpdate.release.version || anyActive} title={anyActive ? '请先停止所有实例' : '安装前将退出启动器'} onClick={() => confirm('安装启动器更新', '请确认所有实例已停止。启动器将退出并在原目录自动完成更新，保留当前数据目录，完成后自动重新打开。Windows 可能要求权限确认；无需操作安装向导。', 'launcher:install', () => invoke('install_launcher_update'))}>自动更新并重启</button></div>{anyActive && <p className="muted">下载可在后台完成；安装前请先停止所有实例。</p>}</>}
          </section>
          <section className="panel"><div className="section-heading"><div><h2>启动与窗口</h2><p className="muted">保存后应用到桌面启动器。</p></div></div>{([['close_to_tray', '关闭窗口时最小化到托盘', '关闭窗口后继续在后台管理实例。'], ['autostart', '开机启动', '登录 Windows 时自动启动桌面启动器。'], ['autostart_hidden', '开机启动时隐藏窗口', '自动启动时直接驻留托盘。'], ['restore_instances', '恢复上次运行的实例', '启动时恢复此前运行中的工作空间。']] as [keyof Settings, string, string][]).map(([key, title, description]) => <label className="setting-row" key={key}><div><strong>{title}</strong><small>{description}</small></div><input className="switch" type="checkbox" checked={settings[key]} disabled={!snapshot || busy.has('settings') || key === 'autostart_hidden' && !settings.autostart} onChange={e => setSettingsDraft({ ...settings, [key]: e.target.checked })} /></label>)}<div className="panel-actions"><button disabled={!settingsDraft || busy.has('settings')} onClick={() => setSettingsDraft(null)}>撤销修改</button><button className="primary" disabled={!snapshot || !settingsDraft || busy.has('settings')} onClick={() => void run('settings', '保存设置', async () => { const saved = await rpc<Settings>('settings_set', { ...settings }); setSnapshot(old => old ? { ...old, settings: saved } : old); setSettingsDraft(null); })}>保存设置</button></div></section>
          <section className="panel">
            <div className="section-heading"><div><h2>数据目录</h2><p className="muted">实例、版本、组件和备份等所有数据都存储在此目录中。</p></div></div>
            <div className="data-directory">
              <strong>当前数据目录</strong>
              <div className="path-control">
                <span className="mono" title={displayPath(snapshot?.root)}>{displayPath(snapshot?.root) || '等待本地服务连接…'}</span>
                <button disabled={!snapshot || busy.has('workspace')} onClick={() => void run('workspace', '打开数据目录', () => invoke('reveal_workspace'))}>打开</button>
                <button className="primary" disabled={!snapshot || busy.has('workspace:change') || anyActive} title={anyActive ? '请先停止所有实例' : '选择新的数据目录并重启启动器'} onClick={() => void chooseWorkspace()}>更改</button>
              </div>
              <small className="muted">更改后启动器会自动重启；当前数据不会自动迁移。</small>
            </div>
            <div className="panel-actions"><button className="danger" onClick={() => confirm('退出启动器', '确定退出桌面启动器吗？', 'exit', () => invoke('exit_app'))}>退出应用</button></div>
          </section>
        </>}
        <footer><span className="workspace-root" title={displayPath(snapshot?.root)}>{displayPath(snapshot?.root) || '本地桌面工作空间'}</span><span>WeChat OneBot · {snapshot?.version ?? desktopVersion}</span></footer>
      </div>
    </main>

    {modal && <Modal title={modal.kind === 'editor' ? modal.instance ? '编辑实例' : '创建实例' : modal.kind === 'upgrade' ? '升级实例版本' : modal.kind === 'credentials' ? '首次登录凭据' : modal.title} busy={modalBusy} close={closeModal}>
      {modal.kind === 'editor' && <form onSubmit={e => void saveInstance(e)}><div className="modal-body"><p className="muted">{modal.instance ? '修改实例名称与端口。' : '选择已安装版本，创建一个 Helper 微信桥接实例。'}</p><label className="field">实例名称<input required maxLength={80} autoFocus value={name} disabled={modalBusy} onChange={e => setName(e.target.value)} placeholder="例如：我的机器人" /></label>{!modal.instance && <><label className="field">运行版本<select required value={version} disabled={modalBusy} onChange={e => setVersion(e.target.value)}><option value="" disabled>选择已安装版本</option>{versions.map(v => <option key={v.version} value={v.version}>{v.version}</option>)}</select></label></>}<label className="field">WebUI 端口<input type="number" min={1024} max={65535} step={1} required={!!modal.instance} disabled={modalBusy} value={port} onChange={e => setPort(e.target.value)} placeholder="留空，自动分配" /><small>范围 1024–65535{!modal.instance && '，留空自动分配'}。</small></label>{modalError && <p className="instance-error" role="alert">{modalError}</p>}</div><div className="modal-actions"><button type="button" disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={modalBusy || !modal.instance && !versions.length}>{modalBusy ? '保存中…' : modal.instance ? '保存修改' : '创建实例'}</button></div></form>}
      {modal.kind === 'upgrade' && <form onSubmit={e => { e.preventDefault(); const i = instances.find(i => i.id === modal.instance.id); if (!i || active(i)) { setModalError('请先停止实例再升级。'); return; } if (!version || !versions.some(v => v.version === version)) { setModalError('请选择已安装版本。'); return; } void run(i.id, '升级实例', () => rpc('upgrade', { id: i.id, version })).then(ok => { if (ok) setModal(null); }); }}><div className="modal-body"><p>将“{modal.instance.name}”从 {modal.instance.version} 切换到已安装版本。建议提前备份。</p><label className="field">目标版本<select value={version} onChange={e => setVersion(e.target.value)} disabled={modalBusy} required>{versions.filter(v => v.version !== modal.instance.version).map(v => <option key={v.version} value={v.version}>{v.version}</option>)}</select></label>{modalError && <p className="instance-error" role="alert">{modalError}</p>}</div><div className="modal-actions"><button type="button" disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={modalBusy || !version}>确认升级</button></div></form>}
      {modal.kind === 'confirm' && <><div className="modal-body"><p>{modal.text}</p>{modalError && <p className="instance-error" role="alert">{modalError}</p>}</div><div className="modal-actions"><button disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={modalBusy} onClick={() => void run(modal.key, modal.title, modal.action).then(ok => { if (ok) setModal(null); })}>{modalBusy ? '处理中…' : '确认'}</button></div></>}
      {modal.kind === 'credentials' && <><div className="modal-body"><p>“{modal.name}”的首次登录令牌。打开实例 WebUI 后在登录页粘贴，请妥善保管。</p><div className="token-row"><input aria-label="实例令牌" type={tokenVisible ? 'text' : 'password'} readOnly value={modal.token} autoComplete="off" /><button aria-pressed={tokenVisible} onClick={() => setTokenVisible(v => !v)}>{tokenVisible ? '隐藏' : '显示'}</button></div><small className="muted">关闭此窗口后清除本次展示的凭据。</small>{modalError && <p className="instance-error" role="alert">{modalError}</p>}</div><div className="modal-actions"><button onClick={closeModal}>关闭</button><button className="primary" onClick={() => void navigator.clipboard.writeText(modal.token).then(() => setNotice('令牌已复制')).catch(e => setModalError(`复制失败：${messageOf(e)}，请显示令牌后手动复制。`))}>复制令牌</button></div></>}
    </Modal>}
  </div>;
}
