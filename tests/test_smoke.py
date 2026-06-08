import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agentlog import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    load_spans,
    build_traces,
    replay_trace,
    audit_trace,
    summarize,
)
from agentlog.cli import main  # noqa: E402

DEMO = os.path.join(os.path.dirname(__file__), "..", "demos", "01-basic", "spans.json")

SIMPLE = json.dumps([
    {"span_id": "r", "trace_id": "t1", "name": "agent.run",
     "attributes": {"gen_ai.operation.name": "invoke_agent"}},
    {"span_id": "k", "trace_id": "t1", "parent_span_id": "r", "name": "chat",
     "start_ns": 10, "end_ns": 20, "status": "OK",
     "attributes": {"gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "claude-opus-4",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 50}},
])


class TestIngest(unittest.TestCase):
    def test_meta(self):
        self.assertEqual(TOOL_NAME, "agentlog")
        self.assertTrue(TOOL_VERSION)

    def test_load_array(self):
        spans = load_spans(SIMPLE)
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[1].input_tokens, 100)

    def test_load_jsonl(self):
        jsonl = "\n".join(json.dumps(o) for o in json.loads(SIMPLE))
        self.assertEqual(len(load_spans(jsonl)), 2)

    def test_otel_keyvalue_attrs(self):
        raw = json.dumps([{
            "span_id": "x", "name": "chat",
            "attributes": [
                {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 42}},
            ],
        }])
        s = load_spans(raw)[0]
        self.assertEqual(s.operation, "chat")
        self.assertEqual(s.input_tokens, 42)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            load_spans("   ")

    def test_missing_span_id_raises(self):
        with self.assertRaises(ValueError):
            load_spans('[{"name": "x"}]')


class TestReplay(unittest.TestCase):
    def test_tree_order_and_depth(self):
        trace = build_traces(load_spans(SIMPLE))[0]
        steps = replay_trace(trace)
        self.assertEqual([s["depth"] for s in steps], [0, 1])
        self.assertEqual(steps[0]["span_id"], "r")
        self.assertEqual(steps[1]["tokens"], 150)


class TestAudit(unittest.TestCase):
    def setUp(self):
        with open(DEMO, encoding="utf-8") as fh:
            self.trace = build_traces(load_spans(fh.read()))[0]
        self.report = audit_trace(self.trace)

    def test_metrics(self):
        m = self.report.metrics
        self.assertEqual(m["total_tokens"], 4740)
        self.assertEqual(m["errors"], 1)
        self.assertGreaterEqual(m["tool_calls"], 3)

    def test_detects_secret_leak(self):
        codes = {f.code for f in self.report.findings}
        self.assertIn("secret_leak", codes)

    def test_detects_prompt_injection(self):
        codes = {f.code for f in self.report.findings}
        self.assertIn("prompt_injection", codes)

    def test_detects_dangerous_tool(self):
        codes = {f.code for f in self.report.findings}
        self.assertIn("dangerous_tool", codes)

    def test_exit_failing(self):
        self.assertTrue(self.report.exit_failing())

    def test_clean_trace_passes(self):
        rep = audit_trace(build_traces(load_spans(SIMPLE))[0])
        self.assertFalse(rep.exit_failing())
        self.assertEqual(rep.findings, [])

    def test_summarize(self):
        s = summarize([self.trace])
        self.assertEqual(s["trace_count"], 1)


class TestCli(unittest.TestCase):
    def test_audit_json_exit_nonzero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--format", "json", "audit", DEMO])
        self.assertEqual(rc, 1)
        data = json.loads(buf.getvalue())
        self.assertTrue(data["failing"])

    def test_replay_table_ok(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["replay", DEMO])
        self.assertEqual(rc, 0)
        self.assertIn("trace-research-001", buf.getvalue())

    def test_summary_clean_exit_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--format", "json", "summary", DEMO])
        self.assertEqual(rc, 0)

    def test_missing_file_exit_two(self):
        rc = main(["audit", "does-not-exist.json"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
