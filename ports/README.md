# Ports of agentlog

The reference implementation is the Python package in [`../agentlog/`](../agentlog/).
These ports re-implement the **`audit`** surface — the security/correctness core of
agentlog — so you can drop the same checks into any stack or ship a single static
binary with no Python runtime.

Every port:

- accepts the same span input: a JSON **array**, an object with a `"spans"` array,
  or **JSONL** (one span object per line), read from a file argument or `-` (stdin);
- applies the same detections — `secret_leak`, `dangerous_tool`, `prompt_injection`,
  `span_error`, `broken_trace`, `runaway_loop`;
- emits the same JSON shape: `{ "tool", "version", "metrics", "findings", "failing" }`,
  with findings sorted critical-first;
- exits **1** when any critical/high finding is present, **0** otherwise, **2** on bad input;
- is **offline only** — it reads local files / stdin and never touches the network.

| Language | Path | Run | Test |
|---|---|---|---|
| Python (reference) | [`../agentlog/`](../agentlog/) | `agentlog audit demos/01-basic/spans.json` | `python -m pytest` |
| Go | [`go/`](go/) | `cd ports/go && go run . ../../demos/01-basic/spans.json` | `go test ./...` |
| Rust | [`rust/`](rust/) | `cd ports/rust && cargo run -- ../../demos/01-basic/spans.json` | `cargo test` |
| JavaScript / Node | [`javascript/`](javascript/) | `node ports/javascript/index.js demos/01-basic/spans.json` | `node ports/javascript/test.js` |
| Shell (POSIX + jq) | [`shell/`](shell/) | `sh ports/shell/agentlog.sh demos/01-basic/spans.json` | `sh ports/shell/test.sh` |

## Continuous verification

The [`ports.yml`](../.github/workflows/ports.yml) GitHub Actions workflow builds and
tests **every** port on each push that touches `ports/`:

- **Go** — `go vet` + `go test` + a release build that must report a `secret_leak` on the demo;
- **Rust** — `cargo test` (the parser, loader, and audit are unit-tested) + a release-build smoke run;
- **Node** — `node test.js` + a smoke run that must report `prompt_injection`;
- **Shell** — installs `jq`, runs the smoke test, and checks for `dangerous_tool`.

This keeps the ports honest: if a port drifts from the reference behaviour, CI goes red.

## Output shape

```json
{
  "tool": "agentlog",
  "version": "1.2.5",
  "metrics": { "spans": 6, "tool_calls": 3, "llm_calls": 2, "errors": 1 },
  "findings": [
    { "severity": "critical", "code": "secret_leak", "span_id": "c3",
      "message": "possible api_key_assign exposed in span attributes" }
  ],
  "failing": true
}
```

Contributions of additional ports (Ruby, C#, Bun, Deno, WASM) are welcome — see
[../CONTRIBUTING.md](../CONTRIBUTING.md). A new port should match the input formats,
finding codes, JSON shape, and exit codes above, and ship a smoke test wired into CI.
