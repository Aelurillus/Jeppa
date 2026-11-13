"""Trusted external plugin: load with examples/greet.py:setup."""
from jeppa import Context, Plugin, PluginInfo

PLUGIN = PluginInfo(id="greet", name="问候", version="1.0.0", description="向指定名字打招呼。")


async def setup(plugin: Plugin) -> None:
    if plugin.config:
        raise ValueError("问候插件不接受配置选项")

    async def greet(context: Context, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            await context.reply("用法：/greet <名字>")
            return
        await context.reply(f"👋 你好，{args[0]}！")

    plugin.command("greet", greet, description="打个招呼：/greet <名字>")
