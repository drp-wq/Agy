"""Long-polling Telegram getUpdates worker with persistent offset and error backoff."""

from __future__ import annotations

import asyncio
import logging

from app.telegram.client import TelegramAPIError, TelegramClient
from app.telegram.commands import BOT_COMMANDS, TelegramCommandHandler
from app.telegram.state import TelegramStateStore

logger = logging.getLogger("multi_exchange.telegram.poller")


class TelegramPollingWorker:
    """Async getUpdates long-poller with persisted cursor and backoff."""

    def __init__(
        self,
        client: TelegramClient,
        handler: TelegramCommandHandler,
        state_store: TelegramStateStore,
        poll_timeout_sec: int = 25,
    ) -> None:
        self.client = client
        self.handler = handler
        self.state = state_store
        self.poll_timeout_sec = poll_timeout_sec
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> int:
        """Fetch and process one batch of updates; returns count of updates processed."""
        offset = self.state.get_update_offset()
        updates = await self.client.get_updates(offset=offset, timeout=self.poll_timeout_sec)
        self.state.record_poll_heartbeat()
        processed = 0
        max_next_offset = offset

        for upd in updates:
            upd_id = upd.get("update_id")
            if isinstance(upd_id, int):
                max_next_offset = max(max_next_offset, upd_id + 1)

            reply = self.handler.handle_update(upd)
            if reply is not None:
                html_text, markup = reply
                try:
                    await self.client.send_message(html_text, reply_markup=markup)
                except TelegramAPIError as exc:
                    logger.warning("Failed to send command reply: %s", exc)
            processed += 1

        if max_next_offset > offset:
            self.state.set_update_offset(max_next_offset)
        return processed

    async def _run_loop(self) -> None:
        try:
            await self.client.set_my_commands(BOT_COMMANDS)
            await self.client.set_chat_menu_button()
        except Exception as exc:
            logger.warning("Could not register Telegram bot commands/menu: %s", type(exc).__name__)

        backoff = 2.0
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except TelegramAPIError as exc:
                self.state.record_poll_heartbeat(error=str(exc))
                logger.warning("Telegram poll error (retrying in %.1fs): %s", backoff, exc)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 60.0)
            except Exception as exc:
                self.state.record_poll_heartbeat(error=type(exc).__name__)
                logger.warning("Unexpected poll error (%s)", type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 60.0)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
