#!/usr/bin/env node
// JavaScript / Node port of the agentlog audit core — same rules, same shape
// as the reference Python CLI's `audit` subcommand. Zero runtime deps.
//
//   node index.js ../../demos/01-basic/spans.json
//   cat spans.json | node index.js -
//
// Offline only: reads local files / stdin, never the network.
import { readFileSync } from "fs";
import { pathToFileURL } from "url";

const VERSION = "1.2.5";

const SECRET_PATTERNS = [
  ["aws_access_key", /AKIA[0-9A-Z]{16}/],
  ["private_key", /-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/],
  ["bearer_token", /bearer\s+[A-Za-z0-9._\-]{20,}/i],
  ["api_key_assign", /(?:api[_-]?key|secret|password)\s*[=:]\s*[A-Za-z0-9._\-]{8,}/i],
  ["email", /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/],
];

const DANGEROUS_TOOLS = new Set([
  "shell", "bash", "exec", "execute_command", "run_command", "delete_file", "rm",
  "write_file", "http_request", "send_email", "transfer_funds", "execute_sql", "sql",
  "kubectl", "terraform_apply",
]);

const INJECTION_MARKERS = [
  "ignore previous instructions", "ignore all previous", "disregard the above",
  "system prompt", "you are now", "new instructions:", "reveal your", "exfiltrate",
];

const SEV_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

function attrStr(span, key) {
  const a = span.attributes || {};
  const v = a[key];
  return typeof v === "string" ? v : "";
}

function textBlob(span) {
  const a = span.attributes || {};
  const parts = [];
  for (const k of Object.keys(a).sort()) {
    const v = a[k];
    if (typeof v === "string") parts.push(v);
    else if (v && typeof v === "object") parts.push(JSON.stringify(v));
  }
  return parts.join("\n");
}

function isError(status) {
  const s = String(status || "").toUpperCase();
  return !["OK", "UNSET", "", "0", "1"].includes(s);
}

export function loadSpans(text) {
  const t = (text || "").trim();
  if (!t) throw new Error("empty input: no spans to load");
  let objs = [];
  try {
    const doc = JSON.parse(t);
    if (Array.isArray(doc)) objs = doc.filter((o) => o && typeof o === "object");
    else if (doc && Array.isArray(doc.spans)) objs = doc.spans;
    else if (doc && typeof doc === "object") objs = [doc];
  } catch {
    for (const line of t.split("\n")) {
      const l = line.trim();
      if (l) objs.push(JSON.parse(l));
    }
  }
  if (!objs.length) throw new Error("no span objects found in input");
  for (const o of objs) {
    if (!(o.span_id || o.spanId || o.id))
      throw new Error("span missing a span_id");
  }
  return objs;
}

export function audit(spans) {
  const findings = [];
  const ids = new Set(spans.map((s) => s.span_id || s.spanId || s.id));
  const metrics = { spans: spans.length, llm_calls: 0, tool_calls: 0, errors: 0 };
  const toolFreq = {};

  for (const s of spans) {
    const sid = s.span_id || s.spanId || s.id || "";
    const op = attrStr(s, "gen_ai.operation.name");
    const tool = attrStr(s, "gen_ai.tool.name");
    if (op === "execute_tool" || tool) metrics.tool_calls++;
    if (["chat", "text_completion", "generate_content"].includes(op)) metrics.llm_calls++;

    if (isError(s.status)) {
      metrics.errors++;
      findings.push({ severity: "high", code: "span_error", span_id: sid,
        message: `span '${s.name || ""}' ended with status ${s.status}` });
    }
    const parent = s.parent_span_id || s.parentSpanId;
    if (parent && !ids.has(parent)) {
      findings.push({ severity: "medium", code: "broken_trace", span_id: sid,
        message: `parent_span_id '${parent}' not present in trace` });
    }
    const blob = textBlob(s);
    for (const [label, re] of SECRET_PATTERNS) {
      if (re.test(blob))
        findings.push({ severity: "critical", code: "secret_leak", span_id: sid,
          message: `possible ${label} exposed in span attributes` });
    }
    if (tool && DANGEROUS_TOOLS.has(tool.toLowerCase())) {
      findings.push({ severity: "high", code: "dangerous_tool", span_id: sid,
        message: `high-blast-radius tool '${tool}' invoked` });
    }
    const low = blob.toLowerCase();
    for (const m of INJECTION_MARKERS) {
      if (low.includes(m)) {
        findings.push({ severity: "high", code: "prompt_injection", span_id: sid,
          message: `prompt-injection marker '${m}' found in span content` });
        break;
      }
    }
    if (tool) toolFreq[tool] = (toolFreq[tool] || 0) + 1;
  }
  for (const [name, cnt] of Object.entries(toolFreq)) {
    if (cnt >= 10)
      findings.push({ severity: "medium", code: "runaway_loop", span_id: "",
        message: `tool '${name}' called ${cnt} times (possible loop)` });
  }
  findings.sort((a, b) =>
    (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9) ||
    a.code.localeCompare(b.code));
  return { findings, metrics };
}

export function auditText(text) {
  const spans = loadSpans(text);
  const { findings, metrics } = audit(spans);
  const failing = findings.some((f) => f.severity === "critical" || f.severity === "high");
  return { tool: "agentlog", version: VERSION, metrics, findings, failing };
}

// Cross-platform "run as script" check (Windows file URLs differ from POSIX).
const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  const src = process.argv[2] || "-";
  let text;
  try {
    text = src === "-" ? readFileSync(0, "utf8") : readFileSync(src, "utf8");
  } catch (e) {
    console.error("error:", e.message);
    process.exit(2);
  }
  let result;
  try {
    result = auditText(text);
  } catch (e) {
    console.error("error:", e.message);
    process.exit(2);
  }
  console.log(JSON.stringify(result, null, 2));
  process.exit(result.failing ? 1 : 0);
}
