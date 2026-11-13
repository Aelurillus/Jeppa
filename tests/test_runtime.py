import asyncio
import unittest

from jeppa import Context, Message, Runtime
from jeppa.runtime import builtins


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime("self")
        self.replies = []
        await self.runtime.install(builtins, name="builtin")

    async def asyncTearDown(self):
        await self.runtime.close()

    async def dispatch(self, text, person="other"):
        async def reply(value):
            self.replies.append(value)
        return await self.runtime.dispatch(Context(Message(text, "area", "channel", person, "message", False), reply))

    async def test_command_boundary_arguments_help_and_self_loop(self):
        self.assertFalse(await self.dispatch("/pingpong"))
        self.assertFalse(await self.dispatch("/ping", person="self"))
        self.assertTrue(await self.dispatch("/ping unexpected"))
        self.assertEqual(self.replies, ["Usage: /ping"])
        await self.dispatch("/ping")
        self.assertEqual(self.replies[-1], "pong")
        await self.dispatch("/help")
        self.assertEqual({line.split()[0] for line in self.replies[-1].splitlines()}, {"/help", "/ping"})


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
