#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <map>
#include <unordered_map>
#include <memory>
#include <algorithm>
#include <chrono>
#include <iomanip>
#include <ctime>
#include <regex>

namespace agentlog {

// OTel GenAI Semantic Conventions constants
const std::string ATTR_OP_NAME = "gen_ai.operation.name";
const std::string ATTR_REQ_MODEL = "gen_ai.request.model";
const std::string ATTR_RESP_ID = "gen_ai.response.id";
const std::string ATTR_USAGE_COMPLETION = "gen_ai.usage.completion_tokens";
const std::string ATTR_USAGE_PROMPT = "gen_ai.usage.prompt_tokens";

// Message types for workflow reconstruction
enum class MessageType {
    SYSTEM,
    USER,
    AGENT,
    TOOL_CALL,
    TOOL_RESPONSE,
    UNKNOWN
};

struct Message {
    std::string id;
    std::chrono::system_clock::time_point timestamp;
    MessageType type = MessageType::UNKNOWN;
    std::string model;
    int completion_tokens = 0;
    int prompt_tokens = 0;
    std::string content;
    std::map<std::string, std::string> attributes;
    
    bool operator<(const Message& other) const {
        return timestamp < other.timestamp;
    }
};

struct WorkflowSession {
    std::string session_id;
    std::chrono::system_clock::time_point start_time;
    std::vector<Message> messages;
    std::map<std::string, int> model_usage;
    
    void addMessage(Message msg) {
        messages.push_back(std::move(msg));
    }
};

// Parse timestamp string to time point
std::chrono::system_clock::time_point parseTimestamp(const std::string& ts_str) {
    try {
        auto parts = splitString(ts_str, 'T');
        if (parts.size() >= 2) {
            // Handle ISO format with timezone offset
            std::string dt_part = parts[0];
            auto time_parts = splitString(dt_part, ':');
            
            int year = std::stoi(time_parts[0]);
            int month = std::stoi(time_parts[1]);
            int day = std::stoi(time_parts[2]);
            int hour = 0;
            int min = 0;
            int sec = 0;
            
            if (time_parts.size() >= 4) {
                hour = std::stoi(time_parts[3]);
                min = std::stoi(time_parts[4]);
                sec = std::stoi(time_parts[5]);
            }
            
            auto now = std::chrono::system_clock::now();
            auto time_t_val = std::mktime(std::tm{0, month - 1, day, hour, min, sec, 0, 0, year - 1900});
            return std::chrono::system_clock::from_time_t(time_t_val);
        }
    } catch (...) {}
    
    // Fallback: use current time
    return std::chrono::system_clock::now();
}

// Split string by delimiter
std::vector<std::string> splitString(const std::string& str, char delim) {
    std::vector<std::string> result;
    size_t start = 0;
    while (true) {
        auto pos = str.find(delim, start);
        if (pos == std::string::npos) {
            result.push_back(str.substr(start));
            break;
        }
        result.push_back(str.substr(start, pos - start));
        start = pos + 1;
    }
    return result;
}

// Parse OTel span attributes into a Message
Message parseOTelSpan(const std::string& json_span) {
    Message msg;
    
    // Extract timestamp
    auto ts_match = std::regex_search(json_span, std::regex(R"(\\"timestamp"\s*:\s*"([^"]+)")");
    if (ts_match) {
        msg.timestamp = parseTimestamp(ts_match[1].str());
    } else {
        msg.timestamp = std::chrono::system_clock::now();
    }
    
    // Extract operation name and determine type
    auto op_name_match = std::regex_search(json_span, std::regex(R"(\\"gen_ai\.operation\.name"\s*:\s*"([^"]+)")");
    if (op_name_match) {
        msg.attributes[ATTR_OP_NAME] = op_name_match[1].str();
        
        // Infer message type from operation name
        if (op_name_match[1].str().find("user") != std::string::npos || 
            op_name_match[1].str() == "chat/completions/user") {
            msg.type = MessageType::USER;
        } else if (op_name_match[1].str().find("assistant") != std::string::npos) {
            msg.type = MessageType::AGENT;
        } else if (op_name_match[1].str() == "tool/call") {
            msg.type = MessageType::TOOL_CALL;
        } else if (op_name_match[1].str().find("tool/response") != std::string::npos) {
            msg.type = MessageType::TOOL_RESPONSE;
        }
    }
    
    // Extract model info
    auto model_match = std::regex_search(json_span, std::regex(R"(\\"gen_ai\.request\.model"\s*:\s*"([^"]+)")");
    if (model_match) {
        msg.model = model_match[1].str();
    }
    
    // Extract token usage
    auto completion_match = std::regex_search(json_span, std::regex(R"(\\"gen_ai\.usage\.completion_tokens"\s*:\s*(\d+)"));
    if (completion_match) {
        msg.completion_tokens = std::stoi(completion_match[1].str());
    }
    
    auto prompt_match = std::regex_search(json_span, std::regex(R"(\\"gen_ai\.usage\.prompt_tokens"\s*:\s*(\d+)"));
    if (prompt_match) {
        msg.prompt_tokens = std::stoi(prompt_match[1].str());
    }
    
    // Extract content/response
    auto content_match = std::regex_search(json_span, std::regex(R"(\\"content"\s*:\s*"([^"]+)")");
    if (content_match) {
        msg.content = content_match[1].str();
    }
    
    return msg;
}

// Reconstruct workflow from OTel spans
WorkflowSession reconstructWorkflow(const std::string& json_spans, const std::string& session_id) {
    WorkflowSession session;
    session.session_id = session_id;
    session.start_time = std::chrono::system_clock::now();
    
    // Parse each span into a message
    auto spans_match = std::regex_search(json_spans, std::regex(R"(\{[^{}]*\\"timestamp"[^{}]*\}"));
    while (spans_match) {
        try {
            Message msg = parseOTelSpan(spans_match[0].str());
            
            // Track model usage
            if (!msg.model.empty()) {
                session.model_usage[msg.model]++;
            }
            
            session.addMessage(std::move(msg));
        } catch (...) {}
        
        spans_match = std::regex_search(json_spans, std::regex(R"(\{[^{}]*\\"timestamp"[^{}]*\}"));
    }
    
    // Sort messages chronologically
    std::sort(session.messages.begin(), session.messages.end());
    
    return session;
}

// Format timestamp for display
std::string formatTimestamp(const std::chrono::system_clock::time_point& tp) {
    auto now = std::chrono::system_clock::now();
    auto diff = std::chrono::duration_cast<std::chrono::seconds>(tp - now).count();
    
    if (diff >= 0 && diff < 60) {
        return "now";
    } else if (diff >= 60 && diff < 3600) {
        int mins = std::abs(diff / 60);
        return std::to_string(mins) + "m ago";
    } else if (diff >= 3600 && diff < 86400) {
        int hrs = std::abs(diff / 3600);
        return std::to_string(hrs) + "h ago";
    } else {
        auto time_t_val = std::chrono::system_clock::to_time_t(tp);
        std::tm* tm_ptr = std::localtime(&time_t_val);
        char buf[64];
        std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", tm_ptr);
        return std::string(buf);
    }
}

// Render workflow as readable audit trail
std::string renderAuditTrail(const WorkflowSession& session) {
    std::ostringstream oss;
    
    oss << "=== Workflow Audit Trail ===\n";
    oss << "Session ID: " << session.session_id << "\n";
    oss << "Start Time: " << formatTimestamp(session.start_time) << "\n\n";
    
    // Summary stats
    int total_tokens = 0;
    for (const auto& [model, count] : session.model_usage) {
        total_tokens += model * count;
    }
    
    oss << "--- Summary ---\n";
    oss << "Total Messages: " << session.messages.size() << "\n";
    oss << "Models Used:\n";
    for (const auto& [model, count] : session.model_usage) {
        oss << "  - " << model << ": " << count << " calls\n";
    }
    oss << "---\n\n";
    
    // Detailed message log
    oss << "--- Message Log ---\n";
    for (const auto& msg : session.messages) {
        oss << "\n[Message #" << (msg.id.empty() ? "?" : msg.id) << "]\n";
        oss << "  Type: ";
        
        switch (msg.type) {
            case MessageType::SYSTEM: oss << "[SYSTEM]"; break;
            case MessageType::USER: oss << "[USER]"; break;
            case MessageType::AGENT: oss << "[AGENT]"; break;
            case MessageType::TOOL_CALL: oss << "[TOOL_CALL]"; break;
            case MessageType::TOOL_RESPONSE: oss << "[TOOL_RESPONSE]"; break;
            default: oss << "[UNKNOWN]"; break;
        }
        
        if (!msg.model.empty()) {
            oss << " | Model: " << msg.model;
        }
        
        int total_tokens = msg.completion_tokens + msg.prompt_tokens;
        if (total_tokens > 0) {
            oss << " | Tokens: " << total_tokens;
        }
        
        oss << "\n";
        
        // Show content preview
        std::string preview = msg.content;
        if (preview.length() > 200) {
            preview = preview.substr(0, 200) + "...";
        }
        oss << "  Content: " << preview << "\n";
    }
    
    oss << "\n=== End of Audit ===\n";
    
    return oss.str();
}

// Main demo function
int main() {
    // Sample OTel span data (simulating what would come from a real system)
    std::string sample_spans = R"(
{
  "span_id": "1",
  "parent_span_id": null,
  "name": "gen_ai.operation.name: chat/completions/user",
  "timestamp": "2024-01-15T10:30:00Z",
  "attributes": {
    "gen_ai.request.model": "gpt-4o",
    "gen_ai.usage.prompt_tokens": 128,
    "content": "Hello, how are you?"
  }
},
{
  "span_id": "2",
  "parent_span_id": "1",
  "name": "gen_ai.operation.name: chat/completions/assistant",
  "timestamp": "2024-01-15T10:30:01Z",
  "attributes": {
    "gen_ai.request.model": "gpt-4o",
    "gen_ai.usage.completion_tokens": 64,
    "content": "I'm doing well, thank you for asking!"
  }
},
{
  "span_id": "3",
  "parent_span_id": "2",
  "name": "gen_ai.operation.name: tool/call",
  "timestamp": "2024-01-15T10:30:02Z",
  "attributes": {
    "tool_name": "weather_api",
    "parameters": {"city": "London"},
    "gen_ai.usage.completion_tokens": 32,
    "content": "The weather in London is rainy."
  }
}
)";

    // Reconstruct the workflow
    WorkflowSession session = reconstructWorkflow(sample_spans, "session_001");

    // Output the audit trail
    std::string output = renderAuditTrail(session);
    
    // Also write to file for persistence
    std::ofstream log_file("agentlog/workflow_audit.log", std::ios::app);
    if (log_file.is_open()) {
        log_file << "Session: " << session.session_id << "\n";
        log_file << output;
        log_file.close();
    }

    // Print to console
    std::cout << output;

    return 0;
}