"""Explicit commands and transactional plugin resources, independent of Oopz."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
import re
import sys
import time
from types import MappingProxyType
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
    platform: str = field(kw_only=True)
    bot_id: str = field(kw_only=True)

    def __post_init__(self) -> None:
        for identity in (self.platform, self.bot_id):
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("Message platform and bot_id must be nonempty strings")

    @property
    def session_id(self) -> tuple[str, ...]:
        if self.is_private:
            return (self.platform, self.bot_id, "private", self.channel, self.person)
        return (self.platform, self.bot_id, "channel", self.area, self.channel)


@dataclass(slots=True)
class Context:
    message: Message
    _reply: Callable[[str], Awaitable[Any]]

    async def reply(self, text: str) -> Any:
        return await self._reply(text)


Handler = Callable[[Context, tuple[str, ...]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PluginInfo:
    id: str
    name: str
    version: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or re.fullmatch(r"[a-z][a-z0-9_-]*", self.id) is None:
            raise ValueError("Plugin id must match [a-z][a-z0-9_-]*")
        if any(not isinstance(value, str) or not value.strip() for value in (self.name, self.version, self.description)):
            raise ValueError("Plugin metadata must contain nonempty strings")


@dataclass(frozen=True, slots=True)
class CommandInfo:
    name: str
    description: str
    plugin_id: str
    restricted: bool


@dataclass(frozen=True, slots=True)
class _Command:
    handler: Handler
    description: str
    restricted: bool
    owner: Plugin


class Plugin:
    """Own commands and register cleanup immediately after acquiring a resource."""

    def __init__(
        self, runtime: Runtime, info: PluginInfo, *, config: Mapping[str, Any] | None = None,
        areas: tuple[str, ...] | None = None, channels: tuple[str, ...] | None = None,
    ):
        self.runtime = runtime
        self.info = info
        if config is not None and not isinstance(config, Mapping):
            raise TypeError("Plugin config must be a mapping")
        self.config = MappingProxyType(dict(config) if config is not None else {})
        for scope in (areas, channels):
            if scope is not None and (not isinstance(scope, (tuple, list)) or any(not isinstance(value, str) for value in scope)):
                raise TypeError("Plugin scopes must be sequences of strings")
        self._areas = None if areas is None else tuple(areas)
        self._channels = None if channels is None else tuple(channels)
        self._commands: list[str] = []
        self._cleanup: list[Callable[[], Any]] = []
        self._closed = False

    @property
    def id(self) -> str:
        return self.info.id

    def _in_scope(self, message: Message) -> bool:
        return (
            (self._areas is None or (not message.is_private and message.area in self._areas))
            and (self._channels is None or message.channel in self._channels)
        )

    def command(self, name: str, handler: Handler, *, description: str = "", restricted: bool = False) -> None:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
            raise ValueError("Command name must match [a-z][a-z0-9_-]*")
        if name in self.runtime._commands:
            raise ValueError("Command already registered")
        self.runtime._commands[name] = _Command(handler, description, restricted, self)
        self._commands.append(name)

    def store(self, context: Context, *, scope: str = "user") -> Namespace:
        if self._closed:
            raise RuntimeError("Plugin is closed")
        if self.runtime.storage is None:
            raise RuntimeError("Plugin storage requires a configured Storage instance")
        return self.runtime.storage.namespace(self.info.id, context.message, scope=scope)

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
        self.runtime._plugin_ids.discard(self.info.id)
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
        self, *, default_policy: CommandPolicy | None = None,
        policies: Mapping[str, CommandPolicy] | None = None, storage: Storage | None = None,
    ):
        self.default_policy = default_policy if default_policy is not None else CommandPolicy()
        self.policies = dict(policies) if policies is not None else {}
        self.storage = storage
        self._commands: dict[str, _Command] = {}
        self._plugins: list[Plugin] = []
        self._plugin_ids: set[str] = set()
        self._cooldowns: dict[tuple[str, tuple[str, ...], str], float] = {}
        self._started = time.monotonic()
        self._connections: dict[tuple[str, str], dict[str, bool]] = {}
        self.command_successes = 0
        self.command_failures = 0
        self.failure = asyncio.Event()
        self.error: BaseException | None = None

    def fail(self, error: BaseException) -> None:
        if self.error is None:
            self.error = error
        self.failure.set()

    async def install(
        self, setup: Callable[[Plugin], Awaitable[None]], *, info: PluginInfo,
        config: Mapping[str, Any] | None = None, areas: tuple[str, ...] | None = None,
        channels: tuple[str, ...] | None = None,
    ) -> Plugin:
        if not isinstance(info, PluginInfo):
            raise TypeError("Plugin info must be PluginInfo")
        if info.id in self._plugin_ids:
            raise ValueError("Plugin id already registered")
        if not inspect.iscoroutinefunction(setup):
            raise TypeError("Plugin setup must be async def setup(plugin)")
        plugin = Plugin(self, info, config=config, areas=areas, channels=channels)
        self._plugin_ids.add(info.id)
        try:
            await setup(plugin)
            if plugin._closed:
                raise RuntimeError("Plugin was closed during setup")
        except BaseException as error:
            try:
                await plugin.close()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup("Plugin startup and rollback failed", [error, cleanup_error]) from None
            raise
        self._plugins.append(plugin)
        return plugin

    async def load(self, entry: str, *, settings: Mapping[str, Mapping[str, Any]] | None = None) -> Plugin | None:
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
            info = getattr(module, "PLUGIN")
            if not isinstance(info, PluginInfo):
                raise TypeError("Plugin module PLUGIN must be PluginInfo")
            options = {} if settings is None else settings.get(info.id, {})
            if not isinstance(options, Mapping) or set(options) - {"enabled", "config", "areas", "channels"}:
                raise ValueError("Invalid plugin settings")
            enabled = options.get("enabled", True)
            if not isinstance(enabled, bool):
                raise TypeError("Plugin enabled must be boolean")
            if not enabled:
                if module_name is not None:
                    sys.modules.pop(module_name, None)
                return None
            setup = getattr(module, attribute)
            if not inspect.iscoroutinefunction(setup):
                raise TypeError("Plugin setup must be async def setup(plugin)")
            plugin = await self.install(
                setup, info=info, config=options.get("config"),
                areas=options.get("areas"), channels=options.get("channels"),
            )
        except BaseException:
            if module_name is not None:
                sys.modules.pop(module_name, None)
            raise
        if module_name is not None:
            plugin.defer(lambda: sys.modules.pop(module_name, None))
        return plugin

    def commands(self, message: Message) -> tuple[CommandInfo, ...]:
        return tuple(
            CommandInfo(name, command.description, command.owner.info.id, command.restricted)
            for name, command in sorted(self._commands.items())
            if message.person != message.bot_id and self._allowed(name, command, message)
        )

    def plugins(self) -> tuple[PluginInfo, ...]:
        return tuple(plugin.info for plugin in self._plugins)

    def set_connection(
        self, platform: str, bot_id: str, *, transport_open: bool,
        auth_confirmed: bool, subscription_sent: bool,
    ) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (platform, bot_id)):
            raise ValueError("Connection identity must contain nonempty strings")
        if any(not isinstance(value, bool) for value in (transport_open, auth_confirmed, subscription_sent)):
            raise TypeError("Connection state must be boolean")
        self._connections[(platform, bot_id)] = {
            "transport_open": transport_open, "auth_confirmed": auth_confirmed,
            "subscription_sent": subscription_sent,
        }

    def status(self, message: Message) -> dict[str, Any]:
        return {
            "uptime": max(0, int(time.monotonic() - self._started)),
            "plugins": self.plugins(),
            "connection": dict(self._connections.get((message.platform, message.bot_id), {
                "transport_open": False, "auth_confirmed": False, "subscription_sent": False,
            })),
            "command_successes": self.command_successes,
            "command_failures": self.command_failures,
        }

    def _allowed(self, name: str, command: _Command, message: Message) -> bool:
        policy = self.policies.get(name, self.default_policy)
        if command.restricted and not policy.users:
            return False
        return (
            command.owner._in_scope(message)
            and (policy.users is None or message.person in policy.users)
            and (policy.channels is None or message.channel in policy.channels)
        )

    async def dispatch(self, context: Context) -> bool:
        started = time.monotonic()
        message = context.message
        if message.person == message.bot_id:
            return False
        parts = message.text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return False
        name = parts[0][1:]
        command = self._commands.get(name)
        if command is None:
            return False
        if not self._allowed(name, command, message):
            logger.info("命令=%s 结果=无权限 耗时=%.6f秒", name, time.monotonic() - started)
            return False
        if self._cooldowns:
            self._cooldowns = {key: expiry for key, expiry in self._cooldowns.items() if expiry > started}
        key = (name, message.session_id, message.person)
        if key in self._cooldowns:
            logger.info("命令=%s 结果=冷却中 耗时=%.6f秒", name, time.monotonic() - started)
            return False
        cooldown = self.policies.get(name, self.default_policy).cooldown
        if cooldown > 0:
            self._cooldowns[key] = started + cooldown
        try:
            await command.handler(context, tuple(parts[1:]))
        except asyncio.CancelledError:
            logger.info("命令=%s 结果=已取消 耗时=%.6f秒", name, time.monotonic() - started)
            raise
        except Exception as error:
            self.command_failures += 1
            logger.error("命令=%s 结果=失败 耗时=%.6f秒 异常=%s", name, time.monotonic() - started, type(error).__name__)
            raise
        self.command_successes += 1
        logger.info("命令=%s 结果=成功 耗时=%.6f秒", name, time.monotonic() - started)
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

