"""HTTP client for the Memo northbound API (grpc-gateway, POST-RPC).

Auth is a single ``x-api-key`` header (MATE_INSTANCE subject); the memory
scope is derived server-side from the credential — no request ever carries
scope fields. Recall-family calls degrade instead of failing: partial
results ship with ``degraded: true`` and the caller uses them as-is.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("hermes.memory.memo.client")


class MemoError(Exception):
    """Transport or server error from the Memo API."""


class MemoClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"x-api-key": api_key, "content-type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    # -- transport ---------------------------------------------------------

    def _post(self, path: str, body: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            resp = self._client.post(url, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise MemoError(f"memo request failed: {e}") from e
        if resp.status_code != 200:
            detail = resp.text[:300]
            raise MemoError(f"memo {path} -> {resp.status_code}: {detail}")
        try:
            return resp.json()
        except ValueError as e:
            raise MemoError(f"memo {path} returned non-JSON body") from e

    # -- northbound verbs ----------------------------------------------------

    def capture(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._post("/memo/v1/capture", {"events": events})

    def recent_raw(
        self,
        channel_type: str,
        conversation_ref: str,
        *,
        max_tokens: int = 0,
        window_hours: int = 0,
    ) -> Dict[str, Any]:
        return self._post("/memo/v1/recent-raw", {
            "channel_type": channel_type,
            "channel_conversation_ref": conversation_ref,
            "max_tokens": max_tokens,
            "window_hours": window_hours,
        })

    def recall(
        self,
        query: str,
        *,
        kind: str = "",
        project: str = "",
        sources: str = "",
        top_k: int = 0,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"query": query}
        if kind:
            body["kind"] = kind
        if project:
            body["project"] = project
        if sources:
            body["sources"] = sources
        if top_k:
            body["top_k"] = top_k
        return self._post("/memo/v1/recall", body, timeout=timeout)

    def get(self, ref: str) -> Dict[str, Any]:
        return self._post("/memo/v1/get", {"ref": ref})

    def remember(self, content: str, *, kind: str = "", pinned: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content": content, "pinned": pinned}
        if kind:
            body["kind"] = kind
        return self._post("/memo/v1/remember", body)

    def forget(self, *, ref: str = "", query: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if ref:
            body["ref"] = ref
        if query:
            body["query"] = query
        return self._post("/memo/v1/forget", body)
