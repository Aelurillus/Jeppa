from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from jeppa.runtime import Message
from jeppa.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "nested" / "state.sqlite3"
        self.storage = Storage(self.path)
        self.addCleanup(self.storage.close)
        self.message = Message("never persist this message", "area", "channel", "person", "message-id", False, platform="oopz", bot_id="bot")

    def namespace(self, *, plugin="plugin", message=None, scope="user"):
        return self.storage.namespace(plugin, self.message if message is None else message, scope=scope)

    def test_json_roundtrip_restart_update_and_delete(self):
        store = self.namespace()
        value = {"unicode": "你好", "nested": [None, True, False, 17, 1.5, {"key": "value"}]}
        store.set("settings", value)
        store.set("nullable", None)
        self.assertEqual(store.get("settings"), value)
        self.assertIsNone(store.get("nullable", "missing"))
        self.assertEqual(store.get("absent", "default"), "default")
        self.storage.close()

        reopened = Storage(self.path)
        self.addCleanup(reopened.close)
        store = reopened.namespace("plugin", self.message)
        self.assertEqual(store.items(), {"nullable": None, "settings": value})
        store.set("settings", ["replacement"])
        self.assertEqual(store.get("settings"), ["replacement"])
        self.assertTrue(store.delete("settings"))
        self.assertFalse(store.delete("settings"))
        self.assertEqual(store.items(), {"nullable": None})
        reopened.close()

        final = Storage(self.path)
        self.addCleanup(final.close)
        self.assertEqual(final.namespace("plugin", self.message).items(), {"nullable": None})

    def test_public_scope_sharing_and_plugin_isolation(self):
        for scope in ("area", "channel", "user"):
            self.namespace(scope=scope).set("key", scope)
        other_person = replace(self.message, person="other")
        other_channel = replace(self.message, channel="other")
        other_area = replace(self.message, area="other")

        self.assertEqual(self.namespace(scope="area", message=other_person).get("key"), "area")
        self.assertEqual(self.namespace(scope="area", message=other_channel).get("key"), "area")
        self.assertEqual(self.namespace(scope="channel", message=other_person).get("key"), "channel")
        self.assertIsNone(self.namespace(scope="channel", message=other_channel).get("key"))
        self.assertIsNone(self.namespace(scope="user", message=other_person).get("key"))
        self.assertIsNone(self.namespace(scope="user", message=other_channel).get("key"))
        for scope in ("area", "channel", "user"):
            with self.subTest(scope=scope):
                self.assertIsNone(self.namespace(scope=scope, message=other_area).get("key"))
                self.assertIsNone(self.namespace(scope=scope, plugin="other").get("key"))
                self.assertEqual(self.namespace(scope=scope).get("key"), scope)

    def test_private_scopes_isolate_peers_channels_and_public_data(self):
        private = replace(self.message, is_private=True)
        for scope in ("channel", "user"):
            with self.subTest(scope=scope):
                self.namespace(scope=scope).set("secret", "public")
                store = self.namespace(scope=scope, message=private)
                self.assertIsNone(store.get("secret"))
                store.set("secret", scope)
                self.assertIsNone(self.namespace(scope=scope, message=replace(private, person="other")).get("secret"))
                self.assertIsNone(self.namespace(scope=scope, message=replace(private, channel="other")).get("secret"))
                self.assertIsNone(self.namespace(scope=scope, message=private, plugin="other").get("secret"))
                self.assertEqual(self.namespace(scope=scope, message=replace(private, area="unused")).get("secret"), scope)
                self.assertEqual(self.namespace(scope=scope).get("secret"), "public")
        self.assertEqual(self.namespace(scope="channel", message=private).get("secret"), "channel")
        self.assertEqual(self.namespace(scope="user", message=private).get("secret"), "user")

    def test_invalid_scopes_and_private_area_are_rejected(self):
        for scope in ("", "global", "USER"):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.namespace(scope=scope)
        with self.assertRaises(ValueError):
            self.namespace(scope="area", message=replace(self.message, is_private=True))

    def test_schema_version_is_two_and_future_version_is_not_changed(self):
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        self.storage.close()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 3")
        with self.assertRaisesRegex(RuntimeError, "schema version"):
            Storage(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_invalid_json_does_not_replace_committed_value(self):
        store = self.namespace()
        store.set("stable", {"old": True})
        for value in (object(), float("nan"), float("inf")):
            with self.subTest(value=type(value).__name__), self.assertRaises((TypeError, ValueError)):
                store.set("stable", value)
            self.assertEqual(store.get("stable"), {"old": True})
        value = store.get("stable")
        value["old"] = False
        self.assertEqual(store.get("stable"), {"old": True})

    def test_sql_parameters_and_no_automatic_message_history(self):
        key = "quote'; DROP TABLE plugin_values; --"
        self.namespace(plugin="plugin'quoted").set(key, "safe")
        self.assertEqual(self.namespace(plugin="plugin'quoted").get(key), "safe")
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT * FROM plugin_values").fetchall()
        serialized = json.dumps(rows)
        self.assertNotIn(self.message.text, serialized)
        self.assertNotIn(self.message.message_id, serialized)
        self.assertEqual(len(rows), 1)

    def test_all_scopes_isolate_platforms_and_bot_accounts(self):
        for private in (False, True):
            message = replace(self.message, is_private=private)
            for scope in (("channel", "user") if private else ("area", "channel", "user")):
                with self.subTest(private=private, scope=scope):
                    self.namespace(message=message, scope=scope).set("key", "original")
                    for other in (replace(message, platform="other"), replace(message, bot_id="second")):
                        store = self.namespace(message=other, scope=scope)
                        self.assertEqual(store.items(), {})
                        store.set("key", "isolated")
                        self.assertEqual(self.namespace(message=message, scope=scope).get("key"), "original")

    def legacy_database(self, name="legacy.sqlite3", rows=None):
        path = self.path.parent / name
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("""CREATE TABLE plugin_values (
                    plugin TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
                    area TEXT NOT NULL, channel TEXT NOT NULL, person TEXT NOT NULL,
                    key TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY (plugin, kind, scope, area, channel, person, key)
                ) WITHOUT ROWID""")
                connection.executemany("INSERT INTO plugin_values VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows or [
                    ("jeppa.plugins.utility:setup", "public", "area", "area", "", "", "rules", '"旧版答案"'),
                    ("custom.module:setup", "private", "user", "", "channel", "person", "setting", '{"old":true}'),
                ])
                connection.execute("PRAGMA user_version = 1")
        return path

    def test_v1_upgrades_without_claiming_then_migrates_faq_and_keeps_custom_id(self):
        path = self.legacy_database()
        storage = Storage(path)
        self.addCleanup(storage.close)
        self.assertEqual(storage.legacy_oopz_count(), 2)
        self.assertEqual(storage.namespace("default", self.message, scope="area").items(), {})
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT DISTINCT platform, bot_id FROM plugin_values").fetchall(), [("oopz", "")])
        self.assertEqual(storage.migrate_legacy_oopz("bot"), 2)
        self.assertEqual(storage.legacy_oopz_count(), 0)
        self.assertEqual(storage.namespace("default", self.message, scope="area").items(), {"rules": "旧版答案"})
        private = replace(self.message, is_private=True)
        self.assertEqual(storage.namespace("custom.module:setup", private).get("setting"), {"old": True})
        self.assertEqual(storage.namespace("default", replace(self.message, bot_id="other"), scope="area").items(), {})
        self.assertEqual(storage.namespace("default", replace(self.message, platform="other"), scope="area").items(), {})
        self.assertEqual(storage.migrate_legacy_oopz("bot"), 0)
        self.assertEqual(storage.migrate_legacy_oopz("other"), 0)
        storage.close()
        reopened = Storage(path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.namespace("default", self.message, scope="area").get("rules"), "旧版答案")

    def test_claim_collision_rolls_back_all_rows_without_overwrite(self):
        path = self.legacy_database()
        storage = Storage(path)
        self.addCleanup(storage.close)
        target = storage.namespace("default", self.message, scope="area")
        target.set("rules", "新账号已有答案")
        with self.assertRaises(sqlite3.IntegrityError):
            storage.migrate_legacy_oopz("bot")
        self.assertEqual(storage.legacy_oopz_count(), 2)
        self.assertEqual(target.items(), {"rules": "新账号已有答案"})
        self.assertEqual(storage.namespace("custom.module:setup", replace(self.message, is_private=True)).items(), {})
        with closing(sqlite3.connect(path)) as connection:
            rows = connection.execute("SELECT plugin, bot_id FROM plugin_values ORDER BY plugin").fetchall()
        self.assertEqual(rows, [("custom.module:setup", ""), ("default", "bot"), ("jeppa.plugins.utility:setup", "")])
        target.delete("rules")
        self.assertEqual(storage.migrate_legacy_oopz("bot"), 2)
        self.assertEqual(target.get("rules"), "旧版答案")

    def test_legacy_alias_collision_is_atomic_even_without_existing_target_account(self):
        path = self.legacy_database(rows=[
            ("jeppa.plugins.utility:setup", "public", "area", "area", "", "", "rules", '"旧入口"'),
            ("default", "public", "area", "area", "", "", "rules", '"稳定标识"'),
        ])
        storage = Storage(path)
        self.addCleanup(storage.close)
        with self.assertRaises(sqlite3.IntegrityError):
            storage.migrate_legacy_oopz("bot")
        self.assertEqual(storage.legacy_oopz_count(), 2)
        self.assertEqual(storage.namespace("default", self.message, scope="area").items(), {})

    def test_failed_schema_upgrade_rolls_back_ddl_and_version(self):
        path = self.legacy_database()
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("CREATE TABLE plugin_values_v1 (untouched TEXT)")
                connection.execute("INSERT INTO plugin_values_v1 VALUES ('keep')")
        with self.assertRaises(sqlite3.OperationalError):
            Storage(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM plugin_values").fetchone()[0], 2)
            self.assertNotIn("bot_id", [row[1] for row in connection.execute("PRAGMA table_info(plugin_values)")])
            self.assertEqual(connection.execute("SELECT * FROM plugin_values_v1").fetchall(), [("keep",)])

    def test_claim_rejects_empty_identity_without_modifying_legacy_rows(self):
        path = self.legacy_database()
        storage = Storage(path)
        self.addCleanup(storage.close)
        for identity in ("", " ", None, 42):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                storage.migrate_legacy_oopz(identity)
        self.assertEqual(storage.legacy_oopz_count(), 2)

    def test_claim_does_not_touch_another_platform_or_already_assigned_account(self):
        self.namespace(scope="area").set("rules", "existing account")
        other = replace(self.message, platform="other")
        self.namespace(message=other, scope="area").set("rules", "other platform")
        self.assertEqual(self.storage.migrate_legacy_oopz("second"), 0)
        self.assertEqual(self.namespace(scope="area").get("rules"), "existing account")
        self.assertEqual(self.namespace(message=other, scope="area").get("rules"), "other platform")

    def test_copy_failure_after_table_rename_restores_original_schema_and_data(self):
        path = self.path.parent / "malformed-v1.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("CREATE TABLE plugin_values (plugin TEXT, original TEXT)")
                connection.execute("INSERT INTO plugin_values VALUES ('keep', 'original-data')")
                connection.execute("PRAGMA user_version = 1")
        with self.assertRaises(sqlite3.OperationalError):
            Storage(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT * FROM plugin_values").fetchall(), [("keep", "original-data")])
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("plugin_values",)])
