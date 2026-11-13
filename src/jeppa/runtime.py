"""Explicit commands and transactional plugin resources, independent of Oopz."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
import re
import sys
import time
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from .storage import Namespace, Storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    users: tuple[str, ...] | None = None
    channels: tuple[str, ...] | None = None
    cooldown: float = 0


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


@dataclass(frozen=True, slots=True)
class _Command:
    handler: Handler
    description: str
    restricted: bool


class Plugin:
    """Own commands and register cleanup immediately after acquiring a resource."""

    def __init__(self, runtime: Runtime, name: str):
        self.runtime = runtime
        self.name = name
        self._commands: list[str] = []
        self._cleanup: list[Callable[[], Any]] = []
        self._closed = False

    def command(self, name: str, handler: Handler, *, description: str = "", restricted: bool = False) -> None:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise ValueError("Command name must match [a-z][a-z0-9_-]*")
        if name in self.runtime._commands:
            raise ValueError("Command already registered")
        self.runtime._commands[name] = _Command(handler, description, restricted)
        self._commands.append(name)

    def store(self, context: Context, *, scope: str = "user") -> Namespace:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        if self.runtime.storage is None:
            raise RuntimeError("Plugin storage requires a configured Storage instance")
        return self.runtime.storage.namespace(self.name, context.message, scope=scope)

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
        self.runtime._cooldowns = {
            key: expiry for key, expiry in self.runtime._cooldowns.items()
            if key[0] not in self._commands
        }
        if self in self.runtime._plugins:
            self.runtime._plugins.remove(self)
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
    def __init__(
        self, self_id: str = "", *, default_policy: CommandPolicy | None = None,
        policies: Mapping[str, CommandPolicy] | None = None, storage: Storage | None = None,
    ):
        self.self_id = self_id
        self.default_policy = default_policy if default_policy is not None else CommandPolicy()
        self.policies = dict(policies) if policies is not None else {}
        self.storage = storage
        self._commands: dict[str, _Command] = {}
        self._plugins: list[Plugin] = []
        self._cooldowns: dict[tuple[str, tuple[str, ...], str], float] = {}
        self._started = time.monotonic()
        self.connection = {"transport_open": False, "auth_confirmed": False, "subscription_sent": False}
        self.command_successes = 0
        self.command_failures = 0
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

    def _allowed(self, name: str, command: _Command, message: Message) -> bool:
        policy = self.policies.get(name, self.default_policy)
        if command.restricted and not policy.users:
            return False
        return (
            (policy.users is None or message.person in policy.users)
            and (policy.channels is None or message.channel in policy.channels)
        )

    async def dispatch(self, context: Context) -> bool:
        started = time.monotonic()
        message = context.message
        if message.person == self.self_id:
            return False
        parts = message.text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return False
        name = parts[0][1:]
        command = self._commands.get(name)
        if command is None:
            return False
        if not self._allowed(name, command, message):
            logger.info("command=%s outcome=denied elapsed=%.6f", name, time.monotonic() - started)
            return False
        if self._cooldowns:
            self._cooldowns = {key: expiry for key, expiry in self._cooldowns.items() if expiry > started}
        key = (name, message.session_id, message.person)
        if key in self._cooldowns:
            logger.info("command=%s outcome=cooldown elapsed=%.6f", name, time.monotonic() - started)
            return False
        cooldown = self.policies.get(name, self.default_policy).cooldown
        if cooldown > 0:
            self._cooldowns[key] = started + cooldown
        try:
            await command.handler(context, tuple(parts[1:]))
        except asyncio.CancelledError:
            logger.info("command=%s outcome=cancelled elapsed=%.6f", name, time.monotonic() - started)
            raise
        except Exception as error:
            self.command_failures += 1
            logger.error("command=%s outcome=failed elapsed=%.6f exception=%s", name, time.monotonic() - started, type(error).__name__)
            raise
        self.command_successes += 1
        logger.info("command=%s outcome=success elapsed=%.6f", name, time.monotonic() - started)
        return True

    async def close(self) -> None:
        errors = []
        for plugin in reversed(tuple(self._plugins)):
            try:
                await plugin.close()
            except BaseException as error:
                errors.append(error)
        self._plugins.clear()
        if errors:
            raise BaseExceptionGroup("Runtime cleanup failed", errors)


def _plugin_label(name: str) -> str:
    source, separator, setup = name.rpartition(":")
    if not separator or not setup.isidentifier():
        source, setup = name, ""
    label = source.replace("\\", "/").rsplit("/", 1)[-1]
    if label.endswith(".py"):
        label = label[:-3]
    return f"{label}:{setup}" if setup else label


async def builtins(plugin: Plugin) -> None:
    async def ping(context: Context, args: tuple[str, ...]) -> None:
        await context.reply("Usage: /ping" if args else "pong")

    async def help_command(context: Context, args: tuple[str, ...]) -> None:
        if args:
            await context.reply("Usage: /help")
            return
        output = "\n".join(
            f"/{name} — {command.description}"
            for name, command in sorted(plugin.runtime._commands.items())
            if plugin.runtime._allowed(name, command, context.message)
        )
        await context.reply(output if len(output) <= 2000 else output[:1997] + "...")

    async def status(context: Context, args: tuple[str, ...]) -> None:
        if args:
            await context.reply("Usage: /status")
            return
        runtime = plugin.runtime
        state = runtime.connection
        output = "\n".join((
            f"Uptime: {max(0, int(time.monotonic() - runtime._started))}s",
            f"Transport open: {str(state['transport_open']).lower()}",
            f"Auth confirmed: {str(state['auth_confirmed']).lower()}",
            f"Subscription sent: {str(state['subscription_sent']).lower()}",
            f"Commands succeeded: {runtime.command_successes}",
            f"Commands failed: {runtime.command_failures}",
            "Plugins: " + (", ".join(_plugin_label(loaded.name) for loaded in runtime._plugins) or "none"),
        ))
        await context.reply(output if len(output) <= 2000 else output[:1997] + "...")

    plugin.command("ping", ping, description="Check bot response")
    plugin.command("help", help_command, description="List registered commands")
    plugin.command("status", status, description="Show local runtime and connection status")
