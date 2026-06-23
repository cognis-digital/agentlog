"""Core engine for AGENTLOG.

Models OpenTelemetry GenAI-semantic-convention spans, reconstructs traces,
replays the agent execution tree, and audits for findings.

Recognised OTel GenAI attributes (subset, per the GenAI semantic conventions):
    gen_ai.system                e.g. "anthropic", "openai"
    gen_ai.operation.name        "chat" | "execute_tool" | "embeddings" | ...
    gen_ai.request.model
    gen_ai.response.model
    gen_ai.usage.input_tokens
    gen_ai.usage.output_tokens
    gen_ai.tool.name
    gen_ai.tool.call.arguments    (may contain sensitive data)
    gen_ai.response.finish_reasons

A span maps to one unit of agent work. parent_span_id links them into a tree.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

TOOL_NAME = "agentlog"


def _read_version() -> str:
    """Resolve the tool version from the repo VERSION file, falling back
    to a sane default so the package never fails to import."""
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, os.pardir, "VERSION"),
        os.path.join(here, "VERSION"),
    ):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                v = fh.read().strip()
            if v:
                return v
        except OSError:
            continue
    return "1.2.5"


TOOL_VERSION = _read_version()


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

# OTel span status codes.
_STATUS_OK = {"OK", "UNSET", "0", "1", "", None}

# Patterns that indicate a secret/PII leaking through tool arguments or prompts.
_SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}")),
    ("api_key_assign", re.compile(r"(?i)(?:api[_-]?key|secret|password)\s*[=:]\s*[A-Za-z0-9._\-]{8,}")),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
]

# Tool names that perform irreversible / high-blast-radius actions.
_DANGEROUS_TOOLS = {
    "shell", "bash", "exec", "execute_command", "run_command",
    "delete_file", "rm", "write_file", "http_request", "send_email",
    "transfer_funds", "execute_sql", "sql", "kubectl", "terraform_apply",
}

# Prompt-injection markers commonly seen in untrusted tool outputs.
_INJECTION_MARKERS = [
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "new instructions:",
    "reveal your",
    "exfiltrate",
]


@dataclass
class Span:
    """One OTel span describing a unit of agentic work."""

    span_id: str
    name: str
    trace_id: str = ""
    parent_span_id: Optional[str] = None
    start_ns: int = 0
    end_ns: int = 0
    status: str = "UNSET"
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_ns and self.start_ns:
            return round((self.end_ns - self.start_ns) / 1e6, 3)
        return 0.0

    @property
    def operation(self) -> str:
        return str(self.attributes.get("gen_ai.operation.name", "") or "")

    @property
    def is_error(self) -> bool:
        return str(self.status).upper() not in {s for s in _STATUS_OK if s}

    @property
    def input_tokens(self) -> int:
        return _as_int(self.attributes.get("gen_ai.usage.input_tokens"))

    @property
    def output_tokens(self) -> int:
        return _as_int(self.attributes.get("gen_ai.usage.output_tokens"))

    @property
    def tool_name(self) -> str:
        return str(self.attributes.get("gen_ai.tool.name", "") or "")

    def text_blob(self) -> str:
        """All free-text attributes concatenated, for content scanning."""
        parts: List[str] = []
        for k, v in self.attributes.items():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (list, dict)):
                parts.append(json.dumps(v, default=str))
        return "\n".join(parts)


@dataclass
class Trace:
    trace_id: str
    spans: List[Span]

    def by_id(self) -> Dict[str, Span]:
        return {s.span_id: s for s in self.spans}

    def roots(self) -> List[Span]:
        ids = self.by_id()
        return [s for s in self.spans
                if not s.parent_span_id or s.parent_span_id not in ids]


@dataclass
class Finding:
    severity: str          # critical | high | medium | low | info
    code: str
    span_id: str
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "span_id": self.span_id,
            "message": self.message,
        }


@dataclass
class AuditReport:
    trace_id: str
    findings: List[Finding]
    metrics: Dict[str, Any]

    def exit_failing(self) -> bool:
        return any(f.severity in ("critical", "high") for f in self.findings)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "metrics": self.metrics,
            "findings": [f.as_dict() for f in self.findings],
        }


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _flatten_attrs(raw: Any) -> Dict[str, Any]:
    """Accept either {'key': value} or OTel KeyValue list form."""
    if isinstance(raw, dict):
        return dict(raw)
    out: Dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            val = item.get("value", item.get("v"))
            if isinstance(val, dict):
                # OTel AnyValue: {"stringValue": ...}, {"intValue": ...}, etc.
                for vk in ("stringValue", "intValue", "doubleValue", "boolValue"):
                    if vk in val:
                        val = val[vk]
                        break
            if key is not None:
                out[str(key)] = val
    return out


def _coerce_span(obj: Dict[str, Any]) -> Span:
    attrs = _flatten_attrs(obj.get("attributes", obj.get("attrs", {})))
    return Span(
        span_id=str(obj.get("span_id") or obj.get("spanId") or obj.get("id") or ""),
        name=str(obj.get("name", "")),
        trace_id=str(obj.get("trace_id") or obj.get("traceId") or "default"),
        parent_span_id=(str(obj["parent_span_id"]) if obj.get("parent_span_id")
                        else (str(obj["parentSpanId"]) if obj.get("parentSpanId") else None)),
        start_ns=_as_int(obj.get("start_ns") or obj.get("startTimeUnixNano") or 0),
        end_ns=_as_int(obj.get("end_ns") or obj.get("endTimeUnixNano") or 0),
        status=str((obj.get("status") or {}).get("code")
                   if isinstance(obj.get("status"), dict) else obj.get("status", "UNSET")),
        attributes=attrs,
    )


def load_spans(text: str) -> List[Span]:
    """Parse spans from JSON (array or object) or JSONL.

    Raises ValueError on unusable input.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty input: no spans to load")

    objs: List[Dict[str, Any]] = []
    try:
        doc = json.loads(text)
        if isinstance(doc, list):
            objs = [o for o in doc if isinstance(o, dict)]
        elif isinstance(doc, dict):
            if isinstance(doc.get("spans"), list):
                objs = [o for o in doc["spans"] if isinstance(o, dict)]
            else:
                objs = [doc]
    except json.JSONDecodeError:
        # Fall back to JSONL.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            objs.append(json.loads(line))

    if not objs:
        raise ValueError("no span objects found in input")

    spans = [_coerce_span(o) for o in objs]
    bad = [s for s in spans if not s.span_id]
    if bad:
        raise ValueError(f"{len(bad)} span(s) missing a span_id")
    return spans


def build_traces(spans: Iterable[Span]) -> List[Trace]:
    groups: Dict[str, List[Span]] = {}
    for s in spans:
        groups.setdefault(s.trace_id or "default", []).append(s)
    return [Trace(tid, sorted(ss, key=lambda x: x.start_ns))
            for tid, ss in sorted(groups.items())]


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

def replay_trace(trace: Trace) -> List[Dict[str, Any]]:
    """Deterministic depth-first replay of the agent execution tree.

    Returns ordered step records with depth, so a viewer can render the
    causal flow exactly as the agent ran it.
    """
    by_id = trace.by_id()
    children: Dict[Optional[str], List[Span]] = {}
    for s in trace.spans:
        pid = s.parent_span_id if s.parent_span_id in by_id else None
        children.setdefault(pid, []).append(s)
    for kids in children.values():
        kids.sort(key=lambda x: (x.start_ns, x.span_id))

    steps: List[Dict[str, Any]] = []
    order = {"n": 0}

    def walk(span: Span, depth: int) -> None:
        order["n"] += 1
        label = span.tool_name or span.attributes.get("gen_ai.request.model") or ""
        steps.append({
            "step": order["n"],
            "depth": depth,
            "span_id": span.span_id,
            "name": span.name,
            "operation": span.operation,
            "detail": str(label),
            "duration_ms": span.duration_ms,
            "tokens": span.input_tokens + span.output_tokens,
            "status": "ERROR" if span.is_error else "OK",
        })
        for child in children.get(span.span_id, []):
            walk(child, depth + 1)

    for root in sorted(trace.roots(), key=lambda x: (x.start_ns, x.span_id)):
        walk(root, 0)
    return steps


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def _scan_secrets(blob: str) -> List[str]:
    hits: List[str] = []
    for label, pat in _SECRET_PATTERNS:
        if pat.search(blob):
            hits.append(label)
    return hits


def audit_trace(trace: Trace, max_tokens: int = 100_000) -> AuditReport:
    findings: List[Finding] = []
    by_id = trace.by_id()

    total_in = total_out = 0
    error_count = 0
    tool_calls = 0
    llm_calls = 0
    models: Dict[str, int] = {}

    for s in trace.spans:
        total_in += s.input_tokens
        total_out += s.output_tokens
        op = s.operation
        if op == "execute_tool" or s.tool_name:
            tool_calls += 1
        if op in ("chat", "text_completion", "generate_content"):
            llm_calls += 1
        m = s.attributes.get("gen_ai.response.model") or s.attributes.get("gen_ai.request.model")
        if m:
            models[str(m)] = models.get(str(m), 0) + 1

        if s.is_error:
            error_count += 1
            findings.append(Finding("high", "span_error", s.span_id,
                                    f"span '{s.name}' ended with status {s.status}"))

        # Orphaned span (parent referenced but absent) breaks audit trail.
        if s.parent_span_id and s.parent_span_id not in by_id:
            findings.append(Finding("medium", "broken_trace", s.span_id,
                                    f"parent_span_id '{s.parent_span_id}' not present in trace"))

        # Secret / PII leakage through any text attribute.
        leaks = _scan_secrets(s.text_blob())
        for leak in leaks:
            findings.append(Finding("critical", "secret_leak", s.span_id,
                                    f"possible {leak} exposed in span attributes"))

        # Dangerous tool invocation.
        if s.tool_name and s.tool_name.lower() in _DANGEROUS_TOOLS:
            findings.append(Finding("high", "dangerous_tool", s.span_id,
                                    f"high-blast-radius tool '{s.tool_name}' invoked"))

        # Prompt-injection markers in tool output / messages.
        low = s.text_blob().lower()
        for marker in _INJECTION_MARKERS:
            if marker in low:
                findings.append(Finding("high", "prompt_injection", s.span_id,
                                        f"prompt-injection marker '{marker}' found in span content"))
                break

    total_tokens = total_in + total_out
    if total_tokens > max_tokens:
        findings.append(Finding("medium", "token_budget", trace.trace_id,
                                f"trace used {total_tokens} tokens (budget {max_tokens})"))

    # Runaway loop: an agent re-invoking the same tool many times.
    tool_freq: Dict[str, int] = {}
    for s in trace.spans:
        if s.tool_name:
            tool_freq[s.tool_name] = tool_freq.get(s.tool_name, 0) + 1
    for name, cnt in tool_freq.items():
        if cnt >= 10:
            findings.append(Finding("medium", "runaway_loop", trace.trace_id,
                                    f"tool '{name}' called {cnt} times (possible loop)"))

    metrics = {
        "spans": len(trace.spans),
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "errors": error_count,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_tokens,
        "models": models,
    }

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: (sev_rank.get(f.severity, 9), f.code))
    return AuditReport(trace.trace_id, findings, metrics)


def summarize(traces: List[Trace]) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    for t in traces:
        rep = audit_trace(t)
        out.append({
            "trace_id": t.trace_id,
            "spans": rep.metrics["spans"],
            "total_tokens": rep.metrics["total_tokens"],
            "errors": rep.metrics["errors"],
            "findings": len(rep.findings),
        })
    return {"traces": out, "trace_count": len(traces)}


def scan(target: str, max_tokens: int = 100_000) -> Dict[str, Any]:
    """Audit a span file (or a directory of ``*.json`` / ``*.jsonl`` span files)
    and return a single JSON-serializable report.

    This is the entry point the MCP server exposes so Cognis.Studio / Claude
    Desktop / Cursor agents can call agentlog as a scoped capability. It is
    fully offline: it only reads local files, never the network.
    """
    paths: List[str] = []
    if os.path.isdir(target):
        for root, _dirs, files in os.walk(target):
            for fn in sorted(files):
                if fn.endswith((".json", ".jsonl")):
                    paths.append(os.path.join(root, fn))
    else:
        paths.append(target)

    all_reports: List[AuditReport] = []
    errors: List[Dict[str, str]] = []
    for p in sorted(paths):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                spans = load_spans(fh.read())
        except (OSError, ValueError) as e:
            errors.append({"file": p, "error": str(e)})
            continue
        for tr in build_traces(spans):
            all_reports.append(audit_trace(tr, max_tokens=max_tokens))

    failing = any(r.exit_failing() for r in all_reports)
    return {
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "target": target,
        "files_scanned": len(paths) - len(errors),
        "reports": [r.as_dict() for r in all_reports],
        "findings_total": sum(len(r.findings) for r in all_reports),
        "failing": failing,
        "errors": errors,
    }


# --------------------------------------------------------------------------
# Renderers — SARIF (GitHub code-scanning) and HTML (shareable report)
# --------------------------------------------------------------------------

# SARIF 2.1.0 only knows error/warning/note. Map agentlog severities onto it.
_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def to_sarif(reports: List[AuditReport]) -> Dict[str, Any]:
    """Render audit findings as a SARIF 2.1.0 log.

    SARIF drops directly into GitHub code-scanning and IDE problem panes.
    Each agentlog finding code becomes a SARIF rule; each finding becomes a
    result whose ``logicalLocation`` is the offending span id.
    """
    rule_meta = {
        "span_error": ("Span ended in an error state", "high"),
        "broken_trace": ("Span references a parent that is absent", "medium"),
        "secret_leak": ("Secret or PII exposed in span attributes", "critical"),
        "dangerous_tool": ("High-blast-radius tool invoked", "high"),
        "prompt_injection": ("Prompt-injection marker in span content", "high"),
        "token_budget": ("Trace exceeded its token budget", "medium"),
        "runaway_loop": ("Tool invoked repeatedly (possible loop)", "medium"),
    }
    seen: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []
    for rep in reports:
        for f in rep.findings:
            desc, _ = rule_meta.get(f.code, (f.code, f.severity))
            if f.code not in seen:
                seen[f.code] = {
                    "id": f.code,
                    "name": f.code,
                    "shortDescription": {"text": desc},
                    "defaultConfiguration": {
                        "level": _SARIF_LEVEL.get(f.severity, "warning")
                    },
                }
            results.append({
                "ruleId": f.code,
                "level": _SARIF_LEVEL.get(f.severity, "warning"),
                "message": {"text": f.message},
                "properties": {
                    "severity": f.severity,
                    "trace_id": rep.trace_id,
                    "span_id": f.span_id,
                },
                "locations": [{
                    "logicalLocations": [{
                        "name": f.span_id,
                        "fullyQualifiedName": f"{rep.trace_id}/{f.span_id}",
                        "kind": "span",
                    }]
                }],
            })
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": TOOL_NAME,
                    "version": TOOL_VERSION,
                    "informationUri": "https://github.com/cognis-digital/agentlog",
                    "rules": list(seen.values()),
                }
            },
            "results": results,
        }],
    }


_SEV_COLOR = {
    "critical": "#c0392b",
    "high": "#e67e22",
    "medium": "#d4a017",
    "low": "#2980b9",
    "info": "#7f8c8d",
}


def to_html(reports: List[AuditReport]) -> str:
    """Render a self-contained, shareable HTML report (no external assets)."""
    def esc(x: Any) -> str:
        return html.escape(str(x))

    total_findings = sum(len(r.findings) for r in reports)
    failing = any(r.exit_failing() for r in reports)
    rows: List[str] = []
    for rep in reports:
        m = rep.metrics
        rows.append(
            f"<h2>trace <code>{esc(rep.trace_id)}</code></h2>"
            f"<p class='metrics'>spans={m['spans']} &middot; llm={m['llm_calls']} "
            f"&middot; tools={m['tool_calls']} &middot; errors={m['errors']} "
            f"&middot; tokens={m['total_tokens']} "
            f"(in {m['input_tokens']} / out {m['output_tokens']})</p>"
        )
        if not rep.findings:
            rows.append("<p class='clean'>No findings.</p>")
            continue
        rows.append("<table><thead><tr><th>Severity</th><th>Code</th>"
                    "<th>Span</th><th>Message</th></tr></thead><tbody>")
        for f in rep.findings:
            color = _SEV_COLOR.get(f.severity, "#555")
            rows.append(
                f"<tr><td><span class='sev' style='background:{color}'>"
                f"{esc(f.severity.upper())}</span></td>"
                f"<td><code>{esc(f.code)}</code></td>"
                f"<td><code>{esc(f.span_id)}</code></td>"
                f"<td>{esc(f.message)}</td></tr>"
            )
        rows.append("</tbody></table>")

    banner = ("FAIL — critical/high findings present" if failing
              else "PASS — no blocking findings")
    banner_color = "#c0392b" if failing else "#27ae60"
    body = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TOOL_NAME} audit report</title>
<style>
 body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1a1a1a}}
 h1{{margin:0 0 .25rem}} code{{font-family:ui-monospace,monospace}}
 .banner{{padding:.6rem 1rem;border-radius:6px;color:#fff;font-weight:600;background:{banner_color};display:inline-block}}
 .metrics{{color:#555}} .clean{{color:#27ae60}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem;overflow-x:auto;display:block}}
 th,td{{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #eee;vertical-align:top}}
 .sev{{color:#fff;padding:.1rem .5rem;border-radius:4px;font-size:.8rem;font-weight:600}}
</style></head><body>
<h1>{TOOL_NAME} &mdash; agent trace audit</h1>
<p>{len(reports)} trace(s) &middot; {total_findings} finding(s)</p>
<p class="banner">{esc(banner)}</p>
{body}
</body></html>"""


__all__ = [
    "TOOL_NAME", "TOOL_VERSION",
    "Span", "Trace", "Finding", "AuditReport",
    "load_spans", "build_traces", "replay_trace",
    "audit_trace", "summarize", "scan", "to_sarif", "to_html",
]
