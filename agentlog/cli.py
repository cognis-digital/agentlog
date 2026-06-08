"""AGENTLOG command-line interface."""
from cognis_core import build_cli
from agentlog.core import scan, TOOL_NAME, TOOL_VERSION

main = build_cli(
    tool_name=TOOL_NAME,
    tool_version=TOOL_VERSION,
    description="Agentic workflow replay & audit with OTel GenAI compatibility",
    scan_fn=scan,
)

if __name__ == "__main__":
    import sys
    sys.exit(main())
