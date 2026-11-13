"""Offline diagnostics, sanitized logs and real early-startup cleanup."""
import asyncio
from contextlib import redirect_stderr
from importlib.metadata import version
import io
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jeppa import cli, daemon
from jeppa.config import Config
from test_daemon import subprocess_environment


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, *arguments):
        return subprocess.run([sys.executable, "-m", "jeppa", *arguments], cwd=self.root,
                              env=subprocess_environment(), capture_output=True, encoding="utf-8", timeout=25)

    def test_version_uses_installed_metadata_and_needs_no_configuration(self):
        result = self.invoke("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "jeppa " + version("jeppa"))

    def test_offline_management_does_not_require_account_credentials(self):
        self.assertEqual(self.invoke("status").returncode, 0)
        self.assertRegex(self.invoke("status").stdout, r"[\u4e00-\u9fff]")
        result = self.invoke("logs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"[\u4e00-\u9fff]")
        result = self.invoke("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"[\u4e00-\u9fff]")

    def test_help_and_syntax_diagnostics_are_chinese_without_configuration(self):
        for arguments in (("--help",), ("run", "--help")):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, r"[\u4e00-\u9fff]")
                self.assertNotRegex(result.stdout, r"(?i)usage:|options:|show this help|positional arguments:")
                self.assertIn("--help", result.stdout)
        for arguments in ((), ("unknown",), ("status", "--config"),
                          ("status", "--unknown"), ("--version=extra",)):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertRegex(result.stderr, r"[\u4e00-\u9fff]")
                self.assertNotRegex(result.stderr, r"(?i)usage:|error:|invalid choice|expected one argument|unrecognized arguments|required:")
                self.assertEqual(result.stdout, "")

    def test_startup_failure_is_real_subprocess_redacted_and_releases_ownership(self):
        # Failure happens in actual child plugin loading, before any SDK login.
        # No mocked Popen/transport is used as proof of process launch.
        plugin = self.root / "broken.py"
        plugin.write_text("from jeppa import PluginInfo\nPLUGIN = PluginInfo('broken', '故障测试', '1.0.0', '清理回归')\nasync def setup(plugin):\n    raise RuntimeError('jwt-super-private-secret')\n", encoding="utf-8")
        config = self.root / "config.toml"
        config.write_text('''[oopz]
device_id = "test-device"
person_uid = "test-person"
jwt_token = "jwt-super-private-secret"
[bot]
plugins = ["broken.py:setup"]
[storage]
path = "state.sqlite3"
[logging]
path = "jeppa.log"
''', encoding="utf-8")
        result = self.invoke("start", "--config", str(config))
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"[\u4e00-\u9fff]")
        self.assertEqual(result.stdout, "")
        self.assertNotIn("jwt-super-private-secret", result.stdout + result.stderr)
        database = self.root / "state.sqlite3"
        self.assertFalse(daemon.is_running(database))
        self.assertFalse(daemon.control_paths(database)[1].exists())
        log = (self.root / "jeppa.log").read_text(encoding="utf-8")
        self.assertIn("RuntimeError", log)
        self.assertRegex(log, r"[\u4e00-\u9fff]")
        self.assertNotIn("jwt-super-private-secret", log)
        self.assertNotIn("Traceback", log)
        # Repeating startup demonstrates cleanup rather than a poisoned lock.
        again = self.invoke("run", "--config", str(config))
        self.assertNotEqual(again.returncode, 0)
        self.assertFalse(daemon.is_running(database))
        self.assertFalse(daemon.control_paths(database)[1].exists())

    def test_logs_only_prints_bounded_recognized_sanitized_messages(self):
        log = self.root / "jeppa.log"
        log.write_text("INFO jwt-super-private-secret\n" + "INFO 本地运行时已停止\n" * 70 +
                       "INFO runtime-stopped\nINFO 任意中文 jwt-super-private-secret\n"
                       "ERROR Traceback jwt-super-private-secret\n", encoding="utf-8")
        config = self.root / "offline.toml"
        config.write_text('[logging]\npath = "jeppa.log"\n', encoding="utf-8")
        result = self.invoke("logs", "--config", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), cli.LOG_LINES)
        self.assertNotIn("jwt-super-private-secret", result.stdout + result.stderr)
        self.assertNotIn("runtime-stopped", result.stdout)

    def test_logging_rejects_untrusted_sources_and_nonfixed_payloads(self):
        sanitizer = cli._Sanitized()
        for name, pathname, message in (
            ("plugin", cli.__file__, "本地运行时已停止"),
            ("jeppa.cli", str(self.root / "plugin.py"), "本地运行时已停止"),
            ("jeppa.cli", cli.__file__, "本地运行时已停止 jwt-super-private-secret"),
            ("jeppa.cli", cli.__file__, "runtime-stopped"),
            ("jeppa.cli", cli.__file__, "命令=ping 结果=成功 耗时=0.001000秒 异常=RuntimeError"),
        ):
            with self.subTest(name=name, pathname=pathname, message=message):
                record = logging.LogRecord(name, logging.INFO, pathname, 1, message, (), None)
                self.assertFalse(sanitizer.filter(record))


    def test_rotating_handlers_are_sanitized_closed_and_restored(self):
        config = SimpleNamespace(log_file=self.root / "jeppa.log", log_max_bytes=120, log_backups=2)
        async def execute_commands():
            from jeppa import Context, Message, Runtime
            runtime = Runtime()
            async def reply(text):
                pass
            try:
                await runtime.load("jeppa.plugins.default:setup")
                for _ in range(20):
                    await runtime.dispatch(Context(Message("/ping jwt-super-private-secret", "a", "c", "p", "m", False,
                                                           platform="oopz", bot_id="self"), reply))
                cli.logger.error("jwt-super-private-secret", exc_info=RuntimeError("secret"))
                logging.getLogger("oopz_sdk").error("jwt-super-private-secret")
            finally:
                await runtime.close()
        with redirect_stderr(io.StringIO()):
            for _ in range(2):
                with cli._logging(config):
                    asyncio.run(execute_commands())
        rotated = config.log_file.with_name("jeppa.log.1")
        rotated_text = rotated.read_text(encoding="utf-8")
        self.assertIn("ping", rotated_text)
        self.assertRegex(rotated_text, r"[\u4e00-\u9fff]")
        for path in self.root.glob("jeppa.log*"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("jwt-super-private-secret", text)
            self.assertNotIn("Traceback", text)


class LaunchCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_failure_closes_storage_and_runtime(self):
        class Store:
            closed = False

            def __init__(self, path):
                self.path = path

            def close(self):
                self.closed = True

        class BrokenRuntime:
            closed = False

            def __init__(self, **options):
                self.store = options["storage"]
                self.options = options

            async def load(self, *args, **kwargs):
                raise RuntimeError("private-text")

            async def close(self):
                self.closed = True

        stores, runtimes = [], []

        def make_store(path):
            value = Store(path)
            stores.append(value)
            return value

        def make_runtime(**options):
            value = BrokenRuntime(**options)
            runtimes.append(value)
            return value

        config = Config()
        with patch.object(cli, "Storage", side_effect=make_store), patch.object(cli, "Runtime", side_effect=make_runtime):
            with self.assertRaises(RuntimeError):
                await cli.launch(config)
        self.assertTrue(stores[0].closed)
        self.assertTrue(runtimes[0].closed)
