"""Trusted external plugin: load with examples/greet.py:setup."""
from jeppa import Context, Plugin


async def setup(plugin: Plugin) -> None:
    async def greet(context: Context, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            await context.reply("用法：/greet <名字>")
            return
        await context.reply(f"👋 你好，{args[0]}！")

    plugin.command("greet", greet, description="打个招呼：/greet <名字>")
