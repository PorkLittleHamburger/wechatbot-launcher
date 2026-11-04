import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { version as desktopVersion } from '../../package.json';
import { rpc } from '../api';
import { displayPath } from '../path-display';
import { active } from '../presentation';
import type { Settings, LauncherUpdate } from '../types';
import type { Workspace } from '../useWorkspace';
import type { Confirm } from '../ui-types';
const defaultSettings: Settings = { close_to_tray: true, autostart: false, autostart_hidden: false, restore_instances: false, restore_bridges: false };
export function SettingsPage({ workspace, confirm }: { workspace: Workspace; confirm: Confirm }) {
  const { snapshot, connected, busy, run, mounted, setSnapshot, setNotice } = workspace;
  const [launcherUpdate, setLauncherUpdate] = useState<LauncherUpdate | null>(null);
  const [installerVersion, setInstallerVersion] = useState('');
  const [settingsDraft, setSettingsDraft] = useState<Settings | null>(null);
  const [autostartExplicit, setAutostartExplicit] = useState(false);
  const settings = settingsDraft ?? snapshot?.settings ?? defaultSettings;
  const bridgeRestoreBlocked = !settings.autostart || !settings.restore_instances;
  const settingsError = snapshot?.diagnostics?.find(d => d.section === 'settings');
  const instancesError = snapshot?.diagnostics?.find(d => d.section === 'instances');
  const lifecycleBlocked = !!instancesError || !snapshot || !connected;
  const anyActive = snapshot?.instances.some(active) ?? false;
  const updateBusy = !connected || [...busy].some(k => k.startsWith('launcher:'));
  const checkLauncherUpdate = (): Promise<boolean> => run('launcher:check', '检查启动器更新', async () => {
    setInstallerVersion('');
    setLauncherUpdate(null);
    const result = await rpc<LauncherUpdate>('launcher_update_check');
    if (mounted.current) setLauncherUpdate(result);
  }, () => { void checkLauncherUpdate(); });
  const downloadLauncherUpdate = (next: string): Promise<boolean> => run('launcher:download', '下载并校验启动器', async operationId => {
    setInstallerVersion('');
    await rpc('launcher_update_download', { version: next }, operationId);
    if (mounted.current) setInstallerVersion(next);
  }, () => { void downloadLauncherUpdate(next); }, { operation: 'launcher_update_download', id: next });
  async function chooseWorkspace() {
    if (!snapshot || anyActive || lifecycleBlocked) return;
    await run('workspace:choose', '选择数据目录', async () => {
      const path = await open({ title: '选择数据目录', multiple: false, directory: true });
      if (!mounted.current || typeof path !== 'string') return false;
      confirm('更改数据目录', `将切换到“${displayPath(path)}”。启动器会自动重启；当前实例、版本和备份不会自动迁移。`, 'workspace:change', () => invoke('change_workspace', { path }));
      return 'confirmation';
    });
  }
  return <>
    {instancesError && <p className="instance-error" role="alert">实例列表读取失败，无法确认活动实例状态。已禁用数据目录切换和自动更新，请先修复并刷新。{instancesError.message}</p>}
    {settingsError && <p className="instance-error" role="alert">设置读取失败，已禁用保存。请先修复数据目录中的 settings.json，然后刷新；当前显示的默认设置不能覆盖损坏文件。{settingsError.message}</p>}

          <section className="panel">
            <div className="section-heading"><div><h2>启动器更新</h2><p className="muted">当前桌面版本 {snapshot?.version ?? desktopVersion}。更新启动器本体，已有实例的网关运行版本保持不变。</p></div><button disabled={!connected || updateBusy} onClick={() => void checkLauncherUpdate()}>{busy.has('launcher:check') ? '检查中…' : '检查更新'}</button></div>
            {launcherUpdate && !launcherUpdate.release && <p>当前已是最新可用正式版。</p>}
            {launcherUpdate?.release && <><p><strong>{launcherUpdate.release.name}</strong> · {launcherUpdate.release.version}</p><pre className="log-output" style={{ maxHeight: 260, whiteSpace: 'pre-wrap' }}>{launcherUpdate.release.notes || '此版本未提供更新说明。'}</pre><div className="panel-actions"><button className="primary" disabled={updateBusy} onClick={() => void downloadLauncherUpdate(launcherUpdate.release!.version)}>{busy.has('launcher:download') ? '下载并校验中…' : installerVersion ? '重新下载' : '下载并校验安装器'}</button><button disabled={lifecycleBlocked || updateBusy || installerVersion !== launcherUpdate.release.version || anyActive} title={instancesError ? '实例列表读取失败，无法确认全部实例已停止，请先修复并刷新' : anyActive ? '请先停止所有实例' : updateBusy ? '请等待更新操作完成' : installerVersion !== launcherUpdate.release.version ? '请先下载并校验安装器' : '安装前将退出启动器'} onClick={() => confirm('安装启动器更新', '请确认所有实例已停止。启动器将退出并在原目录自动完成更新，保留当前数据目录，完成后自动重新打开。Windows 可能要求权限确认；无需操作安装向导。', 'launcher:install', () => invoke('install_launcher_update'))}>自动更新并重启</button></div>{anyActive && <p className="muted">下载可在后台完成；安装前请先停止所有实例。</p>}</>}
          </section>
          {snapshot?.autostart_status && <p className="muted">Windows 启动应用：{snapshot.autostart_status.entry_name} · {snapshot.autostart_status.registered ? snapshot.autostart_status.enabled === null ? '状态未知' : snapshot.autostart_status.enabled ? '已启用' : '已禁用' : '未注册'}。在任务管理器中禁用后，启动器会保留该选择。</p>}<section className="panel"><div className="section-heading"><div><h2>启动与窗口</h2><p className="muted">保存后应用到桌面启动器。</p></div></div>{([['close_to_tray', '关闭窗口时最小化到托盘', '关闭窗口后继续在后台管理实例。'], ['autostart', '开机启动', '登录 Windows 时自动启动桌面启动器。'], ['autostart_hidden', '开机启动时隐藏窗口', '自动启动时直接驻留托盘。'], ['restore_instances', '恢复上次运行的实例', '启动时恢复此前运行中的工作空间。'], ['restore_bridges', '开机自启时恢复上次桥接', '仅在开机自启并恢复实例时，尝试恢复上次桥接；需微信登录及运行版本支持。']] as [keyof Settings, string, string][]).map(([key, title, description]) => <label className="setting-row" key={key}><div><strong>{title}</strong><small id={`setting-${key}-help`}>{description}{key === 'restore_bridges' && bridgeRestoreBlocked && ' 请先启用“开机启动”和“恢复上次运行的实例”；当前选项已禁用，已保存的值保持不变。'}</small></div><input className="switch" type="checkbox" aria-label={title} aria-describedby={`setting-${key}-help`} checked={settings[key]} disabled={!!settingsError || !connected || !snapshot || busy.has('settings') || key === 'autostart_hidden' && !settings.autostart || key === 'restore_bridges' && bridgeRestoreBlocked} onChange={e => { if (key === 'autostart') setAutostartExplicit(true); setSettingsDraft({ ...settings, [key]: e.target.checked }); }} /></label>)}<p className="muted">{busy.has('settings') ? '正在保存设置并同步 Windows 自启，请稍候。' : settingsDraft ? '有尚未保存的修改。开机启动仅在你主动切换并保存后更新。' : '当前显示服务返回的设置；自启实际状态请查看 Windows 启动应用状态。'}{!settings.autostart && ' 开机启动关闭时，隐藏自启选项不可用。'}</p><div className="panel-actions"><button title={!settingsDraft ? '没有待撤销的修改' : '放弃尚未保存的修改'} disabled={!settingsDraft || busy.has('settings')} onClick={() => { setSettingsDraft(null); setAutostartExplicit(false); setNotice('已撤销设置修改'); }}>撤销修改</button><button className="primary" title={settingsError ? '请先修复数据目录中的 settings.json 并刷新' : !settingsDraft ? '没有尚未保存的修改' : busy.has('settings') ? '正在保存并同步 Windows 自启' : '保存并应用设置'} disabled={!!settingsError || !connected || !snapshot || !settingsDraft || busy.has('settings')} onClick={() => void run('settings', '保存设置', async operationId => { const saved = await rpc<Settings>('settings_set', { ...settings, autostart_explicit: autostartExplicit }, operationId); if (!mounted.current) return; setSnapshot(old => old ? { ...old, settings: saved } : old); setSettingsDraft(null); setAutostartExplicit(false); }, undefined, { operation: 'settings_set' })}>{busy.has('settings') ? '保存并同步自启中…' : '保存设置'}</button></div></section>
          <section className="panel">
            <div className="section-heading"><div><h2>数据目录</h2><p className="muted">实例、版本、组件和备份等所有数据都存储在此目录中。</p></div></div>
            <div className="data-directory">
              <strong>当前数据目录</strong>
              <div className="path-control">
                <span className="mono" title={displayPath(snapshot?.root)}>{displayPath(snapshot?.root) || '等待本地服务连接…'}</span>
                <button disabled={!connected || !snapshot || busy.has('workspace')} onClick={() => void run('workspace', '打开数据目录', () => invoke('reveal_workspace'))}>打开</button>
                <button className="primary" disabled={lifecycleBlocked || busy.has('workspace:change') || busy.has('workspace:choose') || anyActive} title={instancesError ? '实例列表读取失败，无法确认全部实例已停止' : anyActive ? '请先停止所有实例' : '选择新的数据目录并重启启动器'} onClick={() => void chooseWorkspace()}>更改</button>
              </div>
              <small className="muted">更改后启动器会自动重启；当前数据不会自动迁移。</small>
            </div>
            <div className="panel-actions"><button className="danger" onClick={() => confirm('退出启动器', '确定退出桌面启动器吗？', 'exit', () => invoke('exit_app'))}>退出应用</button></div>
          </section>
        </>;
}
