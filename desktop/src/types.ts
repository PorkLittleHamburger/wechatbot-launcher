export interface Instance {
  id: string; name: string; mode: 'mock' | 'helper'; port: number; state: string;
  pid: number | null; error: string; url: string; version: string;
}
export interface InstalledVersion { version: string; path: string; [key: string]: unknown }
export interface Backup { name: string; size: number; instance_name: string; version: string }
export interface Archive { id: string; name: string; version: string; archive_name: string }
export interface Settings {
  close_to_tray: boolean; autostart: boolean; autostart_hidden: boolean; restore_instances: boolean;
}
export interface Snapshot {
  instances: Instance[]; versions: InstalledVersion[]; backups: Backup[]; archives: Archive[];
  settings: Settings; root: string; version: string;
  autostart_error?: string | null;
}
export interface Release {
  version: string; name: string; published_at: string; compatible: boolean;
  reason: string; download_url: string; size: number;
}
export interface OperationProgress { operation: string; message: string; progress?: number | null }
