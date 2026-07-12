# Hermes Memo Plugin

Voyager **Memo** memory-service provider for Hermes Agent. It gives a Hermes
agent platform-managed long-term memory: every conversation turn is captured
to Memo, sessions start with the conversation's recent raw context
(RecentRaw), and the model gets the four canonical Memo tools
(`memo_recall` / `memo_get` / `memo_remember` / `memo_forget`).

The provider id inside Hermes is `memo`.

## Layout

```text
hermes-memo/
  src/memo/
    plugin.yaml         # hermes plugin manifest
    __init__.py         # MemoMemoryProvider + register(ctx)
    client.py           # Memo northbound HTTP client (x-api-key)
    config.py           # env + $HERMES_HOME/memo.json
    conversation.py     # conversation-container mapping + msg_ref synthesis
    capture.py          # at-least-once delivery worker (buffer + backoff)
    tools.py            # canonical tool schemas (registered as-is)
  tests/unit            # stub-backed unit tests (no network)
  docs/spec  docs/test
```

## Install

```bash
# Option A — symlink (recommended for dev)
ln -s "$(pwd)/src/memo" "$HERMES_HOME/plugins/memo"

# Option B — copy
cp -r src/memo "$HERMES_HOME/plugins/memo"
```

Activate in hermes config:

```yaml
# $HERMES_HOME/config.yaml
memory:
  provider: memo
```

Configure the service endpoint and the MATE_INSTANCE credential:

```bash
export MEMO_BASE_URL=http://127.0.0.1:8000   # Memo HTTP gateway
export MEMO_API_KEY=<mate-instance-key>       # opaque; scope derives server-side
```

Or `$HERMES_HOME/memo.json` (JSON overrides env):

```json
{ "base_url": "http://127.0.0.1:8000", "api_key": "…", "auto_recall": false }
```

Restart hermes; the log shows `memo provider initialized (conversation=…)`.

## Configuration

| Key | Env var | Default | Notes |
|---|---|---|---|
| `base_url` | `MEMO_BASE_URL` | `http://127.0.0.1:8000` | Memo HTTP gateway |
| `api_key` | `MEMO_API_KEY` | _(required)_ | MATE_INSTANCE key; never carries scope |
| `timeout_seconds` | `MEMO_TIMEOUT` | `10` | Per-request HTTP timeout |
| `recent_raw_max_tokens` | `MEMO_RECENT_RAW_MAX_TOKENS` | `4000` | Session-start injection budget |
| `recent_raw_window_hours` | — | `0` (server default 48h) | Outer window |
| `auto_recall` | `MEMO_AUTO_RECALL` | `false` | Per-turn automatic recall dial |
| `auto_recall_top_k` | — | `5` | Auto-recall hit budget |
| `auto_recall_timeout_seconds` | — | `2` | Timeouts skip silently |

Kill switch:

```bash
export HERMES_MEMO_DISABLE=1
```

## How it maps hermes onto Memo conversations

Same logical conversation → same channel-native ref, fixed once per shape
(the Memo adapter contract's idempotency requirement):

| hermes call shape | channel_type | conversation ref |
|---|---|---|
| gateway session (Telex/Discord/…) | platform id | gateway session key |
| ACP (Cursor/Zed) | `acp` | ACP session id |
| terminal (cli/tui) | `cli` | user id (stable across restarts) |
| anything else | `hermes` | `session:<session_id>` |
| cron / subagent | _(provider inactive)_ | — |

Context compression rotates `session_id` without splitting the container;
the plugin re-fetches RecentRaw at that boundary — the contract's sanctioned
re-fetch point.

## Contract obligations

See [docs/spec/td_hermes-memo-plugin_zh.md](docs/spec/td_hermes-memo-plugin_zh.md)
for the full host-adapter contract self-check (身份与幂等 / 原文完整性 /
字段语义 / 注入与工具纪律) with per-item evidence.

## Development

```bash
uv venv .venv --python 3.12
uv pip install -p .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
```

Unit tests stub the Memo client; hermes-agent itself is resolved from
`$HERMES_AGENT_PATH` (see `tests/conftest.py`).
