# AGENTLOG — Agentic workflow replay & audit with OTel GenAI semantic conventions

> Part of the **[Cognis Neural Suite](https://github.com/cognis-digital)** by [Cognis Digital](https://cognis.digital)
> Cognis Open Collaboration License (COCL) v1.0 · domain: `ai-security`

[![PyPI](https://img.shields.io/pypi/v/cognis-agentlog.svg)](https://pypi.org/project/cognis-agentlog/)
[![CI](https://github.com/cognis-digital/agentlog/actions/workflows/ci.yml/badge.svg)](https://github.com/cognis-digital/agentlog/actions)
[![Ports](https://github.com/cognis-digital/agentlog/actions/workflows/ports.yml/badge.svg)](https://github.com/cognis-digital/agentlog/actions/workflows/ports.yml)
[![License: COCL 1.0](https://img.shields.io/badge/License-COCL%201.0-2b6cb0.svg)](LICENSE)
[![Suite](https://img.shields.io/badge/Cognis-Neural%20Suite-6b46c1.svg)](https://github.com/cognis-digital)

**Replay any agent run, then audit it for security, cost, and correctness — straight from the OpenTelemetry GenAI spans your agent already emits.**

Modern agents call models, invoke tools, and loop — and the only durable record of what they actually *did* is their telemetry. `agentlog` reads that telemetry (OTel GenAI-semantic-convention spans), rebuilds the execution tree exactly as it ran, and flags leaked secrets, dangerous tool calls, prompt-injection in tool outputs, broken traces, token over-spend, and runaway loops. It is a single-file, dependency-free Python package that runs anywhere Python 3.10+ does — including fully **offline / air-gapped**.

---

## Install

```bash
pip install cognis-agentlog
# or, from this repo:
git clone https://github.com/cognis-digital/agentlog
cd agentlog && pip install -e ".[dev]"
```

The core has **zero runtime dependencies** (Python standard library only). Optional extras:
`[connect]` (forward findings via cognis-connect), `[mcp]` (MCP server), `[web]` (HTTP service), `[dev]` (pytest).

## What it ingests

A span is one unit of agentic work. `agentlog` reads spans from:

- a **JSON array** of span objects,
- an **object** with a top-level `"spans": [...]` array,
- or **JSONL** (one span object per line),

from a file path or `-` (stdin). It understands both the plain `{"key": value}` attribute form and the
OTel KeyValue list form (`[{"key": ..., "value": {"stringValue": ...}}]`), and both `snake_case`
(`span_id`, `parent_span_id`, `start_ns`) and `camelCase` (`spanId`, `parentSpanId`, `startTimeUnixNano`) keys.

Recognised [OTel GenAI attributes](https://opentelemetry.io/docs/specs/semconv/gen-ai/) include
`gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.response.model`,
`gen_ai.usage.input_tokens` / `output_tokens`, `gen_ai.tool.name`, and `gen_ai.tool.call.arguments`.

## Commands

| Command | What it does |
|---|---|
| `agentlog replay <file>` | Reconstruct and print the agent execution tree (depth-ordered causal flow). |
| `agentlog audit <file>` | Security / cost / correctness findings. Exits non-zero on blocking findings. |
| `agentlog summary <file>` | Per-trace rollup (spans, tokens, errors, finding count). |
| `agentlog scan <path>` | Audit a single file **or a whole directory** of span files in one shot. |

Global flags: `--version`, `--format {table,json,sarif,html}` (precedes the subcommand).

## Quickstart — worked example

The repo ships demo traces. Replay one to see the causal tree:

```console
$ agentlog replay demos/01-basic/spans.json
trace trace-research-001  (6 steps)
  - #1   invoke_agent  [8200.0ms, 0tok]
  - #2     chat claude-opus-4  [2200.0ms, 2160tok]
  - #3       execute_tool web_search  [700.0ms, 0tok]
  - #4       execute_tool fetch_url  [700.0ms, 0tok]
  - #5     chat claude-opus-4  [1800.0ms, 2580tok]
  x #6       execute_tool send_email  [500.0ms, 0tok]
```

Now audit the same run:

```console
$ agentlog audit demos/01-basic/spans.json
trace trace-research-001
  spans=6 llm=2 tools=3 errors=1 tokens=4740 (in 4220 / out 520)
  models: claude-opus-4x2
  findings (6):
    [CRITICAL] secret_leak      c2: possible email exposed in span attributes
    [CRITICAL] secret_leak      c3: possible aws_access_key exposed in span attributes
    [CRITICAL] secret_leak      c3: possible email exposed in span attributes
    [HIGH    ] dangerous_tool   c3: high-blast-radius tool 'send_email' invoked
    [HIGH    ] prompt_injection c2: prompt-injection marker 'ignore previous instructions' found in span content
    [HIGH    ] span_error       c3: span 'execute_tool send_email' ended with status ERROR

result: FAIL
```

`audit` exits **1** when any blocking finding is present (default floor: `high`; tune with `--fail-on`),
**0** when clean, and **2** on bad input — so it drops straight into a CI gate.

Read spans from stdin with `-`:

```bash
my-agent --emit-otel | agentlog audit -
```

## Detections

| Code | Severity | Trigger |
|---|---|---|
| `secret_leak` | critical | AWS access key, PEM private key, bearer token, `api_key=…`/`password=…`, or email address in span attributes |
| `dangerous_tool` | high | High-blast-radius tool invoked (`shell`, `exec`, `rm`, `delete_file`, `http_request`, `send_email`, `execute_sql`, `kubectl`, `terraform_apply`, …) |
| `prompt_injection` | high | Injection markers in tool output / messages (`ignore previous instructions`, `system prompt`, `exfiltrate`, …) |
| `span_error` | high | A span ended in an error status |
| `broken_trace` | medium | A span references a `parent_span_id` that is absent (broken audit trail) |
| `token_budget` | medium | Trace exceeded `--max-tokens` (default 100,000) |
| `runaway_loop` | medium | The same tool was invoked ≥ 10 times (possible loop) |

All detection is **read-only pattern analysis over local telemetry** — `agentlog` never calls a model, never
executes a tool, and never touches the network.

## Output formats

- **`table`** (default) — human-readable terminal summary.
- **`json`** — machine-readable metrics + findings for pipelines.
- **`sarif`** — [SARIF 2.1.0](https://sarifweb.azurewebsites.net/), drops directly into GitHub code-scanning
  and IDE problem panes. Each finding code becomes a SARIF rule; each finding's `logicalLocation` is the span id.
- **`html`** — a self-contained, shareable report (no external assets) with a pass/fail banner and severity rollups.

```bash
agentlog --format sarif audit demos/01-basic/spans.json --out agentlog.sarif
agentlog --format html  scan  demos/                     --out report.html
```

## CI gate

```yaml
- run: pip install cognis-agentlog
- run: my-agent --emit-otel > traces/run.jsonl
- run: agentlog --format sarif audit traces/run.jsonl --out agentlog.sarif --fail-on high
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: agentlog.sarif
```

## Edge / air-gap

`agentlog`'s core is pure Python standard library with **no network calls and no external data files** — there
is nothing to fetch and nothing to refresh. Copy the package (or a single language port, below) onto an isolated
host and it runs identically. Telemetry stays local; findings stay local.

## Polyglot ports

The security `audit` core is re-implemented in four more languages under [`ports/`](ports/), so you can run the
same checks with a single static binary and no Python runtime. All ports share the input formats, finding codes,
JSON output shape, and exit codes, and every one is built + tested in CI ([`ports.yml`](.github/workflows/ports.yml)):

| Language | Run | Test |
|---|---|---|
| Go | `cd ports/go && go run . ../../demos/01-basic/spans.json` | `go test ./...` |
| Rust | `cd ports/rust && cargo run -- ../../demos/01-basic/spans.json` | `cargo test` |
| JavaScript / Node | `node ports/javascript/index.js demos/01-basic/spans.json` | `node ports/javascript/test.js` |
| Shell (POSIX + jq) | `sh ports/shell/agentlog.sh demos/01-basic/spans.json` | `sh ports/shell/test.sh` |

## Built-in demo scenarios

Each scenario folder includes a `SCENARIO.md` describing the situation and the findings to expect.

- [`demos/01-basic/`](demos/01-basic/SCENARIO.md) — research agent that leaks a key and trips a prompt-injection.
- [`demos/01-langchain-trace/`](demos/01-langchain-trace/SCENARIO.md)
- [`demos/02-autogen-workflow/`](demos/02-autogen-workflow/SCENARIO.md)
- [`demos/03-pii-leak-incident/`](demos/03-pii-leak-incident/SCENARIO.md)

## Use as a library

```python
from agentlog import load_spans, build_traces, audit_trace, to_sarif

with open("traces/run.jsonl", encoding="utf-8") as fh:
    traces = build_traces(load_spans(fh.read()))
reports = [audit_trace(t) for t in traces]
sarif = to_sarif(reports)            # SARIF 2.1.0 dict, ready to json.dump
failing = any(r.exit_failing() for r in reports)
```

## How it fits the Cognis Neural Suite

`agentlog` is one tool in the [Cognis Neural Suite](https://github.com/cognis-digital). Every tool ships an MCP
server, so [Cognis.Studio](https://cognis.studio) agents can call them as scoped capabilities.

**Sibling tools in `ai-security`:** [`aegis`](https://github.com/cognis-digital/aegis), [`promptmirror`](https://github.com/cognis-digital/promptmirror), [`ledgermind`](https://github.com/cognis-digital/ledgermind), [`adversa`](https://github.com/cognis-digital/adversa), [`guardpost`](https://github.com/cognis-digital/guardpost), [`hallumark`](https://github.com/cognis-digital/hallumark), [`aicard`](https://github.com/cognis-digital/aicard), [`biascope`](https://github.com/cognis-digital/biascope), [`mcpharden`](https://github.com/cognis-digital/mcpharden), [`ragshield`](https://github.com/cognis-digital/ragshield)

## Architecture & roadmap

- Design notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Planned work: [`ROADMAP.md`](ROADMAP.md)

## Interoperability

`agentlog` composes with the Cognis suite — JSON in/out and a shared OpenAI-compatible `/v1` backbone.
See **[INTEROP.md](INTEROP.md)** for the suite map, composition patterns, and reference stacks.

## Integrations

Forward `agentlog`'s findings to STIX/MISP/Sigma/Splunk/Elastic/Slack/webhooks via
[`cognis-connect`](https://github.com/cognis-digital/cognis-connect). See **[INTEGRATIONS.md](INTEGRATIONS.md)**.

```bash
agentlog --format json audit traces/run.jsonl | agentlog-emit --to sigma
```

## Contributing

PRs, new detections, demo scenarios, and language ports are welcome under the collaboration-pull model.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

Source-available under the **Cognis Open Collaboration License (COCL) v1.0** — free for personal,
internal-evaluation, research, and educational use; **commercial / production use requires a license**
(licensing@cognis.digital). See [LICENSE](LICENSE).

## Responsible use

This is dual-use security software. `agentlog` is **passive and offline** — it analyses telemetry you already
have and never probes a live system. Use it only against systems, data, and identities you own or are explicitly
authorized in writing to assess, and in compliance with applicable law.

## About

**[Cognis Digital](https://cognis.digital)** — Wyoming, USA · *Making Tomorrow Better Today: Advanced
Cybersecurity, AI Innovation, and Blockchain Expertise.*
