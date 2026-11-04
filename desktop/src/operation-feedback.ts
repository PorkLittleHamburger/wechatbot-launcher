import type { OperationProgress } from './types';

export type Phase = 'start' | 'progress' | 'success' | 'error' | 'cancelled';
export interface Feedback {
  key: string; operationId: string; label: string; operation?: string; id?: string;
  pending: boolean; phase: Phase; message: string; detail?: string; progress?: number | null;
}
export const terminal = (phase: Phase) => ['success', 'error', 'cancelled'].includes(phase);

// Resource IDs identify what is changed; operation IDs isolate each attempt, including retries.
// Ignore legacy events without an operation ID: the complete RPC still supplies the result.
export function applyProgress(rows: Feedback[], event: OperationProgress): Feedback[] {
  const candidates = rows.filter(row => !terminal(row.phase) && row.operationId === event.operation_id && row.operation === event.operation &&
    (event.id == null || row.id === undefined || row.id === event.id));
  if (candidates.length !== 1) return rows;
  return rows.map(row => row !== candidates[0] ? row : {
    ...row, phase: event.phase === 'success' ? 'progress' : event.phase ?? 'progress',
    message: event.phase === 'success' ? '后端步骤已完成，等待完整操作确认…' : event.message,
    detail: event.phase === 'success' ? event.message : row.detail, progress: event.progress,
  });
}

export function finishFeedback(row: Feedback, phase: 'success' | 'error' | 'cancelled', message: string): Feedback {
  // A terminal backend failure must survive a later resolved RPC.
  if (phase === 'success' && (row.phase === 'error' || row.phase === 'cancelled')) return { ...row, pending: false, progress: undefined };
  return { ...row, pending: false, phase, message: phase === 'success' ? row.detail || message : message, progress: undefined };
}
