// polyglot/typescript/workflow_reconstructor.ts

import { Span, SpanKind, Attributes } from '@opentelemetry/api';

/**
 * OTel GenAI Semantic Conventions (simplified subset)
 */
const GEN_AI_ATTRS = {
  OPERATION_NAME: 'gen_ai.operation.name',
  USER_MSG: 'gen_ai.user.message',
  ASSISTANT_MSG: 'gen_ai.assistant.message',
  TOOL_CALL_ID: 'gen_ai.tool.call.id',
  TOOL_CALL_NAME: 'gen_ai.tool.call.name',
  TOOL_CALL_ARGS: 'gen_ai.tool.call.arguments',
  TOOL_RESPONSE: 'gen_ai.tool.response',
  INPUT_MESSAGES: 'gen_ai.input_messages',
  OUTPUT_MESSAGES: 'gen_ai.output_messages',
};

/**
 * Represents a single message in an LLM conversation.
 */
export interface GenAIMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  name?: string; // For tool responses or named agents
}

/**
 * A tool invocation event extracted from spans.
 */
export interface ToolCallEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  response?: string | Record<string, unknown>;
  durationMs: number;
  spanId: string;
}

/**
 * A decision point made by an agent.
 */
export interface AgentDecision {
  id: string;
  timestamp: Date;
  description: string;
  context?: GenAIMessage[];
  reasoning?: string;
  selectedOption: string;
  alternatives: string[];
}

/**
 * A node in the reconstructed workflow graph.
 */
export interface WorkflowNode {
  id: string;
  type: 'agent' | 'tool' | 'llm_turn' | 'user_input' | 'system_event';
  timestamp: Date;
  durationMs?: number;
  data: any;
}

/**
 * A complete reconstructed workflow.
 */
export interface ReconstructedWorkflow {
  rootSpanId: string;
  startTime: Date;
  endTime: Date;
  nodes: WorkflowNode[];
  agentDecisions: AgentDecision[];
  toolCalls: ToolCallEvent[];
  llmTurns: GenAIMessage[][]; // Grouped by conversation thread
  metadata: {
    totalSpans: number;
    spanDepth: number;
    parallelBranches: number;
  };
}

/**
 * Configuration for the reconstructor.
 */
export interface ReconstructorConfig {
  includeSystemSpans?: boolean;
  maxSpanDepth?: number;
  groupLlmTurnsByThread?: boolean;
  extractReasoningFromAttributes?: (span: Span) => string | undefined;
}

/**
 * Default configuration.
 */
const DEFAULT_CONFIG: Required<ReconstructorConfig> = {
  includeSystemSpans: true,
  maxSpanDepth: 10,
  groupLlmTurnsByThread: true,
  extractReasoningFromAttributes: (span) => span.attributes['gen_ai.reasoning'] as string | undefined,
};

/**
 * Extracts the root span ID from a trace.
 */
function getRootSpanId(spans: Span[]): string {
  const parentMap = new Map<string, string>();
  
  for (const span of spans) {
    const parentId = span.parentSpanId || '';
    if (parentId && !parentMap.has(parentId)) {
      parentMap.set(parentId, span.context.spanId);
    }
  }

  let rootId = '';
  for (const [parentId, childId] of parentMap.entries()) {
    const childSpan = spans.find(s => s.context.spanId === childId);
    if (childSpan && !childSpan.parentSpanId) {
      rootId = parentId;
      break;
    }
  }

  return rootId || spans[0]?.context.spanId || '';
}

/**
 * Extracts GenAI messages from a span.
 */
function extractGenAIMessages(span: Span): GenAIMessage[] | null {
  const input = span.attributes[GEN_AI_ATTRS.INPUT_MESSAGES] as string | undefined;
  if (!input) return null;

  try {
    // Parse JSON array of messages
    const messages = JSON.parse(input);
    
    // Normalize to our interface
    return (messages as Array<{role: string, content?: string}>)
      .map(m => ({
        role: m.role as GenAIMessage['role'],
        content: m.content || '',
      }));
  } catch {
    return null;
  }
}

/**
 * Extracts tool call events from a span.
 */
function extractToolCalls(span: Span): ToolCallEvent[] | null {
  const calls: ToolCallEvent[] = [];
  
  // Check for direct tool call attributes
  if (span.attributes[GEN_AI_ATTRS.TOOL_CALL_ID]) {
    const id = span.attributes[GEN_AI_ATTRS.TOOL_CALL_ID] as string;
    const name = span.attributes[GEN_AI_ATTRS.TOOL_CALL_NAME] as string || 'unknown';
    
    // Try to get arguments from a nested attribute or parent
    let args: Record<string, unknown> = {};
    if (span.attributes[GEN_AI_ATTRS.TOOL_CALL_ARGS]) {
      try {
        args = JSON.parse(span.attributes[GEN_AI_ATTRS.TOOL_CALL_ARGS] as string);
      } catch {}
    }

    calls.push({
      id: id,
      name,
      args,
      durationMs: span.duration?.asMilliseconds() || 0,
      spanId: span.context.spanId,
    });
  }

  // Also check for tool response attributes
  if (span.attributes[GEN_AI_ATTRS.TOOL_RESPONSE]) {
    const lastCall = calls[calls.length - 1];
    if (lastCall) {
      try {
        lastCall.response = JSON.parse(span.attributes[GEN_AI_ATTRS.TOOL_RESPONSE] as string);
      } catch {}
    }
  }

  return calls.length > 0 ? calls : null;
}

/**
 * Recursively traverses spans and extracts workflow data.
 */
function traverseSpans(
  span: Span, 
  depth: number = 0, 
  parentSpanId?: string,
  config: Required<ReconstructorConfig>
): { nodes: WorkflowNode[]; decisions: AgentDecision[]; toolCalls: ToolCallEvent[] } {
  const result: any = { nodes: [], decisions: [], toolCalls: [] };

  // Extract GenAI messages (LLM turns)
  let llmMessages: GenAIMessage[] | null = extractGenAIMessages(span);
  
  if (llmMessages && config.includeSystemSpans || span.kind === SpanKind.CONSUMER) {
    const timestamp = new Date(span.startTime.toISOString());
    
    // Determine node type and data
    let nodeType: WorkflowNode['type'] = 'llm_turn';
    let nodeData: any = llmMessages;

    if (span.attributes[GEN_AI_ATTRS.OPERATION_NAME] === 'gen_ai.completion') {
      nodeType = 'agent';
      
      // Extract reasoning if available
      const reasoning = config.extractReasoningFromAttributes(span);
      
      // Infer description from operation name and attributes
      let description = `LLM Completion: ${span.attributes[GEN_AI_ATTRS.OPERATION_NAME]}`;
      if (reasoning) {
        description += ` | Reasoning: ${reasoning.substring(0, 200)}...`;
      }

      // Extract alternatives from input messages
      const alternatives: string[] = [];
      for (const msg of llmMessages) {
        if (msg.role === 'user') {
          alternatives.push(msg.content);
        }
      }

      result.decisions.push({
        id: span.context.spanId,
        timestamp,
        description,
        context: llmMessages.filter(m => m.role !== 'system'),
        reasoning: reasoning,
        selectedOption: llmMessages.find(m => m.role === 'assistant')?.content || '',
        alternatives,
      });

      // Create a separate node for the decision point
      result.nodes.push({
        id: span.context.spanId,
        type: 'agent',
        timestamp,
        durationMs: span.duration?.asMilliseconds(),
        data: {
          operationName: span.attributes[GEN_AI_ATTRS.OPERATION_NAME],
          inputCount: llmMessages.filter(m => m.role === 'user').length,
          outputCount: 1,
          reasoningLength: reasoning?.length || 0,
        },
      });

    } else if (span.kind === SpanKind.CONSUMER) {
      nodeType = 'agent';
      result.nodes.push({
        id: span.context.spanId,
        type: 'agent',
        timestamp,
        durationMs: span.duration?.asMilliseconds(),
        data: {
          operationName: span.attributes[GEN_AI_ATTRS.OPERATION_NAME],
          messagesCount: llmMessages?.length || 0,
        },
      });
    }

    // Create a node for the LLM turn itself
    result.nodes.push({
      id: `${span.context.spanId}-messages`,
      type: 'llm_turn',
      timestamp,
      durationMs: span.duration?.asMilliseconds(),
      data: {
        messages: llmMessages || [],
        messageCount: llmMessages?.length || 0,
        totalTokens: (span.attributes['gen_ai.usage.input_tokens'] as number) + 
                     (span.attributes['gen_ai.usage.output_tokens'] as number),
      },
    });

  } else if (llmMessages && config.includeSystemSpans === false) {
    llmMessages = null;
  }

  // Extract tool calls
  const toolCalls = extractToolCalls(span);
  
  if (toolCalls) {
    for (const tc of toolCalls) {
      result.toolCalls.push(tc);
      
      result.nodes.push({
        id: tc.spanId,
        type: 'tool',
        timestamp: new Date(span.startTime.toISOString()),
        durationMs: tc.durationMs,
        data: {
          id: tc.id,
          name: tc.name,
          args: tc.args,
          response: tc.response,
          hasResponse: !!tc.response,
        },
      });

      // Create a decision node for tool selection
      result.decisions.push({
        id: `${tc.spanId}-decision`,
        timestamp: new Date(span.startTime.toISOString()),
        description: `Tool Selection: ${tc.name}`,
        context: [],
        reasoning: tc.args ? `Arguments provided with ${Object.keys(tc.args).length} parameters` : undefined,
        selectedOption: tc.id,
        alternatives: [tc.name],
      });
    }
  }

  // Extract user input events
  if (span.attributes[GEN_AI_ATTRS.USER_MSG]) {
    const timestamp = new Date(span.startTime.toISOString());
    
    result.nodes.push({
      id: span.context.spanId,
      type: 'user_input',
      timestamp,
      durationMs: span.duration?.asMilliseconds(),
      data: {
        message: span.attributes[GEN_AI_ATTRS.USER_MSG] as string,
        length: (span.attributes[GEN_AI_ATTRS.USER_MSG] as string).length,
      },
    });

    result.decisions.push({
      id: `${span.context.spanId}-user`,
      timestamp,
      description: 'User Input',
      context: [],
      reasoning: undefined,
      selectedOption: span.attributes[GEN_AI_ATTRS.USER_MSG] as string,
      alternatives: [],
    });
  }

  // Extract system events (for debugging/auditing)
  if (span.kind === SpanKind.PRODUCER && config.includeSystemSpans) {
    result.nodes.push({
      id: span.context.spanId,
      type: 'system_event',
      timestamp: new Date(span.startTime.toISOString()),
      durationMs: span.duration?.asMilliseconds(),
      data: {
        kind: span.kind,
        operationName: span.attributes[GEN_AI_ATTRS.OPERATION_NAME],
        attributes: Object.fromEntries(
          Array.from((span as any).attributes.entries()).slice(0, 10) // Limit for performance
        ),
      },
    });
  }

  // Recursively process child spans
  const children = (span as any)._children || [];
  
  if (depth < config.maxSpanDepth && children.length > 0) {
    let maxDepth = depth;
    
    for (const child of children) {
      const childResult = traverseSpans(child, depth + 1, span.context.spanId, config);
      
      result.nodes.push(...childResult.nodes);
      result.decisions.push(...childResult.decisions);
      result.toolCalls.push(...childResult.toolCalls);
      
      maxDepth = Math.max(maxDepth, depth + 1);
    }

    // Calculate span depth metadata
    if (maxDepth > config.maxSpanDepth) {
      config.maxSpanDepth = maxDepth;
    }
  }

  return result;
}

/**
 * Main reconstructor class.
 */
export class WorkflowReconstructor {
  private readonly config: Required<ReconstructorConfig>;
  
  constructor(config?: ReconstructorConfig) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Reconstructs a workflow from OTel spans.
   */
  reconstruct(spans: Span[]): ReconstructedWorkflow {
    const rootSpanId = getRootSpanId(spans);
    
    if (!rootSpanId) {
      throw new Error('No root span found in trace');
    }

    // Traverse and extract all data
    const traversalResult = traverseSpans(spans[0], 0, undefined, this.config);

    // Sort nodes by timestamp
    traversalResult.nodes.sort((a, b) => 
      a.timestamp.getTime() - b.timestamp.getTime()
    );

    // Calculate metadata
    const startTime = new Date(Math.min(
      ...traversalResult.nodes.map(n => n.timestamp.getTime())
    ));

    const endTime = new Date(Math.max(
      ...traversalResult.nodes.map(n => {
        const end = n.timestamp.getTime() + (n.durationMs || 0);
        return end;
      })
    ));

    // Calculate parallel branches
    let maxParallel = 1;
    let currentDepth = 0;
    
    for (const node of traversalResult.nodes) {
      if (node.type === 'agent' || node.type === 'llm_turn') {
        currentDepth++;
        maxParallel = Math.max(maxParallel, currentDepth);
        currentDepth--;
      }
    }

    return {
      rootSpanId,
      startTime,
      endTime,
      nodes: traversalResult.nodes,
      agentDecisions: traversalResult.decisions,
      toolCalls: traversalResult.toolCalls,
      llmTurns: [], // Populated if groupLlmTurnsByThread is true
      metadata: {
        totalSpans: spans.length,
        spanDepth: this.config.maxSpanDepth,
        parallelBranches: maxParallel,
      },
    };
  }

  /**
   * Groups LLM turns by conversation thread.
   */
  private groupLlmTurnsByThread(workflow: ReconstructedWorkflow): GenAIMessage[][] {
    if (!this.config.groupLlmTurnsByThread) return [];

    // Group nodes by parent-child relationships indicating threads
    const threadMap = new Map<string, WorkflowNode[]>();

    for (const node of workflow.nodes) {
      if (node.type === 'llm_turn') {
        // Find the root/parent agent node that started this thread
        let parentId = '';
        
        // Look up the parent in the nodes list
        const parentIndex = workflow.nodes.findIndex(n => 
          n.id !== node.id && 
          n.type === 'agent' &&
          (n.timestamp.getTime() < node.timestamp.getTime())
        );

        if (parentIndex >= 0) {
          parentId = workflow.nodes[parentIndex].id;
        } else {
          // No parent found, use root span ID
          parentId = workflow.rootSpanId;
        }

        if (!threadMap.has(parentId)) {
          threadMap.set(parentId, []);
        }

        threadMap.get(parentId)!.push(node);
      }
    }

    // Extract messages from each thread
    const threads: GenAIMessage[][] = [];
    
    for (const [parentId, nodes] of threadMap.entries()) {
      const messages: GenAIMessage[] = [];
      
      for (const node of nodes) {
        if (node.type === 'llm_turn' && node.data.messages) {
          messages.push(...node.data.messages);
        }