"""Application commands; resource owners provide their own transaction locks."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from ..config import ConfigError

if TYPE_CHECKING:
    from .desktop_manager import DesktopManager

MESSAGES = {
    "create": ("正在创建实例配置", "实例已创建；请启动管理服务后打开 WebUI"),
    "update": ("正在保存实例配置", "实例配置已保存"),
    "start": ("正在启动实例并检查服务，请稍候", "实例管理服务已就绪；微信桥接需在 WebUI 手动启动"),
    "stop": ("正在停止实例并等待进程退出", "实例已停止"),
    "restart": ("正在停止旧进程并重新启动实例", "实例已重新启动"),
    "backup": ("正在备份，请勿移动实例目录", "备份已完成，可在备份恢复页面查看"),
    "backup_delete": ("正在删除备份文件", "备份已删除，实例数据保持不变"),
    "restore": ("正在校验并恢复备份", "已恢复为新实例，当前保持停止"),
    "restore_archive": ("正在校验并恢复归档", "归档已恢复为新实例，原归档仍保留"),
    "archive": ("正在归档实例", "实例已归档，可在备份恢复页面恢复"),
    "upgrade": ("正在备份、切换版本并检查服务", "实例升级检查完成，当前保持停止"),
    "version_import": ("正在校验并导入运行包", "运行版本已安装，现有实例版本不会自动切换"),
    "version_download": ("正在获取下载地址", "运行版本已下载并校验安装"),
    "version_remove": ("正在检查引用并卸载运行版本", "运行版本已卸载"),
    "settings_set": ("正在保存启动器设置", "启动器设置已保存"),
    "launcher_update_download": ("正在下载启动器安装器", "安装器校验完成；停止全部实例后可自动更新并重启"),
}


class DesktopOperations:
    def __init__(self, manager: DesktopManager):
        self.manager = manager

    async def execute(self, method: str, p: dict, progress: Callable[[str, int], None]):
        manager, instance_id = self.manager, p.get("id")
        if method == "settings_set":
            return manager.save_settings(p)
        if method == "create":
            return await manager.create(p.get("name"), p.get("mode", "helper"), p.get("port"), p.get("version"))
        if method == "update":
            await manager.update(instance_id, p.get("name"), p.get("port"))
        elif method == "start":
            await manager.start(instance_id)
        elif method == "stop":
            await manager.stop(instance_id)
        elif method == "restart":
            await manager.restart(instance_id)
        elif method == "archive":
            await manager.archive(instance_id)
        elif method == "backup":
            return {"name": await manager.backup(instance_id)}
        elif method == "backup_delete":
            await manager.delete_backup(p.get("name"))
        elif method == "restore":
            return await manager.restore(p.get("name"), path=p.get("path"))
        elif method == "restore_archive":
            return await manager.restore_archive(p.get("archive_name"))
        elif method == "upgrade":
            return await manager.upgrade(instance_id, p.get("version"))
        elif method == "version_import":
            return await manager.versions.import_archive(
                Path(p["path"]), replace=True, references=manager.referenced_versions)
        elif method == "version_download":
            return await manager.versions.download(p.get("version"), progress)
        elif method == "version_remove":
            await manager.remove_version(p.get("version"))
        elif method == "launcher_update_download":
            return await manager.updater.download(p.get("version"), progress)
        else:
            raise ConfigError(f"不支持的管理方法: {method}")
        return {"ok": True}
