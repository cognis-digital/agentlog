# Scenario: LangChain agent trace with auth gaps

Multi-step agent run where two tool calls bypass auth.

## Expected findings

- AL-MISS-001 (missing trace_id on step 2)
- AL-SEC-001 × 2 (tool calls without auth check)
- AL-PII-001 (PII in trace step 2)

## Why this matters

OpenTelemetry GenAI semantic conventions require these fields. Without them, IR forensics on agent incidents is impossible.
