import { invoke } from '@tauri-apps/api/core';

import { validateResult } from './api-contract';

export async function rpc<T>(method: string, params: Record<string, unknown> = {}, operationId?: string): Promise<T> {
  const result = await invoke<T>('rpc', { method, params: operationId === undefined ? params : { ...params, _operation_id: operationId } });
  validateResult(method, result);
  return result;
}

export function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  try { return JSON.stringify(error) || '操作失败，请重试'; } catch { return '操作失败，请重试'; }
}
