"""Authorized async bridge from Telegram /agy commands to the local Antigravity CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from app.core.config import BASE_DIR
from app.telegram.formatter import esc

logger = logging.getLogger("multi_exchange.telegram.agy_bridge")

AGY_BIN = Path(os.getenv("AGY_BIN_PATH", "/root/.local/bin/agy"))
AGY_TIMEOUT_SEC = float(os.getenv("AGY_TELEGRAM_TIMEOUT_SEC", "180"))
MAX_TG_CHUNK = 3600


def is_agy_bridge_enabled() -> bool:
    return os.getenv("AGY_TELEGRAM_BRIDGE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _extract_agy_reply(stdout_text: str) -> tuple[str, str | None]:
    """Parse agy --output-format json output, falling back to plain text."""
    raw = stdout_text.strip()
    if not raw:
        return "(empty response)", None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            conv_id = data.get("conversation_id") or data.get("conversationId")
            for key in ("response", "result", "text", "content", "message", "output"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip(), str(conv_id) if conv_id else None
            return json.dumps(data, indent=2), str(conv_id) if conv_id else None
    except Exception:
        pass
    return raw, None


def chunk_telegram_text(text: str, max_len: int = MAX_TG_CHUNK) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + max_len])
        start += max_len
    return chunks


class AgyTelegramBridge:
    """Runs serialized `agy -p` prompts for the authorized Telegram owner."""

    def __init__(self, workdir: Path = BASE_DIR, agy_bin: Path = AGY_BIN) -> None:
        self.workdir = workdir
        self.agy_bin = agy_bin
        self.conversation_id: str | None = None
        self._lock = asyncio.Lock()

    def reset_conversation(self) -> None:
        self.conversation_id = None

    async def run_prompt(self, prompt: str) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            return "Usage: <code>/agy your prompt here</code>"
        if not self.agy_bin.exists():
            return f"⚠️ <code>agy</code> binary not found at <code>{esc(str(self.agy_bin))}</code>"

        async with self._lock:
            cmd = [
                str(self.agy_bin),
                "-p",
                cleaned,
                "--output-format",
                "json",
            ]
            if self.conversation_id:
                cmd.extend(["--conversation", self.conversation_id])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=AGY_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"⏱️ <code>agy</code> timed out after {int(AGY_TIMEOUT_SEC)}s."

            stdout_s = stdout_b.decode("utf-8", errors="replace")
            stderr_s = stderr_b.decode("utf-8", errors="replace").strip()
            reply_text, new_conv_id = _extract_agy_reply(stdout_s)
            if new_conv_id:
                self.conversation_id = new_conv_id
            if proc.returncode != 0:
                err_detail = stderr_s or reply_text or f"exit code {proc.returncode}"
                return f"⚠️ <b>agy error</b> (code {proc.returncode}):\n<pre>{esc(err_detail[:3000])}</pre>"
            return reply_text
