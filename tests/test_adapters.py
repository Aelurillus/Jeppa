"""Consumer-visible ownership, sibling cancellation and account isolation."""
import asyncio
from dataclasses import replace
import unittest

from jeppa import Context, Message, Runtime
from jeppa.adapters import IdentityRegistry, run_accounts
from jeppa.config import AccountConfig


class AdapterSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime()
        self.accounts = (AccountConfig("first", "oopz", {}), AccountConfig("second", "oopz", {}))

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_one_account_failure_cancels_and_cleans_its_live_sibling(self):
        started = asyncio.Event()
        cleaned = []
        failure = RuntimeError("account failed")

        async def run(account, runtime, identities, *, migrate_legacy=False):
            try:
                if account.label == "second":
                    started.set()
                    await asyncio.Event().wait()
                else:
                    await started.wait()
                    raise failure
            finally:
                cleaned.append(account.label)

        with self.assertRaises(RuntimeError) as raised:
            await asyncio.wait_for(run_accounts(self.accounts, self.runtime, adapters={"oopz": run}), timeout=2)
        self.assertIs(raised.exception, failure)
        self.assertCountEqual(cleaned, ["first", "second"])

    async def test_runtime_failure_stops_all_accounts_and_propagates_original_error(self):
        started = 0
        ready = asyncio.Event()
        cleaned = []
        failure = ValueError("plugin task failed")

        async def run(account, runtime, identities, *, migrate_legacy=False):
            nonlocal started
            started += 1
            if started == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(account.label)

        job = asyncio.create_task(run_accounts(self.accounts, self.runtime, adapters={"oopz": run}))
        await asyncio.wait_for(ready.wait(), timeout=2)
        self.runtime.fail(failure)
        with self.assertRaises(ValueError) as raised:
            await asyncio.wait_for(job, timeout=2)
        self.assertIs(raised.exception, failure)
        self.assertCountEqual(cleaned, ["first", "second"])

    async def test_owner_cancellation_waits_for_every_account_cleanup(self):
        started = asyncio.Event()
        count = 0
        cleaned = []

        async def run(account, runtime, identities, *, migrate_legacy=False):
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(account.label)

        job = asyncio.create_task(run_accounts(self.accounts, self.runtime, adapters={"oopz": run}))
        await asyncio.wait_for(started.wait(), timeout=2)
        job.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job
        self.assertCountEqual(cleaned, ["first", "second"])

    def test_duplicate_live_identity_is_rejected_and_released_after_failure(self):
        registry = IdentityRegistry()
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            with registry.claim("oopz", "bot"):
                with self.assertRaises(ValueError):
                    with registry.claim("oopz", "bot"):
                        self.fail("duplicate identity was admitted")
                with registry.claim("qq", "bot"):
                    raise RuntimeError("cleanup")
        with registry.claim("oopz", "bot"), registry.claim("qq", "bot"):
            pass

    async def test_self_filter_and_status_are_scoped_to_target_bot(self):
        await self.runtime.load("jeppa.plugins.default:setup")
        self.runtime.set_connection("oopz", "bot-a", transport_open=True, auth_confirmed=True, subscription_sent=True)
        first = Message("/ping", "a", "c", "bot-a", "m", False, platform="oopz", bot_id="bot-a")
        replies = []

        async def reply(text):
            replies.append(text)

        self.assertFalse(await self.runtime.dispatch(Context(first, reply)))
        second = replace(first, bot_id="bot-b")
        self.assertTrue(await self.runtime.dispatch(Context(second, reply)))
        self.assertEqual(replies, ["🏓 在呢！"])
        self.assertTrue(self.runtime.status(first)["connection"]["auth_confirmed"])
        self.assertFalse(self.runtime.status(second)["connection"]["auth_confirmed"])
