import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { useWorkspace } from './useWorkspace';
import { active, confirmationBlockReason } from './presentation';
import type { Scope } from './ui-types';
import { VersionsPage } from './components/VersionsPage';
import { RecoveryPage } from './components/RecoveryPage';
import { InstancesPage } from './components/InstancesPage';
import { OperationFeedback } from './components/OperationFeedback';
import { open } from '@tauri-apps/plugin-dialog';
import { version as desktopVersion } from '../package.json';
import { messageOf, rpc } from './api';
import { displayPath } from './path-display';
import type { Instance } from './types';
import { useInstanceLogs } from './useInstanceLogs';
import { SettingsPage } from './components/SettingsPage';

type Page = '实例管理' | '版本管理' | '备份恢复' | '日志' | '设置';
type ModalState = { kind: 'editor'; instance?: Instance } | { kind: 'upgrade'; instance: Instance }
  | { kind: 'confirm'; title: string; text: string; key: string; action: (operationId: string) => Promise<unknown>; scope?: Scope }
  | { kind: 'credentials'; name: string; token: string };
const pages: [Page, string][] = [['实例管理', '▦'], ['版本管理', '◇'], ['备份恢复', '▤'], ['日志', '≡'], ['设置', '⚙']];


function Modal({ title, busy, close, children }: { title: string; busy: boolean; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; d?.showModal(); return () => { d?.close(); }; }, []);
  return <dialog ref={ref} onCancel={e => { e.preventDefault(); if (!busy) close(); }} aria-labelledby="modal-title">
    <div className="modal-head"><h2 id="modal-title">{title}</h2><button type="button" className="icon-button" disabled={busy} onClick={close} aria-label="关闭">×</button></div>{children}
  </dialog>;
}

export default function App() {
  const [page, setPage] = useState<Page>('实例管理');
  const workspace = useWorkspace();
  const { snapshot, connected, refreshing, busy, errors, setErrors, notice, setNotice, feedback, dismissFeedback, refresh, run, report, mounted } = workspace;
  const [modal, setModal] = useState<ModalState | null>(null);
  const [modalError, setModalError] = useState('');
  const [name, setName] = useState('');
  const [port, setPort] = useState('');
  const [version, setVersion] = useState('');
  const [logId, setLogId] = useState('');
  const [tokenVisible, setTokenVisible] = useState(false);
  const { logText, logAuto, setLogAuto, logLoading, loadLogs } = useInstanceLogs(page === '日志', logId, workspace);

  const instances = snapshot?.instances ?? [];
  const versions = snapshot?.versions ?? [];
  const versionBusy = !connected || [...busy].some(k => k.startsWith('version:'));
  const modalKey = modal?.kind === 'editor' ? modal.instance?.id ?? 'create' : modal?.kind === 'upgrade' ? modal.instance.id : modal?.kind === 'confirm' ? modal.key : '';
  const operationError = feedback.find(row => row.key === modalKey && row.phase === 'error')?.message;
  const modalBusy = !!modal && (modal.kind === 'editor' ? busy.has(modal.instance?.id ?? 'create') : modal.kind === 'upgrade' ? busy.has(modal.instance.id) : modal.kind === 'confirm' ? busy.has(modal.key) : false);
  const modalBlocked = modal?.kind === 'confirm' ? confirmationBlockReason(snapshot, connected, modal.key, modal.scope?.operation) : !connected ? '本地服务未连接，请刷新后重试。' : '';
  function closeModal() { if (!modalBusy) { if (modal && modal.kind !== 'credentials') setNotice(`${modal.kind === 'confirm' ? modal.title : modal.kind === 'upgrade' ? '升级实例' : modal.instance ? '编辑实例' : '创建实例'}已取消，未执行变更`); setModal(null); setModalError(''); setTokenVisible(false); } }
  function showEditor(i?: Instance) { dismissFeedback(i?.id ?? 'create'); setName(i?.name ?? ''); setPort(i ? String(i.port) : ''); setVersion(i?.version ?? versions[0]?.version ?? ''); setModalError(''); setModal({ kind: 'editor', instance: i }); }
  function confirm(title: string, text: string, key: string, action: (operationId: string) => Promise<unknown>, scope?: Scope) { dismissFeedback(key); setModalError(''); setModal({ kind: 'confirm', title, text, key, action, scope }); }
  const instanceAction = (i: Instance, method: string, label: string) => run(i.id, `${i.name} · ${label}`, operationId => rpc(method, { id: i.id }, operationId), undefined, { operation: method, id: i.id });

  async function saveInstance(e: FormEvent) {
    e.preventDefault();
    if (modal?.kind !== 'editor') return;
    const i = modal.instance, parsedPort = port.trim() === '' ? null : Number(port);
    if (!name.trim() || (parsedPort !== null && (!Number.isInteger(parsedPort) || parsedPort < 1024 || parsedPort > 65535)) || (i && parsedPort === null)) { setModalError('请输入名称和有效端口（1024–65535）。'); return; }
    if (!i && !versions.some(v => v.version === version)) { setModalError('请先安装并选择一个版本。'); return; }
    if (i && active(instances.find(row => row.id === i.id) ?? i)) { setModalError('请先停止实例再编辑。'); return; }
    setModalError('');
    const ok = await run(i?.id ?? 'create', i ? '保存实例' : '创建实例', operationId => rpc(i ? 'update' : 'create', i ? { id: i.id, name: name.trim(), port: parsedPort } : { name: name.trim(), mode: 'helper', port: parsedPort, version }, operationId), undefined, { operation: i ? 'update' : 'create', id: i?.id });
    if (ok && mounted.current) setModal(null);
  }
  async function credentials(i: Instance) {
    setModalError('');
    await run(i.id, '读取首次登录凭据', async () => {
      const data = await rpc<{ token: string }>('credentials', { id: i.id });
      if (!data.token) throw new Error('该实例未提供可用的首次登录令牌。');
      if (!mounted.current) return;
      setTokenVisible(false); setModal({ kind: 'credentials', name: i.name, token: data.token });
    });
  }
  async function chooseZip(method: 'version_import' | 'restore') {
    const key = method === 'version_import' ? 'version:import' : 'restore:local';
    await run(key, method === 'version_import' ? '导入版本' : '恢复本地备份', async operationId => {
      const path = await open({ title: method === 'version_import' ? '选择版本 ZIP 安装包' : '选择本地 ZIP 备份', multiple: false, directory: false, filters: [{ name: 'ZIP 压缩包', extensions: ['zip'] }] });
      if (!mounted.current || typeof path !== 'string') return false;
      if (method === 'restore') {
        confirm('恢复本地备份', `确定从“${displayPath(path)}”恢复实例吗？`, key, operationId => rpc('restore', { path }, operationId), { operation: 'restore' });
        return 'confirmation';
      } else await rpc('version_import', { path }, operationId);
    }, undefined, { operation: method });
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
        <div className="page-heading"><div><div className="eyebrow">YOUR BOT WORKSPACE</div><h1>{page}</h1><p>{({ 实例管理: '让每一个机器人，都有自己的工作空间。', 版本管理: '按需安装版本，让每次升级都更从容。', 备份恢复: '保存工作进度，随时找回熟悉的配置。', 日志: '查看实例的运行记录与诊断信息。', 设置: '按你的习惯，安排工作空间的启动与退出。' })[page]}</p></div><div className="actions"><button disabled={refreshing} onClick={() => void refresh()}>↻ 刷新</button>{page === '实例管理' && <button className="primary" title={!connected ? '等待本地服务连接' : !versions.length ? '请先在版本管理中下载或导入版本' : busy.has('create') ? '正在创建实例' : '创建独立实例'} disabled={!connected || !snapshot || !versions.length || busy.has('create')} onClick={() => showEditor()}>{busy.has('create') ? '创建中…' : '＋ 创建实例'}</button>}{page === '版本管理' && <button className="primary" title={!connected ? '等待本地服务连接' : versionBusy ? '请等待版本操作完成' : '选择并校验本地 ZIP 安装包'} disabled={versionBusy} onClick={() => void chooseZip('version_import')}>{busy.has('version:import') ? '导入中…' : '导入 ZIP 版本'}</button>}{page === '备份恢复' && <button className="primary" title={!connected ? '等待本地服务连接' : busy.has('restore:local') ? '请等待本地恢复操作完成' : '选择备份 ZIP 后确认恢复'} disabled={!connected || busy.has('restore:local')} onClick={() => void chooseZip('restore')}>{busy.has('restore:local') ? '恢复操作中…' : '从本地 ZIP 恢复'}</button>}</div></div>
        <div className="alerts" aria-live="polite">{errors.map(e => <div className="alert error" key={e.id}><span><strong>{e.label}</strong> · {e.message}</span>{e.retry && <button onClick={e.retry}>重试</button>}<button aria-label="关闭错误" onClick={() => setErrors(old => old.filter(row => row.id !== e.id))}>×</button></div>)}</div>
        {snapshot?.autostart_error && <div className="alert error" role="alert"><span><strong>开机启动任务未能同步</strong> · {snapshot.autostart_error}。请在设置中重新保存，详细记录见数据目录中的 autostart.log。</span></div>}
        {notice && <div className="alert info" role="status"><span>{notice}</span><button onClick={() => setNotice('')} aria-label="关闭消息">×</button></div>}
        <OperationFeedback rows={feedback} dismiss={dismissFeedback} />
        {snapshot?.diagnostics?.map((d, index) => <div className="alert error" role="alert" key={`${d.section}:${index}`}><span><strong>{d.section}</strong> · {d.message}</span></div>)}

        <div hidden={page !== '实例管理'}><InstancesPage workspace={workspace} confirm={confirm} showEditor={showEditor} credentials={credentials} instanceAction={instanceAction} onLogs={id => { setLogId(id); setPage('日志'); }} onUpgrade={i => { dismissFeedback(i.id); setVersion(versions.find(v => v.version !== i.version)?.version ?? ''); setModalError(''); setModal({ kind: 'upgrade', instance: i }); }} onVersions={() => setPage('版本管理')} /></div>

        <div hidden={page !== '版本管理'}><VersionsPage workspace={workspace} confirm={confirm} /></div>

        <div hidden={page !== '备份恢复'}><RecoveryPage workspace={workspace} confirm={confirm} /></div>

        {page === '日志' && <section className="panel"><div className="log-toolbar"><label className="log-select">实例<select value={logId} onChange={e => setLogId(e.target.value)}><option value="">请选择实例</option>{instances.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}</select></label><label className="checkbox"><input type="checkbox" checked={logAuto} onChange={e => setLogAuto(e.target.checked)} />自动刷新</label><button disabled={!logId || logLoading} onClick={() => void loadLogs()}>{logLoading ? '读取中…' : '↻ 刷新日志'}</button></div>{logId && !instances.some(i => i.id === logId) && <p className="instance-error">该实例已不在当前列表中，请重新选择。</p>}<pre className="log-output" tabIndex={0} aria-label="实例日志">{logId ? logText : '请选择一个实例查看日志。'}</pre></section>}

        <div hidden={page !== '设置'}><SettingsPage workspace={workspace} confirm={confirm} /></div>
        <footer><span className="workspace-root" title={displayPath(snapshot?.root)}>{displayPath(snapshot?.root) || '本地桌面工作空间'}</span><span>WeChat OneBot · {snapshot?.version ?? desktopVersion}</span></footer>
      </div>
    </main>

    {modal && <Modal title={modal.kind === 'editor' ? modal.instance ? '编辑实例' : '创建实例' : modal.kind === 'upgrade' ? '升级实例版本' : modal.kind === 'credentials' ? '首次登录凭据' : modal.title} busy={modalBusy} close={closeModal}>
      {modal.kind === 'editor' && <form onSubmit={e => void saveInstance(e)}><div className="modal-body"><p className="muted">{modal.instance ? '修改实例名称与端口。' : '选择已安装版本，创建一个 Helper 微信桥接实例。'}</p><label className="field">实例名称<input required maxLength={80} autoFocus value={name} disabled={modalBusy} onChange={e => setName(e.target.value)} placeholder="例如：我的机器人" /></label>{!modal.instance && <><label className="field">运行版本<select required value={version} disabled={modalBusy} onChange={e => setVersion(e.target.value)}><option value="" disabled>选择已安装版本</option>{versions.map(v => <option key={v.version} value={v.version}>{v.version}</option>)}</select></label></>}<label className="field">WebUI 端口<input type="number" min={1024} max={65535} step={1} required={!!modal.instance} disabled={modalBusy} value={port} onChange={e => setPort(e.target.value)} placeholder="留空，自动分配" /><small>范围 1024–65535{!modal.instance && '，留空自动分配'}。</small></label>{(modalError || operationError || modalBlocked) && <p className="instance-error" role="alert">{modalError || operationError || modalBlocked}</p>}</div><div className="modal-actions"><button type="button" disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={!connected || modalBusy || !modal.instance && !versions.length}>{modalBusy ? '保存中…' : modal.instance ? '保存修改' : '创建实例'}</button></div></form>}
      {modal.kind === 'upgrade' && <form onSubmit={e => { e.preventDefault(); const i = instances.find(i => i.id === modal.instance.id); if (!i || active(i)) { setModalError('请先停止实例再升级。'); return; } if (!version || !versions.some(v => v.version === version)) { setModalError('请选择已安装版本。'); return; } setModalError(''); void run(i.id, '升级实例', operationId => rpc('upgrade', { id: i.id, version }, operationId), undefined, { operation: 'upgrade', id: i.id }).then(ok => { if (ok && mounted.current) setModal(null); }); }}><div className="modal-body"><p>将“{modal.instance.name}”从 {modal.instance.version} 切换到已安装版本。建议提前备份。</p><label className="field">目标版本<select value={version} onChange={e => setVersion(e.target.value)} disabled={modalBusy} required>{versions.filter(v => v.version !== modal.instance.version).map(v => <option key={v.version} value={v.version}>{v.version}</option>)}</select></label>{(modalError || operationError || modalBlocked) && <p className="instance-error" role="alert">{modalError || operationError || modalBlocked}</p>}</div><div className="modal-actions"><button type="button" disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={!connected || modalBusy || !version}>确认升级</button></div></form>}
      {modal.kind === 'confirm' && <><div className="modal-body"><p>{modal.text}</p>{(modalError || operationError || modalBlocked) && <p className="instance-error" role="alert">{modalError || operationError || modalBlocked}</p>}</div><div className="modal-actions"><button disabled={modalBusy} onClick={closeModal}>取消</button><button className="primary" disabled={!!modalBlocked || modalBusy} onClick={() => void run(modal.key, modal.title, modal.action, undefined, modal.scope).then(ok => { if (ok && mounted.current) setModal(null); })}>{modalBusy ? '处理中…' : '确认'}</button></div></>}
      {modal.kind === 'credentials' && <><div className="modal-body"><p>“{modal.name}”的首次登录令牌。打开实例 WebUI 后在登录页粘贴，请妥善保管。</p><div className="token-row"><input aria-label="实例令牌" type={tokenVisible ? 'text' : 'password'} readOnly value={modal.token} autoComplete="off" /><button aria-pressed={tokenVisible} onClick={() => setTokenVisible(v => !v)}>{tokenVisible ? '隐藏' : '显示'}</button></div><small className="muted">关闭此窗口后清除本次展示的凭据。</small>{(modalError || operationError || modalBlocked) && <p className="instance-error" role="alert">{modalError || operationError || modalBlocked}</p>}</div><div className="modal-actions"><button onClick={closeModal}>关闭</button><button className="primary" onClick={() => void navigator.clipboard.writeText(modal.token).then(() => { if (mounted.current) setNotice('令牌已复制'); }).catch(e => { if (mounted.current) setModalError(`复制失败：${messageOf(e)}，请显示令牌后手动复制。`); })}>复制令牌</button></div></>}
    </Modal>}
  </div>;
}
