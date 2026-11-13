import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jeppa import Context, Message, Runtime
from jeppa.runtime import CommandPolicy, builtins


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime("self")
        self.replies = []
        await self.runtime.install(builtins, name="builtin")

    async def asyncTearDown(self):
        await self.runtime.close()

    async def dispatch(self, text, person="other", *, area="area", channel="channel", private=False):
        async def reply(value):
            self.replies.append(value)
        return await self.runtime.dispatch(Context(Message(text, area, channel, person, "message", private), reply))

    async def test_command_boundary_arguments_help_and_self_loop(self):
        self.assertFalse(await self.dispatch("/pingpong"))
        self.assertFalse(await self.dispatch("/ping", person="self"))
        self.assertTrue(await self.dispatch("/ping unexpected"))
        self.assertIn("/ping", self.replies[-1])
        await self.dispatch("/ping")
        self.assertRegex(self.replies[-1], r"[\u4e00-\u9fff]")
        await self.dispatch("/help")
        self.assertEqual({line.split()[0] for line in self.replies[-1].splitlines() if line.startswith("/")}, {"/help", "/ping", "/status"})


    async def test_startup_rollback_revokes_commands_and_lifo_resources(self):
        closed = []
        async def setup(plugin):
            plugin.defer(lambda: closed.append("first"))
            plugin.command("temporary", lambda *_: None)
            plugin.defer(lambda: closed.append("second"))
            raise ValueError("startup failed")
        with self.assertRaises(ValueError):
            await self.runtime.install(setup, name="broken")
        self.assertEqual(closed, ["second", "first"])
        self.assertFalse(await self.dispatch("/temporary"))

    async def test_cleanup_failure_does_not_skip_other_resources(self):
        closed = []
        async def setup(plugin):
            plugin.defer(lambda: closed.append("closed"))
            def fail():
                raise RuntimeError("cleanup failed")
            plugin.defer(fail)
        plugin = await self.runtime.install(setup, name="broken-close")
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
        plugin = await self.runtime.install(setup, name="tasks")
        await asyncio.wait_for(self.runtime.failure.wait(), timeout=1)
        self.assertIsInstance(self.runtime.error, RuntimeError)
        with self.assertRaises(BaseExceptionGroup):
            await plugin.close()
        self.assertTrue(cancelled.is_set())

    def test_session_identity_keeps_area_and_private_peer(self):
        channel = Message("", "a", "c", "p", "m", False)
        other_area = Message("", "b", "c", "p", "m", False)
        private = Message("", "", "c", "p", "m", True)
        other_peer = Message("", "", "c", "q", "m", True)
        self.assertNotEqual(channel.session_id, other_area.session_id)
        self.assertNotEqual(channel.session_id, private.session_id)
        self.assertNotEqual(private.session_id, other_peer.session_id)

    async def test_constructor_defaults_and_per_command_policy_override(self):
        await self.runtime.close()
        self.runtime = Runtime(
            "self", default_policy=CommandPolicy(users=("operator",), channels=("staff",)),
            policies={"ping": CommandPolicy(users=("other",), channels=("channel",))},
        )
        await self.runtime.install(builtins, name="builtin")
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
        await self.runtime.install(setup, name="admin")
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
        await self.runtime.install(setup, name="slow-plugin")
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
        await self.runtime._plugins[0].close()
        self.assertEqual(self.runtime._cooldowns, {})
        self.assertEqual(self.runtime._plugins, [])
        self.assertFalse(await self.dispatch("/ping"))

    async def test_status_has_separate_states_counters_and_only_loaded_plugins(self):
        async def empty(plugin):
            pass
        temporary = await self.runtime.install(empty, name="temporary")
        await temporary.close()
        async def broken(plugin):
            raise ValueError("secret rollback detail")
        with self.assertRaises(ValueError):
            await self.runtime.install(broken, name="rolled-back")
        self.runtime.connection.update(transport_open=True, auth_confirmed=False, subscription_sent=True)
        await self.dispatch("/ping")
        await self.dispatch("/status")
        output = self.replies[-1]
        for expected in (
            "已加载插件：builtin", "连接通道：已连接", "鉴权确认：未确认",
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
        await self.runtime.install(setup, name="errors")
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
        await self.runtime.install(setup, name="cancellation")
        with self.assertLogs("jeppa.runtime", level="INFO") as logs:
            with self.assertRaises(asyncio.CancelledError):
                await self.dispatch("/cancelled")
        self.assertEqual(self.runtime.command_successes, 0)
        self.assertEqual(self.runtime.command_failures, 0)
        self.assertFalse(self.runtime.failure.is_set())

    async def test_plugin_store_requires_configured_storage(self):
        context = Context(Message("", "a", "c", "p", "m", False), None)
        with self.assertRaisesRegex(RuntimeError, "configured Storage"):
            self.runtime._plugins[0].store(context)


    async def test_startup_rollback_clears_reserved_cooldowns(self):
        self.runtime.policies["temporary"] = CommandPolicy(cooldown=10)
        async def setup(plugin):
            async def temporary(context, args):
                await context.reply("temporary")
            plugin.command("temporary", temporary)
            await self.dispatch("/temporary")
            raise ValueError("startup failed")
        with self.assertRaises(ValueError):
            await self.runtime.install(setup, name="rollback")
        self.assertEqual(self.runtime._cooldowns, {})
        self.assertFalse(await self.dispatch("/temporary"))
        self.assertEqual([plugin.name for plugin in self.runtime._plugins], ["builtin"])

    async def test_status_counts_failed_handlers_without_exposing_errors(self):
        async def setup(plugin):
            async def broken(context, args):
                raise ValueError("SECRET credential")
            plugin.command("broken", broken)
        await self.runtime.install(setup, name="errors")
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
        await self.runtime.install(commands, name="C:\\Users\\PRIVATEUSER\\plugins\\tools.py:setup")
        await self.runtime.install(empty, name="/home/PRIVATEUSER/plugins/greet.py:setup")
        await self.dispatch("/status")
        self.assertIn("tools:setup", self.replies[-1])
        self.assertIn("greet:setup", self.replies[-1])
        self.assertNotIn("PRIVATEUSER", self.replies[-1])
        self.assertNotIn("plugins/", self.replies[-1])
        await self.dispatch("/help")
        self.assertLessEqual(len(self.replies[-1]), 2000)
        self.assertTrue(self.replies[-1].endswith("..."))
        for index in range(80):
            await self.runtime.install(empty, name=f"plugin{index}" + "x" * 40)
        await self.dispatch("/status")
        self.assertLessEqual(len(self.replies[-1]), 2000)
        self.assertTrue(self.replies[-1].endswith("..."))
        self.assertIn("鉴权确认：未确认", self.replies[-1])
        self.assertIn("命令失败：0", self.replies[-1])

    async def test_builtin_replies_use_chinese_without_changing_command_names(self):
        for command in ("/ping", "/ping extra", "/help", "/help extra", "/status", "/status extra"):
            with self.subTest(command=command):
                await self.dispatch(command)
                self.assertRegex(self.replies[-1], r"[\u4e00-\u9fff]")
        await self.dispatch("/help")
        self.assertIn("📖", self.replies[-1])
        for line in self.replies[-1].splitlines():
            if line.startswith("/"):
                self.assertRegex(line.partition(" — ")[2], r"[\u4e00-\u9fff]")
