use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// OTEL span attributes for agentic workflows
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SemanticAttributes {
    pub span_name: String,
    pub span_kind: SpanKind,
    pub parent_id: Option<String>,
    pub trace_id: String,
    pub span_id: String,
    // Agentic-specific fields
    pub model_name: Option<String>,
    pub request_tokens: Option<u64>,
    pub response_tokens: Option<u64>,
}

// SpanKind from OTEL spec
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SpanKind {
    Internal,
    Server,
    Client,
    Producer,
    Consumer,
}

impl SpanKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            SpanKind::Internal => "internal",
            SpanKind::Server => "server",
            SpanKind::Client => "client",
            SpanKind::Producer => "producer",
            SpanKind::Consumer => "consumer",
        }
    }

    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "internal" | "" => SpanKind::Internal,
            "server" => SpanKind::Server,
            "client" => SpanKind::Client,
            "producer" => SpanKind::Producer,
            "consumer" => SpanKind::Consumer,
            _ => SpanKind::Internal,
        }
    }
}

// Main parser that handles the OTEL JSON format
pub struct SemanticParser {
    pub config: ParserConfig,
}

impl SemanticParser {
    pub fn new(config: Option<ParserConfig>) -> Self {
        let cfg = config.unwrap_or_default();
        Self { config: cfg }
    }

    pub fn parse(&self, json_str: &str) -> Result<Vec<SemanticAttributes>, ParseError> {
        // Deserialize OTEL JSON format
        let spans: Vec<OTelSpan> = serde_json::from_str(json_str)?;
        
        let mut results = Vec::new();
        
        for span in spans {
            let attrs = self.extract_semantics(&span)?;
            results.push(attrs);
        }
        
        Ok(results)
    }

    fn extract_semantics(&self, span: &OTelSpan) -> Result<SemanticAttributes, ParseError> {
        // Extract core OTEL fields
        let name = span.name.clone();
        let kind = SpanKind::from_str(span.kind.as_str());
        let parent_id = span.parent.span_id.map(|s| s.to_string());
        
        Ok(SemanticAttributes {
            span_name: name,
            span_kind: kind,
            parent_id,
            trace_id: span.trace_id.to_string(),
            span_id: span.span_id.to_string(),
            model_name: self.extract_model_info(&span.attributes)?,
            request_tokens: self.extract_token_count(&span.attributes, "request_tokens")?,
            response_tokens: self.extract_token_count(&span.attributes, "response_tokens")?,
        })
    }

    fn extract_model_info(&self, attrs: &HashMap<String, serde_json::Value>) -> Result<Option<String>, ParseError> {
        // Look for model name in common OTEL semantic conventions
        if let Some(model) = attrs.get("gen_ai.request.model")?.as_str() {
            return Ok(Some(model.to_string()));
        }

        Ok(None)
    }

    fn extract_token_count(&self, attrs: &HashMap<String, serde_json::Value>, field: &str) -> Result<Option<u64>, ParseError> {
        if let Some(tokens) = attrs.get(field)?.as_u64() {
            return Ok(Some(tokens));
        }

        Ok(None)
    }
}

// OTEL JSON span representation
#[derive(Debug, Clone)]
struct OTelSpan {
    name: String,
    kind: SpanKindStr,
    parent: ParentInfo,
    trace_id: TraceId,
    span_id: SpanId,
    attributes: HashMap<String, serde_json::Value>,
}

impl OTelSpan {
    fn from_json(json: &serde_json::Value) -> Self {
        let name = json.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let kind = SpanKindStr::from_json(json);
        let parent = ParentInfo::from_json(json);
        let trace_id = TraceId::from_json(json);
        let span_id = SpanId::from_json(json);
        let attributes: HashMap<_, _> = json.get("attributes")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|attr| {
                        if let Some(key) = attr.get("key").as_str() {
                            serde_json::from_value(attr.clone()).ok()
                        } else {
                            None
                        }
                    })
                    .collect()
            }).unwrap_or_default();

        OTelSpan {
            name,
            kind,
            parent,
            trace_id,
            span_id,
            attributes,
        }
    }
}

#[derive(Debug, Clone)]
enum SpanKindStr {
    Internal,
    Server,
    Client,
    Producer,
    Consumer,
    Unknown(String),
}

impl SpanKindStr {
    fn from_json(json: &serde_json::Value) -> Self {
        if let Some(kind) = json.get("kind").and_then(|v| v.as_str()) {
            match kind {
                "internal" => SpanKindStr::Internal,
                "server" => SpanKindStr::Server,
                "client" => SpanKindStr::Client,
                "producer" => SpanKindStr::Producer,
                "consumer" => SpanKindStr::Consumer,
                _ => SpanKind