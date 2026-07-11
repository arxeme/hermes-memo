"""pytest config — inject hermes-agent and src/ into sys.path.

Plugin code imports ``from agent.memory_provider import MemoryProvider``;
hermes-agent is expected at ``$HERMES_AGENT_PATH`` (default:
``~/Work/project/ai-study/ref/hermes-agent``), same convention as
hermes-membrain-plugin.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULT_HERMES_PATH = Path.home() / "Work" / "project" / "ai-study" / "ref" / "hermes-agent"
_hermes_path = Path(os.environ.get("HERMES_AGENT_PATH", str(_DEFAULT_HERMES_PATH))).expanduser().resolve()

if str(_hermes_path) not in sys.path:
    sys.path.insert(0, str(_hermes_path))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
