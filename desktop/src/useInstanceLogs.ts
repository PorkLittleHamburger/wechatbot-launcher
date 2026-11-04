import { useCallback, useEffect, useRef, useState } from 'react';
import { rpc, messageOf } from './api';
import type { Workspace } from './useWorkspace';
export function useInstanceLogs(enabled: boolean, logId: string, { mounted, report }: Workspace) {
  const [logText, setLogText] = useState('请选择一个实例查看日志。');
  const [logAuto, setLogAuto] = useState(true);
  const [logLoading, setLogLoading] = useState(false);
  const logRequest = useRef<Promise<void> | null>(null);
  const selectedLog = useRef('');
  selectedLog.current = logId;

  const loadLogs = useCallback((): Promise<void> => {
    if (logRequest.current || !selectedLog.current) return logRequest.current ?? Promise.resolve();
    const id = selectedLog.current;
    setLogLoading(true);
    logRequest.current = (async () => {
      try { const data = await rpc<{ text: string }>('logs', { id }); if (mounted.current && selectedLog.current === id) setLogText(data.text || '暂无日志。'); }
      catch (e) { if (mounted.current && selectedLog.current === id) { setLogText(`读取失败：${messageOf(e)}。可点击刷新重试。`); report('读取日志', e); } }
      finally { logRequest.current = null; if (mounted.current) setLogLoading(false); }
    })();
    return logRequest.current;
  }, [report, mounted]);
  useEffect(() => {
    if (!enabled || !logId) return;
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
  }, [enabled, logId, logAuto, loadLogs]);

  return { logText, logAuto, setLogAuto, logLoading, loadLogs };
}
