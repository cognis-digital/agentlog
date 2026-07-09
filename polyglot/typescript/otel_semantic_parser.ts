import { Span, LogRecord } from '@opentelemetry/sdk-trace-base';

// ============================================================================
// TYPE DEFINITIONS - OTel GenAI Semantic Conventions
// ============================================================================

export namespace OtelGenAiConventions {
  // Model identifiers and versions
  export const ATTR_GEN_AI_MODEL_NAME = 'gen_ai.model.name';
  export const ATTR_GEN_AI_MODEL_VERSION = 'gen_ai.model.version';
  
  // Request/response metadata
  export const ATTR_GEN_AI_REQUEST_ID = 'gen_ai.request.id';
  export const ATTR_GEN_AI_PROMPT = 'gen_ai.prompt';
  export const ATTR_GEN_AI_COMPLETION = 'gen_ai.completion';
  export const ATTR_GEN_AI_TOKENS_USED = 'gen_ai.usage.input_tokens';
  export const ATTR_GEN_AI_OUTPUT_TOKENS = 'gen_ai.usage.output_tokens';
  
  // Tool/function calls
  export const ATTR_GEN_AI_TOOL_CALL_ID = 'gen_ai.tool_call.id';
  export const ATTR_GEN_AI_TOOL_NAME = 'gen_ai.tool.name';
  export const ATTR_GEN_AI_TOOL_ARGUMENTS = 'gen_ai.tool.arguments';
  
  // Context/Session tracking
  export const ATTR_GEN_AI_SESSION_ID = 'gen_ai.session.id';
  export const ATTR_GEN_AI_USER_ID = 'gen_ai.user.id';
}

// ============================================================================
// PARSER TYPES & INTERFACES
// ============================================================================

export interface ParsedSpan {
  traceId: string;
  spanId: string;
  parentId: string | null;
  name: string;
  kind: SpanKind;
  startTime: Date;
  endTime: Date;
  durationMs: number;
  attributes: Record<string, any>;
  
  // Extracted semantic metadata
  genAiModel?: {
    name: string;
    version?: string;
  };
  
  genAiRequest?: {
    id: string | null;
    prompt: string[];
    completion: string | null;
    inputTokens: number | null;
    outputTokens: number | null;
    sessionId: string | null;
    userId: string | null;
  };
  
  genAiToolCall?: {
    id: string | null;
    name: string | null;
    arguments: Record<string, any> | null;
  }[];
}

export interface ParsedLogRecord {
  traceId: string | null;
  spanId: string | null;
  timestamp: Date;
  severityText: string | null;
  body: string;
  attributes: Record<string, any>;
  
  // Extracted semantic metadata
  genAiRequest?: {
    id: string | null;
    prompt: string[];
    completion: string | null;
    inputTokens: number | null;
    outputTokens: number | null;
    sessionId: string | null;
    userId: string | null;
  };
  
  genAiToolCall?: {
    id: string | null;
    name: string | null;
    arguments: Record<string, any> | null;
  }[];
}

export type SpanKind = 'INTERNAL' | 'SERVER' | 'CLIENT' | 'PRODUCER' | 'CONSUMER';

// ============================================================================
// PARSER IMPLEMENTATION
// ============================================================================

class OtelSemanticParser {
  private readonly DEFAULT_KIND: SpanKind = 'INTERNAL';

  /**
   * Parse a single span into structured semantic metadata.
   */
  public parseSpan(span: Span): ParsedSpan {
    const attrs = this.normalizeAttributes(span.attributes);
    
    return {
      traceId: span.traceId,
      spanId: span.spanId,
      parentId: span.parentSpanId || null,
      name: span.name,
      kind: this.extractKindFromName(span),
      startTime: new Date(span.startTime.toISOString()),
      endTime: new Date(span.endTime.toISOString()),
      durationMs: span.duration ? span.duration / 1000000 : 0,
      attributes: attrs,
      
      // Extract GenAI-specific metadata
      genAiModel: this.extractGenAiModel(attrs),
      genAiRequest: this.extractGenAiRequest(attrs),
      genAiToolCall: this.extractGenAiToolCalls(attrs),
    };
  }

  /**
   * Parse a single log record into structured semantic metadata.
   */
  public parseLogRecord(log: LogRecord): ParsedLogRecord {
    const attrs = this.normalizeAttributes(log.attributes);
    
    return {
      traceId: log.traceId || null,
      spanId: log.spanId || null,
      timestamp: new Date(log.timestamp.toISOString()),
      severityText: log.severityText || null,
      body: log.body,
      attributes: attrs,
      
      // Extract GenAI-specific metadata from logs too
      genAiRequest: this.extractGenAiRequest(attrs),
      genAiToolCall: this.extractGenAiToolCalls(attrs),
    };
  }

  /**
   * Parse a batch of spans and log records.
   */
  public parseBatch(spans: Span[], logs: LogRecord[]): {
    parsedSpans: ParsedSpan[];
    parsedLogs: ParsedLogRecord[];
    errors: Array<{ type: string, recordId: string | null, message: string }>;
  } {
    const parsedSpans = spans.map(span => ({
      span,
      result: this.parseSpan(span),
    })).map(({ span, result }) => result);

    const parsedLogs = logs.map(log => ({
      log,
      result: this.parseLogRecord(log),
    })).map(({ log, result }) => result);

    // Collect any errors during parsing
    const errors: Array<{ type: string; recordId: string | null; message: string }> = [];

    return { parsedSpans, parsedLogs, errors };
  }

  /**
   * Build a replay-friendly representation of the workflow.
   */
  public buildReplayGraph(spans: ParsedSpan[]): Map<string, ParsedSpan[]> {
    const graph = new Map<string, ParsedSpan[]>();
    
    for (const span of spans) {
      if (!span.parentId || !graph.has(span.parentId)) {
        graph.set(span.traceId, []);
      }
      
      const parentGroup = graph.get(span.traceId) || [];
      parentGroup.push(span);
    }

    return graph;
  }

  // ========================================================================
  // EXTRACTOR HELPERS
  // ========================================================================

  private extractKindFromName(span: Span): SpanKind {
    const nameLower = span.name.toLowerCase();
    
    if (nameLower.includes('http') || nameLower.includes('request')) {
      return 'CLIENT';
    } else if (nameLower.includes('response') || nameLower.includes('server')) {
      return 'SERVER';
    } else if (nameLower.includes('producer') || nameLower.includes('publish')) {
      return 'PRODUCER';
    } else if (nameLower.includes('consumer') || nameLower.includes('receive')) {
      return 'CONSUMER';
    }

    return this.DEFAULT_KIND;
  }

  private extractGenAiModel(attrs: Record<string, any>): { name?: string; version?: string } | undefined {
    const modelName = attrs[OtelGenAiConventions.ATTR_GEN_AI_MODEL_NAME] as string | null;
    const modelVersion = attrs[OtelGenAiConventions.ATTR_GEN_AI_MODEL_VERSION] as string | null;

    if (!modelName) return undefined;

    return {
      name: modelName,
      version: modelVersion || undefined,
    };
  }

  private extractGenAiRequest(attrs: Record<string, any>): 
    | { id?: string; prompt: string[]; completion: string | null; inputTokens: number | null; outputTokens: number | null; sessionId: string | null; userId: string | null; }
    | undefined {
    const requestId = attrs[OtelGenAiConventions.ATTR_GEN_AI_REQUEST_ID] as string | null;
    const sessionId = attrs[OtelGenAiConventions.ATTR_GEN_AI_SESSION_ID] as string | null;
    const userId = attrs[OtelGenAiConventions.ATTR_GEN_AI_USER_ID] as string | null;

    // Extract prompts - can be single or array
    let prompt: string[] = [];
    if (Array.isArray(attrs[OtelGenAiConventions.ATTR_GEN_AI_PROMPT])) {
      prompt = attrs[OtelGenAiConventions.ATTR_GEN_AI_PROMPT] as unknown as string[];
    } else if (typeof attrs[OtelGenAiConventions.ATTR_GEN_AI_PROMPT] === 'string') {
      prompt = [attrs[OtelGenAiConventions.ATTR_GEN_AI_PROMPT]];
    }

    // Extract completion
    let completion: string | null = null;
    if (Array.isArray(attrs[OtelGenAiConventions.ATTR_GEN_AI_COMPLETION])) {
      completion = attrs[OtelGenAiConventions.ATTR_GEN_AI_COMPLETION][0] as string || null;
    } else if (typeof attrs[OtelGenAiConventions.ATTR_GEN_AI_COMPLETION] === 'string') {
      completion = attrs[OtelGenAiConventions.ATTR_GEN_AI_COMPLETION];
    }

    // Extract token usage - handle both singular and plural forms
    const inputTokensAttr = 
      attrs[OtelGenAiConventions.ATTR_GEN_AI_TOKENS_USED] ||
      attrs['gen_ai.usage.input_tokens'] ||
      attrs['gen_ai.usage.token_count.input'];
    
    const outputTokensAttr = 
      attrs['gen_ai.usage.output_tokens'] ||
      attrs['gen_ai.usage.token_count.output'];

    return {
      id: requestId,
      prompt,
      completion,
      inputTokens: this.safeParseNumber(inputTokensAttr),
      outputTokens: this.safeParseNumber(outputTokensAttr),
      sessionId,
      userId,
    };
  }

  private extractGenAiToolCalls(attrs: Record<string, any>): 
    | { id?: string; name?: string; arguments: Record<string, any> | null; }[]
    | undefined {
    const toolCallId = attrs[OtelGenAiConventions.ATTR_GEN_AI_TOOL_CALL_ID] as string | null;
    
    if (!toolCallId) return undefined;

    // Extract name - try multiple possible attribute names
    let name: string | null = null;
    for (const key of [
      OtelGenAiConventions.ATTR_GEN_AI_TOOL_NAME,
      'gen_ai.tool.name',
      'tool.name'
    ]) {
      const val = attrs[key];
      if (val) {
        name = String(val);
        break;
      }
    }

    // Extract arguments - try multiple possible attribute names
    let args: Record<string, any> | null = null;
    for (const key of [
      OtelGenAiConventions.ATTR_GEN_AI_TOOL_ARGUMENTS,
      'gen_ai.tool.arguments',
      'tool.arguments'
    ]) {
      const val = attrs[key];
      if (val) {
        args = typeof val === 'object' ? val : null;
        break;
      }
    }

    return [{ id: toolCallId, name, arguments: args }];
  }

  // ========================================================================
  // UTILITY HELPERS
  // ========================================================================

  private normalizeAttributes(attrs: Record<string, any>): Record<string, any> {
    const normalized: Record<string, any> = {};
    
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined) continue;
      
      // Convert numbers to integers where appropriate
      if (typeof value === 'number' && !Number.isInteger(value)) {
        normalized[key] = Math.round(value);
      } else {
        normalized[key] = value;
      }
    }

    return normalized;
  }

  private safeParseNumber<T>(value: any): T | null {
    if (value === null || value === undefined) return null;
    
    const num = Number(value);
    if (!Number.isNaN(num)) return num as unknown as T;
    
    return null;
  }

  // ========================================================================
  // EXPORTED HELPERS FOR AGENTLOG WORKFLOW
  // ========================================================================

  /**
   * Check if a span/log is likely GenAI-related.
   */
  public static isGenAiRelated(span: Span | ParsedSpan): boolean {
    const attrs = typeof span === 'object' && (span as any).attributes 
      ? (span as any).attributes 
      : ((span as ParsedSpan).attributes || {});

    return Object.keys(attrs).some(key => 
      key.toLowerCase().includes('gen_ai') ||
      key.toLowerCase().includes('llm') ||
      key.toLowerCase().includes('completion') ||
      key.toLowerCase().includes('prompt')
    );
  }

  /**
   * Get a summary of the GenAI activity in a span.
   */
  public static getGenAiSummary(span: ParsedSpan): string | null {
    if (!span.genAiModel && !span.genAiRequest) return null;

    const parts: string[] = [];

    if (span.genAiModel?.name) {
      parts.push(`model=${span.genAiModel.name}${span.genAiModel.version ? `:${span.genAiModel.version}` : ''}`);
    }

    if (span.genAiRequest?.id) {
      parts.push(`request_id=${span.genAiRequest.id.slice(0, 8)}...`);
    }

    if (span.genAiRequest?.inputTokens !== null) {
      parts.push(`${span.genAiRequest.inputTokens} input tokens`);
    }

    if (span.genAiRequest?.outputTokens !== null) {
      parts.push(`${span.genAiRequest.outputTokens} output tokens`);
    }

    return parts.length > 0 ? parts.join(', ') : null;
  }

  /**
   * Estimate cost based on token usage and model pricing.
   */
  public static estimateCost(
    span: ParsedSpan, 
    pricing?: { inputPerToken: number; outputPerToken: number }
  ): number | null {
    if (!span.genAiRequest) return null;

    const inputTokens = span.genAiRequest.inputTokens || 0;
    const outputTokens = span.genAiRequest.outputTokens || 0;

    const defaultPricing = { inputPerToken: 0.0015, outputPerToken: 0.002 }; // GPT-4 defaults

    const effectivePricing = pricing || defaultPricing;
    
    return (inputTokens * effectivePricing.inputPerToken + 
            outputTokens * effectivePricing.outputPerToken);
  }

  /**
   * Create a human-readable audit trail entry.
   */
  public static createAuditEntry(span: ParsedSpan): string {
    const timestamp = span.startTime.toISOString();
    const durationMs = Math.round(span.durationMs * 100) / 100;

    let parts = [
      `[${timestamp}]`,
      `span="${span.name}" (${durationMs}ms)`,
      `trace_id=${span.traceId.slice(0, 8)}...`,
    ];

    const summary = OtelSemanticParser.getGenAiSummary(span);
    if (summary) {
      parts.push(`gen_ai: ${summary}`);
    }

    return parts.join(' | ');
  }
}

// ============================================================================
// RUNNABLE DEMO / ENTRY POINT
// ============================================================================

function runDemo() {
  console.log('\n=== OTel Semantic Parser Demo ===\n');

  // Create mock spans with GenAI data
  const mockSpans: Span[] = [
    new Span({
      name: 'gen_ai.generate',
      kind: 'CLIENT' as any,
      startTime: new Date(Date.now() - 1000),
      endTime: new Date(Date.now()),
      attributes: {
        'gen_ai.model.name': 'gpt-4o-mini',
        'gen_ai.request.id': 'req_abc123def456',
        'gen_ai.prompt': ['Hello, how are you?', 'What is the weather?'],
        'gen_ai.usage.input_tokens': 12,
        'gen_ai.usage.output_tokens': 8,
        'gen_ai.session.id': 'sess_xyz789',
      },
    }),

    new Span({
      name: 'http.request',
      kind: 'CLIENT' as any,
      startTime: new Date(Date.now() - 500),
      endTime: new Date(Date.now()),
      attributes: {
        'url': 'https://api.example.com/v1/chat/completions',
        'method': 'POST',
        'status_code': 200,
      },
    }),

    new Span({
      name: 'gen_ai.tool_call',
      kind: 'INTERNAL' as any,
      startTime: new Date(Date.now() - 300),
      endTime: new Date(Date.now()),
      attributes: {
        'gen_ai.tool_call.id': 'tool_001',
        'gen_ai.tool.name': 'search_web',
        'gen_ai.tool.arguments': { query: 'weather forecast' },
      },
    }),
  ];

  // Parse all spans
  const parser = new OtelSemanticParser();
  const results = parser.parseBatch(mockSpans, []);

  console.log('Parsed Spans: