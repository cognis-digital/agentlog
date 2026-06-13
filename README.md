# AGENTLOG — Agentic workflow replay & audit with OTel GenAI semantic conventions

> Part of the **[Cognis Neural Suite](https://github.com/cognis-digital)** by [Cognis Digital](https://cognis.digital)
> Cognis Open Collaboration License (COCL) v1.0 · domain: `ai-security`

[![PyPI](https://img.shields.io/pypi/v/cognis-agentlog.svg)](https://pypi.org/project/cognis-agentlog/)
[![CI](https://github.com/cognis-digital/agentlog/actions/workflows/ci.yml/badge.svg)](https://github.com/cognis-digital/agentlog/actions)
[![License: COCL 1.0](https://img.shields.io/badge/License-COCL%201.0-2b6cb0.svg)](LICENSE)
[![Suite](https://img.shields.io/badge/Cognis-Neural%20Suite-6b46c1.svg)](https://github.com/cognis-digital)

**Agentic workflow replay & audit with OTel GenAI semantic conventions.**

*AI Security & Governance — securing LLMs, agents, and the MCP supply chain.*

## Usage — step by step

1. **Install** the tool:
   ```bash
   pip install cognis-agentlog
   ```
2. **Replay an agent run** from a file of OTel GenAI spans (JSON or JSONL) to reconstruct the execution tree:
   ```bash
   agentlog replay demos/trace.jsonl
   ```
   Pass `-` instead of a path to read spans from stdin.
3. **Audit the run** for security, cost, and correctness issues. `--max-tokens` sets the budget that flags over-spend:
   ```bash
   agentlog audit demos/trace.jsonl --max-tokens 100000
   ```
4. **Read the output** as machine-readable JSON (the global `--format` flag precedes the subcommand), or get a per-trace rollup:
   ```bash
   agentlog --format json audit demos/trace.jsonl
   agentlog summary demos/trace.jsonl
   ```
5. **Automate in CI** — export your agent's OTel spans, then audit them on every run:
   ```yaml
   - run: pip install cognis-agentlog
   - run: agentlog --format json audit traces/run.jsonl > agentlog.json
   ```

## Why

Security and intelligence teams need agentic workflow replay & audit with OTel GenAI semantic conventions without standing up heavyweight infrastructure. `agentlog` is single-purpose, scriptable, CI-friendly, and self-hostable: point it at a target, get prioritized findings in the format your workflow already speaks (table, JSON, SARIF, HTML), and wire it into agents over MCP when you want it autonomous.

## Install

```bash
pip install cognis-agentlog
# or, from this repo:
pip install -e ".[dev]"
```

## Quick start

```bash
agentlog --version
agentlog scan demos/                      # run against the bundled demo
agentlog scan demos/ --format sarif --out r.sarif --fail-on high
agentlog scan demos/ --format html --out report.html
agentlog mcp                              # expose as an MCP server (Cognis.Studio / Claude Desktop / Cursor)
```

## Built-in demo scenarios

Each scenario folder includes a `SCENARIO.md` describing the situation and the findings to expect.

- [`demos/01-basic/`](demos/01-basic/SCENARIO.md)
- [`demos/01-langchain-trace/`](demos/01-langchain-trace/SCENARIO.md)
- [`demos/02-autogen-workflow/`](demos/02-autogen-workflow/SCENARIO.md)
- [`demos/03-pii-leak-incident/`](demos/03-pii-leak-incident/SCENARIO.md)

## Output formats

- **Table** (default) — human-readable terminal summary
- **JSON** — machine-readable findings for pipelines
- **SARIF** — drops into GitHub code-scanning / IDE problem panes
- **HTML** — shareable report with severity rollups

## How it fits the Cognis Neural Suite

`agentlog` is one of **52 tools** in the [Cognis Neural Suite](https://github.com/cognis-digital). Every tool ships an MCP server, so [Cognis.Studio](https://cognis.studio) agents can call them as scoped capabilities.

**Sibling tools in `ai-security`:** [`aegis`](https://github.com/cognis-digital/aegis), [`promptmirror`](https://github.com/cognis-digital/promptmirror), [`ledgermind`](https://github.com/cognis-digital/ledgermind), [`adversa`](https://github.com/cognis-digital/adversa), [`guardpost`](https://github.com/cognis-digital/guardpost), [`hallumark`](https://github.com/cognis-digital/hallumark), [`aicard`](https://github.com/cognis-digital/aicard), [`biascope`](https://github.com/cognis-digital/biascope), [`mcpharden`](https://github.com/cognis-digital/mcpharden), [`ragshield`](https://github.com/cognis-digital/ragshield)

## Architecture & roadmap

- Design notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Planned work: [`ROADMAP.md`](ROADMAP.md)

## Contributing

PRs, new detections, and demo scenarios are welcome under the collaboration-pull model. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Interoperability

`{}` composes with the 300+ tool Cognis suite — JSON in/out and a shared
OpenAI-compatible `/v1` backbone. See **[INTEROP.md](INTEROP.md)** for the
suite map, composition patterns, and reference stacks.

## License

Source-available under the **Cognis Open Collaboration License (COCL) v1.0** — free for personal, internal-evaluation, research, and educational use; **commercial / production use requires a license** (licensing@cognis.digital). See [LICENSE](LICENSE).

## Responsible use

This is dual-use security software. Use it only against systems, data, and identities you own or are explicitly authorized in writing to test, and in compliance with applicable law.

## About

**[Cognis Digital](https://cognis.digital)** — Wyoming, USA · *Making Tomorrow Better Today: Advanced Cybersecurity, AI Innovation, and Blockchain Expertise.*
