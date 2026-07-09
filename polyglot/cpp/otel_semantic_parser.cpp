#include <iostream>
#include <string>
#include <vector>
#include <map>
#include <variant>
#include <optional>
#include <sstream>
#include <iomanip>
#include <algorithm>
#include <cctype>
#include <memory>
#include <functional>

namespace polyglot {
namespace cpp {
namespace otel_semantic_parser {

// ============================================================================
// Forward declarations and type definitions
// ============================================================================

struct SpanAttributes;

class SemanticParser;

// Represents a single OTel span with its attributes
struct SpanAttributes {
    std::string operation_name;      // gen_ai.operation.name
    std::string system;              // gen_ai.system
    std::string model;               // gen_ai.request.model
    int64_t input_tokens = 0;        // gen_ai.usage.input_tokens
    int64_t output_tokens = 0;       // gen_ai.usage.output_tokens
    int64_t total_tokens = 0;        // gen_ai.usage.total_tokens
    std::string response_id;         // gen_ai.response.id
    double latency_ms = 0.0;         // gen_ai.request.duration (ms)
    
    bool has_input = false;
    bool has_output = false;
    bool has_response_id = false;
};

// ============================================================================
// Utility: Zero-copy string helpers
// ============================================================================

inline std::string_view trim(std::string_view sv) {
    auto start = sv.find_first_not_of(" \t\r\n");
    if (start == std::string_view::npos) return {};
    
    auto end = sv.find_last_not_of(" \t\r\n");
    return sv.substr(start, end - start + 1);
}

inline bool startsWith(std::string_view str, std::string_view prefix) {
    if (prefix.size() > str.size()) return false;
    return str.compare(0, prefix.size(), prefix) == 0;
}

// ============================================================================
// Attribute extraction helpers
// ============================================================================

template<typename T>
inline bool getAttribute(const SpanAttributes& attrs, const std::string& key, 
                         T& out_value) {
    // This would be called with actual attribute maps in production
    return false;  // Placeholder - real impl reads from span context
}

// ============================================================================
// Semantic Parser Core
// ============================================================================

class SemanticParser {
public:
    using AttributesMap = std::map<std::string, std::variant<int64_t, double, 
                                                              std::string, bool>>;
    
    // Parse a single OTel span and extract semantic fields
    static SpanAttributes parse(const AttributesMap& attrs) {
        SpanAttributes result;
        
        // Helper lambda to get string value from variant
        auto getString = [](const AttributesMap& m, const std::string& key) -> std::optional<std::string> {
            if (m.find(key) == m.end()) return std::nullopt;
            
            auto it = m.find(key);
            if (auto* str_ptr = std::get_if<std::string>(&it->second)) {
                return *str_ptr;
            } else if (auto* s_ptr = std::get_if<std::string_view>(&it->second)) {
                return std::string(*s_ptr);
            }
            return std::nullopt;
        };
        
        // Helper lambda to get numeric value
        auto getDouble = [](const AttributesMap& m, const std::string& key) -> std::optional<double> {
            if (m.find(key) == m.end()) return std::nullopt;
            
            auto it = m.find(key);
            if (auto* d_ptr = std::get_if<double>(&it->second)) {
                return *d_ptr;
            } else if (auto* i_ptr = std::get_if<int64_t>(&it->second)) {
                return static_cast<double>(*i_ptr);
            }
            return std::nullopt;
        };
        
        // Helper lambda to get int value
        auto getInt = [](const AttributesMap& m, const std::string& key) -> std::optional<int64_t> {
            if (m.find(key) == m.end()) return std::nullopt;
            
            auto it = m.find(key);
            if (auto* i_ptr = std::get_if<int64_t>(&it->second)) {
                return *i_ptr;
            } else if (auto* d_ptr = std::get_if<double>(&it->second)) {
                return static_cast<int64_t>(*d_ptr);
            }
            return std::nullopt;
        };
        
        // Extract core semantic fields with proper OTel convention names
        
        // gen_ai.operation.name (required for LLM operations)
        if (auto op = getString(attrs, "gen_ai.operation.name")) {
            result.operation_name = *op;
        } else if (auto op = getString(attrs, "operation.name")) {
            // Fallback to generic operation name
            result.operation_name = *op;
        }
        
        // gen_ai.system (model provider)
        if (auto sys = getString(attrs, "gen_ai.system")) {
            result.system = *sys;
        } else if (auto sys = getString(attrs, "ai.system")) {
            // Legacy/alternative convention
            result.system = *sys;
        }
        
        // gen_ai.request.model (specific model)
        if (auto mdl = getString(attrs, "gen_ai.request.model")) {
            result.model = *mdl;
        } else if (auto mdl = getString(attrs, "request.model")) {
            // Fallback
            result.model = *mdl;
        }
        
        // gen_ai.usage.input_tokens
        if (auto inp = getInt(attrs, "gen_ai.usage.input_tokens")) {
            result.input_tokens = *inp;
            result.has_input = true;
        } else if (auto inp = getDouble(attrs, "gen_ai.usage.input_tokens")) {
            result.input_tokens = static_cast<int64_t>(*inp);
            result.has_input = true;
        }
        
        // gen_ai.usage.output_tokens  
        if (auto outp = getInt(attrs, "gen_ai.usage.output_tokens")) {
            result.output_tokens = *outp;
            result.has_output = true;
        } else if (auto outp = getDouble(attrs, "gen_ai.usage.output_tokens")) {
            result.output_tokens = static_cast<int64_t>(*outp);
            result.has_output = true;
        }
        
        // gen_ai.usage.total_tokens
        if (auto totl = getInt(attrs, "gen_ai.usage.total_tokens")) {
            result.total_tokens = *totl;
        } else if (auto totl = getDouble(attrs, "gen_ai.usage.total_tokens")) {
            result.total_tokens = static_cast<int64_t>(*totl);
        }
        
        // gen_ai.response.id (for tracing/correlation)
        if (auto rid = getString(attrs, "gen_ai.response.id")) {
            result.response_id = *rid;
            result.has_response_id = true;
        } else if (auto rid = getString(attrs, "response.id")) {
            // Fallback
            result.response_id = *rid;
            result.has_response_id = true;
        }
        
        // gen_ai.request.duration or similar for latency
        if (auto dur = getDouble(attrs, "gen_ai.request.duration")) {
            result.latency_ms = *dur / 1000.0;  // Convert from seconds to ms
        } else if (auto dur = getInt(attrs, "gen_ai.request.duration")) {
            result.latency_ms = static_cast<double>(*dur);
        }
        
        return result;
    }
    
    // Parse a batch of spans and aggregate statistics
    static std::map<std::string, int64_t> parseBatch(const AttributesMap& attrs) {
        auto result = parse(attrs);
        
        std::map<std::string, int64_t> stats;
        
        if (result.has_input) {
            stats["input_tokens"] += result.input_tokens;
        }
        if (result.has_output) {
            stats["output_tokens"] += result.output_tokens;
        }
        if (!result.response_id.empty()) {
            stats["spans_with_response_id"]++;
        }
        
        return stats;
    }
    
    // Validate parsed span for completeness
    static bool validate(const SpanAttributes& attrs) {
        // Critical fields that should be present
        std::vector<std::string> critical_fields = {};
        
        if (attrs.operation_name.empty()) {
            std::cerr << "Warning: Missing operation name" << std::endl;
        }
        
        if (!attrs.system.empty() && attrs.system.find("unknown") != 0) {
            // Good - we have a known system
        } else {
            std::cerr << "Info: Unknown or missing system provider" << std::endl;
        }
        
        return !attrs.operation_name.empty();
    }
    
    // Convert span to JSON-serializable format for logging/audit
    static std::string toJson(const SpanAttributes& attrs) {
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(3);
        
        oss << "{\n";
        oss << "  \"operation_name\": \"" << escapeJson(attrs.operation_name) << "\",\n";
        oss << "  \"system\": \"" << escapeJson(attrs.system) << "\",\n";
        oss << "  \"model\": \"" << escapeJson(attrs.model) << "\",\n";
        
        if (attrs.has_input) {
            oss << "  \"input_tokens\": " << attrs.input_tokens << ",\n";
        } else {
            oss << "  \"input_tokens\": null,\n";
        }
        
        if (attrs.has_output) {
            oss << "  \"output_tokens\": " << attrs.output_tokens << ",\n";
        } else {
            oss << "  \"output_tokens\": null,\n";
        }
        
        oss << "  \"total_tokens\": " << attrs.total_tokens << ",\n";
        
        if (!attrs.response_id.empty()) {
            oss << "  \"response_id\": \"" << escapeJson(attrs.response_id) << "\",\n";
        } else {
            oss << "  \"response_id\": null,\n";
        }
        
        oss << "  \"latency_ms\": " << attrs.latency_ms << "\n";
        oss << "}\n";
        
        return oss.str();
    }

private:
    static std::string escapeJson(const std::string& s) {
        std::ostringstream oss;
        for (char c : s) {
            switch (c) {
                case '"':  oss << "\\\""; break;
                case '\\': oss << "\\\\"; break;
                case '\b': oss << "\\b"; break;
                case '\f': oss << "\\f"; break;
                case '\n': oss << "\\n"; break;
                case '\r': oss << "\\r"; break;
                case '\t': oss << "\\t"; break;
                default:   if (static_cast<unsigned char>(c) < 0x20) {
                              oss << "\\u" << std::hex << std::setw(4) 
                                   << std::setfill('0') << static_cast<int>(c);
                          } else {
                              oss << c;
                          }
            }
        }
        return oss.str();
    }
};

// ============================================================================
// Demo / Test harness - self-contained runnable example
// ============================================================================

int main() {
    std::cout << "=== OTel Semantic Parser Demo ===" << std::endl;
    std::cout << std::endl;
    
    // Sample 1: Complete LLM completion span
    auto attrs1 = [](const AttributesMap& m) -> AttributesMap {
        return {{
            {"gen_ai.operation.name", "completion"},
            {"gen_ai.system", "openai"},
            {"gen_ai.request.model", "gpt-4o-mini"},
            {"gen_ai.usage.input_tokens", 128},
            {"gen_ai.usage.output_tokens", 64},
            {"gen_ai.usage.total_tokens", 192},
            {"gen_ai.response.id", "resp_abc123xyz"}
        }};
    };
    
    auto attrs1_map = attrs1({});
    auto parsed1 = SemanticParser::parse(attrs1_map);
    
    std::cout << "--- Sample 1: Complete Completion ---" << std::endl;
    std::cout << "Operation: " << parsed1.operation_name << std::endl;
    std::cout << "System: " << parsed1.system << std::endl;
    std::cout << "Model: " << parsed1.model << std::endl;
    std::cout << "Input tokens: " << parsed1.input_tokens << std::endl;
    std::cout << "Output tokens: " << parsed1.output_tokens << std::endl;
    std::cout << "Total tokens: " << parsed1.total_tokens << std::endl;
    std::cout << "Response ID: " << (parsed1.has_response_id ? parsed1.response_id : "(none)") << std::endl;
    
    // Sample 2: Embedding operation with minimal data
    auto attrs2 = [](const AttributesMap& m) -> AttributesMap {
        return {{
            {"operation.name", "embedding"},
            {"ai.system", "cohere"},
            {"request.model", "embed-english-v3.0"}
        }};
    };
    
    auto attrs2_map = attrs2({});
    auto parsed2 = SemanticParser::parse(attrs2_map);
    
    std::cout << "\n--- Sample 2: Minimal Embedding ---" << std::endl;
    std::cout << "Operation: '" << parsed2.operation_name << "'" << std::endl;
    std::cout << "System: '" << parsed2.system << "'" << std::endl;
    std::cout << "Model: '" << parsed2.model << "'" << std::endl;
    
    // Sample 3: Malformed/empty span (edge case)
    auto attrs3 = [](const AttributesMap& m) -> AttributesMap {
        return {{}};  // Empty attributes
    };
    
    auto attrs3_map = attrs3({});
    auto parsed3 = SemanticParser::parse(attrs3_map);
    
    std::cout << "\n--- Sample 3: Empty/Malformed ---" << std::endl;
    std::cout << "Operation: '" << (parsed3.operation_name.empty() ? "(empty)" : parsed3.operation_name) << "'" << std::endl;
    std::cout << "System: '" << parsed3.system << "'" << std::endl;
    
    // Sample 4: Batch statistics
    auto batch_stats = SemanticParser::parseBatch(attrs1_map);
    std::cout << "\n--- Batch Statistics ---" << std::endl;
    for (const auto& [key, value] : batch_stats) {
        std::cout << "  " << key << ": " << value << std::endl;
    }
    
    // Sample 5: JSON output format
    std::cout << "\n--- JSON Output Format ---" << std::endl;
    std::cout << SemanticParser::toJson(parsed1);
    
    // Validation check
    bool valid = SemanticParser::validate(parsed1);
    std::cout << "Validation result: " << (valid ? "PASSED" : "FAILED") << std::endl;
    
    std::cout << "\n=== Demo Complete ===" << std::endl;
    
    return 0;
}

// ============================================================================
// Module exports - what this header would expose
// ============================================================================

inline SemanticParser& get_parser_instance() {
    static SemanticParser instance;
    return instance;
}

inline SpanAttributes parse_span(const AttributesMap& attrs) {
    return SemanticParser::parse(attrs);
}

inline bool is_valid_span(const SpanAttributes& s) {
    return SemanticParser::validate(s);
}

inline std::string span_to_json(const SpanAttributes& s) {
    return SemanticParser::toJson(s);
}

}  // namespace otel_semantic_parser
}  // namespace cpp
}  // namespace polyglot

// ============================================================================
// End of file: polyglot/cpp/otel_semantic_parser.cpp
// ============================================================================