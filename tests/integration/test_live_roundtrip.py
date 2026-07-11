"""Live integration tests against a locally running Memo service.

Preconditions (skipped unless MEMO_LIVE=1):
  - the memo service is up on MEMO_BASE_URL (default http://127.0.0.1:8000)
    with its shared-infra tunnel active (memo repo: `make tunnel`, then
    `test/start.sh`);
  - dev credentials from memo configs/debug/config.yaml: MEMO_API_KEY
    (MATE_INSTANCE) and MEMO_ADMIN_KEY (PLATFORM).

The tests purge the dev scope up front, so every assertion sees only its
own material. Covers TP IT-1..IT-4 — the northbound half of the S1-S3
chains; the hermes-runtime half runs in the remote E2E environment.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

if os.environ.get("MEMO_LIVE") != "1":
    pytest.skip("live Memo integration (set MEMO_LIVE=1)", allow_module_level=True)

import httpx

import memo as memo_plugin
from memo import MemoMemoryProvider
from memo.client import MemoClient
from memo.config import Config

BASE = os.environ.get("MEMO_BASE_URL", "http://127.0.0.1:8000")
MATE_KEY = os.environ.get("MEMO_API_KEY", "memo-dev-key")
ADMIN_KEY = os.environ.get("MEMO_ADMIN_KEY", "memo-admin-key")
SCOPE = os.environ.get("MEMO_SCOPE", "u-dev:mi-dev")

RUN = uuid.uuid4().hex[:8]  # per-run marker woven into content


def admin(path: str, body: dict) -> dict:
    resp = httpx.post(
        f"{BASE}/memo/admin/v1/{path}",
        json=body,
        headers={"x-api-key": ADMIN_KEY},
        timeout=60,
    )
    assert resp.status_code == 200, f"admin {path}: {resp.status_code} {resp.text[:200]}"
    return resp.json()


@pytest.fixture(scope="module", autouse=True)
def clean_scope():
    admin("scope-purge", {"scope_key": SCOPE})
    yield


def live_config() -> Config:
    return Config(base_url=BASE, api_key=MATE_KEY, recent_raw_wait_seconds=8.0)


def make_provider(monkeypatch, conversation_ref: str, session: str) -> MemoMemoryProvider:
    monkeypatch.setattr(memo_plugin, "load_config", live_config)
    p = MemoMemoryProvider()
    p.initialize(
        session,
        platform="telex",
        gateway_session_key=conversation_ref,
        user_id="u-live",
        user_name="LiveUser",
        agent_identity="helper",
    )
    return p


def wait_until(cond, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.2)
    return False


# ── IT-1 + IT-2: capture → RecentRaw closed loop ─────────────────────────


def test_capture_to_recent_raw(monkeypatch):
    conv = f"it-conv-{RUN}"
    p = make_provider(monkeypatch, conv, "sess-a")
    p.sync_turn(f"部署窗口定在周四晚上 {RUN}", f"好的，周四晚上执行 {RUN}", session_id="sess-a")
    p.sync_turn(f"记得先备份数据库 {RUN}", f"明白，备份后再发布 {RUN}", session_id="sess-a")
    assert p._worker.flush(15.0), "capture drains to ack"
    p.shutdown()

    # A new session on the same conversation starts from RecentRaw.
    p2 = make_provider(monkeypatch, conv, "sess-b")
    block = p2.prefetch("hello", session_id="sess-b")
    p2.shutdown()
    assert f"部署窗口定在周四晚上 {RUN}" in block, "verbatim user turn"
    assert f"备份后再发布 {RUN}" in block, "verbatim agent turn"
    assert "[user @" in block and "[agent @" in block, "roles preserved"


# ── IT-1: provenance lands in the event store (server-side view) ─────────


def test_capture_provenance_via_recent_raw_roles(monkeypatch):
    # RecentRaw only replays user/agent events with roles — reading it back
    # is the northbound-visible provenance check (the DB-level check lives
    # in memo's own TestW05/TestW24 suites).
    conv = f"it-prov-{RUN}"
    p = make_provider(monkeypatch, conv, "sess-p1")
    p.sync_turn(f"来源校验回合 {RUN}", f"收到来源校验 {RUN}", session_id="sess-p1")
    assert p._worker.flush(15.0)
    p.shutdown()

    client = MemoClient(BASE, MATE_KEY)
    resp = client.recent_raw("telex", conv)
    client.close()
    roles = [e.get("speaker_role") for e in resp.get("events", [])]
    assert roles == ["user", "agent"], f"turn_kind/order preserved, got {roles}"


# ── IT-3: four-tool live roundtrip ───────────────────────────────────────


def test_tools_roundtrip(monkeypatch):
    p = make_provider(monkeypatch, f"it-tools-{RUN}", "sess-t")
    try:
        out = json.loads(p.handle_tool_call(
            "memo_remember",
            {"content": f"报销截止日是每月二十五号 {RUN}", "kind": "fact"},
        ))
        ref = out.get("ref")
        assert ref, f"remember returned {out}"

        def recalled() -> bool:
            r = json.loads(p.handle_tool_call("memo_recall", {"query": f"报销 截止 {RUN}"}))
            hits = [h for g in r.get("groups", []) for h in g.get("hits", [])]
            return any(RUN in (h.get("snippet") or "") for h in hits)

        assert wait_until(recalled, 30.0), "explicit note becomes recallable"

        got = json.loads(p.handle_tool_call("memo_get", {"ref": ref}))
        assert RUN in got.get("content", "")

        deleted = json.loads(p.handle_tool_call("memo_forget", {"ref": ref}))
        assert deleted.get("deleted"), f"forget returned {deleted}"

        r = json.loads(p.handle_tool_call("memo_get", {"ref": ref}))
        assert "error" in r, "forgotten ref no longer resolvable"
    finally:
        p.shutdown()


# ── IT-4: candidate chain (S1 second half, Mate approval simulated) ──────


def test_candidate_chain(monkeypatch):
    p = make_provider(monkeypatch, f"it-cand-{RUN}", "sess-c")
    try:
        out = json.loads(p.handle_tool_call(
            "memo_remember",
            {"content": f"生产发布必须先在预发验证 {RUN}", "kind": "norm", "pinned": True},
        ))
        assert out.get("ref")

        admin("consolidate-now", {"scope_key": SCOPE})

        client = httpx.Client(headers={"x-api-key": MATE_KEY}, timeout=30)
        try:
            cands = client.post(
                f"{BASE}/memo/v1/list-promotion-candidates", json={"limit": 20}
            ).json().get("candidates", [])
            mine = [c for c in cands if RUN in (c.get("line") or "")]
            assert mine, f"pinned note surfaced as candidate, got {cands}"
            cand = mine[0]
            assert cand.get("reasons"), "per-signal reasoning attached"

            # Mate approval simulated: feedback goes through the same API
            # Mate calls; Memo records, application of the line is Mate's.
            ok = client.post(
                f"{BASE}/memo/v1/submit-candidate-feedback",
                json={"dedup_key": cand["dedup_key"], "verdict": "accepted"},
            ).json()
            assert ok.get("ok") is True

            cands = client.post(
                f"{BASE}/memo/v1/list-promotion-candidates", json={"limit": 20}
            ).json().get("candidates", [])
            assert not [c for c in cands if RUN in (c.get("line") or "")], \
                "accepted candidate leaves the surfaced list"
        finally:
            client.close()
    finally:
        p.shutdown()


# ── IT-5: S3 cross-channel recall (northbound half) ──────────────────────


def test_cross_channel_recall(monkeypatch):
    # Material lands on channel A; a session on channel B recalls it —
    # memory scope is the agent, conversations only partition provenance.
    conv_a, conv_b = f"it-chanA-{RUN}", f"it-chanB-{RUN}"
    p_a = make_provider(monkeypatch, conv_a, "sess-x1")
    p_a.sync_turn(
        f"新加坡机房的迁移计划编号是 MIG-{RUN}",
        f"好的，记下了迁移计划 MIG-{RUN}",
        session_id="sess-x1",
    )
    assert p_a._worker.flush(15.0)
    p_a.shutdown()

    p_b = make_provider(monkeypatch, conv_b, "sess-x2")
    try:
        def recalled() -> bool:
            r = json.loads(p_b.handle_tool_call("memo_recall", {"query": f"迁移计划 MIG-{RUN}"}))
            hits = [h for g in r.get("groups", []) for h in g.get("hits", [])]
            return any(f"MIG-{RUN}" in (h.get("snippet") or "") for h in hits)

        assert wait_until(recalled, 30.0), "channel-B session recalls channel-A material"
        r = json.loads(p_b.handle_tool_call("memo_recall", {"query": f"迁移计划 MIG-{RUN}"}))
        hits = [h for g in r.get("groups", []) for h in g.get("hits", [])]
        cited = [h for h in hits if f"MIG-{RUN}" in (h.get("snippet") or "")]
        assert any(conv_a in (h.get("citation") or "") for h in cited), \
            "provenance cites the origin conversation"
    finally:
        p_b.shutdown()
