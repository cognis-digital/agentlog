"""agentlog — part of the Cognis Neural Suite.

Agentic workflow replay & audit over OpenTelemetry GenAI-semantic-convention
spans. The public API and identity are re-exported from :mod:`agentlog.core`.
"""
try:  # re-export the tool's public API + identity from core
    from agentlog.core import *  # noqa: F401,F403
    from agentlog.core import TOOL_NAME, TOOL_VERSION
except Exception:  # pragma: no cover - never let import fail hard
    TOOL_NAME = "agentlog"
    TOOL_VERSION = "1.2.5"
__version__ = TOOL_VERSION
