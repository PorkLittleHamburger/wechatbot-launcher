import { useCallback, useEffect, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { messageOf, rpc } from './api';
import type { OperationProgress, Snapshot } from './types';
import { applyProgress, finishFeedback, type Feedback } from './operation-feedback';
export type ErrorEntry = { id: number; label: string; message: string; retry?: () => void };
export function useWorkspace() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const locks = useRef(new Set<string>());
  const [errors, setErrors] = useState<ErrorEntry[]>([]);
  const errorId = useRef(0);
  const [notice, setNotice] = useState('');
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const feedbackRef = useRef<Feedback[]>([]);
  const updateFeedback = useCallback((change: (rows: Feedback[]) => Feedback[]) => {
    feedbackRef.current = change(feedbackRef.current);
    if (mounted.current) setFeedback(feedbackRef.current);
  }, []);
  const snapshotRef = useRef<Promise<void> | null>(null);
  const refreshAgain = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(false);
  const report = useCallback((label: string, error: unknown, retry?: () => void) => {
    if (!mounted.current) return;
    const message = messageOf(error);
    setErrors(old => old.some(e => e.label === label && e.message === message) ? old : [...old, { id: ++errorId.current, label, message, retry }]);
  }, []);
  const refresh = useCallback((): Promise<void> => {
    if (snapshotRef.current) { refreshAgain.current = true; return snapshotRef.current; }
    if (!mounted.current) return Promise.resolve();
    setRefreshing(true);
    snapshotRef.current = (async () => {
      do {
        refreshAgain.current = false;
        const epoch = generation.current;
        try {
          const data = await rpc<Snapshot>('snapshot');
          if (epoch !== generation.current) { refreshAgain.current = true; continue; }
          if (mounted.current) { setSnapshot(data); setConnected(true); }
        } catch (e) { if (mounted.current) { setConnected(false); report('读取工作空间', e); } }
      } while (refreshAgain.current && mounted.current);
    })().finally(() => { snapshotRef.current = null; if (mounted.current) setRefreshing(false); });
    return snapshotRef.current;
  }, [report]);

  const run = useCallback(async (key: string, label: string, job: (operationId: string) => Promise<unknown>, retry?: () => void, scope?: { operation: string; id?: string }): Promise<boolean> => {
    if (!mounted.current || locks.current.has(key)) return false;
    const operationId = crypto.randomUUID();
    locks.current.add(key); generation.current++; setBusy(new Set(locks.current));
    updateFeedback(rows => [...rows.filter(row => row.key !== key), { key, label, operationId, ...scope, pending: true, phase: 'start', message: '正在处理…' }]);
    try {
      const result = await job(operationId);
      if (result === 'confirmation') {
        updateFeedback(rows => rows.filter(row => row.operationId !== operationId));
        return false;
      }
      const phase = result === false ? 'cancelled' : 'success';
      updateFeedback(rows => rows.map(row => row.operationId === operationId ? finishFeedback(row, phase, phase === 'cancelled' ? '已取消，未执行变更' : '已完成') : row));
      const final = feedbackRef.current.find(row => row.operationId === operationId);
      return mounted.current && final?.phase === 'success';
    } catch (e) {
      report(label, e, retry);
      updateFeedback(rows => rows.map(row => row.operationId === operationId ? finishFeedback(row, 'error', messageOf(e)) : row));
      return false;
    } finally {
      locks.current.delete(key); generation.current++;
      if (mounted.current) { setBusy(new Set(locks.current)); void refresh(); }
    }
  }, [refresh, report, updateFeedback]);

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
    void listen<OperationProgress>('operation-progress', event => { if (!disposed && mounted.current) updateFeedback(rows => applyProgress(rows, event.payload)); })
      .then(fn => { if (disposed) fn(); else unlisten = fn; }).catch(() => { /* Progress events are optional. */ });
    return () => { disposed = true; unlisten?.(); };
  }, [updateFeedback]);

  return { snapshot, setSnapshot, connected, refreshing, busy, errors, setErrors, notice, setNotice,
    feedback, dismissFeedback: (key: string) => updateFeedback(rows => rows.filter(row => row.key !== key || row.pending)),
    refresh, run, report, mounted };
}
export type Workspace = ReturnType<typeof useWorkspace>;
