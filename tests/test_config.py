import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from jeppa.cli import main
from jeppa.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_environment_only_and_redacted_repr(self):
        config = load_config(environ={"OOPZ_DEVICE_ID": "device", "OOPZ_PERSON_UID": "person", "OOPZ_JWT_TOKEN": "secret-value"})
        self.assertEqual(config.auth["jwt_token"], "secret-value")
        self.assertNotIn("secret-value", repr(config))

    def test_toml_errors_and_unknown_keys_never_echo_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            for text in ('password = "private-value"\n', '[oopz]\njwt_token = "unterminated-private-value'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ConfigError) as raised:
                    load_config(path, environ={})
                self.assertNotIn("private-value", str(raised.exception))
                self.assertRegex(str(raised.exception), r"[\u4e00-\u9fff]")

    def test_missing_config_actionable_and_exit_two(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["check-config"]), 2)
        self.assertIn("jeppa.example.toml", errors.getvalue())
        self.assertIn("OOPZ_DEVICE_ID", errors.getvalue())

    def test_file_and_environment_precedence_without_secret_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('[oopz]\nmethod="password_api"\nphone="placeholder"\npassword="keep spaces "\n[bot]\nareas=["area"]\nplugins=["examples/greet.py:setup"]\n', encoding="utf-8")
            config = load_config(path, environ={"OOPZ_LOGIN_PASSWORD": " override "})
            self.assertEqual(config.auth["password"], " override ")
            self.assertEqual(config.areas, ("area",))
            self.assertEqual(config.plugins, ("examples/greet.py:setup",))

    def test_policy_inheritance_empty_denial_and_storage_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('''[oopz]
method = "password_api"
phone = "placeholder"
password = "placeholder"
[permissions]
users = ["operator"]
channels = ["general"]
cooldown = 3
[permissions.commands.ping]
users = []
cooldown = 0
[storage]
path = "state/bot.sqlite3"
[logging]
path = "state/bot.log"
max_bytes = 4096
backups = 2
''', encoding="utf-8")
            config = load_config(path, environ={})
            self.assertEqual(config.default_policy.users, ("operator",))
            self.assertEqual(config.policies["ping"].users, ())
            self.assertEqual(config.policies["ping"].channels, ("general",))
            self.assertEqual(config.policies["ping"].cooldown, 0)
            self.assertEqual(config.database, Path("state/bot.sqlite3"))
            self.assertEqual(config.log_file, Path("state/bot.log"))
            self.assertEqual((config.log_max_bytes, config.log_backups), (4096, 2))

    def test_invalid_release_options_are_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            auth = '[oopz]\nmethod="password_api"\nphone="placeholder"\npassword="private-value"\n'
            for text in (
                '[permissions]\ncooldown=nan',
                '[permissions]\ncooldown=-1',
                '[permissions]\ncooldown=true',
                '[permissions]\nusers="private-value"',
                '[permissions.commands.ping]\nunknown="private-value"',
                '[permissions.commands."private value"]\ncooldown=0',
                '[storage]\npath=""',
                '[logging]\nmax_bytes=1',
                '[logging]\nbackups=true',
                '[logging]\nunknown="private-value"',
            ):
                with self.subTest(option=text):
                    path.write_text(auth + text, encoding="utf-8")
                    with self.assertRaises(ConfigError) as raised:
                        load_config(path, environ={})
                    self.assertNotIn("private-value", str(raised.exception))

    def test_offline_management_does_not_require_account_material(self):
        config = load_config(environ={}, require_auth=False)
        self.assertEqual(config.auth, {"method": "credentials"})
        self.assertEqual(config.database, Path("data/jeppa.sqlite3"))
        with self.assertRaises(ConfigError):
            load_config(environ={})
