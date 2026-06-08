# Scenario: PII propagation through agent workflow

User entered SSN in a chat; agent logged + emailed it without redaction.

## Expected findings

- AL-PII-001 × 3

## Why this matters

Common GDPR/CCPA breach pattern. AGENTLOG catches the propagation chain.
