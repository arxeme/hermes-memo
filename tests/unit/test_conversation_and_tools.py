"""Container mapping, msg_ref determinism, config and tool-schema tests."""

from __future__ import annotations

import pytest

from memo.conversation import map_conversation, synthesize_msg_ref
from memo.config import load_config
from memo.tools import all_schemas, clamp_top_k


# ── conversation container mapping (一次定死) ────────────────────────────


def test_gateway_shape_prefers_channel_native_chat_id():
    conv = map_conversation("s1", platform="telex",
                            chat_id="conv-native-1", gateway_session_key="gw-restartable")
    assert conv.channel_type == "telex" and conv.ref == "conv-native-1"
    assert conv.key == "telex:conv-native-1"


def test_gateway_shape_falls_back_to_session_key():
    conv = map_conversation("s1", platform="telex", gateway_session_key="grp-9")
    assert conv.channel_type == "telex" and conv.ref == "grp-9"


def test_cli_shape_is_per_user_stable():
    a = map_conversation("s1", platform="cli", user_id="yy")
    b = map_conversation("s2-after-restart", platform="cli", user_id="yy")
    assert a == b, "CLI container survives session rotation"


def test_acp_shape_is_per_session():
    conv = map_conversation("acp-sess", platform="acp")
    assert (conv.channel_type, conv.ref) == ("acp", "acp-sess")


def test_fallback_shape():
    conv = map_conversation("s9", platform="")
    assert (conv.channel_type, conv.ref) == ("hermes", "session:s9")


@pytest.mark.parametrize("kwargs", [
    {"agent_context": "subagent"},
    {"agent_context": "cron"},
    {"parent_session_id": "parent-1"},
    {"platform": "cron"},
])
def test_non_primary_contexts_skip(kwargs):
    assert map_conversation("s1", **kwargs) is None


# ── msg_ref synthesis ────────────────────────────────────────────────────


def test_msg_ref_deterministic_for_same_event():
    a = synthesize_msg_ref("user", "同一条消息", 1720600000000)
    b = synthesize_msg_ref("user", "同一条消息", 1720600000000)
    assert a == b, "same event synthesizes the same ref at any time"


def test_msg_ref_distinguishes_repeats_and_roles():
    base = synthesize_msg_ref("user", "好的", 1720600000000)
    later = synthesize_msg_ref("user", "好的", 1720600009000)
    agent = synthesize_msg_ref("agent", "好的", 1720600000000)
    assert base != later, "repeated text at a different time is a new event"
    assert base != agent, "role participates in identity"


# ── config ───────────────────────────────────────────────────────────────


def test_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MEMO_DISABLE", "1")
    assert load_config().disabled is True


def test_json_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MEMO_BASE_URL", "http://env:8000")
    (tmp_path / "memo.json").write_text('{"base_url": "http://json:8000", "auto_recall": true}')
    cfg = load_config()
    assert cfg.base_url == "http://json:8000"
    assert cfg.auto_recall is True


# ── canonical tool text ──────────────────────────────────────────────────


def test_four_tools_registered_with_canonical_names():
    names = [s["name"] for s in all_schemas()]
    assert names == ["memo_recall", "memo_get", "memo_remember", "memo_forget"]


def test_tool_text_carries_untrusted_guidance():
    recall = all_schemas()[0]
    assert "UNTRUSTED" in recall["description"]
    assert recall["parameters"]["required"] == ["query"]
    assert recall["parameters"]["properties"]["sources"]["enum"] == ["memory", "references", "all"]


def test_clamp_top_k():
    assert clamp_top_k(None) == 0
    assert clamp_top_k("7") == 7
    assert clamp_top_k(999) == 20
    assert clamp_top_k(-3) == 0
