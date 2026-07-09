"""
polyglot/python/otel_semantic_parser.py

OTel Semantic Parser - Agentic workflow replay & audit utility.

Parses OpenTelemetry GenAI semantic attributes from raw log strings into
structured Python objects for querying and analysis.

Supports:
- Stringified JSON (most common in logs)
- Native Python types (dict, list, int, float, bool, str)
- Mixed/ambiguous formats with auto-detection
"""

import json
from typing import Any, Dict, List, Optional, Union


class SemanticValueError(Exception):
    """Raised when semantic parsing fails."""
    pass


def _detect_type(value: Any) -> type:
    """Detect the actual Python type of a value."""
    if isinstance(value, (dict, list)):
        return type(value)
    if isinstance(value, str):
        # Check for JSON stringification
        try:
            parsed = json.loads(value)
            return _detect_type(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return str
    if isinstance(value, (int, float, bool)):
        return type(value)
    return type(value)


def _parse_json_string(s: str) -> Any:
    """Parse a string that may be JSON or plain text."""
    s = s.strip()
    if not s.startswith('"') and not s.startswith('['):
        # Not JSON, treat as plain string
        return s
    
    try:
        parsed = json.loads(s)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return s


def _normalize_value(value: Any) -> Any:
    """Normalize a value to its canonical Python representation."""
    if isinstance(value, str):
        # Try JSON parsing first for strings that look like structured data
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, (str, int, float, bool)):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Recursively normalize nested structures
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    
    return value


def parse_semantic_attributes(
    raw_attrs: Union[str, Dict[str, Any]],
    strict_mode: bool = False
) -> Dict[str, Any]:
    """
    Parse OpenTelemetry semantic attributes into structured data.

    Args:
        raw_attrs: Raw attribute string or dict (e.g., from log line)
        strict_mode: If True, raise SemanticValueError on parse failures

    Returns:
        Normalized dictionary of parsed values

    Examples:
        >>> # Stringified JSON
        >>> attrs = '{"model_name": "gpt-4", "response_tokens": 128}'
        >>> result = parse_semantic_attributes(attrs)
        >>> print(result['model_name'])  # 'gpt-4'

        >>> # Mixed types
        >>> attrs = '{"prompt_id": "abc-123", "is_streaming": true, "tokens": [10, 20]}'
        >>> result = parse_semantic_attributes(attrs)
    """
    if isinstance(raw_attrs, dict):
        return _normalize_value(raw_attrs)

    # Try JSON parsing first
    try:
        parsed = json.loads(raw_attrs)
        if not isinstance(parsed, (str, int, float, bool)):
            return _normalize_value(parsed)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback to string
    result = str(raw_attrs).strip()
    
    if strict_mode and len(result) > 0:
        SemanticValueError(f"Failed to parse semantic attributes: {raw_attrs[:100]}")

    return result


def extract_genai_fields(
    parsed_data: Dict[str, Any],
    field_mapping: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Extract GenAI-specific fields from parsed semantic data.

    Standard OTel GenAI attribute names per spec:
    - model_name: The LLM model identifier
    - prompt_id: Unique prompt identifier (UUID, trace ID, etc.)
    - response_tokens: Number of tokens in response
    - input_tokens: Number of tokens in user input
    - total_tokens: Combined token count
    - is_streaming: Boolean flag for streaming responses
    - finish_reason: Why generation stopped

    Args:
        parsed_data: Output from parse_semantic_attributes()
        field_mapping: Optional custom mapping (raw_name -> canonical name)

    Returns:
        Dictionary with extracted and normalized GenAI fields
    """
    # Standard OTel GenAI field mappings
    STANDARD_FIELDS = {
        'model_name': ['model_name', 'llm.model', 'model'],
        'prompt_id': ['prompt_id', 'input.id', 'traceId', 'spanId'],
        'response_tokens': ['response_tokens', 'output.tokens', 'tokens.out'],
        'input_tokens': ['input_tokens', 'input.tokens', 'tokens.in'],
        'total_tokens': ['total_tokens', 'tokens.total'],
        'is_streaming': ['is_streaming', 'streaming', 'mode.stream'],
        'finish_reason': ['finish_reason', 'stop_reason', 'completion.reason'],
    }

    result: Dict[str, Any] = {}

    for canonical_name, aliases in STANDARD_FIELDS.items():
        # Search through all nested levels for matching keys
        found_value = _find_nested_key(parsed_data, aliases)
        
        if found_value is not None:
            result[canonical_name] = found_value

    return result


def _find_nested_key(
    data: Any, 
    target_aliases: List[str],
    path: Optional[List[str]] = None
) -> Optional[Any]:
    """Recursively search for a key in nested structures."""
    if isinstance(data, dict):
        # Check current level
        for alias in target_aliases:
            if alias in data:
                return data[alias]
        
        # Recurse into values
        for k, v in data.items():
            found = _find_nested_key(v, target_aliases, path + [k])
            if found is not None:
                return found
    
    elif isinstance(data, list):
        for item in data:
            found = _find_nested_key(item, target_aliases, path)
            if found is not None:
                return found

    return None


def build_audit_record(
    parsed_data: Dict[str, Any],
    timestamp: Optional[str] = None,
    span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a structured audit record for replay/analysis.

    Args:
        parsed_data: Output from parse_semantic_attributes()
        timestamp: ISO 8601 timestamp (defaults to 'now')
        span_id: OTel span ID if available

    Returns:
        Complete audit record ready for storage or replay
    """
    genai_fields = extract_genai_fields(parsed_data)

    return {
        '_meta': {
            'timestamp': timestamp,
            'span_id': span_id,
            'source_format': _detect_type(parsed_data).__name__,
        },
        'genai': genai_fields,
        'raw_attributes': parsed_data,
    }


def replay_workflow(
    audit_records: List[Dict[str, Any]],
    field_mapping: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Reconstruct a workflow from multiple audit records.

    Args:
        audit_records: List of records from build_audit_record()
        field_mapping: Custom field mapping for all records

    Returns:
        Workflow reconstruction with timeline and context
    """
    # Sort by timestamp if available
    sorted_records = sorted(
        audit_records,
        key=lambda r: r.get('_meta', {}).get('timestamp') or '',
    )

    workflow = {
        'records': len(sorted_records),
        'timeline': [],
        'context': {},
    }

    for record in sorted_records:
        timeline_entry = {
            'timestamp': record['_meta'].get('timestamp'),
            'span_id': record['_meta'].get('span_id'),
            'model': record['genai'].get('model_name'),
            'tokens_in': record['genai'].get('input_tokens', 0),
            'tokens_out': record['genai'].get('response_tokens', 0),
        }

        # Extract additional context from raw attributes
        timeline_entry['context'] = {k: v for k, v in record['raw_attributes'].items() 
                                   if not k.startswith('_')}

        workflow['timeline'].append(timeline_entry)

    return workflow


# ============================================
# DEMO / ENTRY POINT
# ============================================

if __name__ == '__main__':
    # Sample raw log data (as it might appear in a real system)
    SAMPLE_LOGS = [
        """{"model_name": "gpt-4o", "prompt_id": "uuid-1234-5678", 
         "input_tokens": 150, "response_tokens": 320, 
         "total_tokens": 470, "is_streaming": false,
         "finish_reason": "user_content_filter"}""",
        
        """{"model_name": "claude-3.5", "prompt_id": "uuid-9abc-def1", 
         "input_tokens": 89, "response_tokens": 201, 
         "total_tokens": 290, "is_streaming": true,
         "finish_reason": "max_tokens"}""",
    ]

    print("=" * 60)
    print("OTEL SEMANTIC PARSER - DEMO")
    print("=" * 60)

    # Parse each log entry
    for i, raw_log in enumerate(SAMPLE_LOGS):
        print(f"\n--- Log Entry {i + 1} ---")
        
        # Step 1: Parse the raw attributes
        parsed = parse_semantic_attributes(raw_log)
        print(f"Parsed type: {_detect_type(parsed).__name__}")

        # Step 2: Extract GenAI fields
        genai = extract_genai_fields(parsed)
        print("Extracted GenAI fields:")
        for key, value in sorted(genai.items()):
            print(f"    {key}: {value}")

        # Step 3: Build audit record
        audit = build_audit_record(
            parsed, 
            timestamp="2024-01-15T10:30:00Z",
            span_id=f"span-{i+1}"
        )
        print(f"\nAudit record created with {len(audit)} top-level keys")

    # Step 4: Reconstruct workflow timeline
    print("\n" + "=" * 60)
    print("WORKFLOW RECONSTRUCTION")
    print("=" * 60)

    workflow = replay_workflow([audit for audit in [build_audit_record(
        parse_semantic_attributes(log), 
        extract_genai_fields(parse_semantic_attributes(log))
    ) for log in SAMPLE_LOGS]])

    print(f"Total records: {workflow['records']}")
    print("\nTimeline:")
    for entry in workflow['timeline']:
        print(f"  [{entry['timestamp']}] Model: {entry.get('model') or 'unknown'}, "
              f"Tokens: {entry['tokens_in']} in / {entry['tokens_out']} out")

    # Step 5: Quick summary statistics
    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)

    models = set()
    total_tokens = 0
    
    for record in [audit for audit in [build_audit_record(
        parse_semantic_attributes(log), 
        extract_genai_fields(parse_semantic_attributes(log))
    ) for log in SAMPLE_LOGS]]:
        model = record['genai'].get('model_name')
        if model:
            models.add(model)
        total_tokens += record['genai'].get('total_tokens', 0)

    print(f"Unique models: {len(models)}")
    for m in sorted(models):
        print(f"  - {m}")
    print(f"Total tokens processed: {total_tokens:,}")