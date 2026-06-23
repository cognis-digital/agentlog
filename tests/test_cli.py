"""Tests for the agentlog CLI surface (replay / audit / summary / scan)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agentlog.cli import main, build_parser  # noqa: E402

HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(HERE, "..", "demos", "01-basic")
DEMO = os.path.join(DEMO_DIR, "spans.json")


def run(argv, feed_stdin=None):
    out, err = io.StringIO(), io.StringIO()
    old_in = sys.stdin
    if feed_stdin is not None:
        sys.stdin = io.StringIO(feed_stdin)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    finally:
        sys.stdin = old_in
    return rc, out.getvalue(), err.getvalue()


class TestVersion(unittest.TestCase):
    def test_version_flag(self):
        with self.assertRaises(SystemExit):
            run(["--version"])


class TestReplayCli(unittest.TestCase):
    def test_table_ok(self):
        rc, out, _ = run(["replay", DEMO])
        self.assertEqual(rc, 0)
        self.assertIn("trace-research-001", out)

    def test_json(self):
        rc, out, _ = run(["--format", "json", "replay", DEMO])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("traces", data)
        self.assertTrue(data["traces"][0]["steps"])

    def test_stdin(self):
        with open(DEMO, encoding="utf-8") as fh:
            payload = fh.read()
        rc, out, _ = run(["replay", "-"], feed_stdin=payload)
        self.assertEqual(rc, 0)


class TestAuditCli(unittest.TestCase):
    def test_table_fail(self):
        rc, out, _ = run(["audit", DEMO])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)

    def test_json_failing(self):
        rc, out, _ = run(["--format", "json", "audit", DEMO])
        self.assertEqual(rc, 1)
        self.assertTrue(json.loads(out)["failing"])

    def test_clean_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clean.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump([{"span_id": "k", "name": "chat", "status": "OK",
                            "attributes": {"gen_ai.operation.name": "chat"}}], fh)
            rc, out, _ = run(["audit", p])
        self.assertEqual(rc, 0)
        self.assertIn("PASS", out)

    def test_fail_on_critical_only(self):
        # A trace whose worst finding is medium should pass under --fail-on critical.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "med.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump([{"span_id": "a", "name": "n",
                            "parent_span_id": "ghost"}], fh)  # broken_trace = medium
            rc, _, _ = run(["audit", p, "--fail-on", "critical"])
        self.assertEqual(rc, 0)

    def test_fail_on_medium_trips(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "med.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump([{"span_id": "a", "name": "n",
                            "parent_span_id": "ghost"}], fh)
            rc, _, _ = run(["audit", p, "--fail-on", "medium"])
        self.assertEqual(rc, 1)

    def test_sarif_output(self):
        rc, out, _ = run(["--format", "sarif", "audit", DEMO])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["version"], "2.1.0")

    def test_html_output(self):
        rc, out, _ = run(["--format", "html", "audit", DEMO])
        self.assertEqual(rc, 1)
        self.assertIn("<!doctype html>", out.lower())

    def test_out_file(self):
        with tempfile.TemporaryDirectory() as d:
            outp = os.path.join(d, "r.sarif")
            rc, _, err = run(["--format", "sarif", "audit", DEMO, "--out", outp])
            self.assertEqual(rc, 1)
            self.assertTrue(os.path.exists(outp))
            with open(outp, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["version"], "2.1.0")
        self.assertIn("wrote", err)

    def test_max_tokens_triggers_budget(self):
        rc, out, _ = run(["--format", "json", "audit", DEMO, "--max-tokens", "10"])
        codes = {f["code"] for r in json.loads(out)["reports"] for f in r["findings"]}
        self.assertIn("token_budget", codes)

    def test_missing_file_exit_two(self):
        rc, _, err = run(["audit", "nope.json"])
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)

    def test_bad_input_exit_two(self):
        rc, _, err = run(["audit", "-"], feed_stdin="   ")
        self.assertEqual(rc, 2)


class TestSummaryCli(unittest.TestCase):
    def test_table(self):
        rc, out, _ = run(["summary", DEMO])
        self.assertEqual(rc, 0)
        self.assertIn("trace", out)

    def test_json(self):
        rc, out, _ = run(["--format", "json", "summary", DEMO])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["trace_count"], 1)


class TestScanCli(unittest.TestCase):
    def test_scan_dir_table(self):
        rc, out, _ = run(["scan", DEMO_DIR])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)

    def test_scan_json(self):
        rc, out, _ = run(["--format", "json", "scan", DEMO_DIR])
        self.assertEqual(rc, 1)
        self.assertTrue(json.loads(out)["failing"])

    def test_scan_sarif(self):
        rc, out, _ = run(["--format", "sarif", "scan", DEMO_DIR])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["version"], "2.1.0")

    def test_scan_html(self):
        rc, out, _ = run(["--format", "html", "scan", DEMO_DIR])
        self.assertEqual(rc, 1)
        self.assertIn("<!doctype html>", out.lower())


class TestParser(unittest.TestCase):
    def test_subcommands_exist(self):
        p = build_parser()
        # parse each subcommand without error
        for cmd in ("replay", "audit", "summary", "scan"):
            ns = p.parse_args([cmd, DEMO]) if cmd != "scan" else p.parse_args([cmd, DEMO_DIR])
            self.assertEqual(ns.command, cmd)

    def test_format_choices(self):
        p = build_parser()
        for fmt in ("table", "json", "sarif", "html"):
            ns = p.parse_args(["--format", fmt, "audit", DEMO])
            self.assertEqual(ns.format, fmt)


if __name__ == "__main__":
    unittest.main()
