"""AGENTLOG command-line interface.

Subcommands:
    replay <file>    reconstruct & print the agent execution tree
    audit  <file>    security / cost / correctness findings (exit 1 on critical/high)
    summary <file>   per-trace rollup

Global: --version, --format {table,json}
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import (
    load_spans,
    build_traces,
    replay_trace,
    audit_trace,
    summarize,
)


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _print_replay_table(trace_id: str, steps) -> None:
    print(f"trace {trace_id}  ({len(steps)} steps)")
    for st in steps:
        indent = "  " * st["depth"]
        marker = "x" if st["status"] == "ERROR" else "-"
        detail = f" {st['detail']}" if st["detail"] else ""
        print(f"  {marker} #{st['step']:<3} {indent}{st['operation'] or st['name']}"
              f"{detail}  [{st['duration_ms']}ms, {st['tokens']}tok]")


def _print_audit_table(rep) -> None:
    m = rep.metrics
    print(f"trace {rep.trace_id}")
    print(f"  spans={m['spans']} llm={m['llm_calls']} tools={m['tool_calls']} "
          f"errors={m['errors']} tokens={m['total_tokens']} "
          f"(in {m['input_tokens']} / out {m['output_tokens']})")
    if m["models"]:
        print(f"  models: {', '.join(f'{k}x{v}' for k, v in m['models'].items())}")
    if not rep.findings:
        print("  findings: none")
        return
    print(f"  findings ({len(rep.findings)}):")
    for f in rep.findings:
        print(f"    [{f.severity.upper():<8}] {f.code:<16} {f.span_id}: {f.message}")


def _cmd_replay(args) -> int:
    traces = build_traces(load_spans(_read(args.file)))
    payload = []
    for t in traces:
        steps = replay_trace(t)
        payload.append({"trace_id": t.trace_id, "steps": steps})
        if args.format == "table":
            _print_replay_table(t.trace_id, steps)
    if args.format == "json":
        _emit_json({"traces": payload})
    return 0


def _cmd_audit(args) -> int:
    if args.max_tokens <= 0:
        print("error: --max-tokens must be a positive integer", file=sys.stderr)
        return 2
    traces = build_traces(load_spans(_read(args.file)))
    reports = [audit_trace(t, max_tokens=args.max_tokens) for t in traces]
    failing = any(r.exit_failing() for r in reports)
    if args.format == "json":
        _emit_json({
            "reports": [r.as_dict() for r in reports],
            "failing": failing,
        })
    else:
        for r in reports:
            _print_audit_table(r)
        print(f"\nresult: {'FAIL' if failing else 'PASS'}")
    return 1 if failing else 0


def _cmd_summary(args) -> int:
    traces = build_traces(load_spans(_read(args.file)))
    summary = summarize(traces)
    if args.format == "json":
        _emit_json(summary)
    else:
        print(f"{summary['trace_count']} trace(s)")
        for t in summary["traces"]:
            print(f"  {t['trace_id']}: spans={t['spans']} tokens={t['total_tokens']} "
                  f"errors={t['errors']} findings={t['findings']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Agentic workflow replay & audit over OTel GenAI spans.",
    )
    p.add_argument("--version", action="version",
                   version=f"{TOOL_NAME} {TOOL_VERSION}")
    p.add_argument("--format", choices=("table", "json"), default="table",
                   help="output format (default: table)")
    sub = p.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("replay", help="reconstruct the agent execution tree")
    rp.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    rp.set_defaults(func=_cmd_replay)

    ap = sub.add_parser("audit", help="security/cost/correctness audit")
    ap.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    ap.add_argument("--max-tokens", type=int, default=100_000,
                    help="token budget per trace (default: 100000)")
    ap.set_defaults(func=_cmd_audit)

    sp = sub.add_parser("summary", help="per-trace rollup")
    sp.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    sp.set_defaults(func=_cmd_summary)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"error: file not found: {e.filename}", file=sys.stderr)
        return 2
    except IsADirectoryError as e:
        print(f"error: path is a directory, not a file: {e.filename}", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"error: permission denied: {e.filename}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: cannot read file: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
