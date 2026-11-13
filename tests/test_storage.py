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
        self.message = Message("never persist this message", "area", "channel", "person", "message-id", False)

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

    def test_schema_version_is_one_and_future_version_is_not_changed(self):
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.storage.close()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 2")
        with self.assertRaisesRegex(RuntimeError, "schema version"):
            Storage(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

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
