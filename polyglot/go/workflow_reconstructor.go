// Package workflowreconstructor provides agentic workflow replay and audit capabilities.
// It uses OpenTelemetry GenAI semantic conventions to reconstruct agent decision trees
// from partial or fragmented telemetry data.
package workflowreconstructor

import (
	"encoding/json"
	"fmt"
	"slices"
	"sync"
	"time"

	"go.opentelemetry.io/otel/sdk/trace"
)

// SpanType identifies the category of an OTel span for GenAI operations.
type SpanType string

const (
	SpanTypeLLMRequest  = "gen_ai.request"
	SpanTypeLLMResponse = "gen_ai.response"
	SpanTypeToolCall    = "tool_call"
	SpanTypeDecision    = "agent.decision"
)

// LLMModel represents a model configuration from GenAI spans.
type LLMModel struct {
	Name      string            `json:"name"`
	Params    map[string]string `json:"params,omitempty"`
	Timestamp time.Time         `json:"timestamp"`
}

// ToolInvocation captures a tool call made during agent execution.
type ToolInvocation struct {
	Name       string          `json:"name"`
	Args       json.RawMessage  `json:"args,omitempty"`
	Result     json.RawMessage  `json:"result,omitempty"`
	DurationMs int64            `json:"duration_ms"`
	Timestamp  time.Time        `json:"timestamp"`
}

// DecisionNode represents a decision point in the agent workflow.
type DecisionNode struct {
	ID         string               `json:"id"`
	Type       string               `json:"type"` // "llm", "tool", "human_in_loop", etc.
	ParentID   *string              `json:"parent_id,omitempty"`
	Children   []*DecisionNode      `json:"children,omitempty"`
	Metadata   map[string]any       `json:"metadata,omitempty"`
	Timestamp  time.Time            `json:"timestamp"`
	DurationMs int64                `json:"duration_ms"`
	State      string               `json:"state"` // "success", "error", "pending"
}

// WorkflowGraph represents the reconstructed agent execution graph.
type WorkflowGraph struct {
	Root *DecisionNode
	Nodes map[string]*DecisionNode
	Edges map[[2]string]struct{}
	Metadata map[string]any
	Timestamp time.Time
}

// SpanData holds parsed OTel span information.
type SpanData struct {
	SpanID      string
	ParentSpanID string
	Name        string
	Type        SpanType
	Attributes  map[string]any
	DurationMs  int64
	Timestamp   time.Time
}

// ReconstructorConfig holds configuration for the workflow reconstructor.
type ReconstructorConfig struct {
	MaxDepth     int           // Maximum depth to traverse when reconstructing
	IncludeMeta  bool          // Include metadata in reconstruction
	Timeout      time.Duration // Timeout for long-running operations
	OnError      func(error)   // Callback for errors during reconstruction
}

// DefaultReconstructorConfig returns a sensible default configuration.
func DefaultReconstructorConfig() ReconstructorConfig {
	return ReconstructorConfig{
		MaxDepth: 10,
		IncludeMeta: true,
		Timeout: 30 * time.Second,
		OnError: func(err error) {
			fmt.Printf("reconstruction warning: %v\n", err)
		},
	}
}

// SpanReader interface for reading spans from various sources.
type SpanReader interface {
	Read() ([]SpanData, error)
	Close() error
}

// MemorySpanReader reads spans from an in-memory buffer.
type MemorySpanReader struct {
	spans []SpanData
	mu    sync.Mutex
}

func NewMemorySpanReader(spans []SpanData) *MemorySpanReader {
	return &MemorySpanReader{spans: spans}
}

func (m *MemorySpanReader) Read() ([]SpanData, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	result := make([]SpanData, len(m.spans))
	copy(result, m.spans)
	return result, nil
}

func (m *MemorySpanReader) Close() error { return nil }

// SpanParser extracts structured data from raw span attributes.
type SpanParser struct {
	config ReconstructorConfig
}

// NewSpanParser creates a new parser with optional configuration overrides.
func NewSpanParser(cfg ReconstructorConfig) *SpanParser {
	if cfg.MaxDepth == 0 {
		cfg.MaxDepth = DefaultReconstructorConfig().MaxDepth
	}
	return &SpanParser{config: cfg}
}

// ParseSpans converts raw OTel spans into a workflow graph.
func (p *SpanParser) ParseSpans(spans []SpanData, reader SpanReader) (*WorkflowGraph, error) {
	if len(spans) == 0 && reader != nil {
		var err error
		spans, err = reader.Read()
		if err != nil {
			return nil, fmt.Errorf("reading spans: %w", err)
		}
	}

	nodes := make(map[string]*DecisionNode)
	edges := make(map[[2]string]struct{})
	var root *DecisionNode

	for _, span := range spans {
		node, err := p.parseSpan(span, nodes)
		if err != nil {
			p.config.OnError(err)
			continue
		}
		nodes[span.SpanID] = node

		if root == nil && len(node.ParentID) > 0 {
			root = node
		} else if root != nil && len(span.ParentSpanID) > 0 {
			edges[[2]string{root.ID, span.SpanID}] = struct{}{}
		}
	}

	if root == nil && len(nodes) > 0 {
		// No explicit root found - use the first node as fallback
		for _, n := range nodes {
			root = n
			break
		}
	}

	return &WorkflowGraph{
		Root:     root,
		Nodes:    nodes,
		Edges:    edges,
		Metadata: make(map[string]any),
		Timestamp: time.Now(),
	}, nil
}

// parseSpan converts a single span into a decision node.
func (p *SpanParser) parseSpan(span SpanData, existingNodes map[string]*DecisionNode) (*DecisionNode, error) {
	node := &DecisionNode{
		ID:        span.SpanID,
		Type:      string(span.Type),
		Metadata:  make(map[string]any),
		Timestamp: span.Timestamp,
		DurationMs: span.DurationMs,
	}

	if len(span.ParentSpanID) > 0 {
		node.ParentID = &span.ParentSpanID
	}

	// Extract common GenAI attributes
	if span.Type == SpanTypeLLMRequest || span.Type == SpanTypeLLMResponse {
		model, ok := span.Attributes["gen_ai.request.model"].(string)
		if !ok && len(span.Attributes) > 0 {
			for k, v := range span.Attributes {
				if k == "model" || k == "name" {
					model = fmt.Sprintf("%v", v)
					break
				}
			}
		}

		if model != "" {
			node.Metadata["llm_model"] = LLMModel{
				Name:      model,
				Timestamp: span.Timestamp,
			}
		}

		if usage, ok := span.Attributes["gen_ai.usage.completion_tokens"].(float64); ok {
			node.Metadata["completion_tokens"] = int(useg)
		}
	}

	// Extract tool call information
	if span.Type == SpanTypeToolCall {
		if name, ok := span.Attributes["tool.name"].(string); ok {
			node.Metadata["tool_name"] = name
		}
	}

	// Extract decision type if applicable
	if span.Type == SpanTypeDecision {
		if reason, ok := span.Attributes["agent.decision.reason"].(string); ok {
			node.Metadata["decision_reason"] = reason
		}
	}

	return node, nil
}

// GraphTraverser handles traversal of the workflow graph.
type GraphTraverser struct {
	graph *WorkflowGraph
	config ReconstructorConfig
}

func NewGraphTraverser(g *WorkflowGraph, cfg ReconstructorConfig) *GraphTraverser {
	if cfg.MaxDepth == 0 {
		cfg.MaxDepth = DefaultReconstructorConfig().MaxDepth
	}
	return &GraphTraverser{graph: g, config: cfg}
}

// Traverse performs a depth-first traversal of the workflow graph.
func (t *GraphTraverser) Traverse() ([]*DecisionNode, error) {
	var result []*DecisionNode
	stack := []*DecisionNode{t.graph.Root}

	for len(stack) > 0 {
		current := stack[len(stack)-1]

		if current == nil || t.isVisited(current.ID) {
			stack = stack[:len(stack)-1]
			continue
		}

		result = append(result, current)

		// Add children to stack (reverse order for correct traversal)
		children := t.getChildren(current)
		for i := len(children) - 1; i >= 0; i-- {
			stack = append(stack, children[i])
		}
	}

	return result, nil
}

func (t *GraphTraverser) getChildren(parent *DecisionNode) []*DecisionNode {
	var children []*DecisionNode
	for _, child := range t.graph.Nodes {
		if parent.ParentID != nil && child.ID == *parent.ParentID {
			children = append(children, child)
		} else if parent.Type == SpanTypeLLMRequest || parent.Type == SpanTypeLLMResponse {
			// For LLM spans, look for response pairs
			for _, c := range t.graph.Nodes {
				if (parent.ID != "" && c.ParentID != nil && *c.ParentID == parent.ID) ||
					(parent.ID != "" && c.Type == SpanTypeLLMResponse && c.Metadata["llm_model"] == parent.Metadata["llm_model"]) {
					children = append(children, c)
				}
			}
		}
	}

	if len(children) > 0 {
		return children
	}

	// Fallback: use edges from the graph
	for _, edge := range t.graph.Edges {
		if edge[0] == parent.ID {
			childID, _ := edge[1].MarshalText()
			if child, ok := t.graph.Nodes[string(childID)]; ok {
				children = append(children, child)
			}
		}
	}

	return children
}

func (t *GraphTraverser) isVisited(id string) bool {
	for _, n := range t.graph.Nodes {
		if n.ID == id && n.Type != SpanTypeLLMResponse {
			return true
		}
	}
	return false
}

// Reconstructor handles the full reconstruction pipeline.
type Reconstructor struct {
	parser   *SpanParser
	traverser *GraphTraverser
	config   ReconstructorConfig
	mu       sync.Mutex
}

func NewReconstructor(cfg ReconstructorConfig) *Reconstructor {
	p := NewSpanParser(cfg)
	t := NewGraphTraverser(nil, cfg)
	return &Reconstructor{
		parser:   p,
		traverser: t,
		config:   cfg,
	}
}

// Reconstruct performs the full workflow reconstruction.
func (r *Reconstructor) Reconstruct(spans []SpanData, reader SpanReader) (*WorkflowGraph, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	return r.parser.ParseSpans(spans, reader)
}

// Replay simulates replaying the reconstructed workflow for verification.
func (r *Reconstructor) Replay(graph *WorkflowGraph) ([]string, error) {
	var log []string

	if graph.Root == nil {
		return log, fmt.Errorf("no root node found")
	}

	stack := []*DecisionNode{graph.Root}

	for len(stack) > 0 {
		current := stack[len(stack)-1]

		log = append(log, fmt.Sprintf(">> %s: %v", current.ID[:8], current.Type))

		if current.Type == SpanTypeLLMRequest || current.Type == SpanTypeLLMResponse {
			model, ok := current.Metadata["llm_model"].(LLMModel)
			if ok && model.Name != "" {
				log = append(log, fmt.Sprintf("    Model: %s", model.Name))
			}
		}

		stack = stack[:len(stack)-1]

		children := r.traverser.getChildren(current)
		for i := len(children) - 1; i >= 0; i-- {
			stack = append(stack, children[i])
		}
	}

	return log, nil
}

// Auditor generates an audit report for the reconstructed workflow.
type Auditor struct {
	graph *WorkflowGraph
	config ReconstructorConfig
}

func NewAuditor(g *WorkflowGraph, cfg ReconstructorConfig) *Auditor {
	return &Auditor{graph: g, config: cfg}
}

// Audit performs a comprehensive audit of the workflow.
func (a *Auditor) Audit() (*AuditReport, error) {
	report := &AuditReport{
		Timestamp: time.Now(),
		Version:   "1.0.0",
		Metadata:  make(map[string]any),
	}

	if a.graph.Root != nil {
		report.TotalNodes = len(a.graph.Nodes)
		report.TotalEdges = len(a.graph.Edges)
		report.DurationMs = int64(report.Timestamp.Sub(a.graph.Root.Timestamp).Milliseconds())
	} else {
		report.TotalNodes = 0
		report.TotalEdges = 0
	}

	// Count by type
	typeCounts := make(map[string]int)
	for _, node := range a.graph.Nodes {
		typeCounts[node.Type]++
	}
	report.TypeBreakdown = typeCounts

	// Calculate statistics
	totalDurationMs := int64(0)
	llmCalls := 0
	toolCalls := 0
	errors := 0

	for _, node := range a.graph.Nodes {
		if node.DurationMs > 0 {
			totalDurationMs += node.DurationMs
		}

		switch node.Type {
		case SpanTypeLLMRequest, SpanTypeLLMResponse:
			llmCalls++
		case SpanTypeToolCall:
			toolCalls++
		case SpanTypeDecision:
			if state, ok := node.Metadata["state"].(string); ok && state == "error" {
				errors++
			}
		}
	}

	report.Statistics = &WorkflowStatistics{
		TotalDurationMs: totalDurationMs,
		LLMCalls:        llmCalls,
		ToolCalls:       toolCalls,
		ErrorCount:      errors,
		AverageNodeSize: int64(totalDurationMs) / max(1, len(a.graph.Nodes)),
	}

	// Extract metadata from root if available
	if a.graph.Root != nil {
		for k, v := range a.graph.Root.Metadata {
			report.Metadata[k] = v
		}
	}

	return report, nil
}

// AuditReport represents the audit trail for a workflow execution.
type AuditReport struct {
	Timestamp   time.Time       `json:"timestamp"`
	Version     string          `json:"version"`
	Metadata    map[string]any  `json:"metadata,omitempty"`
	TotalNodes  int             `json:"total_nodes"`
	TotalEdges  int             `json:"total_edges"`
	DurationMs  int64           `json:"duration_ms"`
	TypeBreakdown map[string]int `json:"type_breakdown,omitempty"`
	Statistics   *WorkflowStatistics `json:"statistics,omitempty"`
}

// WorkflowStatistics contains computed statistics about the workflow.
type WorkflowStatistics struct {
	TotalDurationMs    int64  `json:"total_duration_ms"`
	LLMCalls           int    `json:"llm_calls"`
	ToolCalls          int    `json:"tool_calls"`
	ErrorCount         int    `json:"error_count"`
	AverageNodeSize    int64  `json:"average_node_size,omitempty"`
	MaxNodeDurationMs  int64  `json:"max_node_duration_ms,omitempty"`
	MinNodeDurationMs  int64  `json:"min_node_duration_ms,omitempty"`
}

// ReplayResult holds the result of a workflow replay.
type ReplayResult struct {
	Log         []string        `json:"log"`
	Timestamp   time.Time       `json:"timestamp"`
	DurationMs  int64           `json:"duration_ms"`
	Metadata    map[string]any  `json:"metadata,omitempty"`
	Success     bool            `json:"success"`
	Error       error           `json:"error,omitempty"`
}

// ReplayConfig holds configuration for replay operations.
type ReplayConfig struct {
	MaxDepth int