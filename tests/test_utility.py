from pathlib import Path
import re
import random
import tempfile
import unittest
from unittest.mock import patch

from jeppa.plugins import utility
from jeppa.runtime import CommandPolicy, Context, Message, Runtime
from jeppa.storage import Storage


class UtilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "utility.sqlite3"
        self.storage = Storage(self.path)
        self.addCleanup(self.storage.close)
        self.runtime = Runtime(
            "self", storage=self.storage,
            policies={
                "faq-set": CommandPolicy(users=("operator",)),
                "faq-delete": CommandPolicy(users=("operator",)),
            },
        )
        self.addAsyncCleanup(self.runtime.close)
        self.plugin = await self.runtime.install(utility.setup, name="utility")
        self.replies = []

    async def dispatch(self, text, *, person="reader", area="area", channel="channel", private=False, runtime=None):
        async def reply(value):
            self.assertIsInstance(value, str)
            self.assertLessEqual(len(value), 2000)
            self.replies.append(value)
        message = Message(text, area, channel, person, "message", private)
        return await (self.runtime if runtime is None else runtime).dispatch(Context(message, reply))

    def area_store(self, *, area="area", plugin="utility", storage=None):
        message = Message("", area, "channel", "reader", "", False)
        return (self.storage if storage is None else storage).namespace(plugin, message, scope="area")

    async def test_roll_default_and_valid_boundaries_use_system_random(self):
        self.assertIsInstance(utility._RANDOM, random.SystemRandom)
        with patch.object(utility, "_RANDOM") as source:
            source.randint.return_value = 4
            self.assertTrue(await self.dispatch("/roll"))
            self.assertIn("4", self.replies[-1])
            source.randint.assert_called_once_with(1, 6)
            source.randint.reset_mock()
            source.randint.return_value = 1_000_000
            await self.dispatch("/roll 100D1000000")
            self.assertEqual(source.randint.call_count, 100)
            source.randint.assert_called_with(1, 1_000_000)
            numbers = [int(value) for value in re.findall(r"\d+", self.replies[-1])]
            self.assertEqual(numbers, [100, 1_000_000, *([1_000_000] * 100), 100_000_000])
            source.randint.return_value = 2
            await self.dispatch("/roll 1d2")
            self.assertEqual([int(value) for value in re.findall(r"\d+", self.replies[-1])], [1, 2, 2, 2])

    async def test_roll_rejects_invalid_counts_sides_syntax_and_extra_args(self):
        invalid = (
            "d6", "6", "0d6", "101d6", "1d1", "1d0", "1d1000001",
            "-1d6", "1d-6", "1.5d6", "1d6 trailing", "1d6 2d6", "１d６", "9" * 4000 + "d6",
        )
        with patch.object(utility, "_RANDOM") as source:
            for notation in invalid:
                with self.subTest(notation=notation[:40]):
                    self.assertTrue(await self.dispatch("/roll " + notation))
                    self.assertIn("/roll", self.replies[-1])
            source.randint.assert_not_called()

    async def test_choose_keeps_multiword_options_and_only_returns_offered_choice(self):
        with patch.object(utility, "_RANDOM") as source:
            source.choice.side_effect = lambda options: options[-1]
            await self.dispatch("/choose first option | second choice | third")
            source.choice.assert_called_once_with(["first option", "second choice", "third"])
            self.assertEqual(self.replies[-1], "third")
        for _ in range(10):
            await self.dispatch("/choose alpha | beta")
            self.assertIn(self.replies[-1], {"alpha", "beta"})
        with patch.object(utility, "_RANDOM") as source:
            source.choice.side_effect = lambda options: options[0]
            await self.dispatch("/choose " + "x" * 2000 + " | short")
            self.assertEqual(len(self.replies[-1]), 2000)

    async def test_choose_rejects_empty_missing_and_oversized_choices(self):
        with patch.object(utility, "_RANDOM") as source:
            for args in ("", "one", "one |", "| two", "one || two", "one | | two", "x" * 2001 + " | other"):
                with self.subTest(args=args[:30]):
                    await self.dispatch("/choose " + args)
                    self.assertIn("/choose", self.replies[-1])
            source.choice.assert_not_called()

    async def test_empty_faq_has_actionable_usage_and_no_invented_entries(self):
        self.assertEqual(self.area_store().items(), {})
        await self.dispatch("/faq")
        self.assertIn("/faq-set", self.replies[-1])
        await self.dispatch("/faq missing")
        self.assertIn("/faq", self.replies[-1])
        self.assertEqual(self.area_store().items(), {})

    async def test_faq_operator_set_query_list_replace_and_delete(self):
        self.assertTrue(await self.dispatch("/faq-set Rules Only an example answer", person="operator"))
        self.assertEqual(self.area_store().items(), {"rules": "Only an example answer"})
        await self.dispatch("/faq RULES", channel="another", person="other-reader")
        self.assertEqual(self.replies[-1], "rules: Only an example answer")
        await self.dispatch("/faq")
        self.assertIn("rules", self.replies[-1])
        await self.dispatch("/faq-set rules Replacement answer", person="operator")
        self.assertEqual(self.area_store().get("rules"), "Replacement answer")
        await self.dispatch("/faq-delete RULES", person="operator")
        self.assertEqual(self.area_store().items(), {})
        await self.dispatch("/faq-delete rules", person="operator")
        self.assertEqual(self.area_store().items(), {})

    async def test_unallowlisted_mutations_cannot_change_persisted_data(self):
        await self.dispatch("/faq-set stable Original answer", person="operator")
        before = self.area_store().items()
        replies_before = len(self.replies)
        for text in ("/faq-set stable Attacker replacement", "/faq-set injected New value", "/faq-delete stable"):
            self.assertFalse(await self.dispatch(text, person="outsider"))
        self.assertEqual(len(self.replies), replies_before)
        self.assertEqual(self.area_store().items(), before)
        reopened = Storage(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(self.area_store(storage=reopened).items(), before)

    async def test_unrestricted_or_empty_policy_never_grants_faq_mutation(self):
        self.area_store().set("stable", "Original answer")
        for policy in (CommandPolicy(), CommandPolicy(users=())):
            with self.subTest(policy=policy):
                runtime = Runtime("self", storage=self.storage, default_policy=policy)
                self.addAsyncCleanup(runtime.close)
                await runtime.install(utility.setup, name="utility")
                replies_before = len(self.replies)
                self.assertFalse(await self.dispatch("/faq-set stable Changed", person="operator", runtime=runtime))
                self.assertFalse(await self.dispatch("/faq-delete stable", person="operator", runtime=runtime))
                self.assertEqual(len(self.replies), replies_before)
                self.assertEqual(self.area_store().items(), {"stable": "Original answer"})
        reopened = Storage(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(self.area_store(storage=reopened).items(), {"stable": "Original answer"})

    async def test_faq_private_commands_do_not_fallback_to_public_area(self):
        self.area_store().set("stable", "Public answer")
        for text in ("/faq", "/faq stable", "/faq-set stable Private replacement", "/faq-delete stable"):
            with self.subTest(text=text):
                self.assertTrue(await self.dispatch(text, person="operator", private=True))
        self.assertEqual(self.area_store().items(), {"stable": "Public answer"})
        self.assertEqual(self.storage.namespace("utility", Message("", "", "channel", "operator", "", True)).items(), {})

    async def test_faq_persists_restart_and_isolates_areas_and_plugins(self):
        await self.dispatch("/faq-set example Area-specific answer", person="operator")
        await self.dispatch("/faq example", area="elsewhere")
        self.assertEqual(self.area_store(area="elsewhere").items(), {})
        self.assertEqual(self.area_store(plugin="another-plugin").items(), {})
        await self.runtime.close()
        self.storage.close()
        reopened = Storage(self.path)
        self.addCleanup(reopened.close)
        restarted = Runtime("self", storage=reopened)
        self.addAsyncCleanup(restarted.close)
        await restarted.install(utility.setup, name="utility")
        await self.dispatch("/faq example", runtime=restarted)
        self.assertEqual(self.replies[-1], "example: Area-specific answer")

    async def test_faq_validates_keys_argument_counts_and_oversized_writes(self):
        self.area_store().set("stable", "Original answer")
        invalid = (
            "/faq key extra", "/faq bad/key", "/faq-set", "/faq-set key",
            "/faq-set bad/key answer", "/faq-set " + "k" * 65 + " answer",
            "/faq-set stable " + "a" * 1801,
            "/faq-delete", "/faq-delete stable extra", "/faq-delete bad/key",
        )
        for text in invalid:
            with self.subTest(text=text[:50]):
                self.assertTrue(await self.dispatch(text, person="operator"))
                self.assertIn(text.split()[0], self.replies[-1])
                self.assertEqual(self.area_store().items(), {"stable": "Original answer"})
        reopened = Storage(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(self.area_store(storage=reopened).items(), {"stable": "Original answer"})

    async def test_faq_answer_and_large_key_list_responses_are_bounded(self):
        key = "k" * 64
        answer = "a" * 1800
        await self.dispatch(f"/faq-set {key} {answer}", person="operator")
        await self.dispatch("/faq " + key)
        self.assertEqual(self.replies[-1], f"{key}: {answer}")
        for index in range(60):
            self.area_store().set(f"entry{index:03d}" + "x" * 50, "An operator-provided example")
        await self.dispatch("/faq")
        self.assertEqual(len(self.replies[-1]), 2000)
        self.assertTrue(self.replies[-1].endswith("…"))

    async def test_plugin_prompts_use_chinese_and_preserve_user_text(self):
        for text in ("/roll", "/roll invalid", "/choose one", "/faq", "/faq missing", "/faq-set key", "/faq-delete"):
            with self.subTest(command=text):
                await self.dispatch(text, person="operator")
                self.assertRegex(self.replies[-1], r"[\u4e00-\u9fff]")
        await self.dispatch("/choose 原样保留 | unchanged")
        self.assertIn(self.replies[-1], {"原样保留", "unchanged"})
        await self.dispatch("/faq-set mixed 中文 content", person="operator")
        await self.dispatch("/faq mixed")
        self.assertEqual(self.replies[-1], "mixed: 中文 content")
