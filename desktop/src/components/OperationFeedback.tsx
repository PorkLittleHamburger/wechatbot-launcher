import { terminal, type Feedback } from '../operation-feedback';
const phaseNames = { start: '进行中', progress: '进行中', success: '成功', error: '失败', cancelled: '已取消' };
export function OperationFeedback({ rows, dismiss }: { rows: Feedback[]; dismiss: (key: string) => void }) {
  return <div aria-live="polite" aria-label="操作反馈">{rows.map(row => <div key={row.key} className={`progress-card ${row.phase}`} role={row.phase === 'error' ? 'alert' : 'status'}>
    <div><strong>{row.label} · {phaseNames[row.phase]}</strong><span>{row.message}</span>
      {!row.pending && terminal(row.phase) && <button className="text-button" aria-label={`关闭${row.label}反馈`} onClick={() => dismiss(row.key)}>×</button>}</div>
    {!terminal(row.phase) && typeof row.progress === 'number' && <progress max={100} value={Math.max(0, Math.min(100, row.progress))} />}
  </div>)}</div>;
}
