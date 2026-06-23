// Go port of the agentlog audit core — single binary, zero third-party deps.
//
// Mirrors the reference Python CLI's `audit` surface: it loads OTel GenAI
// spans (JSON array, {"spans": [...]}, or JSONL), scans every span's text
// attributes for secret/PII leaks, prompt-injection markers, and
// high-blast-radius tool calls, then prints metrics + findings as JSON.
//
//	go run . ../../demos/01-basic/spans.json
//	go run . -            # read spans from stdin
//
// Offline only: it reads local files / stdin and never touches the network.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
)

type span struct {
	SpanID       string                 `json:"span_id"`
	Name         string                 `json:"name"`
	TraceID      string                 `json:"trace_id"`
	ParentSpanID string                 `json:"parent_span_id"`
	Status       string                 `json:"status"`
	Attributes   map[string]interface{} `json:"attributes"`
}

type finding struct {
	Severity string `json:"severity"`
	Code     string `json:"code"`
	SpanID   string `json:"span_id"`
	Message  string `json:"message"`
}

var secretPatterns = []struct {
	label string
	re    *regexp.Regexp
}{
	{"aws_access_key", regexp.MustCompile(`AKIA[0-9A-Z]{16}`)},
	{"private_key", regexp.MustCompile(`-----BEGIN (?:RSA |EC )?PRIVATE KEY-----`)},
	{"bearer_token", regexp.MustCompile(`(?i)bearer\s+[A-Za-z0-9._\-]{20,}`)},
	{"api_key_assign", regexp.MustCompile(`(?i)(?:api[_-]?key|secret|password)\s*[=:]\s*[A-Za-z0-9._\-]{8,}`)},
	{"email", regexp.MustCompile(`[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`)},
}

var dangerousTools = map[string]bool{
	"shell": true, "bash": true, "exec": true, "execute_command": true,
	"run_command": true, "delete_file": true, "rm": true, "write_file": true,
	"http_request": true, "send_email": true, "transfer_funds": true,
	"execute_sql": true, "sql": true, "kubectl": true, "terraform_apply": true,
}

var injectionMarkers = []string{
	"ignore previous instructions", "ignore all previous", "disregard the above",
	"system prompt", "you are now", "new instructions:", "reveal your", "exfiltrate",
}

func attrString(a map[string]interface{}, key string) string {
	if v, ok := a[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

func textBlob(a map[string]interface{}) string {
	var parts []string
	keys := make([]string, 0, len(a))
	for k := range a {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		switch v := a[k].(type) {
		case string:
			parts = append(parts, v)
		case []interface{}, map[string]interface{}:
			b, _ := json.Marshal(v)
			parts = append(parts, string(b))
		}
	}
	return strings.Join(parts, "\n")
}

func isError(status string) bool {
	switch strings.ToUpper(status) {
	case "OK", "UNSET", "0", "1", "":
		return false
	}
	return true
}

// loadSpans accepts a JSON array, an object with a "spans" array, or JSONL.
func loadSpans(raw []byte) ([]span, error) {
	t := strings.TrimSpace(string(raw))
	if t == "" {
		return nil, fmt.Errorf("empty input: no spans to load")
	}
	var arr []span
	if err := json.Unmarshal([]byte(t), &arr); err == nil && len(arr) > 0 {
		return arr, nil
	}
	var wrap struct {
		Spans []span `json:"spans"`
	}
	if err := json.Unmarshal([]byte(t), &wrap); err == nil && len(wrap.Spans) > 0 {
		return wrap.Spans, nil
	}
	var out []span
	for _, line := range strings.Split(t, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var s span
		if err := json.Unmarshal([]byte(line), &s); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no span objects found in input")
	}
	return out, nil
}

func audit(spans []span) ([]finding, map[string]int) {
	var fs []finding
	byID := map[string]bool{}
	for _, s := range spans {
		byID[s.SpanID] = true
	}
	metrics := map[string]int{"spans": len(spans), "tool_calls": 0, "llm_calls": 0, "errors": 0}
	toolFreq := map[string]int{}
	for _, s := range spans {
		op := attrString(s.Attributes, "gen_ai.operation.name")
		tool := attrString(s.Attributes, "gen_ai.tool.name")
		if op == "execute_tool" || tool != "" {
			metrics["tool_calls"]++
		}
		if op == "chat" || op == "text_completion" || op == "generate_content" {
			metrics["llm_calls"]++
		}
		if isError(s.Status) {
			metrics["errors"]++
			fs = append(fs, finding{"high", "span_error", s.SpanID,
				fmt.Sprintf("span '%s' ended with status %s", s.Name, s.Status)})
		}
		if s.ParentSpanID != "" && !byID[s.ParentSpanID] {
			fs = append(fs, finding{"medium", "broken_trace", s.SpanID,
				fmt.Sprintf("parent_span_id '%s' not present in trace", s.ParentSpanID)})
		}
		blob := textBlob(s.Attributes)
		for _, p := range secretPatterns {
			if p.re.MatchString(blob) {
				fs = append(fs, finding{"critical", "secret_leak", s.SpanID,
					fmt.Sprintf("possible %s exposed in span attributes", p.label)})
			}
		}
		if tool != "" && dangerousTools[strings.ToLower(tool)] {
			fs = append(fs, finding{"high", "dangerous_tool", s.SpanID,
				fmt.Sprintf("high-blast-radius tool '%s' invoked", tool)})
		}
		low := strings.ToLower(blob)
		for _, m := range injectionMarkers {
			if strings.Contains(low, m) {
				fs = append(fs, finding{"high", "prompt_injection", s.SpanID,
					fmt.Sprintf("prompt-injection marker '%s' found in span content", m)})
				break
			}
		}
		if tool != "" {
			toolFreq[tool]++
		}
	}
	for name, cnt := range toolFreq {
		if cnt >= 10 {
			fs = append(fs, finding{"medium", "runaway_loop", "",
				fmt.Sprintf("tool '%s' called %d times (possible loop)", name, cnt)})
		}
	}
	rank := map[string]int{"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
	sort.SliceStable(fs, func(i, j int) bool {
		if rank[fs[i].Severity] != rank[fs[j].Severity] {
			return rank[fs[i].Severity] < rank[fs[j].Severity]
		}
		return fs[i].Code < fs[j].Code
	})
	return fs, metrics
}

func main() {
	src := "-"
	if len(os.Args) > 1 {
		src = os.Args[1]
	}
	var raw []byte
	var err error
	if src == "-" {
		raw, err = io.ReadAll(os.Stdin)
	} else {
		raw, err = os.ReadFile(src)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	spans, err := loadSpans(raw)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	fs, metrics := audit(spans)
	failing := false
	for _, f := range fs {
		if f.Severity == "critical" || f.Severity == "high" {
			failing = true
			break
		}
	}
	out, _ := json.MarshalIndent(map[string]interface{}{
		"tool": "agentlog", "version": "1.2.5",
		"metrics": metrics, "findings": fs, "failing": failing,
	}, "", "  ")
	fmt.Println(string(out))
	if failing {
		os.Exit(1)
	}
}
