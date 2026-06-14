"""Tests for hardened error handling and edge-case paths.

Covers:
- malformed JSONL input -> ValueError (not raw JSONDecodeError)
- non-dict JSONL line -> ValueError
- permission denied -> exit 2 (clean stderr message)
- directory path given as file -> exit 2
- --max-tokens <= 0 -> exit 2
- empty JSON array -> ValueError (no spans)
- single-span trace (no children) replay
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agentlog.core import load_spans, build_traces, replay_trace  # noqa: E402
from agentlog.cli import main  # noqa: E402


class TestLoadSpansEdgeCases(unittest.TestCase):
    """Edge-case and bad-input handling in load_spans / JSONL path."""

    def test_malformed_jsonl_raises_value_error(self):
        """A corrupted line mid-JSONL must raise ValueError,
        not a raw JSONDecodeError."""
        jsonl = (
            '{"span_id": "a", "name": "x"}\n'
            "{NOT VALID JSON}\n"
            '{"span_id": "b", "name": "y"}'
        )
        with self.assertRaises(ValueError) as ctx:
            load_spans(jsonl)
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_non_dict_jsonl_line_raises_value_error(self):
        """A JSON-valid but non-object line (e.g., a bare number)
        must raise ValueError."""
        jsonl = '{"span_id": "a", "name": "x"}\n42'
        with self.assertRaises(ValueError) as ctx:
            load_spans(jsonl)
        self.assertIn("JSON object", str(ctx.exception))

    def test_empty_json_array_raises_value_error(self):
        """An empty JSON array [] has no spans and must raise ValueError."""
        with self.assertRaises(ValueError):
            load_spans("[]")

    def test_json_null_raises_value_error(self):
        """JSON null is not a valid span source."""
        with self.assertRaises(ValueError):
            load_spans("null")

    def test_json_string_raises_value_error(self):
        """A bare JSON string is not a valid span source."""
        with self.assertRaises(ValueError):
            load_spans('"hello"')

    def test_only_blank_jsonl_lines_raises(self):
        """JSONL consisting only of blank lines yields no spans."""
        with self.assertRaises(ValueError):
            load_spans("\n\n   \n")


class TestCliHardening(unittest.TestCase):
    """CLI must exit 2 with a clear stderr message on expected error conditions."""

    def _run(self, argv):
        """Run main(), capturing stderr and return (exit_code, stderr_text)."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_missing_file_returns_exit_2(self):
        rc, err = self._run(["audit", "no-such-file-xyz.json"])
        self.assertEqual(rc, 2)
        self.assertIn("error", err.lower())

    def test_directory_as_file_returns_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            rc, err = self._run(["audit", d])
        self.assertEqual(rc, 2)
        self.assertIn("error", err.lower())

    def test_permission_denied_returns_exit_2(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"[]")
            fpath = f.name
        try:
            os.chmod(fpath, 0)  # remove all permissions
            rc, err = self._run(["audit", fpath])
            self.assertEqual(rc, 2)
            self.assertIn("error", err.lower())
        except PermissionError:
            # On some systems even root can't be blocked; skip gracefully.
            pass
        finally:
            os.chmod(fpath, stat.S_IRUSR | stat.S_IWUSR)
            os.unlink(fpath)

    def test_max_tokens_zero_returns_exit_2(self):
        import os as _os
        demo = _os.path.join(
            _os.path.dirname(__file__), "..", "demos", "01-basic", "spans.json"
        )
        rc, err = self._run(["audit", demo, "--max-tokens", "0"])
        self.assertEqual(rc, 2)
        self.assertIn("max-tokens", err.lower())

    def test_max_tokens_negative_returns_exit_2(self):
        import os as _os
        demo = _os.path.join(
            _os.path.dirname(__file__), "..", "demos", "01-basic", "spans.json"
        )
        rc, err = self._run(["audit", demo, "--max-tokens", "-1"])
        self.assertEqual(rc, 2)
        self.assertIn("max-tokens", err.lower())

    def test_malformed_json_file_returns_exit_2(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write("{GARBAGE}")
            fpath = f.name
        try:
            rc, err = self._run(["audit", fpath])
            self.assertEqual(rc, 2)
            self.assertIn("error", err.lower())
        finally:
            os.unlink(fpath)


class TestReplayEdgeCases(unittest.TestCase):
    """replay_trace on unusual but valid inputs."""

    def test_single_root_span_no_children(self):
        """A trace with one span and no children replays as a single step."""
        spans = load_spans(
            '[{"span_id": "only", "trace_id": "t", "name": "root"}]'
        )
        trace = build_traces(spans)[0]
        steps = replay_trace(trace)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["depth"], 0)
        self.assertEqual(steps[0]["span_id"], "only")

    def test_multiple_traces_isolated(self):
        """Spans from different trace_ids form separate traces."""
        raw = json.dumps([
            {"span_id": "a", "trace_id": "t1", "name": "root-a"},
            {"span_id": "b", "trace_id": "t2", "name": "root-b"},
        ])
        traces = build_traces(load_spans(raw))
        self.assertEqual(len(traces), 2)
        ids = {t.trace_id for t in traces}
        self.assertEqual(ids, {"t1", "t2"})


if __name__ == "__main__":
    unittest.main()
