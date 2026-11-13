"""Explicit commands and transactional plugin resources, independent of Oopz."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import importlib
import importlib.util
import inspect
from pathlib import Path
import re
import sys
from typing import Any
import uuid


@dataclass(frozen=True, slots=True)
class Message:
    text: str
    area: str
    channel: str
    person: str
    message_id: str
    is_private: bool

    @property
    def session_id(self) -> tuple[str, ...]:
        if self.is_private:
            return ("private", self.channel, self.person)
        return ("channel", self.area, self.channel)


@dataclass(slots=True)
class Context:
    message: Message
    _reply: Callable[[str], Awaitable[Any]]

    async def reply(self, text: str) -> Any:
        return await self._reply(text)


Handler = Callable[[Context, tuple[str, ...]], Awaitable[None]]


class Plugin:
    """Own commands and register cleanup immediately after acquiring a resource."""

    def __init__(self, runtime: Runtime, name: str):
        self.runtime = runtime
        self.name = name
        self._commands: list[str] = []
        self._cleanup: list[Callable[[], Any]] = []
        self._closed = False

    def command(self, name: str, handler: Handler, *, description: str = "") -> None:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise ValueError("Command name must match [a-z][a-z0-9_-]*")
        if name in self.runtime._commands:
            raise ValueError("Command already registered")
        self.runtime._commands[name] = (handler, description)
        self._commands.append(name)

    def defer(self, cleanup: Callable[[], Any]) -> None:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        self._cleanup.append(cleanup)

    def task(self, coroutine: Awaitable[Any]) -> asyncio.Task:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        task = asyncio.ensure_future(coroutine)

        async def stop() -> None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise

        self.defer(stop)
        # Wake the runtime immediately on a background failure; do not leave an
        # unobserved task exception until shutdown.
        def finished(done: asyncio.Future) -> None:
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    self.runtime.fail(error)
        task.add_done_callback(finished)
        return task

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for name in self._commands:
            self.runtime._commands.pop(name, None)
        self._commands.clear()
        errors = []
        for cleanup in reversed(self._cleanup):
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await result
            except BaseException as error:
                errors.append(error)
        self._cleanup.clear()
        if errors:
            raise BaseExceptionGroup("Plugin cleanup failed", errors)


class Runtime:
    def __init__(self, self_id: str = ""):
        self.self_id = self_id
        self._commands: dict[str, tuple[Handler, str]] = {}
        self._plugins: list[Plugin] = []
        self.failure = asyncio.Event()
        self.error: BaseException | None = None

    def fail(self, error: BaseException) -> None:
        if self.error is None:
            self.error = error
        self.failure.set()

    async def install(self, setup: Callable[[Plugin], Awaitable[None]], *, name: str) -> Plugin:
        plugin = Plugin(self, name)
        try:
            await setup(plugin)
        except BaseException as error:
            try:
                await plugin.close()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup("Plugin startup and rollback failed", [error, cleanup_error]) from None
            raise
        self._plugins.append(plugin)
        return plugin

    async def load(self, entry: str) -> Plugin:
        """Import module:setup or /path/plugin.py:setup. Only load trusted code."""
        source, separator, attribute = entry.rpartition(":")
        if not separator or not source or not attribute.isidentifier():
            raise ValueError("Plugin entry must be module:setup or path.py:setup")
        module_name = None
        try:
            if source.endswith(".py"):
                module_name = "_jeppa_plugin_" + uuid.uuid4().hex
                spec = importlib.util.spec_from_file_location(module_name, Path(source).resolve())
                if spec is None or spec.loader is None:
                    raise ImportError("Cannot load plugin file")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            else:
                module = importlib.import_module(source)
            setup = getattr(module, attribute)
            if not inspect.iscoroutinefunction(setup):
                raise TypeError("Plugin setup must be async def setup(plugin)")
            plugin = await self.install(setup, name=entry)
        except BaseException:
            if module_name is not None:
                sys.modules.pop(module_name, None)
            raise
        if module_name is not None:
            plugin.defer(lambda: sys.modules.pop(module_name, None))
        return plugin

    async def dispatch(self, context: Context) -> bool:
        message = context.message
        if message.person == self.self_id:
            return False
        parts = message.text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return False
        command = self._commands.get(parts[0][1:])
        if command is None:
            return False
        await command[0](context, tuple(parts[1:]))
        return True

    async def close(self) -> None:
        errors = []
        for plugin in reversed(self._plugins):
            try:
                await plugin.close()
            except BaseException as error:
                errors.append(error)
        self._plugins.clear()
        if errors:
            raise BaseExceptionGroup("Runtime cleanup failed", errors)


async def builtins(plugin: Plugin) -> None:
    async def ping(context: Context, args: tuple[str, ...]) -> None:
        await context.reply("Usage: /ping" if args else "pong")

    async def help_command(context: Context, args: tuple[str, ...]) -> None:
        if args:
            await context.reply("Usage: /help")
            return
        await context.reply("\n".join(f"/{name} — {description}" for name, (_, description) in sorted(plugin.runtime._commands.items())))

    plugin.command("ping", ping, description="Check bot response")
    plugin.command("help", help_command, description="List registered commands")
