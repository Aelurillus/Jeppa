"""Plugin-owned JSON values, explicitly scoped without storing message history."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import Message

_CREATE_VALUES = """CREATE TABLE plugin_values (
    plugin TEXT NOT NULL,
    platform TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    area TEXT NOT NULL,
    channel TEXT NOT NULL,
    person TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (plugin, platform, bot_id, kind, scope, area, channel, person, key)
) WITHOUT ROWID"""
_IDENTITY_WHERE = "plugin=? AND platform=? AND bot_id=? AND kind=? AND scope=? AND area=? AND channel=? AND person=?"



class Storage:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        try:
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported storage schema version")
            if version != 2:
                with self._connection:
                    self._connection.execute("BEGIN IMMEDIATE")
                    if version == 1:
                        self._connection.execute("ALTER TABLE plugin_values RENAME TO plugin_values_v1")
                    self._connection.execute(_CREATE_VALUES)
                    if version == 1:
                        self._connection.execute(
                            "INSERT INTO plugin_values "
                            "(plugin, platform, bot_id, kind, scope, area, channel, person, key, value) "
                            "SELECT plugin, 'oopz', '', kind, scope, area, channel, person, key, value "
                            "FROM plugin_values_v1"
                        )
                        self._connection.execute("DROP TABLE plugin_values_v1")
                    self._connection.execute("PRAGMA user_version = 2")
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def legacy_oopz_count(self) -> int:
        return self._connection.execute(
            "SELECT COUNT(*) FROM plugin_values WHERE platform='oopz' AND bot_id=''"
        ).fetchone()[0]

    def migrate_legacy_oopz(self, bot_id: str) -> int:
        """Claim all unassigned Oopz rows atomically; collisions never overwrite data."""
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("Legacy migration requires a nonempty bot_id")
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "UPDATE plugin_values SET bot_id=?, "
                "plugin=CASE WHEN plugin='jeppa.plugins.utility:setup' THEN 'default' ELSE plugin END "
                "WHERE platform='oopz' AND bot_id=''",
                (bot_id,),
            )
        return cursor.rowcount

    def namespace(self, plugin: str, message: Message, *, scope: str = "user") -> Namespace:
        if scope not in ("area", "channel", "user"):
            raise ValueError("Storage scope must be area, channel or user")
        if message.is_private:
            if scope == "area":
                raise ValueError("Private messages have no area scope")
            identity = (plugin, message.platform, message.bot_id, "private", scope, "", message.channel, message.person)
        else:
            identity = (
                plugin, message.platform, message.bot_id, "public", scope, message.area,
                message.channel if scope != "area" else "",
                message.person if scope == "user" else "",
            )
        return Namespace(self._connection, identity)


@dataclass(frozen=True, slots=True)
class Namespace:
    _connection: sqlite3.Connection
    _identity: tuple[str, ...]

    def get(self, key: str, default: Any = None) -> Any:
        row = self._connection.execute(
            f"SELECT value FROM plugin_values WHERE {_IDENTITY_WHERE} AND key=?",
            (*self._identity, key),
        ).fetchone()
        return default if row is None else json.loads(row[0])

    def set(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self._connection:
            self._connection.execute(
                "INSERT INTO plugin_values (plugin, platform, bot_id, kind, scope, area, channel, person, key, value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (plugin, platform, bot_id, kind, scope, area, channel, person, key) "
                "DO UPDATE SET value=excluded.value",
                (*self._identity, key, encoded),
            )

    def delete(self, key: str) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                f"DELETE FROM plugin_values WHERE {_IDENTITY_WHERE} AND key=?",
                (*self._identity, key),
            )
        return cursor.rowcount != 0

    def items(self) -> dict[str, Any]:
        rows = self._connection.execute(
            f"SELECT key, value FROM plugin_values WHERE {_IDENTITY_WHERE} ORDER BY key",
            self._identity,
        ).fetchall()
        return {key: json.loads(value) for key, value in rows}
