"""Explicit local configuration; validation never imports adapters or echoes secrets."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import tomllib

from .runtime import CommandPolicy


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class AccountConfig:
    label: str
    platform: str
    auth: dict[str, str] = field(repr=False)
    areas: tuple[str, ...] = ()


@dataclass(frozen=True, repr=False)
class Config:
    accounts: tuple[AccountConfig, ...] = ()
    plugins: tuple[str, ...] = ("jeppa.plugins.default:setup",)
    plugin_settings: dict[str, dict] = field(default_factory=dict)
    default_policy: CommandPolicy = field(default_factory=lambda: CommandPolicy(cooldown=2.0))
    policies: dict[str, CommandPolicy] = field(default_factory=dict)
    database: Path = Path("data/jeppa.sqlite3")
    legacy_account: str | None = None
    log_file: Path = Path("logs/jeppa.log")
    log_max_bytes: int = 1_048_576
    log_backups: int = 3


_AUTH_FIELDS = {"device_id", "person_uid", "jwt_token", "private_key", "phone", "password", "method"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*")


def load_config(path: Path | None = None, *, environ: Mapping[str, str] | None = None, require_auth: bool = True) -> Config:
    env = os.environ if environ is None else environ
    data = {}
    if path is not None:
        try:
            with path.open("rb") as source:
                data = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            raise ConfigError("无法读取 TOML 配置；请检查 --config 路径、权限和语法。") from None
    if set(data) - {"oopz", "accounts", "bot", "plugins", "permissions", "storage", "logging"}:
        raise ConfigError("存在未知配置节；请参阅 jeppa.example.toml。")
    bot = _table(data, "bot", {"areas", "plugins"})
    entries = _strings(bot.get("plugins", ["jeppa.plugins.default:setup"]), "bot.plugins")
    if len(set(entries)) != len(entries):
        raise ConfigError("bot.plugins 不可重复配置同一入口。")
    if "jeppa.plugins.utility:setup" in entries:
        raise ConfigError("0.3.0 已将基础功能合并到 jeppa.plugins.default:setup；请替换旧 utility 入口，FAQ 数据将在账号确认后迁移。")
    accounts = _accounts(data, bot, env, require_auth)
    settings = _table(data, "plugins", set(data.get("plugins", {})) if isinstance(data.get("plugins", {}), dict) else set())
    for identifier, options in settings.items():
        if _IDENTIFIER.fullmatch(identifier) is None or not isinstance(options, dict):
            raise ConfigError("插件设置必须使用有效插件 ID 和配置表。")
        if set(options) - {"enabled", "config", "areas", "channels"}:
            raise ConfigError("插件设置仅允许 enabled、config、areas、channels。")
        if type(options.get("enabled", True)) is not bool or not isinstance(options.get("config", {}), dict):
            raise ConfigError("插件 enabled 必须为布尔值，config 必须为配置表。")
        for key in ("areas", "channels"):
            if key in options:
                _strings(options[key], f"plugins.{key}")
    permissions = _table(data, "permissions", {"users", "channels", "cooldown", "commands"})
    default_policy = _policy(permissions, CommandPolicy(cooldown=2.0))
    overrides = permissions.get("commands", {})
    if not isinstance(overrides, dict):
        raise ConfigError("permissions.commands 必须为配置表。")
    policies = {}
    for name, values in overrides.items():
        if _IDENTIFIER.fullmatch(name) is None or not isinstance(values, dict):
            raise ConfigError("命令权限配置必须使用有效命令名和配置表。")
        if set(values) - {"users", "channels", "cooldown"}:
            raise ConfigError("命令权限配置仅允许 users、channels、cooldown。")
        policies[name] = _policy(values, default_policy)
    storage = _table(data, "storage", {"path", "legacy_account"})
    legacy = storage.get("legacy_account")
    if legacy is not None and (not isinstance(legacy, str) or legacy not in {item.label for item in accounts}):
        raise ConfigError("storage.legacy_account 必须指定一个已配置账号的 id。")
    logs = _table(data, "logging", {"path", "max_bytes", "backups"})
    max_bytes, backups = logs.get("max_bytes", 1_048_576), logs.get("backups", 3)
    if type(max_bytes) is not int or max_bytes < 1024 or type(backups) is not int or not 1 <= backups <= 100:
        raise ConfigError("logging.max_bytes 必须为不小于 1024 的整数；backups 必须为 1 到 100 的整数。")
    return Config(
        accounts=accounts, plugins=entries, plugin_settings=settings,
        default_policy=default_policy, policies=policies,
        database=_path(storage, "data/jeppa.sqlite3"), legacy_account=legacy,
        log_file=_path(logs, "logs/jeppa.log"), log_max_bytes=max_bytes, log_backups=backups,
    )


def _accounts(data: dict, bot: dict, env: Mapping[str, str], required: bool) -> tuple[AccountConfig, ...]:
    if "accounts" not in data:
        auth = _table(data, "oopz", _AUTH_FIELDS)
        has_environment = False
        auth = dict(auth)
        for key in _AUTH_FIELDS:
            name = "OOPZ_LOGIN_" + key.upper() if key in {"phone", "password", "method"} else "OOPZ_" + key.upper()
            if name in env:
                auth[key] = env[name]
                has_environment = True
        areas = _strings(bot.get("areas", []), "bot.areas")
        if not auth and not required and not has_environment:
            return ()
        return (AccountConfig("main", "oopz", _auth(auth, required), areas),)
    if "oopz" in data or "areas" in bot or any(name.startswith("OOPZ_") for name in env):
        raise ConfigError("多账号 accounts 不可与 [oopz]、bot.areas 或 OOPZ_ 环境变量混用；请将各账号写入各自的 accounts.oopz。")
    values = data["accounts"]
    if not isinstance(values, list) or (required and not values):
        raise ConfigError("accounts 必须为非空账号配置表数组。")
    result = []
    labels = set()
    identities = set()
    for value in values:
        if not isinstance(value, dict) or set(value) - {"id", "platform", "areas", "oopz"}:
            raise ConfigError("账号配置仅允许 id、platform、areas 和 oopz。")
        label = value.get("id")
        if not isinstance(label, str) or _IDENTIFIER.fullmatch(label) is None or label in labels:
            raise ConfigError("账号 id 必须为唯一的小写英文字母开头标识，可含数字、下划线、连字符。")
        if value.get("platform") != "oopz":
            raise ConfigError("当前仅提供 oopz 适配器；其他平台尚未实现。")
        auth = _auth(_table(value, "oopz", _AUTH_FIELDS), required)
        identity = auth.get("person_uid")
        if identity and identity in identities:
            raise ConfigError("同一 Oopz 账号不可重复连接。")
        if identity:
            identities.add(identity)
        labels.add(label)
        result.append(AccountConfig(label, "oopz", auth, _strings(value.get("areas", []), "accounts.areas")))
    return tuple(result)


def _auth(value: dict, required: bool) -> dict[str, str]:
    auth = dict(value)
    if any(not isinstance(item, str) for item in auth.values()):
        raise ConfigError("Oopz 凭据中的所有值必须为字符串。")
    method = auth.get("method", "credentials")
    if method not in {"credentials", "password_api"}:
        raise ConfigError("请将 method 设为 credentials 或 password_api；Jeppa 不自动化浏览器登录。")
    fields = ("device_id", "person_uid", "jwt_token") if method == "credentials" else ("phone", "password")
    if required and any(not auth.get(key, "").strip() for key in fields):
        raise ConfigError("缺少 Oopz 配置。请复制 jeppa.example.toml 并填写凭据，或运行 jeppa import-session --manual。单账号也可设置 OOPZ_DEVICE_ID、OOPZ_PERSON_UID、OOPZ_JWT_TOKEN。")
    auth["method"] = method
    return auth


def _table(data: dict, name: str, allowed: set[str]) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConfigError(f"[{name}] 配置表无效或存在未知配置项；请参阅 jeppa.example.toml。")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError(f"{name} 必须为非空 ID 或入口字符串数组。")
    return tuple(value)


def _path(values: dict, default: str) -> Path:
    value = values.get("path", default)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError("存储和日志路径必须为非空路径字符串。")
    return Path(value)


def _policy(values: dict, inherited: CommandPolicy) -> CommandPolicy:
    allowlists = {}
    for key in ("users", "channels"):
        allowlists[key] = _strings(values[key], "权限白名单") if key in values else getattr(inherited, key)
    cooldown = values.get("cooldown", inherited.cooldown)
    if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < 0:
        raise ConfigError("命令冷却时间必须为有限的非负秒数。")
    return CommandPolicy(**allowlists, cooldown=float(cooldown))
