"""In-process adapter supervision; platforms own transport and normalization only."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import contextmanager
from typing import Protocol

from .config import AccountConfig
from .runtime import Runtime


class IdentityRegistry:
    """A bot identity may have only one active connection in this runtime."""

    def __init__(self):
        self._active: set[tuple[str, str]] = set()

    @contextmanager
    def claim(self, platform: str, bot_id: str):
        identity = (platform, bot_id)
        if not platform or not bot_id or identity in self._active:
            raise ValueError("适配器账号身份无效或重复连接")
        self._active.add(identity)
        try:
            yield
        finally:
            self._active.remove(identity)


class AdapterRunner(Protocol):
    def __call__(self, account: AccountConfig, runtime: Runtime, identities: IdentityRegistry,
                 *, migrate_legacy: bool = False) -> Awaitable[None]: ...


async def run_accounts(accounts: tuple[AccountConfig, ...], runtime: Runtime, *,
                       legacy_account: str | None = None,
                       adapters: Mapping[str, AdapterRunner] | None = None) -> None:
    if not accounts:
        raise ValueError("未配置任何账号")
    if adapters is None:
        from .oopz import run
        adapters = {"oopz": run}
    if any(account.platform not in adapters for account in accounts):
        raise ValueError("未注册所配置平台的适配器")
    identities = IdentityRegistry()
    tasks = []
    failed = asyncio.create_task(runtime.failure.wait())
    try:
        for account in accounts:
            tasks.append(asyncio.create_task(adapters[account.platform](
                account, runtime, identities, migrate_legacy=account.label == legacy_account)))
        done, _ = await asyncio.wait([*tasks, failed], return_when=asyncio.FIRST_COMPLETED)
        if failed in done:
            assert runtime.error is not None
            raise runtime.error
        for task in tasks:
            if task in done:
                await task
        raise RuntimeError("账号连接意外结束")
    finally:
        for task in (*tasks, failed):
            task.cancel()
        await asyncio.gather(*tasks, failed, return_exceptions=True)
