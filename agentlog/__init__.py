"""AGENTLOG — Agentic workflow replay & audit over OTel GenAI spans.

Ingests OpenTelemetry GenAI-semantic-convention spans (the agent / LLM /
tool calls an autonomous workflow emits), reconstructs the causal execution
tree, replays it deterministically, and audits it for security, cost, and
correctness findings.

Standard library only, zero install.
"""

from .core import (
    Span,
    Trace,
    Finding,
    AuditReport,
    load_spans,
    build_traces,
    replay_trace,
    audit_trace,
    summarize,
)

TOOL_NAME = "agentlog"
TOOL_VERSION = "1.0.0"

__all__ = [
    "Span",
    "Trace",
    "Finding",
    "AuditReport",
    "load_spans",
    "build_traces",
    "replay_trace",
    "audit_trace",
    "summarize",
    "TOOL_NAME",
    "TOOL_VERSION",
]
