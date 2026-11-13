"""Consumer-visible SDK auth, cancellation, failure and edit boundaries."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oopz_sdk.events.parser import EventParser

from jeppa import Runtime
from jeppa.oopz import OopzAdapter
from jeppa.runtime import CommandPolicy, Context, Message, builtins


def event(number, data):
    return EventParser().parse({"event": number, "body": json.dumps(data)})


def message(number=9, text="/ping", person="other"):
    return event(number, {"data": {"area": "a", "channel": "c", "person": person, "messageId": "m", "text": text}})


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime("self")
        await self.runtime.install(builtins, name="builtin")
        self.bot = SimpleNamespace(
            config=SimpleNamespace(person_uid="self"),
            messages=SimpleNamespace(send_message=AsyncMock(), send_private_message=AsyncMock()),
            ws=SimpleNamespace(send_subscribe_area_events=AsyncMock()),
        )
        self.adapter = OopzAdapter(self.bot, self.runtime, ("a",))

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_open_sends_configured_subscription_without_auth_confirmation(self):
        await self.adapter.opened(None)
        self.assertTrue(self.adapter.transport_open)
        self.assertFalse(self.adapter.auth_confirmed)
        self.assertTrue(self.adapter.subscription_sent)
        self.bot.ws.send_subscribe_area_events.assert_awaited_once_with(["a"])
        self.assertEqual(self.runtime.connection, {
            "transport_open": True, "auth_confirmed": False, "subscription_sent": True,
        })

    async def test_explicit_auth_confirmation_does_not_repeat_subscription(self):
        await self.adapter.opened(None)
        await self.adapter.emit_event(event(21, {"checkRes": "true"}))
        self.assertFalse(self.adapter.auth_confirmed)
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        self.assertTrue(self.adapter.auth_confirmed)
        self.assertTrue(self.runtime.connection["auth_confirmed"])
        self.bot.ws.send_subscribe_area_events.assert_awaited_once_with(["a"])

    async def test_close_then_reopen_resets_confirmation_and_resubscribes(self):
        await self.adapter.opened(None)
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        await self.adapter.closed(None, None)
        self.assertFalse(self.adapter.transport_open)
        self.assertFalse(self.adapter.auth_confirmed)
        self.assertFalse(self.adapter.subscription_sent)
        self.assertEqual(self.runtime.connection, {
            "transport_open": False, "auth_confirmed": False, "subscription_sent": False,
        })
        await self.adapter.opened(None)
        self.assertTrue(self.adapter.transport_open)
        self.assertFalse(self.adapter.auth_confirmed)
        self.assertTrue(self.adapter.subscription_sent)
        self.assertEqual(self.bot.ws.send_subscribe_area_events.await_count, 2)
        self.bot.ws.send_subscribe_area_events.assert_awaited_with(["a"])

    async def test_empty_areas_never_sends_subscription(self):
        self.adapter = OopzAdapter(self.bot, self.runtime, ())
        await self.adapter.opened(None)
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        self.assertTrue(self.adapter.auth_confirmed)
        self.assertFalse(self.adapter.subscription_sent)
        self.bot.ws.send_subscribe_area_events.assert_not_awaited()

    async def test_dispatch_failure_is_fatal_not_retried_or_swallowed(self):
        self.bot.messages.send_message.side_effect = RuntimeError("reply failed")
        with self.assertRaises(RuntimeError):
            await self.adapter.emit_event(message())
        self.assertTrue(self.runtime.failure.is_set())
        self.assertIsInstance(self.runtime.error, RuntimeError)
        self.bot.messages.send_message.assert_awaited_once()
        self.assertEqual(self.runtime.command_successes, 0)
        self.assertEqual(self.runtime.command_failures, 1)

    async def test_cancellation_is_not_recorded_as_failure(self):
        self.bot.messages.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.adapter.emit_event(message())
        self.assertFalse(self.runtime.failure.is_set())
        self.assertEqual(self.runtime.command_failures, 0)

    async def test_message_edit_does_not_execute_command(self):
        edited = message()
        edited.event_name = "message.edit"
        await self.adapter.emit_event(edited)
        self.bot.messages.send_message.assert_not_awaited()
        await self.adapter.emit_event(message())
        self.bot.messages.send_message.assert_awaited_once()

    async def test_false_and_nonboolean_auth_never_claim_confirmation(self):
        await self.adapter.opened(None)
        for value in (False, "true", "false", 1, 0, None):
            with self.subTest(value=value):
                await self.adapter.emit_event(event(21, {"checkRes": value}))
                self.assertFalse(self.adapter.auth_confirmed)
                self.assertFalse(self.runtime.connection["auth_confirmed"])
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        self.assertTrue(self.runtime.connection["auth_confirmed"])
        await self.adapter.emit_event(event(21, {"checkRes": False}))
        self.assertFalse(self.runtime.connection["auth_confirmed"])

    async def test_status_distinguishes_open_subscription_from_authentication(self):
        await self.adapter.opened(None)
        reply = AsyncMock()
        await self.runtime.dispatch(Context(Message("/status", "a", "c", "other", "m", False), reply))
        output = reply.await_args.args[0]
        self.assertRegex(output, r"连接通道：已连接")
        self.assertRegex(output, r"鉴权确认：未确认")
        self.assertRegex(output, r"域订阅：已发送")
        await self.adapter.closed(None, None)
        await self.runtime.dispatch(Context(Message("/status", "a", "c", "other", "m", False), reply))
        output = reply.await_args.args[0]
        self.assertRegex(output, r"连接通道：未连接")
        self.assertRegex(output, r"鉴权确认：未确认")
        self.assertRegex(output, r"域订阅：未发送")

    async def test_failed_subscription_is_not_published_as_sent_or_authenticated(self):
        self.bot.ws.send_subscribe_area_events.side_effect = RuntimeError("secret credential")
        with self.assertRaises(RuntimeError):
            await self.adapter.opened(None)
        self.assertEqual(self.runtime.connection, {
            "transport_open": True, "auth_confirmed": False, "subscription_sent": False,
        })

    async def test_denied_command_does_not_send_reply_or_fail_adapter(self):
        self.runtime.policies["ping"] = CommandPolicy(users=("operator",))
        await self.adapter.emit_event(message())
        self.bot.messages.send_message.assert_not_awaited()
        self.assertFalse(self.runtime.failure.is_set())
        self.assertEqual(self.runtime.command_successes, 0)

    async def test_sdk_errors_log_only_exception_class(self):
        with self.assertLogs("jeppa.oopz", level="ERROR") as logs:
            await self.adapter.errored(None, RuntimeError("Authorization: SECRET-TOKEN private-message"))
        text = "\n".join(logs.output)
        self.assertIn("RuntimeError", text)
        self.assertRegex(text, r"[\u4e00-\u9fff]")
        self.assertNotIn("Authorization", text)
        self.assertNotIn("SECRET-TOKEN", text)
        self.assertNotIn("private-message", text)

    async def test_fatal_reply_error_diagnostics_redact_credentials_and_message(self):
        self.bot.messages.send_message.side_effect = RuntimeError("Authorization: SECRET-TOKEN")
        with self.assertLogs("jeppa.runtime", level="ERROR") as logs:
            with self.assertRaises(RuntimeError):
                await self.adapter.emit_event(message(text="/ping PRIVATE-CONTENT"))
        text = "\n".join(logs.output)
        self.assertIn("ping", text)
        self.assertIn("RuntimeError", text)
        for secret in ("Authorization", "SECRET-TOKEN", "PRIVATE-CONTENT"):
            self.assertNotIn(secret, text)
        self.assertTrue(self.runtime.failure.is_set())
