"""Versioned worker health contract; bridge lifecycle belongs to the gateway."""
from __future__ import annotations

RESUME_VERSION = 1
STATES = {"disabled", "no_target", "waiting_login", "starting", "stopping", "running", "stopped", "failed"}
FIELDS = ("supported", "version", "enabled", "account", "desired", "state", "error")


def unavailable(message: str, *, enabled: bool = True) -> dict:
    return {"supported": False, "version": None, "enabled": enabled,
            "account": None, "desired": "stopped", "state": "unsupported", "error": message}


def read_bridge_status(health: dict, requested: bool) -> dict | None:
    value = health.get("bridgeResume")
    if value is None:
        return unavailable("所选版本的 worker 不支持桥接恢复；请导入支持此能力的新 Bot 版本并切换实例") if requested else None
    valid = (isinstance(value, dict) and all(key in value for key in FIELDS)
             and type(value.get("supported")) is bool
             and type(value.get("enabled")) is bool
             and (value.get("account") is None or isinstance(value["account"], str))
             and value.get("desired") in ("running", "stopped")
             and isinstance(value.get("error"), str))
    if valid and value["supported"]:
        valid = (type(value["version"]) is int and value["version"] == RESUME_VERSION
                 and isinstance(value["state"], str) and value["state"] in STATES)
    elif valid:
        valid = value.get("version") is None and value.get("state") == "unsupported"
    if not valid:
        return unavailable("运行版本返回了不兼容的桥接恢复状态，请升级 Bot 运行版本", enabled=requested)
    return {key: value[key] for key in FIELDS}
