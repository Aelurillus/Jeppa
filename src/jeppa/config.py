"""Local configuration only: validation never imports the SDK or logs values."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from collections.abc import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Config:
    auth: dict[str, str] = field(repr=False)
    areas: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()


def load_config(path: Path | None = None, *, environ: Mapping[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    data = {}
    if path is not None:
        try:
            with path.open("rb") as source:
                data = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError):
            raise ConfigError("Cannot read configuration TOML; check --config path, permissions and syntax.") from None
    if set(data) - {"oopz", "bot"}:
        raise ConfigError("Unknown configuration section; allowed: [oopz], [bot].")
    auth = data.get("oopz", {})
    bot = data.get("bot", {})
    if not isinstance(auth, dict) or not isinstance(bot, dict):
        raise ConfigError("[oopz] and [bot] must be tables.")
    fields = ("device_id", "person_uid", "jwt_token", "private_key", "phone", "password", "method")
    if set(auth) - set(fields) or set(bot) - {"areas", "plugins"}:
        raise ConfigError("Unknown configuration key; see jeppa.example.toml.")
    auth = dict(auth)
    for key in fields:
        name = "OOPZ_LOGIN_" + key.upper() if key in {"phone", "password", "method"} else "OOPZ_" + key.upper()
        if name in env:
            auth[key] = env[name]
        if key in auth and not isinstance(auth[key], str):
            raise ConfigError("All [oopz] values must be strings.")
    method = auth.get("method", "credentials")
    if method not in {"credentials", "password_api"}:
        raise ConfigError("Set method to credentials or password_api; browser login is not supported by Jeppa.")
    required = ("device_id", "person_uid", "jwt_token") if method == "credentials" else ("phone", "password")
    if any(not auth.get(key, "").strip() for key in required):
        raise ConfigError("Missing Oopz configuration. Copy jeppa.example.toml to jeppa.local.toml, fill [oopz], then run jeppa check-config --config jeppa.local.toml. Or set OOPZ_DEVICE_ID/OOPZ_PERSON_UID/OOPZ_JWT_TOKEN; password API login requires OOPZ_LOGIN_METHOD=password_api and OOPZ_LOGIN_PHONE/OOPZ_LOGIN_PASSWORD.")
    auth["method"] = method
    lists = {}
    for key in ("areas", "plugins"):
        value = bot.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigError(f"bot.{key} must be an array of non-empty strings.")
        lists[key] = tuple(value)
    return Config(auth, **lists)
