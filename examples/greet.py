"""Trusted external plugin: load with examples/greet.py:setup."""
from jeppa import Context, Plugin


async def setup(plugin: Plugin) -> None:
    async def greet(context: Context, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            await context.reply("Usage: /greet <name>")
            return
        await context.reply(f"Hello, {args[0]}!")

    plugin.command("greet", greet, description="Greet one name: /greet <name>")
