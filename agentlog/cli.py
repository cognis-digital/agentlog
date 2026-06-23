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
    scan,
    to_sarif,
    to_html,
)


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write_out(args, text: str) -> None:
    """Write rendered output to --out if given, else stdout."""
    out = getattr(args, "out", None)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)


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


_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _fails_threshold(reports, fail_on: str) -> bool:
    """True if any finding meets or exceeds the --fail-on severity floor."""
    floor = _SEV_RANK.get(fail_on, 1)
    return any(_SEV_RANK.get(f.severity, 9) <= floor
               for r in reports for f in r.findings)


def _cmd_audit(args) -> int:
    traces = build_traces(load_spans(_read(args.file)))
    reports = [audit_trace(t, max_tokens=args.max_tokens) for t in traces]
    failing = _fails_threshold(reports, getattr(args, "fail_on", "high"))
    if args.format == "json":
        _write_out(args, json.dumps({
            "reports": [r.as_dict() for r in reports],
            "failing": failing,
        }, indent=2, default=str))
    elif args.format == "sarif":
        _write_out(args, json.dumps(to_sarif(reports), indent=2, default=str))
    elif args.format == "html":
        _write_out(args, to_html(reports))
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


def _cmd_scan(args) -> int:
    """Audit a span file or a directory of span files in one shot."""
    result = scan(args.target, max_tokens=args.max_tokens)
    failing = bool(result["failing"])
    if args.format == "json":
        _write_out(args, json.dumps(result, indent=2, default=str))
    elif args.format in ("sarif", "html"):
        # Re-derive reports for the structured renderers.
        traces = []
        import os as _os
        targets = []
        if _os.path.isdir(args.target):
            for root, _d, files in _os.walk(args.target):
                for fn in sorted(files):
                    if fn.endswith((".json", ".jsonl")):
                        targets.append(_os.path.join(root, fn))
        else:
            targets.append(args.target)
        reports = []
        for p in sorted(targets):
            try:
                spans = load_spans(_read(p))
            except (OSError, ValueError):
                continue
            for t in build_traces(spans):
                reports.append(audit_trace(t, max_tokens=args.max_tokens))
        if args.format == "sarif":
            _write_out(args, json.dumps(to_sarif(reports), indent=2, default=str))
        else:
            _write_out(args, to_html(reports))
    else:
        print(f"scanned {result['files_scanned']} file(s) under {result['target']}")
        print(f"  findings: {result['findings_total']}  "
              f"result: {'FAIL' if failing else 'PASS'}")
        for err in result["errors"]:
            print(f"  skipped {err['file']}: {err['error']}", file=sys.stderr)
    return 1 if failing else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Agentic workflow replay & audit over OTel GenAI spans.",
    )
    p.add_argument("--version", action="version",
                   version=f"{TOOL_NAME} {TOOL_VERSION}")
    p.add_argument("--format", choices=("table", "json", "sarif", "html"),
                   default="table",
                   help="output format (default: table; sarif/html for audit)")
    sub = p.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("replay", help="reconstruct the agent execution tree")
    rp.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    rp.set_defaults(func=_cmd_replay)

    ap = sub.add_parser("audit", help="security/cost/correctness audit")
    ap.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    ap.add_argument("--max-tokens", type=int, default=100_000,
                    help="token budget per trace (default: 100000)")
    ap.add_argument("--fail-on", choices=("critical", "high", "medium", "low", "info"),
                    default="high",
                    help="severity floor that makes the run exit non-zero "
                         "(default: high)")
    ap.add_argument("--out", default=None,
                    help="write json/sarif/html output to this file instead of stdout")
    ap.set_defaults(func=_cmd_audit)

    sp = sub.add_parser("summary", help="per-trace rollup")
    sp.add_argument("file", help="span file (JSON / JSONL), or '-' for stdin")
    sp.set_defaults(func=_cmd_summary)

    scp = sub.add_parser("scan",
                         help="audit a span file or a directory of span files")
    scp.add_argument("target", help="span file or directory of *.json/*.jsonl")
    scp.add_argument("--max-tokens", type=int, default=100_000,
                     help="token budget per trace (default: 100000)")
    scp.add_argument("--out", default=None,
                     help="write json/sarif/html output to this file instead of stdout")
    scp.set_defaults(func=_cmd_scan)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"error: file not found: {e.filename}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
