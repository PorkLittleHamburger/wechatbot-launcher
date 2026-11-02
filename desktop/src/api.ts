import { invoke } from '@tauri-apps/api/core';

export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const result = await invoke<T | string>('rpc', { method, params });
  // The native bridge may return a JSON value or a serialized JSON document.
  return (typeof result === 'string' ? JSON.parse(result) : result) as T;
}

export function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  try { return JSON.stringify(error) || '操作失败，请重试'; } catch { return '操作失败，请重试'; }
}
