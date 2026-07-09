"""
polyglot/python/workflow_reconstructor.py

Agentic workflow replay & audit with OTel GenAI semantic conventions.

Reconstructs a coherent timeline of agent actions, prompts, responses, and tool calls
from OpenTelemetry spans. Includes smoke tests and runnable demo.
"""

import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Union
)

# =============================================================================
# CONSTANTS & CONFIGURATION
# =============================================================================

OTEL_GENAI_ATTR_PREFIX = "gen_ai."
GENAI_OPERATION_NAME = "operation.name"
GENAI_REQUEST_MODEL = "request.model"
GENAI_RESPONSE_ID = "response.id"
GENAI_USAGE_PROMPT_TOKENS = "usage.prompt_tokens"
GENAI_USAGE_COMPLETION_TOKENS = "usage.completion_tokens"

# Default OTel span attributes we care about
SPAN_ATTRS_TO_KEEP = {
    GENAI_OPERATION_NAME,
    GENAI_REQUEST_MODEL,
    GENAI_RESPONSE_ID,
    GENAI_USAGE_PROMPT_TOKENS,
    GENAI_USAGE_COMPLETION_TOKENS,
    "gen_ai.request.messages",  # Full message list if available
    "gen_ai.response.usage",
    "tool.name",
    "tool.input",
    "tool.output",
    "agent.step.id",
    "user.id",
}

# =============================================================================
# ENUMS & BASE TYPES
# =============================================================================

class SpanType(Enum):
    """Classify OTel spans by their semantic meaning."""
    
    LLM_GENERATE = "llm_generate"
    LLM_EMBEDDING = "llm_embedding"
    TOOL_CALL = "tool_call"
    AGENT_STEP = "agent_step"
    USER_ACTION = "user_action"
    SYSTEM_EVENT = "system_event"
    UNKNOWN = "unknown"


class OperationType(Enum):
    """OTel GenAI operation types."""
    
    GENERATE = "generate"
    EMBEDDING = "embedding"
    CHAT_COMPLETION = "chat_completion"
    COMPLETION = "completion"
    UNKNOWN = "unknown"


# =============================================================================
# DATA CLASSES - IMMUTABLE RECORDS
# =============================================================================

@dataclass(frozen=True)
class SpanContext:
    """Immutable context extracted from an OTel span."""
    
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0
    
    def __post_init__(self):
        if self.duration_ms == 0.0 and self.start_time and self.end_time:
            delta = (self.end_time - self.start_time).total_seconds() * 1000
            object.__setattr__(self, 'duration_ms', round(delta, 2))


@dataclass(frozen=True)
class LLMMessage:
    """Represents a single message in an LLM conversation."""
    
    role: str = ""
    content: str = ""
    name: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMMessage":
        return cls(
            role=data.get("role", ""),
            content=data.get("content", ""),
            name=data.get("name"),
        )


@dataclass(frozen=True)
class LLMRequest:
    """Represents an LLM request payload."""
    
    model: str = ""
    messages: List[LLMMessage] = field(default_factory=list)
    temperature: float = 0.7
    top_p: Optional[float] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLLMRequest":
        return cls(
            model=data.get("model", ""),
            messages=[LLMMessage.from_dict(m) for m in data.get("messages", [])],
            temperature=float(data.get("temperature", 0.7)),
            top_p=float(data.get("top_p")),
        )


@dataclass(frozen=True)
class LLMResponse:
    """Represents an LLM response payload."""
    
    id: str = ""
    model: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    choices: List[Dict[str, Any]] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLLMResponse":
        return cls(
            id=data.get("id", ""),
            model=data.get("model", ""),
            usage={k: int(v) for k, v in data.get("usage", {}).items()},
            choices=data.get("choices", []),
        )


@dataclass(frozen=True)
class ToolCallRecord:
    """Represents a tool invocation."""
    
    name: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Optional[str] = None
    duration_ms: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCallRecord":
        return cls(
            name=data.get("name", ""),
            input_data=data.get("input", {}),
            output_data=data.get("output"),
            duration_ms=float(data.get("duration_ms", 0)),
        )


@dataclass(frozen=True)
class AgentStep:
    """Represents a single step in the agent's reasoning process."""
    
    step_id: str = ""
    action_type: str = "unknown"
    description: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Optional[str] = None
    child_steps: List["AgentStep"] = field(default_factory=list)
    span_context: Optional[SpanContext] = None
    
    def add_child(self, child: "AgentStep") -> None:
        self.child_steps.append(child)


# =============================================================================
# SPAN PARSERS - EXTRACTING MEANING FROM RAW DATA
# =============================================================================

class SpanParser:
    """Parses OTel spans and extracts semantic meaning."""
    
    @staticmethod
    def create_span_context(span_data: Dict[str, Any]) -> Optional[SpanContext]:
        """Create a SpanContext from raw span data."""
        
        # Extract timestamps - handle various formats
        start_ns = span_data.get("startTime", 0)
        end_ns = span_data.get("endTime", 0)
        
        if start_ns and start_ns > 0:
            start_time = datetime.fromtimestamp(start_ns / 1e9, timezone.utc)
        else:
            start_time = datetime.now(timezone.utc)
            
        if end_ns and end_ns > 0:
            end_time = datetime.fromtimestamp(end_ns / 1e9, timezone.utc)
        else:
            end_time = datetime.now(timezone.utc)
        
        return SpanContext(
            trace_id=span_data.get("traceId", ""),
            span_id=span_data.get("spanId", ""),
            parent_span_id=span_data.get("parentSpanId"),
            start_time=start_time,
            end_time=end_time,
            duration_ms=(end_ns - start_ns) / 1e6 if (end_ns or start_ns) else 0.0,
        )
    
    @staticmethod
    def get_operation_type(span_data: Dict[str, Any]) -> OperationType:
        """Determine the GenAI operation type from span attributes."""
        
        op_name = span_data.get("attributes", {}).get(GENAI_OPERATION_NAME, "")
        
        if not op_name:
            return OperationType.UNKNOWN
        
        # Map OTel operation names to our types
        mapping = {
            "generate": OperationType.GENERATE,
            "chat_completion": OperationType.CHAT_COMPLETION,
            "completion": OperationType.COMPLETION,
            "embedding": OperationType.EMBEDDING,
            "embeddings": OperationType.EMBEDDING,
        }
        
        return mapping.get(op_name.lower(), OperationType.UNKNOWN)
    
    @staticmethod
    def get_span_type(span_data: Dict[str, Any], operation_type: OperationType) -> SpanType:
        """Classify the span into a higher-level type."""
        
        # Check for tool calls first (they have distinct attributes)
        if "tool.name" in span_data.get("attributes", {}):
            return SpanType.TOOL_CALL
        
        # LLM operations
        if operation_type in (OperationType.GENERATE, OperationType.CHAT_COMPLETION, 
                             OperationType.COMPLETION):
            return SpanType.LLM_GENERATE
        
        if operation_type == OperationType.EMBEDDING:
            return SpanType.LLM_EMBEDDING
        
        # Check for agent-specific attributes
        agent_attrs = {k: v for k, v in span_data.get("attributes", {}).items() 
                      if "agent" in k.lower()}
        
        if agent_attrs:
            return SpanType.AGENT_STEP
        
        # Check for user/system actions
        if "user.id" in span_data.get("attributes", {}):
            return SpanType.USER_ACTION
        
        return SpanType.UNKNOWN
    
    @staticmethod
    def extract_llm_request(span_data: Dict[str, Any]) -> Optional[LLMRequest]:
        """Extract the LLM request payload from span attributes."""
        
        attrs = span_data.get("attributes", {})
        
        # Try to find messages in various attribute locations
        message_attrs = [
            GENAI_REQUEST_MODEL,  # Model name
            "gen_ai.request.messages",  # Full messages list
            "messages",  # Fallback
        ]
        
        model = attrs.get(GENAI_REQUEST_MODEL, "")
        
        # Extract messages if available
        messages_data = None
        
        for attr in ["gen_ai.request.messages", "messages"]:
            if attr in attrs:
                messages_data = attrs[attr]
                break
        
        if not messages_data:
            return LLMRequest(model=model)
        
        try:
            # Parse messages - handle both stringified JSON and list format
            if isinstance(messages_data, str):
                messages_data = json.loads(messages_data)
            
            request = LLMRequest(
                model=model,
                messages=[LLMMessage.from_dict(m) for m in messages_data],
            )
        except (json.JSONDecodeError, TypeError):
            return LLMRequest(model=model)
        
        return request
    
    @staticmethod
    def extract_llm_response(span_data: Dict[str, Any]) -> Optional[LLMResponse]:
        """Extract the LLM response payload from span attributes."""
        
        attrs = span_data.get("attributes", {})
        
        # Try to find response data in various locations
        response_attrs = [
            GENAI_RESPONSE_ID,  # Response ID
            "gen_ai.response.id",
            "response.id",
            "choices",  # Choices array
            "usage",  # Token usage
        ]
        
        response_id = attrs.get(GENAI_RESPONSE_ID, "")
        model = attrs.get("gen_ai.request.model", "")
        
        # Extract token usage if available
        usage_data = attrs.get("gen_ai.response.usage") or {}
        if isinstance(usage_data, str):
            try:
                usage_data = json.loads(usage_data)
            except (json.JSONDecodeError, TypeError):
                pass
        
        return LLMResponse(
            id=response_id,
            model=model,
            usage={k: int(v) for k, v in usage_data.items() if isinstance(v, (int, float))},
        )
    
    @staticmethod
    def extract_tool_call(span_data: Dict[str, Any]) -> Optional[ToolCallRecord]:
        """Extract a tool call record from span attributes."""
        
        attrs = span_data.get("attributes", {})
        
        # Check for tool-specific attributes
        if "tool.name" not in attrs:
            return None
        
        name = attrs["tool.name"]
        input_data = {}
        output_data = None
        duration_ms = 0.0
        
        # Extract tool input/output if available
        for attr_key in ["tool.input", "input"]:
            if attr_key in attrs and attrs[attr_key]:
                try:
                    input_data = json.loads(attrs[attr_key])
                except (json.JSONDecodeError, TypeError):
                    pass
        
        for attr_key in ["tool.output", "output"]:
            if attr_key in attrs and attrs[attr_key]:
                output_data = attrs[attr_key]
        
        # Extract duration from span context
        if span_data.get("duration_ms"):
            duration_ms = float(span_data["duration_ms"]) / 1000.0
        
        return ToolCallRecord(
            name=name,
            input_data=input_data,
            output_data=output_data,
            duration_ms=duration_ms,
        )


# =============================================================================
# WORKFLOW NODE - LOGICAL BUILDING BLOCKS
# =============================================================================

class WorkflowNode:
    """A logical node in the reconstructed workflow."""
    
    def __init__(self, 
                 node_id: str,
                 span_context: SpanContext,
                 span_type: SpanType = SpanType.UNKNOWN,
                 operation_type: OperationType = OperationType.UNKNOWN):
        self.node_id = node_id
        self.span_context = span_context
        self.span_type = span_type
        self.operation_type = operation_type
        
        # Extracted semantic data
        self.llm_request: Optional[LLMRequest] = None
        self.llm_response: Optional[LLMResponse] = None
        self.tool_call: Optional[ToolCallRecord] = None
        self.agent_step: Optional[AgentStep] = None
        
        # Relationships
        self.parent_node_id: Optional[str] = None
        self.child_nodes: List["WorkflowNode"] = []
        
    def add_child(self, child: "WorkflowNode") -> None:
        """Add a child node to this workflow."""
        child.parent_node_id = self.node_id
        self.child_nodes.append(child)
    
    def get_descendants(self) -> List["WorkflowNode"]:
        """Get all descendant nodes recursively."""
        result = [self]
        for child in self.child_nodes:
            result.extend(child.get_descendants())
        return result
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert this node to a dictionary representation."""
        
        data = {
            "node_id": self.node_id,
            "span_context": asdict(self.span_context),
            "span_type": self.span_type.value,
            "operation_type": self.operation_type.value,
            "llm_request": asdict(self.llm_request) if self.llm_request else None,
            "llm_response": asdict(self.llm_response) if self.llm_response else None,
            "tool_call": asdict(self.tool_call) if self.tool_call else None,
            "agent_step": asdict(self.agent_step) if self.agent_step else None,
            "parent_node_id": self.parent_node_id,
        }
        
        # Add child nodes
        data["child_nodes"] = [n.to_dict() for n in self.child_nodes]
        
        return data
    
    def __repr__(self):
        type_str = self.span_type.value.replace("_", " ").title()
        op_str = self.operation_type.value.upper() if self.operation_type != OperationType.UNKNOWN else "?"
        return f"WorkflowNode({type_str}, {op_str})"


# =============================================================================
# WORKFLOW RECONSTRUCTOR - MAIN CLASS
# =============================================================================

class WorkflowReconstructor:
    """
    Reconstructs a coherent workflow timeline from OTel spans.
    
    This class parses raw span data, extracts semantic meaning using
    OTel GenAI conventions, and builds a hierarchical representation
    of the agent's execution flow.
    """
    
    def __init__(self):
        self.nodes: List[WorkflowNode] = []
        self.root_nodes: List[WorkflowNode] = []
        self.span_index: Dict[str, WorkflowNode] = {}  # trace_id -> node mapping
        
    def add_span(self, span_data: Dict[str, Any]) -> None:
        """Add a raw span to the reconstruction pipeline."""
        
        # Create context for this span
        context = SpanParser.create_span_context(span_data)
        if not context:
            return
        
        # Determine operation and span type
        operation_type = SpanParser.get_operation_type(span_data)
        span_type = SpanParser.get_span_type(span_data, operation_type)
        
        # Create a workflow node for this span
        node_id = f"{context.trace_id}_{context.span_id}"