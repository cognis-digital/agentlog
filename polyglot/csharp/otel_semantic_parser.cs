using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;

namespace polyglot.csharp
{
    /// <summary>
    /// Configuration options for the OTEL semantic parser.
    /// </summary>
    public sealed class ParserOptions
    {
        public const string DefaultModelName = "default";
        
        public string? ModelName { get; set; } = DefaultModelName;
        public int MaxPromptLength { get; set; } = 10_000;
        public bool ValidateRequiredFields { get; set; } = true;
        public bool IncludeMetadata { get; set; } = true;
    }

    /// <summary>
    /// Result of a parsing operation.
    /// </summary>
    public sealed class ParseResult<T>
    {
        public T? Data { get; }
        public string? Error { get; }
        public bool Success { get; }
        
        public static ParseResult<T> Ok(T data) => new() { Data = data, Success = true };
        public static ParseResult<T> Fail(string error) => new() { Error = error, Success = false };

        public static implicit operator string(ParseResult<T> result) 
            => result.Success ? JsonSerializer.Serialize(result.Data) : (result.Error ?? "Unknown");
    }

    /// <summary>
    /// Semantic attribute constants for GenAI/LLM telemetry.
    /// </summary>
    internal static class OtelSemanticConstants
    {
        public const string AttributeGenAiModelName = "gen_ai.model.name";
        public const string AttributeGenAiOperation = "gen_ai.operation.name";
        public const string AttributeGenAiRequestType = "gen_ai.request.type";
        public const string AttributeGenAiPrompt = "gen_ai.prompt";
        public const string AttributeGenAiResponse = "gen_ai.response";
        public const string AttributeGenAiTokenUsage = "gen_ai.token.usage";
        
        // HTTP headers for OTEL context propagation
        public const string HeaderTraceParent = "traceparent";
        public const string HeaderTraceState = "tracestate";
    }

    /// <summary>
    /// Main parser class for OpenTelemetry semantic conventions.
    /// </summary>
    public sealed class OtelSemanticParser
    {
        private readonly ParserOptions _options;
        private readonly JsonSerializerOptions _jsonOptions;

        public OtelSemanticParser(ParserOptions options = null)
        {
            _options = options ?? new ParserOptions();
            _jsonOptions = new JsonSerializerOptions 
            { 
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
            };
        }

        /// <summary>
        /// Parses a JSON-encoded OTEL span into semantic format.
        /// </summary>
        public ParseResult<SpanData> Parse(string json)
        {
            try
            {
                var data = JsonSerializer.Deserialize<SpanData>(json, _jsonOptions);
                
                if (data == null || string.IsNullOrEmpty(data.Name))
                    return ParseResult<SpanData>.Fail("Invalid or empty span JSON");

                // Extract semantic attributes
                var semanticAttrs = ExtractSemanticAttributes(data);
                
                // Validate required fields if configured
                if (_options.ValidateRequiredFields)
                {
                    var errors = ValidateRequiredFields(semanticAttrs);
                    if (errors.Count > 0)
                        return ParseResult<SpanData>.Fail($"Validation failed: {string.Join(", ", errors)}");
                }

                // Enrich with metadata
                if (_options.IncludeMetadata)
                {
                    semanticAttrs["__metadata"] = new Dictionary<string, object> 
                    { 
                        { "source", "otel_semantic_parser" },
                        { "parsed_at", DateTime.UtcNow.ToString("o") }
                    };
                }

                return ParseResult<SpanData>.Ok(semanticAttrs);
            }
            catch (JsonException ex)
            {
                return ParseResult<SpanData>.Fail($"JSON parse error: {ex.Message}");
            }
        }

        /// <summary>
        /// Parses a JSON-encoded OTEL log record.
        /// </summary>
        public ParseResult<LogData> ParseLog(string json)
        {
            try
            {
                var data = JsonSerializer.Deserialize<LogData>(json, _jsonOptions);
                
                if (data == null || string.IsNullOrEmpty(data.Body))
                    return ParseResult<LogData>.Fail("Invalid or empty log JSON");

                // Extract semantic attributes from logs
                var semanticAttrs = new Dictionary<string, object> 
                { 
                    { "type", "log" },
                    { "body", data.Body }
                };

                if (!string.IsNullOrEmpty(data.Attributes?.ToString()))
                {
                    foreach (var kvp in JsonSerializer.Deserialize<Dictionary<string, string>>(data.Attributes) ?? new Dictionary<string, string>())
                    {
                        semanticAttrs[kvp.Key] = kvp.Value;
                    }
                }

                // Extract GenAI-specific attributes if present
                var genAiAttrs = ExtractGenAIFromLog(data);
                foreach (var kvp in genAiAttrs)
                {
                    semanticAttrs[kvp.Key] = kvp.Value;
                }

                return ParseResult<LogData>.Ok(semanticAttrs);
            }
            catch (JsonException ex)
            {
                return ParseResult<LogData>.Fail($"JSON parse error: {ex.Message}");
            }
        }

        /// <summary>
        /// Parses a JSON-encoded OTEL metric.
        /// </summary>
        public ParseResult<MetricData> ParseMetric(string json)
        {
            try
            {
                var data = JsonSerializer.Deserialize<MetricData>(json, _jsonOptions);
                
                if (data == null || string.IsNullOrEmpty(data.Name))
                    return ParseResult<MetricData>.Fail("Invalid or empty metric JSON");

                // Extract semantic attributes from metrics
                var semanticAttrs = new Dictionary<string, object> 
                { 
                    { "type", "metric" },
                    { "name", data.Name }
                };

                if (data.Attributes != null)
                {
                    foreach (var kvp in data.Attributes)
                    {
                        semanticAttrs[kvp.Key] = kvp.Value;
                    }
                }

                // Add metric-specific metadata
                semanticAttrs["__metric_metadata"] = new Dictionary<string, object> 
                { 
                    { "unit", data.Unit },
                    { "description", data.Description }
                };

                return ParseResult<MetricData>.Ok(semanticAttrs);
            }
            catch (JsonException ex)
            {
                return ParseResult<MetricData>.Fail($"JSON parse error: {ex.Message}");
            }
        }

        /// <summary>
        /// Extracts semantic attributes from a parsed span.
        /// </summary>
        private Dictionary<string, object> ExtractSemanticAttributes(SpanData span)
        {
            var attrs = new Dictionary<string, object>();

            // Basic span metadata
            attrs["type"] = "span";
            attrs["name"] = span.Name;
            attrs["service_name"] = span.ServiceName ?? "";
            attrs["trace_id"] = span.TraceId?.ToString() ?? "";
            attrs["span_id"] = span.SpanId?.ToString() ?? "";

            // Extract GenAI-specific attributes if present
            var genAiAttrs = ExtractGenAIFromSpan(span);
            foreach (var kvp in genAiAttrs)
            {
                attrs[kvp.Key] = kvp.Value;
            }

            return attrs;
        }

        /// <summary>
        /// Extracts GenAI-specific attributes from any telemetry data.
        /// </summary>
        private Dictionary<string, object> ExtractGenAIFromSpan(SpanData span)
        {
            var genAiAttrs = new Dictionary<string, object>();

            // Check for GenAI operation type
            if (!string.IsNullOrEmpty(span.Kind))
            {
                switch (span.Kind.ToLower())
                {
                    case "completion":
                        genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "completion";
                        break;
                    case "chat":
                        genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "chat";
                        break;
                    case "embedding":
                        genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "embedding";
                        break;
                }
            }

            // Extract model name if available
            var modelName = span.Attributes?.FirstOrDefault(a => a.Key == OtelSemanticConstants.AttributeGenAiModelName)?.Value as string;
            if (!string.IsNullOrEmpty(modelName))
            {
                genAiAttrs[OtelSemanticConstants.AttributeGenAiModelName] = modelName;
            }

            // Extract prompt/response data if present
            var prompts = span.Attributes?.FirstOrDefault(a => a.Key == OtelSemanticConstants.AttributeGenAiPrompt)?.Value as string;
            if (!string.IsNullOrEmpty(prompts))
            {
                genAiAttrs[OtelSemanticConstants.AttributeGenAiPrompt] = TruncateString(prompts, _options.MaxPromptLength);
            }

            return genAiAttrs;
        }

        /// <summary>
        /// Extracts GenAI-specific attributes from a log record.
        /// </summary>
        private Dictionary<string, object> ExtractGenAIFromLog(LogData log)
        {
            var genAiAttrs = new Dictionary<string, object>();

            // Check for common GenAI operation names in the body or attributes
            if (!string.IsNullOrEmpty(log.Body))
            {
                var lowerBody = log.Body.ToLower();
                
                if (lowerBody.Contains("completion") || lowerBody.Contains("chat"))
                    genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "completion";

                if (lowerBody.Contains("embedding"))
                    genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "embedding";
            }

            // Extract model name from attributes or body
            var modelNameAttr = log.Attributes?.FirstOrDefault(a => a.Key == OtelSemanticConstants.AttributeGenAiModelName)?.Value as string;
            if (string.IsNullOrEmpty(modelNameAttr))
            {
                modelNameAttr = log.Body?.Split(new[] { "model=" }, StringSplitOptions.None)
                    .LastOrDefault()?.Split(' ')[0];
            }

            if (!string.IsNullOrEmpty(modelNameAttr))
            {
                genAiAttrs[OtelSemanticConstants.AttributeGenAiModelName] = TruncateString(modelNameAttr, 256);
            }

            return genAiAttrs;
        }

        /// <summary>
        /// Extracts GenAI-specific attributes from a metric.
        /// </summary>
        private Dictionary<string, object> ExtractGenAIFromMetric(MetricData metric)
        {
            var genAiAttrs = new Dictionary<string, object>();

            // Check for token usage metrics (common in LLM monitoring)
            if (!string.IsNullOrEmpty(metric.Name))
            {
                if (metric.Name.Contains("token") || metric.Name.Contains("completion"))
                    genAiAttrs[OtelSemanticConstants.AttributeGenAiRequestType] = "usage";
                
                // Extract model name from metric tags/attributes
                var modelNameAttr = metric.Attributes?.FirstOrDefault(a => a.Key == OtelSemanticConstants.AttributeGenAiModelName)?.Value as string;
                if (string.IsNullOrEmpty(modelNameAttr))
                {
                    modelNameAttr = metric.Name.Split(new[] { "model=" }, StringSplitOptions.None)
                        .LastOrDefault()?.Split(' ')[0];
                }

                if (!string.IsNullOrEmpty(modelNameAttr))
                {
                    genAiAttrs[OtelSemanticConstants.AttributeGenAiModelName] = TruncateString(modelNameAttr, 256);
                }
            }

            return genAiAttrs;
        }

        /// <summary>
        /// Validates required fields based on semantic conventions.
        /// </summary>
        private List<string> ValidateRequiredFields(Dictionary<string, object> attrs)
        {
            var errors = new List<string>();

            // For spans: require at least a name and trace ID
            if (attrs["type"]?.ToString() == "span")
            {
                if (!attrs.ContainsKey("name"))
                    errors.Add("Span must have a 'name' attribute");
                
                if (!attrs.ContainsKey("trace_id"))
                    errors.Add("Span must have a 'trace_id' attribute");
            }

            // For logs: require at least a body
            else if (attrs["type"]?.ToString() == "log")
            {
                if (!attrs.ContainsKey("body"))
                    errors.Add("Log must have a 'body' attribute");
            }

            return errors;
        }

        /// <summary>
        /// Truncates a string to the specified length, adding ellipsis if needed.
        /// </summary>
        private static string TruncateString(string value, int maxLength)
        {
            if (string.IsNullOrEmpty(value))
                return value;

            if (value.Length <= maxLength)
                return value;

            var truncated = value.Substring(0, maxLength - 3);
            return truncated + "...";
        }
    }

    /// <summary>
    /// Represents an OpenTelemetry span.
    /// </summary>
    public sealed class SpanData
    {
        public string? Name { get; set; }
        public string? Kind { get; set; } // "completion", "chat", "embedding"
        public string? ServiceName { get; set; }
        public Guid? TraceId { get; set; }
        public Guid? SpanId { get; set; }
        public Dictionary<string, object>? Attributes { get; set; }

        public static SpanData Create(string name) 
            => new() { Name = name };
    }

    /// <summary>
    /// Represents an OpenTelemetry log record.
    /// </summary>
    public sealed class LogData
    {
        public string? Body { get; set; }
        public Dictionary<string, object>? Attributes { get; set; }
        public DateTime? Timestamp { get; set; }

        public static LogData Create(string body) 
            => new() { Body = body };
    }

    /// <summary>
    /// Represents an OpenTelemetry metric.
    /// </summary>
    public sealed class MetricData
    {
        public string? Name { get; set; }
        public string? Unit { get; set; }
        public string? Description { get; set; }
        public Dictionary<string, object>? Attributes { get; set; }

        public static MetricData Create(string name) 
            => new() { Name = name };
    }

    /// <summary>
    /// Extension methods for convenient parsing.
    /// </summary>
    public static class OtelParserExtensions
    {
        private static readonly Lazy<OtelSemanticParser> _defaultParser = 
            new(() => new OtelSemanticParser());

        public static ParseResult<SpanData> Parse(this string json) 
            => _defaultParser.Value.Parse(json);

        public static ParseResult<LogData> ParseLog(this string json) 
            => _defaultParser.Value.ParseLog(json);

        public static ParseResult<MetricData> ParseMetric(this string json) 
            => _defaultParser.Value.ParseMetric(json);
    }

    /// <summary>
    /// Demo/entry point for the OTEL semantic parser.
    /// </summary>
    internal class Program
    {
        private static void Main(string[] args)
        {
            Console.WriteLine("OTEL Semantic Parser Demo");
            Console.WriteLine("=========================\n");

            var parser = new OtelSemanticParser(
                new ParserOptions 
                { 
                    MaxPromptLength = 500,
                    IncludeMetadata = true
                });

            // Sample span JSON (simulating a GenAI completion request)
            string sampleSpanJson = @"{
                ""name"": ""gen_ai.completion"",
                ""kind"": ""completion"",
                ""service_name"": ""llm-service"",
                ""attributes"": {
                    ""gen_ai.model.name"": ""gpt-4o-mini"",
                    ""gen_ai.operation.name"": ""complete"",
                    ""gen_ai.request.type"": ""chat"",
                    ""gen_ai.prompt"": [""Hello, how are you?""],
                    ""gen_ai.response"": [""I'm doing well, thank you!""],
                    ""gen_ai.token.usage"": {
                        ""prompt_tokens"": 5,
                        ""completion_tokens"": 12,
                        ""total_tokens"": 17
                    }
                },
                ""trace_id"": ""a1b2c3d4-e5f6-7890-abcd-ef1234567890"",
                ""span_id"": ""12345678-1234-1234-1234-12345