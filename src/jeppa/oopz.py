"""oopz-sdk 0.16.0 boundary. No signing material or wire reimplementation."""
from __future__ import annotations

import json
import logging

from .config import AccountConfig
from .runtime import Context, Message, Runtime

logger = logging.getLogger(__name__)


class OopzAdapter:
    def __init__(self, bot, runtime: Runtime, areas: tuple[str, ...]):
        self.bot = bot
        self.runtime = runtime
        self.areas = areas
        self.bot_id = bot.config.person_uid
        self.transport_open = False
        self.auth_confirmed = False
        self.subscription_sent = False
        self._publish_connection()

    def _publish_connection(self) -> None:
        self.runtime.set_connection(
            "oopz", self.bot_id,
            transport_open=self.transport_open,
            auth_confirmed=self.auth_confirmed,
            subscription_sent=self.subscription_sent,
        )

    async def opened(self, _context) -> None:
        self.transport_open = True
        self.auth_confirmed = False
        self.subscription_sent = False
        self._publish_connection()
        logger.info("连接通道已打开；已发送鉴权；等待 event=21 checkRes=true（尚未确认就绪）")
        if self.areas:
            await self.bot.ws.send_subscribe_area_events(list(self.areas))
            self.subscription_sent = True
            self._publish_connection()
            logger.info("已发送 %d 个配置域的订阅；鉴权和订阅确认仍不可知", len(self.areas))
        else:
            logger.info("未配置域；未发送域订阅")

    async def closed(self, _context, _event) -> None:
        self.transport_open = False
        self.auth_confirmed = False
        self.subscription_sent = False
        self._publish_connection()
        logger.warning("连接通道已关闭；鉴权和订阅状态已重置；SDK 可能重连")

    async def errored(self, _context, error) -> None:
        # Exception messages and payloads may include HTTP headers or credentials.
        logger.error("SDK 错误（%s）；已隐藏详情以保护凭据", type(error).__name__)

    async def emit_event(self, event) -> None:
        from oopz_sdk.events.context import EventContext
        from oopz_sdk.models import MessageEvent

        if event.event_type == 21:
            body = event.raw.get("body", {})
            if isinstance(body, str):
                body = json.loads(body)
            if isinstance(body, dict) and body.get("checkRes") is True and not self.auth_confirmed:
                self.auth_confirmed = True
                logger.info("鉴权已确认（event=21 checkRes=true）")
                self._publish_connection()
            elif isinstance(body, dict) and body.get("checkRes") is False:
                self.auth_confirmed = False
                self._publish_connection()
            return
        if not isinstance(event, MessageEvent) or event.event_name not in {"message", "message.private"}:
            return
        incoming = event.message
        normalized = Message(
            text=incoming.text,
            area=incoming.area,
            channel=incoming.channel,
            person=incoming.sender_id,
            message_id=incoming.message_id,
            is_private=event.is_private,
            platform="oopz",
            bot_id=self.bot_id,
        )
        sdk_context = EventContext(bot=self.bot, config=self.bot.config, event=event)
        try:
            await self.runtime.dispatch(Context(normalized, sdk_context.reply))
        except Exception as error:
            self.runtime.fail(error)
            raise


async def run(config: AccountConfig, runtime: Runtime, identities, *, migrate_legacy: bool = False) -> None:
    from oopz_sdk import OopzBot, OopzConfig

    sdk_config = OopzConfig(ignore_self_messages=True, auto_subscribe_joined_areas=False)
    await sdk_config.login_async(**config.auth)
    with identities.claim("oopz", sdk_config.person_uid):
        if migrate_legacy and runtime.storage is not None:
            runtime.storage.migrate_legacy_oopz(sdk_config.person_uid)
        bot = OopzBot(sdk_config)
        adapter = OopzAdapter(bot, runtime, config.areas)
        try:
            bot.add_adapter(adapter)
            bot.on_ready(adapter.opened)  # Transport open, NOT authenticated.
            bot.on_close(adapter.closed)
            bot.on_error(adapter.errored)
            await bot.run()
        finally:
            try:
                await bot.stop()
            finally:
                await adapter.closed(None, None)
