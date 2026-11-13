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
