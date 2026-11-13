"""Explicit dice, choice and operator-maintained, area-local FAQ commands."""
from __future__ import annotations

import random
import re

from ..runtime import Context, Plugin


_RANDOM = random.SystemRandom()
_MAX_REPLY = 2000
_MAX_ANSWER = 1800
_ROLL_USAGE = "用法：/roll [骰子数d面数]，例如 /roll 2d6。支持 1–100 颗骰子，每颗 2–1000000 面，默认 1d6。"
_FAQ_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_FAQ_SET_USAGE = (
    "用法：/faq-set <条目名> <内容>。条目名为 1–64 个英文字母、数字、下划线或连字符，"
    "以字母或数字开头；内容最多 1800 字符。仅限白名单操作员。"
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
        await context.reply(f"🎲 {count}d{sides}：{', '.join(map(str, values))}（合计 {sum(values)}）")

    async def choose(context: Context, args: tuple[str, ...]) -> None:
        choices = [choice.strip() for choice in " ".join(args).split("|")]
        if len(choices) < 2 or any(not choice or len(choice) > _MAX_REPLY for choice in choices):
            await context.reply("用法：/choose 选项一 | 选项二 | ……，至少提供两个非空选项，每项最多 2000 字符。")
            return
        await context.reply(_RANDOM.choice(choices))

    async def faq(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("常见问题按域保存，请在域内的文字频道使用 /faq，私信中不可查询。")
            return
        if len(args) > 1 or (args and _FAQ_KEY.fullmatch(args[0]) is None):
            await context.reply("用法：/faq [条目名]。条目名为 1–64 个英文字母、数字、下划线或连字符，以字母或数字开头。")
            return
        store = plugin.store(context, scope="area")
        if args:
            key = args[0].lower()
            answer = store.get(key)
            text = f"{key}: {answer}" if answer is not None else "没有找到这个条目，可以发送 /faq 查看已有条目。"
        else:
            keys = sorted(store.items())
            text = (
                "📚 常见问题（发送 /faq <条目名> 查询）：" + "、".join(keys)
                if keys else
                "本域还没有常见问题。白名单操作员可以使用 /faq-set <条目名> <内容> 添加。"
            )
        if len(text) > _MAX_REPLY:
            text = text[:_MAX_REPLY - 1] + "…"
        await context.reply(text)

    async def faq_set(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("常见问题只能在域内的文字频道修改，私信中不可操作。")
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
        await context.reply(f"✅ 已保存条目：{key}")

    async def faq_delete(context: Context, args: tuple[str, ...]) -> None:
        if context.message.is_private:
            await context.reply("常见问题只能在域内的文字频道修改，私信中不可操作。")
            return
        if len(args) != 1 or _FAQ_KEY.fullmatch(args[0]) is None:
            await context.reply("用法：/faq-delete <条目名>。条目名为 1–64 个英文字母、数字、下划线或连字符，以字母或数字开头。")
            return
        removed = plugin.store(context, scope="area").delete(args[0].lower())
        await context.reply("✅ 条目已删除。" if removed else "没有找到这个条目。")

    plugin.command("roll", roll, description="掷骰子，例如 /roll 2d6")
    plugin.command("choose", choose, description="帮你选一个，用 | 分隔选项")
    plugin.command("faq", faq, description="查看本域的常见问题")
    plugin.command("faq-set", faq_set, description="添加或修改常见问题（仅限操作员）", restricted=True)
    plugin.command("faq-delete", faq_delete, description="删除常见问题（仅限操作员）", restricted=True)
