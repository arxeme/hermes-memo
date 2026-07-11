"""Provider lifecycle unit tests against a fake Memo backend.

The fake substitutes MemoClient at the provider boundary, recording every
call — the same seam the openclaw adapter tests will use, so contract
assertions (provenance fields, once-per-session RecentRaw, tool text) stay
comparable across runtimes.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List

import pytest

import memo as memo_plugin
from memo import MemoMemoryProvider
from memo.client import MemoError
from memo.config import Config


class FakeMemoClient:
    def __init__(self) -> None:
        self.captured: List[Dict[str, Any]] = []
        self.capture_calls = 0
        self.capture_fail_times = 0
        self.recent_raw_calls: List[Dict[str, Any]] = []
        self.recall_calls: List[Dict[str, Any]] = []
        self.recent_raw_response: Dict[str, Any] = {"events": [], "truncated": False}
        self.recall_response: Dict[str, Any] = {
            "packed_context": "", "groups": [], "citations": [], "degraded": False,
        }
        self.lock = threading.Lock()

    def capture(self, events):
        with self.lock:
            self.capture_calls += 1
            if self.capture_fail_times > 0:
                self.capture_fail_times -= 1
                raise MemoError("boom")
            self.captured.extend(events)
        return {"accepted": len(events)}

    def recent_raw(self, channel_type, ref, *, max_tokens=0, window_hours=0):
        with self.lock:
            self.recent_raw_calls.append({
                "channel_type": channel_type, "ref": ref,
                "max_tokens": max_tokens, "window_hours": window_hours,
            })
        return self.recent_raw_response

    def recall(self, query, *, kind="", project="", sources="", top_k=0, timeout=None):
        with self.lock:
            self.recall_calls.append({
                "query": query, "kind": kind, "project": project,
                "sources": sources, "top_k": top_k,
            })
        return self.recall_response

    def get(self, ref):
        return {"content": f"full:{ref}", "anchors": {"path": "notes/fact/x.md"}}

    def remember(self, content, *, kind="", pinned=False):
        return {"ref": "n:42", "status": "active"}

    def forget(self, *, ref="", query=""):
        return {"deleted": [ref or query]}

    def close(self):
        pass


def make_provider(monkeypatch, fake, cfg):
    """Wire a provider to the fake and initialize with the gateway shape.

    The RecentRaw fetch fires inside initialize(), so tests that assert on
    its payload must set fake.recent_raw_response BEFORE calling this."""
    monkeypatch.setattr(memo_plugin, "load_config", lambda: cfg)
    monkeypatch.setattr(memo_plugin, "MemoClient", lambda *a, **kw: fake)
    p = MemoMemoryProvider()
    p.initialize(
        "sess-1",
        platform="telex",
        gateway_session_key="grp-777",
        user_id="u-100",
        user_name="Alice",
        agent_identity="helper",
    )
    return p


@pytest.fixture()
def provider(monkeypatch):
    """An initialized provider wired to a FakeMemoClient (gateway shape)."""
    fake = FakeMemoClient()
    cfg = Config(api_key="k-test", recent_raw_wait_seconds=2.0)
    p = make_provider(monkeypatch, fake, cfg)
    yield p, fake, cfg
    p.shutdown()


def wait_until(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── capture provenance ──────────────────────────────────────────────────


def test_sync_turn_reports_provenance(provider):
    p, fake, _ = provider
    p.sync_turn("帮我记一下部署流程", "好的，已了解。", session_id="sess-1")
    assert wait_until(lambda: len(fake.captured) == 2)

    user_ev, agent_ev = fake.captured
    for ev in (user_ev, agent_ev):
        assert ev["channel_type"] == "telex"
        assert ev["channel_conversation_ref"] == "grp-777"
        assert ev["channel_msg_ref"].startswith("hm-")
        assert ev["ts"]  # event time present (ISO)
    assert user_ev["turn_kind"] == "user"
    assert user_ev["speaker"] == {
        "role": "user", "channel_user_ref": "u-100", "display_name": "Alice",
    }
    assert agent_ev["turn_kind"] == "agent"
    assert agent_ev["speaker"]["role"] == "agent"
    assert agent_ev["speaker"]["display_name"] == "helper"


def test_capture_retries_until_ack(provider):
    p, fake, _ = provider
    fake.capture_fail_times = 2  # first two deliveries blow up
    p._worker._retry_base = 0.05  # fast test
    p.sync_turn("重试语义", "收到", session_id="sess-1")
    assert wait_until(lambda: len(fake.captured) == 2)
    assert fake.capture_calls >= 3
    refs = {e["channel_msg_ref"] for e in fake.captured}
    assert len(refs) == 2, "refs frozen at enqueue — retries never re-synthesize"


def test_empty_turn_content_skipped(provider):
    p, fake, _ = provider
    p.sync_turn("", "只有回复", session_id="sess-1")
    assert wait_until(lambda: len(fake.captured) == 1)
    assert fake.captured[0]["turn_kind"] == "agent"


# ── RecentRaw discipline ─────────────────────────────────────────────────


def test_recent_raw_once_per_session(monkeypatch):
    fake = FakeMemoClient()
    fake.recent_raw_response = {
        "events": [
            {"seq": 1, "speaker_role": "user", "ts": "2026-07-10T02:00:00Z", "content": "昨天说到部署"},
            {"seq": 2, "speaker_role": "agent", "ts": "2026-07-10T02:00:05Z", "content": "是的，用蓝绿"},
        ],
        "truncated": True,
    }
    cfg = Config(api_key="k-test", recent_raw_wait_seconds=2.0)
    p = make_provider(monkeypatch, fake, cfg)
    first = p.prefetch("hello", session_id="sess-1")
    assert "昨天说到部署" in first and "是的，用蓝绿" in first, "verbatim events injected"
    assert "truncated" in first
    second = p.prefetch("next turn", session_id="sess-1")
    assert second == "", "RecentRaw consumed once per session"
    assert len(fake.recent_raw_calls) == 1
    assert fake.recent_raw_calls[0]["channel_type"] == "telex"
    assert fake.recent_raw_calls[0]["ref"] == "grp-777"


def test_recent_raw_refetch_on_compression(provider):
    p, fake, _ = provider
    p.prefetch("t1", session_id="sess-1")
    assert len(fake.recent_raw_calls) == 1
    # Context compression rotates session_id — the sanctioned re-fetch point.
    p.on_session_switch("sess-2", parent_session_id="sess-1", reset=False)
    assert wait_until(lambda: len(fake.recent_raw_calls) == 2)
    # Conversation container survives the rotation (gateway key stable).
    assert fake.recent_raw_calls[1]["ref"] == "grp-777"


def test_recent_raw_failure_is_silent(provider):
    p, fake, _ = provider

    def boom(*a, **kw):
        raise MemoError("recent-raw down")

    fake.recent_raw = boom
    p.on_session_switch("sess-3", reset=True)  # re-schedule against the broken fake
    assert p.prefetch("q", session_id="sess-3") == "", "cold start, no exception"


# ── tools ────────────────────────────────────────────────────────────────


def test_recall_tool_passthrough_and_degraded(provider):
    p, fake, _ = provider
    fake.recall_response = {
        "packed_context": "<untrusted-memory>…</untrusted-memory>",
        "groups": [{"source": "memory", "hits": [{"ref": "c:1", "snippet": "预发验证", "score": 0.9}]}],
        "citations": ["telex:grp-777#12"],
        "degraded": True,
    }
    out = json.loads(p.handle_tool_call("memo_recall", {"query": "部署流程", "sources": "all"}))
    assert out["degraded"] is True, "degraded results used as-is, not an error"
    assert out["packed_context"].startswith("<untrusted-memory>")
    assert fake.recall_calls[0]["sources"] == "all"


def test_tool_validation(provider):
    p, _, _ = provider
    assert "error" in json.loads(p.handle_tool_call("memo_recall", {}))
    assert "error" in json.loads(p.handle_tool_call("memo_recall", {"query": "x", "sources": "bogus"}))
    assert "error" in json.loads(p.handle_tool_call("memo_get", {}))
    assert "error" in json.loads(p.handle_tool_call("memo_remember", {}))
    assert "error" in json.loads(p.handle_tool_call("memo_forget", {"ref": "a", "query": "b"}))
    assert "error" in json.loads(p.handle_tool_call("memo_forget", {}))
    assert "error" in json.loads(p.handle_tool_call("nope", {"x": 1}))


def test_remember_and_forget_roundtrip(provider):
    p, _, _ = provider
    out = json.loads(p.handle_tool_call("memo_remember", {"content": "报销截止每月25号", "pinned": True}))
    assert out["ref"] == "n:42"
    out = json.loads(p.handle_tool_call("memo_forget", {"ref": "n:42"}))
    assert out["deleted"] == ["n:42"]


def test_tool_error_never_raises(provider):
    p, fake, _ = provider

    def boom(*a, **kw):
        raise MemoError("recall backend down")

    fake.recall = boom
    out = json.loads(p.handle_tool_call("memo_recall", {"query": "x"}))
    assert "error" in out


# ── auto-recall dial ─────────────────────────────────────────────────────


def test_auto_recall_default_off(provider):
    p, fake, _ = provider
    p.prefetch("warmup", session_id="sess-1")  # consume RecentRaw
    p.queue_prefetch("下一轮的问题", session_id="sess-1")
    assert p.prefetch("下一轮的问题", session_id="sess-1") == ""
    assert not fake.recall_calls, "auto recall stays off by default"


def test_auto_recall_on_injects_packed(provider):
    p, fake, cfg = provider
    cfg.auto_recall = True
    fake.recall_response = {"packed_context": "<untrusted-memory>相关记忆</untrusted-memory>",
                            "groups": [], "citations": [], "degraded": False}
    p.prefetch("warmup", session_id="sess-1")  # consume RecentRaw
    p.queue_prefetch("部署流程是什么", session_id="sess-1")
    assert wait_until(lambda: len(fake.recall_calls) == 1)
    time.sleep(0.05)
    out = p.prefetch("部署流程是什么", session_id="sess-1")
    assert "相关记忆" in out


def test_auto_recall_timeout_silent(provider):
    p, fake, cfg = provider
    cfg.auto_recall = True

    def slow(*a, **kw):
        time.sleep(1.0)
        return {"packed_context": "too late"}

    fake.recall = slow
    p.prefetch("warmup", session_id="sess-1")
    p.queue_prefetch("q", session_id="sess-1")
    start = time.time()
    out = p.prefetch("q", session_id="sess-1")
    assert out == "", "timeout skips silently"
    assert time.time() - start < 0.8, "never blocks the reply"
