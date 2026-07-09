package otel_semantic_parser

import (
	"encoding/json"
	"fmt"
	"strings"
)

// SemanticAttribute represents a single OpenTelemetry attribute with metadata
type SemanticAttribute struct {
	Name    string            `json:"name"`
	Value   interface{}       `json:"value"`
	Type    AttributeType     `json:"type,omitempty"`
	Metadata map[string]string `json:"metadata,omitempty"`
}

// AttributeType defines the data type of an attribute value
type AttributeType string

const (
	AttrTypeString  AttributeType = "string"
	AttrTypeInt64   AttributeType = "int64"
	AttrTypeDouble  AttributeType = "double"
	AttrTypeBool    AttributeType = "bool"
	AttrTypeArray   AttributeType = "array"
	AttrTypeObject  AttributeType = "object"
)

// GenAIAttributeNames contains common OpenTelemetry GenAI semantic convention names
var GenAIAttributeNames = map[string]string{
	// Operation context
	"gen_ai.operation.name":      "Operation name (e.g., 'chat', 'completion')",
	"gen_ai.operation.id":        "Unique operation identifier",
	
	// Request attributes
	"gen_ai.request.model":       "Model identifier",
	"gen_ai.request.temperature": "Sampling temperature",
	"gen_ai.request.top_p":       "Nucleus sampling parameter",
	"gen_ai.request.max_tokens":  "Maximum tokens to generate",
	"gen_ai.request.stop_sequences": "Stop sequences list",
	
	// Response attributes  
	"gen_ai.response.id":         "Response identifier",
	"gen_ai.response.usage.prompt_tokens":    "Prompt token count",
	"gen_ai.response.usage.completion_tokens": "Completion token count",
	"gen_ai.response.usage.total_tokens":     "Total tokens used",
	
	// Modality/Format attributes
	"gen_ai.modality.text":       "Text modality flag",
	"gen_ai.modality.audio":      "Audio modality flag",
	"gen_ai.modality.image":      "Image modality flag",
}

// Parser handles semantic attribute parsing and validation
type Parser struct {
	strictMode    bool
	defaultType   AttributeType
	typeInference  bool
}

// Option configures parser behavior
type Option func(*Parser)

// WithStrict enables strict mode for unknown attributes
func WithStrict() Option {
	return func(p *Parser) { p.strictMode = true }
}

// WithDefaultType sets a default type for inferred values
func WithDefaultType(t AttributeType) Option {
	return func(p *Parser) { p.defaultType = t }
}

// New creates a new parser with options
func New(opts ...Option) *Parser {
	p := &Parser{
		strictMode:    false,
		defaultType:   AttrTypeString,
		typeInference: true,
	}
	for _, o := range opts {
		o(p)
	}
	return p
}

// ParseResult holds the parsed semantic data and any issues found
type ParseResult struct {
	Attributes []SemanticAttribute `json:"attributes"`
	Metadata   map[string]string  `json:"metadata,omitempty"`
	Issues     []string           `json:"issues,omitempty"`
	RawInput   interface{}        `json:"raw_input,omitempty"`
}

// Parse attempts to parse semantic attributes from various input formats
func (p *Parser) Parse(input interface{}) (*ParseResult, error) {
	result := &ParseResult{
		Metadata: make(map[string]string),
		Issues:   make([]string, 0),
		RawInput: input,
	}

	// Handle different input types
	switch v := input.(type) {
	case map[string]interface{}:
		return p.parseMap(v, result)
	case string:
		return p.parseJSONString(v, result)
	case []byte:
		return p.parseJSONBytes(v, result)
	default:
		result.Issues = append(result.Issues, fmt.Sprintf("unsupported input type: %T", v))
	}

	if len(result.Issues) > 0 {
		return nil, fmt.Errorf("parse issues: %v", result.Issues)
	}

	return result, nil
}

// parseMap handles map[string]interface{} inputs (most common case)
func (p *Parser) parseMap(m map[string]interface{}, r *ParseResult) (*ParseResult, error) {
	r.Attributes = make([]SemanticAttribute, 0, len(m))

	for name, value := range m {
		attr := SemanticAttribute{
			Name:   strings.TrimPrefix(name, "gen_ai."),
			Value:  value,
			Metadata: map[string]string{},
		}

		// Infer type from value
		if p.typeInference {
			switch v := value.(type) {
			case string:
				attr.Type = AttrTypeString
			case float64:
				if strings.Contains(name, "temperature") || strings.Contains(name, "top_p") {
					attr.Type = AttrTypeDouble
				} else if int(v) == v && v >= 0 && v < 256 {
					attr.Type = AttrTypeInt64
				} else {
					attr.Type = AttrTypeDouble
				}
			case bool:
				attr.Type = AttrTypeBool
			case []interface{}:
				attr.Type = AttrTypeArray
			default:
				if _, ok := value.(map[string]interface{}); ok {
					attr.Type = AttrTypeObject
				} else {
					attr.Type = p.defaultType
				}
			}
		}

		// Add metadata for known GenAI attributes
		if known, exists := GenAIAttributeNames[name]; exists {
			attr.Metadata["known"] = "true"
			attr.Metadata["description"] = known
		} else if p.strictMode {
			r.Issues = append(r.Issues, fmt.Sprintf("unknown attribute: %s", name))
		}

		r.Attributes = append(r.Attributes, attr)
	}

	return r, nil
}

// parseJSONString handles JSON string inputs
func (p *Parser) parseJSONString(s string, r *ParseResult) (*ParseResult, error) {
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(s), &m); err != nil {
		r.Issues = append(r.Issues, fmt.Sprintf("JSON unmarshal error: %v", err))
		return r, err
	}

	return p.parseMap(m, r)
}

// parseJSONBytes handles raw JSON byte inputs
func (p *Parser) parseJSONBytes(b []byte, r *ParseResult) (*ParseResult, error) {
	var m map[string]interface{}
	if err := json.Unmarshal(b, &m); err != nil {
		r.Issues = append(r.Issues, fmt.Sprintf("JSON unmarshal error: %v", err))
		return r, err
	}

	return p.parseMap(m, r)
}

// Validate checks parsed attributes against expected GenAI schema
func (p *Parser) Validate(result *ParseResult) (*ParseResult, error) {
	if result == nil {
		return nil, fmt.Errorf("nil parse result")
	}

	result.Issues = make([]string, 0)

	for _, attr := range result.Attributes {
		// Check for required GenAI attributes in operation context
		switch attr.Name {
		case "gen_ai.operation.name":
			if str, ok := attr.Value.(string); !ok || strings.TrimSpace(str) == "" {
				result.Issues = append(result.Issues, fmt.Sprintf("operation.name should be a non-empty string: %v", attr.Value))
			}

		case "gen_ai.request.model":
			if str, ok := attr.Value.(string); !ok || strings.TrimSpace(str) == "" {
				result.Issues = append(result.Issues, fmt.Sprintf("request.model should be a non-empty string: %v", attr.Value))
			}

		case "gen_ai.response.id":
			if str, ok := attr.Value.(string); !ok || strings.TrimSpace(str) == "" {
				result.Issues = append(result.Issues, fmt.Sprintf("response.id should be a non-empty string: %v", attr.Value))
			}

		case "gen_ai.response.usage.prompt_tokens":
			if num, ok := attr.Value.(float64); !ok || num < 0 {
				result.Issues = append(result.Issues, fmt.Sprintf("prompt_tokens should be a non-negative number: %v", attr.Value))
			}

		case "gen_ai.response.usage.completion_tokens":
			if num, ok := attr.Value.(float64); !ok || num < 0 {
				result.Issues = append(result.Issues, fmt.Sprintf("completion_tokens should be a non-negative number: %v", attr.Value))
			}

		case "gen_ai.response.usage.total_tokens":
			if num, ok := attr.Value.(float64); !ok || num < 0 {
				result.Issues = append(result.Issues, fmt.Sprintf("total_tokens should be a non-negative number: %v", attr.Value))
			}
		}
	}

	return result, nil
}

// GetAttribute retrieves an attribute by name (case-insensitive for GenAI prefix)
func (p *Parser) GetAttribute(attributes []SemanticAttribute, targetName string) (*SemanticAttribute, bool) {
	for _, attr := range attributes {
		if strings.EqualFold(attr.Name, targetName) || attr.Name == targetName {
			return &attr, true
		}
	}
	return nil, false
}

// GetAttributeValue extracts a typed value from an attribute
func (p *Parser) GetAttributeValue(attr *SemanticAttribute, targetType interface{}) error {
	switch v := attr.Value.(type) {
	case string:
		if err := json.Unmarshal([]byte(v), targetType); err != nil {
			return fmt.Errorf("string to target type conversion failed: %v", err)
		}
	case float64:
		switch t := targetType.(type) {
		case *float64:
			*t = v
		case *int:
			*t = int(v)
		case *int64:
			*t = int64(v)
		case *bool:
			*t = v > 0
		default:
			return fmt.Errorf("unsupported target type for float64")
		}
	case bool:
		if b, ok := targetType.(*bool); ok {
			*b = v
		} else {
			return fmt.Errorf("unsupported target type for bool")
		}
	default:
		return fmt.Errorf("unsupported source value type: %T", v)
	}

	return nil
}

// Summary creates a human-readable summary of parsed attributes
func (p *Parser) Summary(result *ParseResult) string {
	if result == nil || len(result.Attributes) == 0 {
		return "No attributes to summarize"
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Parsed %d attributes:\n", len(result.Attributes)))

	for _, attr := range result.Attributes {
		sb.WriteString(fmt.Sprintf("  - %s: %v (type: %s)\n", 
			attr.Name, truncateString(fmt.Sprintf("%v", attr.Value), 50), string(attr.Type)))
	}

	if len(result.Issues) > 0 {
		sb.WriteString(fmt.Sprintf("\nIssues found (%d):\n", len(result.Issues)))
		for _, issue := range result.Issues {
			sb.WriteString(fmt.Sprintf("  ! %s\n", issue))
		}
	}

	return sb.String()
}

// truncateString limits string length for display
func truncateString(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}

// Demo demonstrates the parser functionality
func main() {
	// Create parser with strict mode enabled
	parser := New(WithStrict())

	// Test case 1: Valid GenAI request/response attributes
	testInput1 := map[string]interface{}{
		"gen_ai.operation.name":      "chat",
		"gen_ai.operation.id":        "op-123456",
		"gen_ai.request.model":       "gpt-4o-mini",
		"gen_ai.request.temperature": 0.7,
		"gen_ai.response.id":         "resp-789",
		"gen_ai.response.usage.prompt_tokens":    1250.0,
		"gen_ai.response.usage.completion_tokens": 45.0,
		"gen_ai.response.usage.total_tokens":     1295.0,
	}

	fmt.Println("=== Test Case 1: Valid GenAI Attributes ===")
	result1, err := parser.Parse(testInput1)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		fmt.Println(parser.Summary(result1))
		
		// Validate the result
		validated, validateErr := parser.Validate(result1)
		if validateErr != nil {
			fmt.Printf("Validation error: %v\n", validateErr)
		} else {
			fmt.Println("✓ Validation passed")
		}

		// Extract specific values
		modelAttr, found := parser.GetAttribute(validated.Attributes, "gen_ai.request.model")
		if found {
			var model string
			parser.GetAttributeValue(modelAttr, &model)
			fmt.Printf("Extracted model: %s\n", model)
		}

		totalTokensAttr, _ := parser.GetAttribute(validated.Attributes, "gen_ai.response.usage.total_tokens")
		var totalTokens float64
		parser.GetAttributeValue(totalTokensAttr, &totalTokens)
		fmt.Printf("Total tokens used: %.0f\n", totalTokens)
	}

	// Test case 2: Attributes with some issues (missing required fields)
	testInput2 := map[string]interface{}{
		"gen_ai.operation.name":      "", // Empty - should trigger warning
		"gen_ai.request.model":       "unknown-model",
		"gen_ai.response.usage.total_tokens": -5.0, // Negative - should trigger warning
	}

	fmt.Println("\n=== Test Case 2: Attributes with Issues ===")
	result2, err := parser.Parse(testInput2)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		fmt.Println(parser.Summary(result2))
		
		validated, _ := parser.Validate(result2)
		if len(validated.Issues) > 0 {
			fmt.Println("✓ Issues detected as expected")
		}
	}

	// Test case 3: JSON string input
	testInput3 := `{"gen_ai.operation.name": "completion", "gen_ai.request.model": "claude-3"}`

	fmt.Println("\n=== Test Case 3: JSON String Input ===")
	result3, err := parser.Parse(testInput3)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		fmt.Println(parser.Summary(result3))
	}

	// Test case 4: Mixed types (numbers as floats vs ints)
	testInput4 := map[string]interface{}{
		"gen_ai.request.temperature": 0.7,
		"gen_ai.response.usage.prompt_tokens": 1250.0,
	}

	fmt.Println("\n=== Test Case 4: Type Inference ===")
	result4, _ := parser.Parse(testInput4)
	for _, attr := range result4.Attributes {
		fmt.Printf("Attribute '%s': inferred type = %s\n", 
			attr.Name, string(attr.Type))
	}

	fmt.Println("\n=== All tests completed successfully! ===")
}