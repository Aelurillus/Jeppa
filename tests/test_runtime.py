import asyncio
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from jeppa import Context, Message, Runtime
from jeppa.runtime import CommandPolicy, PluginInfo
from jeppa.plugins import default


def info(identity, name=None):
    return PluginInfo(identity, identity if name is None else name, "1.0.0", "测试插件")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime()
        self.replies = []
        self.plugin = await self.runtime.install(default.setup, info=default.PLUGIN)

    async def asyncTearDown(self):
        await self.runtime.close()

    async def dispatch(self, text, person="other", *, area="area", channel="channel", private=False, platform="oopz", bot_id="self"):
        async def reply(value):
            self.replies.append(value)
        return await self.runtime.dispatch(Context(Message(text, area, channel, person, "message", private, platform=platform, bot_id=bot_id), reply))

    async def test_command_boundary_arguments_help_and_self_loop(self):
        self.assertFalse(await self.dispatch("/pingpong"))
        self.assertFalse(await self.dispatch("/ping", person="self"))
        self.assertTrue(await self.dispatch("/ping unexpected"))
        self.assertIn("/ping", self.replies[-1])
        await self.dispatch("/ping")
        self.assertRegex(self.replies[-1], r"[\u4e00-\u9fff]")
        await self.dispatch("/help")
        self.assertEqual({line.split()[0] for line in self.replies[-1].splitlines() if line.startswith("/")}, {"/help", "/ping", "/status", "/roll", "/choose", "/faq"})


    async def test_startup_rollback_revokes_commands_and_lifo_resources(self):
        closed = []
        async def setup(plugin):
            plugin.defer(lambda: closed.append("first"))
            plugin.command("temporary", lambda *_: None)
            plugin.defer(lambda: closed.append("second"))
            raise ValueError("startup failed")
        with self.assertRaises(ValueError):
            await self.runtime.install(setup, info=info("broken"))
        self.assertEqual(closed, ["second", "first"])
        self.assertFalse(await self.dispatch("/temporary"))

    async def test_cleanup_failure_does_not_skip_other_resources(self):
        closed = []
        async def setup(plugin):
            plugin.defer(lambda: closed.append("closed"))
            def fail():
                raise RuntimeError("cleanup failed")
            plugin.defer(fail)
        plugin = await self.runtime.install(setup, info=info("broken-close"))
        with self.assertRaises(BaseExceptionGroup):
            await plugin.close()
        self.assertEqual(closed, ["closed"])


    async def test_background_failure_visible_and_task_cancelled_on_close(self):
        cancelled = asyncio.Event()
        async def waiting():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def broken():
            raise RuntimeError("background failed")
        async def setup(plugin):
            plugin.task(waiting())
            plugin.task(broken())
        plugin = await self.runtime.install(setup, info=info("tasks"))
        await asyncio.wait_for(self.runtime.failure.wait(), timeout=1)
        self.assertIsInstance(self.runtime.error, RuntimeError)
        with self.assertRaises(BaseExceptionGroup):
            await plugin.close()
        self.assertTrue(cancelled.is_set())

    def test_session_identity_keeps_area_and_private_peer(self):
        channel = Message("", "a", "c", "p", "m", False, platform="oopz", bot_id="self")
        other_area = replace(channel, area="b")
        private = replace(channel, area="", is_private=True)
        other_peer = replace(private, person="q")
        self.assertNotEqual(channel.session_id, other_area.session_id)
        self.assertNotEqual(channel.session_id, private.session_id)
        self.assertNotEqual(private.session_id, other_peer.session_id)

    async def test_constructor_defaults_and_per_command_policy_override(self):
        await self.runtime.close()
        self.runtime = Runtime(
            default_policy=CommandPolicy(users=("operator",), channels=("staff",)),
            policies={"ping": CommandPolicy(users=("other",), channels=("channel",))},
        )
        self.plugin = await self.runtime.install(default.setup, info=default.PLUGIN)
        self.assertTrue(await self.dispatch("/ping"))
        self.assertFalse(await self.dispatch("/ping", person="operator", channel="staff"))
        self.assertFalse(await self.dispatch("/help"))
        self.assertTrue(await self.dispatch("/help", person="operator", channel="staff"))
        self.assertNotIn("/ping", self.replies[-1])

    async def test_empty_allowlists_deny_and_denials_never_reply_or_execute(self):
        for policy in (
            CommandPolicy(users=()), CommandPolicy(channels=()),
            CommandPolicy(users=("operator",)), CommandPolicy(channels=("staff",)),
        ):
            with self.subTest(policy=policy):
                self.runtime.policies["ping"] = policy
                self.assertFalse(await self.dispatch("/ping sensitive-argument"))
        self.assertEqual(self.replies, [])
        self.assertEqual(self.runtime.command_successes, 0)
        self.assertEqual(self.runtime.command_failures, 0)
        self.assertEqual(self.runtime._cooldowns, {})

    async def test_restricted_command_requires_nonempty_explicit_effective_users(self):
        async def setup(plugin):
            async def operator(context, args):
                await context.reply("changed")
            plugin.command("operator", operator, restricted=True)
        await self.runtime.install(setup, info=info("admin"))
        for policy in (CommandPolicy(), CommandPolicy(users=()), CommandPolicy(channels=("channel",))):
            with self.subTest(policy=policy):
                self.runtime.policies["operator"] = policy
                self.assertFalse(await self.dispatch("/operator"))
                await self.dispatch("/help")
                self.assertNotIn("/operator", self.replies[-1])
        self.runtime.policies["operator"] = CommandPolicy(users=("other",), channels=("staff",))
        self.assertFalse(await self.dispatch("/operator"))
        self.assertTrue(await self.dispatch("/operator", channel="staff"))
        self.assertEqual(self.replies[-1], "changed")
        await self.dispatch("/help", channel="staff")
        self.assertIn("/operator", self.replies[-1])
        self.runtime.policies.pop("operator")
        self.runtime.default_policy = CommandPolicy(users=("other",))
        self.assertTrue(await self.dispatch("/operator"))

    async def test_help_does_not_consume_listed_command_cooldown(self):
        self.runtime.policies["ping"] = CommandPolicy(cooldown=10)
        with patch("jeppa.runtime.time", SimpleNamespace(monotonic=lambda: 100)):
            await self.dispatch("/help")
            self.assertTrue(await self.dispatch("/ping"))
            self.assertFalse(await self.dispatch("/ping"))
            await self.dispatch("/help")
            self.assertIn("/ping", self.replies[-1])

    async def test_cooldown_isolates_command_area_channel_user_and_private_peer(self):
        self.runtime.default_policy = CommandPolicy(cooldown=10)
        with patch("jeppa.runtime.time", SimpleNamespace(monotonic=lambda: 100)):
            self.assertTrue(await self.dispatch("/ping"))
            self.assertFalse(await self.dispatch("/ping"))
            self.assertTrue(await self.dispatch("/status"))
            self.assertTrue(await self.dispatch("/ping", area="another-area"))
            self.assertTrue(await self.dispatch("/ping", channel="another-channel"))
            self.assertTrue(await self.dispatch("/ping", person="another-person"))
            self.assertTrue(await self.dispatch("/ping", private=True))
            self.assertFalse(await self.dispatch("/ping", private=True))
            self.assertTrue(await self.dispatch("/ping", person="another-person", private=True))

    async def test_concurrent_dispatch_reserves_cooldown_before_handler_awaits(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []
        async def setup(plugin):
            async def slow(context, args):
                calls.append(1)
                entered.set()
                await release.wait()
            plugin.command("slow", slow)
        await self.runtime.install(setup, info=info("slow-plugin"))
        self.runtime.policies["slow"] = CommandPolicy(cooldown=10)
        with patch("jeppa.runtime.time", SimpleNamespace(monotonic=lambda: 100)):
            first = asyncio.create_task(self.dispatch("/slow"))
            try:
                await entered.wait()
                self.assertFalse(await self.dispatch("/slow"))
            finally:
                release.set()
                self.assertTrue(await first)
        self.assertEqual(calls, [1])

    async def test_expired_and_unloaded_cooldowns_are_removed(self):
        self.runtime.default_policy = CommandPolicy(cooldown=10)
        with patch("jeppa.runtime.time", SimpleNamespace(monotonic=lambda: 100)):
            await self.dispatch("/ping", person="old-user")
            await self.dispatch("/ping", person="another-old-user")
        self.assertEqual(len(self.runtime._cooldowns), 2)
        with patch("jeppa.runtime.time", SimpleNamespace(monotonic=lambda: 110)):
            self.assertTrue(await self.dispatch("/ping"))
        self.assertEqual(len(self.runtime._cooldowns), 1)
        await self.plugin.close()
        self.assertEqual(self.runtime._cooldowns, {})
        self.assertEqual(self.runtime.plugins(), ())
        self.assertFalse(await self.dispatch("/ping"))

    async def test_status_has_separate_states_counters_and_only_loaded_plugins(self):
        async def empty(plugin):
            pass
        temporary = await self.runtime.install(empty, info=info("temporary"))
        await temporary.close()
        async def broken(plugin):
            raise ValueError("secret rollback detail")
        with self.assertRaises(ValueError):
            await self.runtime.install(broken, info=info("rolled-back"))
        self.runtime.set_connection("oopz", "self", transport_open=True, auth_confirmed=False, subscription_sent=True)
        await self.dispatch("/ping")
        await self.dispatch("/status")
        output = self.replies[-1]
        for expected in (
            "已加载插件：默认功能", "连接通道：已连接", "鉴权确认：未确认",
            "域订阅：已发送", "命令成功：1", "命令失败：0",
        ):
            self.assertIn(expected, output)
        for secret in ("temporary", "rolled-back", "secret", "other", "area", "channel", "message"):
            self.assertNotIn(secret, output)
        await self.dispatch("/status extra")
        self.assertIn("/status", self.replies[-1])

    async def test_command_diagnostics_redact_arguments_context_and_error_text(self):
        async def setup(plugin):
            async def broken(context, args):
                raise RuntimeError("credential=SUPERSECRET")
            plugin.command("broken", broken)
        await self.runtime.install(setup, info=info("errors"))
        self.runtime.policies["ping"] = CommandPolicy(users=())
        self.runtime.policies["help"] = CommandPolicy(cooldown=10)
        with self.assertLogs("jeppa.runtime", level="INFO") as logs:
            self.assertFalse(await self.dispatch("/ping SUPERSECRET"))
            self.assertTrue(await self.dispatch("/help SUPERSECRET"))
            self.assertFalse(await self.dispatch("/help SUPERSECRET"))
            with self.assertRaisesRegex(RuntimeError, "SUPERSECRET"):
                await self.dispatch("/broken SUPERSECRET", person="PRIVATEPERSON", area="PRIVATEAREA")
        text = "\n".join(logs.output)
        self.assertIn("RuntimeError", text)
        self.assertEqual([record.levelname for record in logs.records], ["INFO", "INFO", "INFO", "ERROR"])
        for secret in ("SUPERSECRET", "PRIVATEPERSON", "PRIVATEAREA", "credential=", "Traceback"):
            self.assertNotIn(secret, text)
        self.assertEqual(self.runtime.command_successes, 1)
        self.assertEqual(self.runtime.command_failures, 1)

    async def test_cancelled_command_propagates_without_failure_count(self):
        async def setup(plugin):
            async def cancelled(context, args):
                raise asyncio.CancelledError()
            plugin.command("cancelled", cancelled)
        await self.runtime.install(setup, info=info("cancellation"))
        with self.assertLogs("jeppa.runtime", level="INFO") as logs:
            with self.assertRaises(asyncio.CancelledError):
                await self.dispatch("/cancelled")
        self.assertEqual(self.runtime.command_successes, 0)
        self.assertEqual(self.runtime.command_failures, 0)
        self.assertFalse(self.runtime.failure.is_set())

    async def test_plugin_store_requires_configured_storage(self):
        context = Context(Message("", "a", "c", "p", "m", False, platform="oopz", bot_id="self"), None)
        with self.assertRaisesRegex(RuntimeError, "configured Storage"):
            self.plugin.store(context)


    async def test_startup_rollback_clears_reserved_cooldowns(self):
        self.runtime.policies["temporary"] = CommandPolicy(cooldown=10)
        async def setup(plugin):
            async def temporary(context, args):
                await context.reply("temporary")
            plugin.command("temporary", temporary)
            await self.dispatch("/temporary")
            raise ValueError("startup failed")
        with self.assertRaises(ValueError):
            await self.runtime.install(setup, info=info("rollback"))
        self.assertEqual(self.runtime._cooldowns, {})
        self.assertFalse(await self.dispatch("/temporary"))
        self.assertEqual(self.runtime.plugins(), (default.PLUGIN,))

    async def test_status_counts_failed_handlers_without_exposing_errors(self):
        async def setup(plugin):
            async def broken(context, args):
                raise ValueError("SECRET credential")
            plugin.command("broken", broken)
        await self.runtime.install(setup, info=info("errors"))
        with self.assertLogs("jeppa.runtime", level="ERROR"):
            with self.assertRaises(ValueError):
                await self.dispatch("/broken")
        await self.dispatch("/status")
        self.assertIn("命令成功：0", self.replies[-1])
        self.assertIn("命令失败：1", self.replies[-1])
        self.assertNotIn("SECRET", self.replies[-1])
        self.assertNotIn("credential", self.replies[-1])

    async def test_unknown_command_content_is_not_logged(self):
        with patch("jeppa.runtime.logger") as logger:
            self.assertFalse(await self.dispatch("/SECRET-TOKEN confidential-body"))
        logger.info.assert_not_called()
        logger.error.assert_not_called()

    async def test_help_and_status_are_bounded_and_hide_plugin_paths(self):
        async def commands(plugin):
            async def noop(context, args):
                pass
            for index in range(80):
                plugin.command(f"command{index}", noop, description="description" * 10)
        async def empty(plugin):
            pass
        await self.runtime.install(commands, info=info("tools", "工具插件"))
        await self.runtime.install(empty, info=info("greet", "问候插件"))
        await self.dispatch("/status")
        self.assertIn("工具插件", self.replies[-1])
        self.assertIn("问候插件", self.replies[-1])
        self.assertNotIn("PRIVATEUSER", self.replies[-1])
        self.assertNotIn("plugins/", self.replies[-1])
        await self.dispatch("/help")
        self.assertLessEqual(len(self.replies[-1]), 2000)
        self.assertTrue(self.replies[-1].endswith("..."))
        for index in range(80):
            await self.runtime.install(empty, info=info(f"plugin{index}", "插件" + "x" * 40))
        await self.dispatch("/status")
        self.assertLessEqual(len(self.replies[-1]), 2000)
        self.assertTrue(self.replies[-1].endswith("..."))
        self.assertIn("鉴权确认：未确认", self.replies[-1])
        self.assertIn("命令失败：0", self.replies[-1])

    async def test_default_replies_use_chinese_without_changing_command_names(self):
        for command in ("/ping", "/ping extra", "/help", "/help extra", "/status", "/status extra"):
            with self.subTest(command=command):
                await self.dispatch(command)
                self.assertRegex(self.replies[-1], r"[\u4e00-\u9fff]")
        await self.dispatch("/help")
        self.assertIn("📖", self.replies[-1])
        for line in self.replies[-1].splitlines():
            if line.startswith("/"):
                self.assertRegex(line.partition(" — ")[2], r"[\u4e00-\u9fff]")

    def message(self, **changes):
        return replace(Message("", "area", "channel", "other", "m", False, platform="oopz", bot_id="self"), **changes)

    def test_message_requires_nonempty_keyword_account_identity(self):
        with self.assertRaises(TypeError):
            Message("", "a", "c", "p", "m", False)
        for platform, bot_id in (("", "self"), ("oopz", ""), (" ", "self"), ("oopz", None)):
            with self.subTest(platform=platform, bot_id=bot_id), self.assertRaises(ValueError):
                self.message(platform=platform, bot_id=bot_id)
        original = self.message()
        self.assertNotEqual(original.session_id, replace(original, platform="other").session_id)
        self.assertNotEqual(original.session_id, replace(original, bot_id="second").session_id)
        private = replace(original, is_private=True)
        self.assertNotEqual(private.session_id, replace(private, bot_id="second").session_id)

    async def test_cooldowns_and_self_filter_are_per_platform_account(self):
        self.runtime.policies["ping"] = CommandPolicy(cooldown=10)
        self.assertTrue(await self.dispatch("/ping"))
        self.assertFalse(await self.dispatch("/ping"))
        self.assertTrue(await self.dispatch("/ping", bot_id="second"))
        self.assertTrue(await self.dispatch("/ping", platform="other"))
        self.assertFalse(await self.dispatch("/ping", person="second", bot_id="second"))
        self.assertTrue(await self.dispatch("/ping", person="self", bot_id="second"))

    def test_connection_snapshots_isolate_accounts_and_cannot_mutate_runtime(self):
        self.runtime.set_connection("oopz", "self", transport_open=True, auth_confirmed=True, subscription_sent=False)
        self.runtime.set_connection("other", "self", transport_open=False, auth_confirmed=True, subscription_sent=True)
        snapshot = self.runtime.status(self.message())
        self.assertEqual(snapshot["connection"], {"transport_open": True, "auth_confirmed": True, "subscription_sent": False})
        snapshot["connection"]["transport_open"] = False
        self.assertTrue(self.runtime.status(self.message())["connection"]["transport_open"])
        self.assertEqual(self.runtime.status(self.message(bot_id="second"))["connection"], {
            "transport_open": False, "auth_confirmed": False, "subscription_sent": False,
        })
        self.assertTrue(self.runtime.status(self.message(platform="other"))["connection"]["subscription_sent"])
        self.assertEqual(snapshot["plugins"], (default.PLUGIN,))
        self.assertIsInstance(snapshot["uptime"], int)
        self.assertFalse(hasattr(self.runtime, "self_id"))
        self.assertFalse(hasattr(self.runtime, "connection"))

    async def test_plugin_scope_and_policy_intersect_in_public_commands_and_dispatch(self):
        async def setup(plugin):
            async def scoped(context, args):
                await context.reply("scoped")
            plugin.command("scoped", scoped, description="受范围约束", restricted=True)
        plugin = await self.runtime.install(setup, info=info("scope"), areas=("area",), channels=("channel",))
        self.runtime.policies["scoped"] = CommandPolicy(users=("other",), cooldown=10)
        self.assertIn("scoped", {command.name for command in self.runtime.commands(self.message())})
        entry = next(command for command in self.runtime.commands(self.message()) if command.name == "scoped")
        self.assertEqual((entry.plugin_id, entry.restricted), ("scope", True))
        with self.assertRaises(FrozenInstanceError):
            entry.name = "changed"
        for changes in ({"area": "elsewhere"}, {"channel": "elsewhere"}, {"person": "outsider"}, {"is_private": True}):
            with self.subTest(changes=changes):
                self.assertNotIn("scoped", {command.name for command in self.runtime.commands(self.message(**changes))})
        self.assertFalse(await self.dispatch("/scoped", area="elsewhere"))
        self.assertFalse(await self.dispatch("/scoped", channel="elsewhere"))
        self.assertFalse(await self.dispatch("/scoped", private=True))
        self.assertFalse(await self.dispatch("/scoped", person="outsider"))
        self.assertEqual(self.replies, [])
        self.assertTrue(await self.dispatch("/scoped"))
        self.assertFalse(await self.dispatch("/scoped"))
        self.assertIn("scoped", {command.name for command in self.runtime.commands(self.message())})
        await plugin.close()
        self.assertNotIn(info("scope"), self.runtime.plugins())
        for areas, channels in (((), None), (None, ()), (("",), None)):
            plugin = await self.runtime.install(setup, info=info("scope"), areas=areas, channels=channels)
            self.assertFalse(await self.dispatch("/scoped", private=True, area=""))
            await plugin.close()

    async def test_duplicate_plugin_id_rejected_before_setup_and_reserved_during_startup(self):
        calls = []
        entered = asyncio.Event()
        release = asyncio.Event()
        async def setup(plugin):
            calls.append(plugin.id)
            entered.set()
            await release.wait()
        first = asyncio.create_task(self.runtime.install(setup, info=info("unique")))
        try:
            await entered.wait()
            with self.assertRaisesRegex(ValueError, "already registered"):
                await self.runtime.install(setup, info=info("unique"))
        finally:
            release.set()
            loaded = await first
        self.assertEqual(calls, ["unique"])
        with self.assertRaises(ValueError):
            await self.runtime.install(setup, info=info("unique"))
        await loaded.close()
        await self.runtime.install(setup, info=info("unique"))
        self.assertEqual(calls, ["unique", "unique"])

    async def test_configuration_is_copied_and_top_level_immutable(self):
        original = {"answer": 42}
        seen = []
        async def setup(plugin):
            seen.append(dict(plugin.config))
        plugin = await self.runtime.install(setup, info=info("configurable"), config=original)
        original["answer"] = 0
        self.assertEqual(plugin.config["answer"], 42)
        self.assertEqual(seen, [{"answer": 42}])
        with self.assertRaises(TypeError):
            plugin.config["answer"] = 1
        with self.assertRaises(FrozenInstanceError):
            plugin.info.id = "changed"
        for invalid in ("Invalid", "", "with.dot", "1number"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                info(invalid)

    async def test_cancelled_startup_rolls_back_commands_resources_and_id(self):
        entered = asyncio.Event()
        released = []
        async def setup(plugin):
            plugin.defer(lambda: released.append(True))
            async def command(context, args):
                await context.reply("temporary")
            plugin.command("temporary", command)
            entered.set()
            await asyncio.Event().wait()
        starting = asyncio.create_task(self.runtime.install(setup, info=info("cancelled")))
        await entered.wait()
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(released, [True])
        self.assertFalse(await self.dispatch("/temporary"))
        async def replacement(plugin):
            pass
        await self.runtime.install(replacement, info=info("cancelled"))

    async def test_external_module_metadata_settings_disable_and_stable_storage_identity(self):
        from jeppa.storage import Storage
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.py"
            second = root / "renamed.py"
            source = (
                "from jeppa import PluginInfo\n"
                "PLUGIN = PluginInfo('stable', '稳定插件', '1.0.0', '存储回归')\n"
                "async def setup(plugin):\n"
                "    if plugin.config.get('reject'): raise ValueError('拒绝配置')\n"
                "    async def remember(context, args):\n"
                "        store = plugin.store(context)\n"
                "        await context.reply(str(store.get('count', 0)))\n"
                "        store.set('count', store.get('count', 0) + 1)\n"
                "    plugin.command('remember', remember, description='记忆计数')\n"
            )
            first.write_text(source, encoding="utf-8")
            second.write_text(source, encoding="utf-8")
            storage = Storage(root / "data.sqlite3")
            try:
                self.runtime.storage = storage
                disabled = await self.runtime.load(f"{first}:setup", settings={"stable": {"enabled": False, "config": {"reject": True}}})
                self.assertIsNone(disabled)
                self.assertNotIn("stable", {item.id for item in self.runtime.plugins()})
                with self.assertRaises(ValueError):
                    await self.runtime.load(f"{first}:setup", settings={"stable": {"config": {"reject": True}}})
                loaded = await self.runtime.load(f"{first}:setup", settings={"stable": {"config": {"example": 42}, "areas": ["area"], "channels": ["channel"]}})
                self.assertEqual(loaded.config["example"], 42)
                self.assertFalse(await self.dispatch("/remember", area="other"))
                self.assertTrue(await self.dispatch("/remember"))
                self.assertEqual(self.replies[-1], "0")
                with self.assertRaises(ValueError):
                    await self.runtime.load(f"{second}:setup")
                await loaded.close()
                moved = await self.runtime.load(f"{second}:setup")
                self.assertEqual(moved.info.id, "stable")
                await self.dispatch("/remember")
                self.assertEqual(self.replies[-1], "1")
                self.assertNotIn(str(root), str(self.runtime.status(self.message())))
                await moved.close()
            finally:
                storage.close()

    async def test_load_rejects_missing_or_invalid_plugin_metadata_and_sync_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.py"
            for source, error in (
                ("async def setup(plugin): pass\n", AttributeError),
                ("PLUGIN = 'not metadata'\nasync def setup(plugin): pass\n", TypeError),
                ("from jeppa import PluginInfo\nPLUGIN = PluginInfo('sync', '同步', '1', '拒绝同步加载')\ndef setup(plugin): pass\n", TypeError),
            ):
                with self.subTest(source=source):
                    path.write_text(source, encoding="utf-8")
                    with self.assertRaises(error):
                        await self.runtime.load(f"{path}:setup")
            self.assertEqual(self.runtime.plugins(), (default.PLUGIN,))

    async def test_example_greet_uses_module_metadata_and_rejects_unknown_configuration(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "greet.py"
        with self.assertRaises(ValueError):
            await self.runtime.load(f"{example}:setup", settings={"greet": {"config": {"unknown": True}}})
        plugin = await self.runtime.load(f"{example}:setup")
        self.assertEqual(plugin.info.id, "greet")
        await self.dispatch("/greet 世界")
        self.assertEqual(self.replies[-1], "👋 你好，世界！")
