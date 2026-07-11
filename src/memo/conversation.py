"""Conversation container mapping + channel_msg_ref synthesis.

The Memo adapter contract fixes each channel's "conversation container"
level once (Memo TD §4.1 身份与幂等): the same logical conversation must
always report the same channel-native conversation ref, because the
idempotency namespace and provenance both key off it.

Container decisions for hermes (fixed here, versioned with the plugin):

| hermes call shape                  | channel_type | conversation ref        |
|------------------------------------|--------------|-------------------------|
| gateway message (has chat_id)      | platform id  | chat_id (channel-native)|
| gateway session w/o chat_id        | platform id  | gateway_session_key     |
| ACP (Cursor/Zed)                   | acp          | session_id              |
| terminal (cli/tui, incl. oneshot)  | cli          | user_id (one per user)  |
| anything else                      | hermes       | session:<session_id>    |
| cron / subagent / non-primary      | (skipped — no capture, no recall scope) |

chat_id is the channel-native conversation id (e.g. the Telex conversation
the platform plugin reports via build_source) and survives gateway
restarts; gateway_session_key is a gateway-internal session record that
does NOT (live-verified 2026-07-11) — it stays only as a fallback for
platforms that never report chat_id.

Compression-driven session_id rotation never splits the container (the
gateway key / user identity is stable across it), matching the contract's
"conversation = channel-side container" semantics.
"""

from __future__ import annotations

import getpass
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Conversation:
    channel_type: str
    ref: str

    @property
    def key(self) -> str:
        return f"{self.channel_type}:{self.ref}"


def map_conversation(
    session_id: str,
    *,
    agent_context: str = "primary",
    platform: str = "",
    **kwargs: Any,
) -> Optional[Conversation]:
    """Return the conversation container for this hermes call, or None to
    disable Memo for the call (non-primary contexts never write or read)."""
    if agent_context != "primary":
        return None
    if kwargs.get("parent_session_id"):
        return None
    if platform == "cron":
        return None

    if chat_id := kwargs.get("chat_id"):
        return Conversation(platform or "hermes", str(chat_id))
    if key := kwargs.get("gateway_session_key"):
        return Conversation(platform or "hermes", str(key))
    if platform == "acp":
        return Conversation("acp", session_id)
    if platform in ("cli", "tui"):
        user_id = kwargs.get("user_id") or getpass.getuser()
        return Conversation("cli", str(user_id))
    return Conversation("hermes", f"session:{session_id}")


def synthesize_msg_ref(role: str, content: str, ts_ms: int) -> str:
    """Deterministic channel_msg_ref for events hermes gives us without a
    channel-native message id.

    Synthesized exactly once when the event is enqueued and frozen in the
    delivery buffer, so every retry of the same event carries the same ref
    (the contract's determinism requirement). ts_ms keeps refs unique when
    the same text repeats in a conversation.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"hm-{ts_ms}-{role}-{digest}"


def now_ms() -> int:
    return int(time.time() * 1000)
