"""Explicit dice, choice and operator-maintained, area-local FAQ commands."""
from __future__ import annotations

import random
import re

from ..runtime import Context, Plugin


_RANDOM = random.SystemRandom()
_MAX_REPLY = 2000
_MAX_ANSWER = 1800
_ROLL_USAGE = "Usage: /roll [NdM] (1–100 dice, 2–1000000 sides; default 1d6)."
_FAQ_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_FAQ_SET_USAGE = (
    "Usage: /faq-set <key> <answer...> (key: 1–64 letters, digits, _ or -; "
    "answer: at most 1800 characters). Requires an allowlisted operator."
)


async def setup(plugin: Plugin) -> None:
    async def roll(context: Context, args: tuple[str, ...]) -> None:
        notation = args[0] if args else "1d6"
        match = re.fullmatch(r"([0-9]{1,3})[dD]([0-9]{1,7})", notation)
        if len(args) > 1 or match is None:
            await context.reply(_ROLL_USAGE)
            return
        count, sides = map(int, match.groups())
        if not 1 <= count <= 100 or not 2 <= sides <= 1_000_000:
            await context.reply(_ROLL_USAGE)
            return
        values = [_RANDOM.randint(1, sides) for _ in range(count)]
        await context.reply(f"{count}d{sides}: {', '.join(map(str, values))} (total {sum(values)})")

    async def choose(context: Context, args: tuple[str, ...]) -> None:
        choices = [choice.strip() for choice in " ".join(args).split("|")]
        if len(choices) < 2 or any(not choice or len(choice) > _MAX_REPLY for choice in choices):
            await context.reply("Usage: /choose option1 | option2 | ... (at least two nonempty choices, each at most 2000 characters).")
            return
        await context.reply(_RANDOM.choice(choices))

    async def faq(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("FAQ is area-local. Use /faq in a public area channel.")
            return
        if len(args) > 1 or (args and _FAQ_KEY.fullmatch(args[0]) is None):
            await context.reply("Usage: /faq [key] (key: 1–64 letters, digits, _ or -).")
            return
        store = plugin.store(context, scope="area")
        if args:
            key = args[0].lower()
            answer = store.get(key)
            text = f"{key}: {answer}" if answer is not None else "No FAQ entry for that key. Use /faq to list available keys."
        else:
            keys = sorted(store.items())
            text = (
                "FAQ keys (use /faq <key>): " + ", ".join(keys)
                if keys else
                "No FAQ entries in this area. An allowlisted operator can add one with /faq-set <key> <answer...>."
            )
        if len(text) > _MAX_REPLY:
            text = text[:_MAX_REPLY - 1] + "…"
        await context.reply(text)

    async def faq_set(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("FAQ can only be changed in a public area channel.")
            return
        if len(args) < 2 or _FAQ_KEY.fullmatch(args[0]) is None:
            await context.reply(_FAQ_SET_USAGE)
            return
        answer = " ".join(args[1:])
        if not answer.strip() or len(answer) > _MAX_ANSWER:
            await context.reply(_FAQ_SET_USAGE)
            return
        key = args[0].lower()
        plugin.store(context, scope="area").set(key, answer)
        await context.reply(f"FAQ saved: {key}.")

    async def faq_delete(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("FAQ can only be changed in a public area channel.")
            return
        if len(args) != 1 or _FAQ_KEY.fullmatch(args[0]) is None:
            await context.reply("Usage: /faq-delete <key> (key: 1–64 letters, digits, _ or -).")
            return
        removed = plugin.store(context, scope="area").delete(args[0].lower())
        await context.reply("FAQ entry deleted." if removed else "No FAQ entry for that key.")

    plugin.command("roll", roll, description="Roll dice: /roll [NdM]")
    plugin.command("choose", choose, description="Choose between options separated by |")
    plugin.command("faq", faq, description="List or query this area's FAQ")
    plugin.command("faq-set", faq_set, description="Set an area FAQ entry (allowlisted operators)", restricted=True)
    plugin.command("faq-delete", faq_delete, description="Delete an area FAQ entry (allowlisted operators)", restricted=True)
