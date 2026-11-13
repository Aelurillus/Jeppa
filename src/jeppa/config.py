"""Local configuration only: validation never imports the SDK or logs values."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import math
import re
import tomllib
from collections.abc import Mapping

from .runtime import CommandPolicy


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Config:
    auth: dict[str, str] = field(repr=False)
    areas: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()
    default_policy: CommandPolicy = field(default_factory=lambda: CommandPolicy(cooldown=2.0))
    policies: dict[str, CommandPolicy] = field(default_factory=dict)
    database: Path = Path("data/jeppa.sqlite3")
    log_file: Path = Path("logs/jeppa.log")
    log_max_bytes: int = 1_048_576
    log_backups: int = 3


def load_config(path: Path | None = None, *, environ: Mapping[str, str] | None = None, require_auth: bool = True) -> Config:
    env = os.environ if environ is None else environ
    data = {}
    if path is not None:
        try:
            with path.open("rb") as source:
                data = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            raise ConfigError("无法读取 TOML 配置；请检查 --config 路径、权限和语法。") from None
    if set(data) - {"oopz", "bot", "permissions", "storage", "logging"}:
        raise ConfigError("存在未知配置节；请参阅 jeppa.example.toml。")
    auth = data.get("oopz", {})
    bot = data.get("bot", {})
    if not isinstance(auth, dict) or not isinstance(bot, dict):
        raise ConfigError("[oopz] 和 [bot] 必须为配置表。")
    fields = ("device_id", "person_uid", "jwt_token", "private_key", "phone", "password", "method")
    if set(auth) - set(fields) or set(bot) - {"areas", "plugins"}:
        raise ConfigError("存在未知配置项；请参阅 jeppa.example.toml。")
    auth = dict(auth)
    for key in fields:
        name = "OOPZ_LOGIN_" + key.upper() if key in {"phone", "password", "method"} else "OOPZ_" + key.upper()
        if name in env:
            auth[key] = env[name]
        if key in auth and not isinstance(auth[key], str):
            raise ConfigError("[oopz] 中的所有值必须为字符串。")
    method = auth.get("method", "credentials")
    if method not in {"credentials", "password_api"}:
        raise ConfigError("请将 method 设为 credentials 或 password_api；Jeppa 不支持浏览器登录。")
    required = ("device_id", "person_uid", "jwt_token") if method == "credentials" else ("phone", "password")
    if require_auth and any(not auth.get(key, "").strip() for key in required):
        raise ConfigError("缺少 Oopz 配置。请将 jeppa.example.toml 复制为 jeppa.local.toml，填写 [oopz]，再运行 jeppa check-config --config jeppa.local.toml。也可设置 OOPZ_DEVICE_ID/OOPZ_PERSON_UID/OOPZ_JWT_TOKEN；密码 API 登录需要设置 OOPZ_LOGIN_METHOD=password_api 和 OOPZ_LOGIN_PHONE/OOPZ_LOGIN_PASSWORD。")
    auth["method"] = method
    lists = {}
    for key in ("areas", "plugins"):
        value = bot.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigError(f"bot.{key} 必须为非空字符串数组。")
        lists[key] = tuple(value)
    permissions = _table(data, "permissions", {"users", "channels", "cooldown", "commands"})
    default_policy = _policy(permissions, CommandPolicy(cooldown=2.0))
    overrides = permissions.get("commands", {})
    if not isinstance(overrides, dict):
        raise ConfigError("permissions.commands 必须为配置表。")
    policies = {}
    for name, values in overrides.items():
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None or not isinstance(values, dict):
            raise ConfigError("命令权限配置必须使用有效命令名和配置表。")
        if set(values) - {"users", "channels", "cooldown"}:
            raise ConfigError("存在未知命令权限配置项；仅允许 users、channels、cooldown。")
        policies[name] = _policy(values, default_policy)
    storage = _table(data, "storage", {"path"})
    logs = _table(data, "logging", {"path", "max_bytes", "backups"})
    max_bytes = logs.get("max_bytes", 1_048_576)
    backups = logs.get("backups", 3)
    if type(max_bytes) is not int or max_bytes < 1024 or type(backups) is not int or not 1 <= backups <= 100:
        raise ConfigError("logging.max_bytes 必须为不小于 1024 的整数；backups 必须为 1 到 100 的整数。")
    return Config(
        auth, **lists, default_policy=default_policy, policies=policies,
        database=_path(storage, "data/jeppa.sqlite3"),
        log_file=_path(logs, "logs/jeppa.log"),
        log_max_bytes=max_bytes, log_backups=backups,
    )


def _table(data: dict, name: str, allowed: set[str]) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConfigError(f"[{name}] 配置表无效或存在未知配置项；请参阅 jeppa.example.toml。")
    return value


def _path(values: dict, default: str) -> Path:
    value = values.get("path", default)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError("存储和日志路径必须为非空路径字符串。")
    return Path(value)


def _policy(values: dict, inherited: CommandPolicy) -> CommandPolicy:
    allowlists = {}
    for key in ("users", "channels"):
        if key not in values:
            allowlists[key] = getattr(inherited, key)
            continue
        value = values[key]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigError("权限白名单必须为非空 ID 字符串数组。")
        allowlists[key] = tuple(value)
    cooldown = values.get("cooldown", inherited.cooldown)
    if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < 0:
        raise ConfigError("命令冷却时间必须为有限的非负秒数。")
    return CommandPolicy(**allowlists, cooldown=float(cooldown))
