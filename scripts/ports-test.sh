#!/usr/bin/env bash
# Build/run + smoke-test every language port against the bundled demo trace.
# Each port mirrors the Python `audit` surface; a port that can't run locally
# is reported as skipped (CI's ports.yml builds them all on Linux runners).
set -u
DEMO="demos/01-basic/spans.json"
fail=0

run() { # label, command...
  printf '== %s ==\n' "$1"
  shift
  if "$@"; then :; else
    # exit 1 is expected (the demo has blocking findings); only >1 is a failure
    rc=$?
    if [ "$rc" -gt 1 ]; then
      echo "  -> FAILED (exit $rc)"; fail=1
    fi
  fi
}

if command -v node >/dev/null 2>&1; then
  node ports/javascript/test.js || fail=1
  run "node" node ports/javascript/index.js "$DEMO"
else
  echo "node: skipped"
fi

if command -v jq >/dev/null 2>&1; then
  sh ports/shell/test.sh || fail=1
  run "shell" sh ports/shell/agentlog.sh "$DEMO"
else
  echo "shell: skipped (needs jq)"
fi

if command -v go >/dev/null 2>&1; then
  ( cd ports/go && go test ./... ) || fail=1
  run "go" sh -c 'cd ports/go && go run . ../../'"$DEMO"
else
  echo "go: skipped"
fi

if command -v cargo >/dev/null 2>&1; then
  ( cd ports/rust && cargo test ) || fail=1
  run "rust" sh -c 'cd ports/rust && cargo run -- ../../'"$DEMO"
else
  echo "rust: skipped"
fi

exit "$fail"
