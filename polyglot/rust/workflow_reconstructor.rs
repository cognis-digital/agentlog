use serde::{Deserialize, Serialize};
use std::collections::{HashMap, BTreeMap};
use std::time::Duration;

// =============================================================================
// OTel GenAI Semantic Conventions (simplified for reconstruction)
// =============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SpanKind {
    UserMessage,
    AssistantMessage,
    ToolCall,
    ToolResult,
    Internal,
}

impl SpanKind {
    pub fn from_string(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "user_message" => SpanKind::UserMessage,
            "assistant_message" | "assistant_msg" | "response" => SpanKind::AssistantMessage,
            "tool_call" | "tool" | "function_call" => SpanKind::ToolCall,
            "tool_result" | "tool_response" | "observation" => SpanKind::ToolResult,
            _ => SpanKind::Internal,
        }
    }

    pub fn is_user_input(&self) -> bool {
        matches!(self, SpanKind::UserMessage)
    }

    pub fn is_model_output(&self) -> bool {
        matches!(self, SpanKind::AssistantMessage)
    }

    pub fn is_tool_interaction(&self) -> bool {
        matches!(self, SpanKind::ToolCall | SpanKind::ToolResult)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenAiAttributes {
    #[serde(default)]
    pub request_model: Option<String>,
    #[serde(default)]
    pub response_id: Option<String>,
    #[serde(default)]
    pub usage: Option<UsageMetrics>,
    #[serde(default)]
    pub tool_calls: Vec<ToolCallInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageMetrics {
    #[serde(default)]
    pub prompt_tokens: u64,
    #[serde(default)]
    pub completion_tokens: u64,
    #[serde(default)]
    pub total_tokens: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallInfo {
    pub name: String,
    pub arguments: HashMap<String, serde_json::Value>,
    #[serde(default)]
    pub id: Option<String>,
}

// =============================================================================
// Workflow Event Types for Reconstruction
// =============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum WorkflowEvent {
    UserInput {
        content: String,
        timestamp: i64, // Unix ms
        span_id: String,
    },
    
    ModelResponse {
        content: String,
        metadata: ResponseMetadata,
        timestamp: i64,
        span_id: String,
    },
    
    ToolCall {
        tool_name: String,
        arguments: serde_json::Value,
        call_id: String,
        timestamp: i64,
    },
    
    ToolResult {
        tool_name: String,
        result: serde_json::Value,
        call_id: String,
        timestamp: i64,
    },
    
    StateTransition {
        from_state: Option<String>,
        to_state: String,
        reason: String,
        timestamp: i64,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResponseMetadata {
    pub model: String,
    pub id: String,
    #[serde(default)]
    pub usage: UsageMetrics,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

// =============================================================================
// OTel Span Parser for GenAI Workflows
// =============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OtelSpan {
    pub span_id: String,
    pub parent_span_id: Option<String>,
    pub kind: String,
    #[serde(default)]
    pub attributes: HashMap<String, serde_json::Value>,
    pub start_time_unix_nano: i64, // nanoseconds from epoch
    pub end_time_unix_nano: i64,
}

impl OtelSpan {
    pub fn as_ms(&self) -> i64 {
        self.start_time_unix_nano / 1_000_000
    }
    
    pub fn duration_ms(&self) -> u64 {
        (self.end_time_unix_nano - self.start_time_unix_nano) / 1_000_000
    }
}

pub struct SpanParser;

impl SpanParser {
    /// Parse OTel spans into a workflow reconstruction
    pub fn reconstruct(spans: &[OtelSpan]) -> Result<WorkflowReconstruction, ReconstructionError> {
        let mut events = Vec::new();
        let mut span_map: HashMap<String, &OtelSpan> = HashMap::new();
        
        // Build lookup map
        for span in spans.iter() {
            span_map.insert(span.span_id.clone(), span);
        }

        // Sort by start time
        let mut sorted_spans = spans.to_vec();
        sorted_spans.sort_by_key(|s| s.start_time_unix_nano);

        // Parse each span into events
        for span in &sorted_spans {
            let kind = SpanKind::from_string(&span.kind);
            
            match (kind, &span.attributes) {
                (SpanKind::UserMessage, attrs) => {
                    if let Some(content) = Self::extract_user_content(attrs)? {
                        events.push(WorkflowEvent::UserInput {
                            content,
                            timestamp: span.as_ms(),
                            span_id: span.span_id.clone(),
                        });
                    }
                },
                
                (SpanKind::AssistantMessage, attrs) => {
                    if let Some(content) = Self::extract_assistant_content(attrs)? {
                        let metadata = ResponseMetadata {
                            model: attrs.get("gen_ai.request.model")
                                .and_then(|v| v.as_str())
                                .map(String::from)
                                .unwrap_or_else(|| "unknown".to_string()),
                            id: span.span_id.clone(),
                            usage: UsageMetrics {
                                prompt_tokens: attrs.get("gen_ai.usage.prompt_tokens")
                                    .and_then(|v| v.as_i64())
                                    .map(u64::from)
                                    .unwrap_or(0),
                                completion_tokens: attrs.get("gen_ai.usage.completion_tokens")
                                    .and_then(|v| v.as_i64())
                                    .map(u64::from)
                                    .unwrap_or(0),
                                total_tokens: attrs.get("gen_ai.usage.total_tokens")
                                    .and_then(|v| v.as_i64())
                                    .map(u64::from)
                                    .unwrap_or(0),
                            },
                            finish_reason: attrs.get("gen_ai.response.finish_reason")
                                .and_then(|v| v.as_str())
                                .map(String::from),
                        };
                        
                        events.push(WorkflowEvent::ModelResponse {
                            content,
                            metadata,
                            timestamp: span.as_ms(),
                            span_id: span.span_id.clone(),
                        });
                    }
                },
                
                (SpanKind::ToolCall, attrs) => {
                    let tool_name = attrs.get("gen_ai.tool.name")
                        .and_then(|v| v.as_str())
                        .map(String::from)
                        .unwrap_or_else(|| "unknown".to_string());
                    
                    let call_id = span.span_id.clone();
                    
                    // Extract arguments from various possible locations
                    let args = if let Some(args_val) = attrs.get("gen_ai.tool.arguments") {
                        serde_json::from_value::<serde_json::Value>(args_val.clone()).unwrap_or(serde_json::json!({}))
                    } else {
                        serde_json::json!({})
                    };
                    
                    events.push(WorkflowEvent::ToolCall {
                        tool_name,
                        arguments: args,
                        call_id,
                        timestamp: span.as_ms(),
                    });
                },
                
                (SpanKind::ToolResult, attrs) => {
                    let tool_name = attrs.get("gen_ai.tool.name")
                        .and_then(|v| v.as_str())
                        .map(String::from)
                        .unwrap_or_else(|| "unknown".to_string());
                    
                    // Find parent call_id from span hierarchy
                    let call_id = Self::find_parent_call_id(&span.span_id, &span_map);
                    
                    if let Some(result_val) = attrs.get("gen_ai.tool.result") {
                        events.push(WorkflowEvent::ToolResult {
                            tool_name,
                            result: serde_json::from_value::<serde_json::Value>(result_val.clone()).unwrap_or(serde_json::json!({})),
                            call_id,
                            timestamp: span.as_ms(),
                        });
                    } else if let Some(output) = attrs.get("gen_ai.tool.output") {
                        events.push(WorkflowEvent::ToolResult {
                            tool_name,
                            result: serde_json::from_value::<serde_json::Value>(output.clone()).unwrap_or(serde_json::json!({})),
                            call_id,
                            timestamp: span.as_ms(),
                        });
                    }
                },
                
                _ => {}, // Ignore internal or unknown spans for now
            }
        }

        Ok(WorkflowReconstruction { events })
    }

    fn extract_user_content(attrs: &HashMap<String, serde_json::Value>) -> Result<Option<String>, ReconstructionError> {
        let content = attrs.get("gen_ai.user.message.content")
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| {
                // Fallback: try to find any text content
                attrs.values()
                    .filter_map(|v| v.as_str().map(String::from))
                    .next()
            });
        Ok(content)
    }

    fn extract_assistant_content(attrs: &HashMap<String, serde_json::Value>) -> Result<Option<String>, ReconstructionError> {
        let content = attrs.get("gen_ai.assistant.message.content")
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| {
                // Fallback: try to find any text content
                attrs.values()
                    .filter_map(|v| v.as_str().map(String::from))
                    .next()
            });
        Ok(content)
    }

    fn find_parent_call_id(span_id: &str, span_map: &HashMap<String, &OtelSpan>) -> String {
        // Walk up the parent chain to find a tool call
        let mut current = Some((span_id.to_string(), span_map.get(span_id)));
        
        while let Some((id, span)) = current {
            if span.map(|s| s.kind.as_str() == "tool_call").unwrap_or(false) {
                return id;
            }
            
            if let Some(parent_id) = span.map(|s| s.parent_span_id.clone()) {
                current = Some((parent_id, span_map.get(&parent_id)));
            } else {
                break;
            }
        }
        
        span_id.to_string()
    }
}

// =============================================================================
// WorkflowReconstruction: Main Struct
// =============================================================================

#[derive(Debug, Clone)]
pub struct WorkflowReconstruction {
    pub events: Vec<WorkflowEvent>,
    pub metadata: ReconstructionMetadata,
}

impl WorkflowReconstruction {
    /// Create from OTel spans
    pub fn from_spans(spans: &[OtelSpan]) -> Result<Self, ReconstructionError> {
        SpanParser::reconstruct(spans)
    }

    /// Validate the reconstruction for audit purposes
    pub fn validate(&self) -> ValidationResult {
        let mut errors = Vec::new();
        
        // Check 1: Events should be chronologically ordered
        let prev_time = -1i64;
        for event in &self.events {
            match event {
                WorkflowEvent::UserInput { timestamp, .. }
                | WorkflowEvent::ModelResponse { timestamp, .. }
                | WorkflowEvent::ToolCall { timestamp, .. }
                | WorkflowEvent::ToolResult { timestamp, .. }
                | WorkflowEvent::StateTransition { timestamp, .. } => {
                    if *timestamp < prev_time && prev_time >= 0 {
                        errors.push(ValidationIssue::OutOfOrder {
                            event_type: format!("{:?}", event),
                            expected: prev_time,
                            actual: *timestamp,
                        });
                    }
                    prev_time = *timestamp;
                },
            }
        }

        // Check 2: Tool calls should have matching results (if present)
        let mut tool_calls: BTreeMap<String, &WorkflowEvent> = BTreeMap::new();
        for event in &self.events {
            match event {
                WorkflowEvent::ToolCall { call_id, .. } => {
                    tool_calls.insert(call_id.clone(), event);
                },
                WorkflowEvent::ToolResult { call_id, .. } => {
                    if let Some(existing) = tool_calls.get(*call_id) {
                        // Verify result came after call
                        if existing.timestamp() >= event.timestamp() {
                            errors.push(ValidationIssue::OutOfOrder {
                                event_type: format!("{:?}", event),
                                expected: existing.timestamp(),
                                actual: event.timestamp(),
                            });
                        }
                    } else {
                        errors.push(ValidationIssue::OrphanedResult { call_id: (*call_id).clone() });
                    }
                },
                _ => {},
            }
        }

        // Check 3: Model responses should follow user inputs or tool results
        let mut last_interaction = -1i64;
        for event in &self.events {
            match event {
                WorkflowEvent::UserInput { timestamp, .. }
                | WorkflowEvent::ToolResult { timestamp, .. } => {
                    last_interaction = *timestamp;
                },
                WorkflowEvent::ModelResponse { timestamp, .. } => {
                    if *timestamp > last_interaction && last_interaction >= 0 {
                        let gap_ms = (*timestamp - last_interaction) as u64;
                        if gap_ms > 5000 { // More than 5 seconds without interaction
                            errors.push(ValidationIssue::LongGap {
                                from: last_interaction,
                                to: *timestamp,
                                duration_ms: gap_ms,
                            });
                        }
                    }
                },
                _ => {},
            }
        }

        ValidationResult { issues: errors }
    }

    /// Generate an audit report
    pub fn generate_audit_report(&self) -> String {
        let mut output = format!(
            "=== Workflow Reconstruction Audit Report ===\n\n",
        );

        // Summary stats
        let user_inputs: usize = self.events.iter()
            .filter(|e| matches!(e, WorkflowEvent::UserInput { .. }))
            .count();
        
        let model_responses: usize = self.events.iter()
            .filter(|e| matches!(e, WorkflowEvent::ModelResponse { .. }))
            .count();
        
        let tool_calls: usize = self.events.iter()
            .filter(|e| matches!(e, WorkflowEvent::ToolCall { .. }))
            .count();
        
        let tool_results: usize = self.events.iter()
            .filter(|e| matches!(e, WorkflowEvent::ToolResult { .. }))
            .count();

        output.push_str(&format!(
            "Summary Statistics:\n",
        ));
        output.push_str(&format!("  - User Inputs:     {}\n", user_inputs));
        output.push_str(&format!("  - Model Responses: {}\n", model_responses));
        output.push_str(&format!("  - Tool Calls:      {}\n", tool_calls));
        output.push_str(&format!("  - Tool Results:    {}\n\n", tool_results));

        // Timeline view
        output.push_str("Timeline:\n");
        output.push_str(&format!("{:<6} | {:<40}\n", "Time", "Event"));
        output.push_str(&format!("{:-<58}\n", ""));
        
        for event in &self.events {
            let time = format!("{:>6}", event.timestamp());
            match event {
                WorkflowEvent::UserInput { content, .. } => {
                    let preview = truncate_string(content, 37);
                    output.push_str(&format!("{:<6} | User: {}\n", time, preview));
                },
                WorkflowEvent::ModelResponse { content, metadata, .. } => {
                    let model = &metadata.model;
                    let preview = truncate_string(content, 37);
                    output.push_str(&format!(
                        "{:<6} | Model({}): {}\n",
                        time, model, preview
                    ));
                },
                WorkflowEvent::ToolCall { tool_name, .. } =>