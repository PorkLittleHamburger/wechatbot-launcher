import { rpc } from '../api';
import { sizeOf } from '../presentation';
import type { Workspace } from '../useWorkspace';
import type { Confirm } from '../ui-types';
export function RecoveryPage({ workspace, confirm }: { workspace: Workspace; confirm: Confirm }) {
  const { snapshot, connected, busy } = workspace;
  const empty = (section: string, text: string) => !snapshot ? '正在读取工作空间…' : !connected ? '读取失败，当前列表可能已过期。请刷新后重试。' : snapshot.diagnostics?.some(d => d.section === section) ? '此分区读取存在错误，请查看上方诊断。' : text;
  return <>
    <section className="panel"><div className="section-heading"><h2>实例备份</h2><span className="muted">{snapshot?.backups.length ?? '—'} 份备份</span></div>
      {snapshot?.backups.map(b => {
        const key = `restore:${b.name}`, reason = b.invalid ? `备份损坏，禁止恢复：${b.error || '备份校验失败'}` : !connected ? '请等待本地服务连接' : busy.has(key) ? '正在恢复此备份，请稍候' : '';
        const deleteReason = !connected ? '请等待本地服务连接' : busy.has(key) ? '此备份正在操作中，请稍候' : snapshot.diagnostics?.some(d => d.section === 'backups') ? '备份列表读取失败，请刷新后重试' : '';
        return <div className="list-row" key={b.name}><div className="row-copy"><strong>{b.instance_name || b.name}</strong><small className="mono">{b.name}</small><small>{b.version || '版本未知'} · {sizeOf(b.size)}</small>{reason && <small className={b.invalid ? 'danger' : ''}>{reason}</small>}</div>
          <div className="actions"><button disabled={!!reason} title={reason} onClick={() => confirm('恢复备份', `确定从“${b.name}”恢复实例吗？`, key, operationId => rpc('restore', { name: b.name }, operationId), { operation: 'restore' })}>{busy.has(key) ? '处理中…' : b.invalid ? '无法恢复' : '恢复实例'}</button>
          <button className="danger" disabled={!!deleteReason} title={deleteReason || '永久删除此备份文件，不删除实例数据'} onClick={() => confirm('删除备份', `确定永久删除备份“${b.name}”吗？此操作不可撤销，不会删除实例或实例数据。`, key, operationId => rpc('backup_delete', { name: b.name }, operationId), { operation: 'backup_delete' })}>删除备份</button></div></div>;
      })}
      {!snapshot?.backups.length && <div className="empty compact">{empty('backups', '暂无备份。停止实例后，可在实例卡片中创建备份。')}</div>}
    </section>
    <section className="panel"><div className="section-heading"><h2>已归档实例</h2><span className="muted">损坏的归档需修复后才能恢复</span></div>
      {snapshot?.archives.map(a => {
        const key = `archive:${a.archive_name}`, reason = a.invalid ? `归档损坏，禁止恢复：${a.error || '归档元数据校验失败'}` : !connected ? '请等待本地服务连接' : busy.has(key) ? '正在恢复此归档，请稍候' : '';
        return <div className="list-row" key={a.archive_name}><div className="row-copy"><strong>{a.name}</strong><small>{a.version} · {a.archive_name}</small>{reason && <small className={a.invalid ? 'danger' : ''}>{reason}</small>}</div>
          <button disabled={!!reason} title={reason} onClick={() => confirm('恢复归档', `确定恢复归档实例“${a.name}”吗？`, key, operationId => rpc('restore_archive', { archive_name: a.archive_name }, operationId), { operation: 'restore_archive' })}>{busy.has(key) ? '恢复中…' : a.invalid ? '无法恢复' : '恢复归档'}</button></div>;
      })}
      {!snapshot?.archives.length && <div className="empty compact">{empty('archives', '暂无已归档实例。')}</div>}
    </section>
  </>;
}
