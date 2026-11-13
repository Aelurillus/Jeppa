"""Consumer-visible SDK auth, cancellation, failure and edit boundaries."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oopz_sdk.events.parser import EventParser

from jeppa import Runtime
from jeppa.oopz import OopzAdapter
from jeppa.runtime import builtins


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

    async def test_explicit_auth_confirmation_does_not_repeat_subscription(self):
        await self.adapter.opened(None)
        await self.adapter.emit_event(event(21, {"checkRes": "true"}))
        self.assertFalse(self.adapter.auth_confirmed)
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        self.assertTrue(self.adapter.auth_confirmed)
        self.bot.ws.send_subscribe_area_events.assert_awaited_once_with(["a"])

    async def test_close_then_reopen_resets_confirmation_and_resubscribes(self):
        await self.adapter.opened(None)
        await self.adapter.emit_event(event(21, {"checkRes": True}))
        await self.adapter.closed(None, None)
        self.assertFalse(self.adapter.transport_open)
        self.assertFalse(self.adapter.auth_confirmed)
        self.assertFalse(self.adapter.subscription_sent)
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

    async def test_cancellation_is_not_recorded_as_failure(self):
        self.bot.messages.send_message.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.adapter.emit_event(message())
        self.assertFalse(self.runtime.failure.is_set())

    async def test_message_edit_does_not_execute_command(self):
        edited = message()
        edited.event_name = "message.edit"
        await self.adapter.emit_event(edited)
        self.bot.messages.send_message.assert_not_awaited()
        await self.adapter.emit_event(message())
        self.bot.messages.send_message.assert_awaited_once()
