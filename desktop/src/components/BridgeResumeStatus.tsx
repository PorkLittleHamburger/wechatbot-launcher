import type { BridgeResume } from '../types';

const states: Record<string, [string, string]> = {
  disabled: ['桥接恢复：未请求', '本次管理服务启动未请求自动恢复桥接；如需运行，请打开 WebUI 手动启动桥接。'],
  waiting_login: ['等待微信登录', '请先登录微信；登录后将继续尝试恢复桥接。'],
  no_target: ['没有可恢复的桥接', '未找到上次桥接目标，请打开 WebUI 手动启动桥接。'],
  starting: ['桥接启动中', '正在恢复桥接，请等待后续状态确认。'],
  stopping: ['桥接停止中', '正在停止桥接，请等待后续状态确认。'],
  running: ['桥接运行中', '后端已确认桥接正在运行。'],
  stopped: ['桥接已停止', '如需运行桥接，请打开 WebUI 手动启动。'],
  failed: ['桥接恢复失败', '请查看错误说明，在 WebUI 检查桥接后重试。'],
  unsupported: ['不支持桥接恢复', '当前运行版本不支持自动恢复，请打开 WebUI 手动启动桥接。'],
};

export function BridgeResumeStatus({ resume }: { resume?: BridgeResume }) {
  if (!resume) return <p className="muted">桥接恢复：未请求。管理服务运行不代表微信桥接已就绪；请在 WebUI 手动启动桥接。</p>;
  const state = resume.state;
  const known = Object.prototype.hasOwnProperty.call(states, state);
  const [label, description] = known ? states[state] : ['桥接状态未知', `无法识别桥接状态“${state}”，请检查运行版本并刷新。`];
  const error = resume.error || (state === 'failed' ? '后端未提供失败原因，请查看实例日志。' : !known ? '后端未提供错误说明，请查看实例日志。' : '');
  return <div aria-live="polite" aria-label="桥接恢复状态">
    <p><span className={`badge ${error ? 'failed' : state === 'running' ? 'running' : ''}`}>{label}</span></p>
    {!(state === 'unsupported' && resume.error) && <p className="muted">{description}</p>}
    {!resume.enabled && <p className="muted">此实例未启用桥接自动恢复。</p>}
    {resume.account && <p className="muted">桥接账号：{resume.account}</p>}
    {error && <p className="instance-error">桥接错误：{error}</p>}
  </div>;
}
