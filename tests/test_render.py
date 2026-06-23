"""Tests for the SARIF and HTML renderers."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agentlog.core import (  # noqa: E402
    load_spans, build_traces, audit_trace, to_sarif, to_html,
)

HERE = os.path.dirname(__file__)
DEMO = os.path.join(HERE, "..", "demos", "01-basic", "spans.json")


def reports():
    with open(DEMO, encoding="utf-8") as fh:
        traces = build_traces(load_spans(fh.read()))
    return [audit_trace(t) for t in traces]


class TestSarif(unittest.TestCase):
    def setUp(self):
        self.sarif = to_sarif(reports())

    def test_version(self):
        self.assertEqual(self.sarif["version"], "2.1.0")

    def test_has_schema(self):
        self.assertIn("$schema", self.sarif)

    def test_single_run(self):
        self.assertEqual(len(self.sarif["runs"]), 1)

    def test_driver_name(self):
        self.assertEqual(self.sarif["runs"][0]["tool"]["driver"]["name"], "agentlog")

    def test_driver_has_version(self):
        self.assertTrue(self.sarif["runs"][0]["tool"]["driver"]["version"])

    def test_rules_present(self):
        rules = self.sarif["runs"][0]["tool"]["driver"]["rules"]
        self.assertTrue(rules)
        for r in rules:
            self.assertIn("id", r)
            self.assertIn("shortDescription", r)

    def test_results_present(self):
        results = self.sarif["runs"][0]["results"]
        self.assertTrue(results)

    def test_result_levels_valid(self):
        for res in self.sarif["runs"][0]["results"]:
            self.assertIn(res["level"], ("error", "warning", "note"))

    def test_result_has_logical_location(self):
        res = self.sarif["runs"][0]["results"][0]
        loc = res["locations"][0]["logicalLocations"][0]
        self.assertIn("name", loc)
        self.assertEqual(loc["kind"], "span")

    def test_critical_maps_to_error(self):
        res = self.sarif["runs"][0]["results"]
        crit = [r for r in res if r["properties"]["severity"] == "critical"]
        self.assertTrue(crit)
        self.assertTrue(all(r["level"] == "error" for r in crit))

    def test_json_serializable(self):
        # must round-trip through JSON without errors
        s = json.dumps(self.sarif)
        self.assertIn("agentlog", s)

    def test_empty_reports(self):
        empty = to_sarif([])
        self.assertEqual(empty["runs"][0]["results"], [])
        self.assertEqual(empty["runs"][0]["tool"]["driver"]["rules"], [])


class TestHtml(unittest.TestCase):
    def setUp(self):
        self.html = to_html(reports())

    def test_doctype(self):
        self.assertTrue(self.html.lstrip().lower().startswith("<!doctype html>"))

    def test_has_title(self):
        self.assertIn("<title>agentlog", self.html)

    def test_self_contained_no_external(self):
        # No external resource references allowed.
        for bad in ("http://", "https://cdn", "src=\"http"):
            self.assertNotIn(bad, self.html)

    def test_shows_fail_banner(self):
        self.assertIn("FAIL", self.html)

    def test_mentions_finding_codes(self):
        self.assertIn("secret_leak", self.html)

    def test_html_escaped(self):
        # The injected text contains '<' style markers? At minimum ensure
        # angle brackets from data don't break structure: we escape via html.escape.
        self.assertNotIn("<script", self.html.lower())

    def test_clean_report_shows_pass(self):
        raw = json.dumps([{"span_id": "k", "name": "chat", "status": "OK",
                           "attributes": {"gen_ai.operation.name": "chat"}}])
        rep = audit_trace(build_traces(load_spans(raw))[0])
        html = to_html([rep])
        self.assertIn("PASS", html)
        self.assertIn("No findings", html)


if __name__ == "__main__":
    unittest.main()
