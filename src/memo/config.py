"""Configuration for the Memo memory provider.

Precedence: ``$HERMES_HOME/memo.json`` overrides environment variables,
which override defaults (same convention as hermes-membrain-plugin).
The API key is the MATE_INSTANCE credential — an opaque secret that stays
on the VM; the plugin never derives or transmits any scope information.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_KILL_SWITCH_ENV = "HERMES_MEMO_DISABLE"

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _kill_switch_on() -> bool:
    return _env(_KILL_SWITCH_ENV) in {"1", "true", "True", "yes"}


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    timeout_seconds: float = 10.0
    # RecentRaw consumption (once per session establishment).
    recent_raw_max_tokens: int = 4000
    recent_raw_window_hours: int = 0  # 0 = server default (48h outer bound)
    recent_raw_wait_seconds: float = 3.0
    # Per-turn automatic recall (host-side dial, default OFF per contract).
    auto_recall: bool = False
    auto_recall_top_k: int = 5
    auto_recall_timeout_seconds: float = 2.0
    # Capture delivery worker.
    capture_batch_max: int = 20
    capture_retry_base_seconds: float = 1.0
    capture_retry_cap_seconds: float = 60.0
    capture_buffer_max_events: int = 2000
    disabled: bool = False
    extra: dict = field(default_factory=dict)


def _config_path() -> Path:
    home = os.environ.get("HERMES_HOME", "~/.hermes")
    return Path(home).expanduser() / "memo.json"


def load_config() -> Config:
    cfg = Config(
        base_url=_env("MEMO_BASE_URL", DEFAULT_BASE_URL),
        api_key=_env("MEMO_API_KEY"),
        disabled=_kill_switch_on(),
    )
    if t := _env("MEMO_TIMEOUT"):
        cfg.timeout_seconds = float(t)
    if t := _env("MEMO_RECENT_RAW_MAX_TOKENS"):
        cfg.recent_raw_max_tokens = int(t)
    if t := _env("MEMO_AUTO_RECALL"):
        cfg.auto_recall = t in {"1", "true", "True", "yes"}

    path = _config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        for key, value in data.items():
            if hasattr(cfg, key) and key != "extra":
                setattr(cfg, key, value)
            else:
                cfg.extra[key] = value
    return cfg
