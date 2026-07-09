package polyglot.java;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

/**
 * OTel Semantic Parser for GenAI/LLM operations.
 * Parses OpenTelemetry spans and extracts semantic metadata following
 * the GenAI semantic conventions specification.
 */
public class OtelSemanticParser {

    // Standard GenAI operation types
    private static final Set<String> KNOWN_OPERATION_TYPES = Set.of(
        "completion", "chat_completion", "embedding", 
        "transcription", "translation", "summarization"
    );

    /**
     * Represents a parsed OTel span with extracted semantic metadata.
     */
    public static class ParsedSpan {
        private final String traceId;
        private final String spanId;
        private final String operationName;
        private final String modelName;
        private final long inputTokens;
        private final long outputTokens;
        private final String responseId;
        private final Instant startTime;
        private final Instant endTime;
        private final Duration duration;
        private final String status;
        private final Map<String, Object> extraAttributes;

        public ParsedSpan(String traceId, String spanId, String operationName, 
                         long inputTokens, long outputTokens, String responseId,
                         Instant startTime, Instant endTime, String status) {
            this.traceId = traceId;
            this.spanId = spanId;
            this.operationName = operationName;
            this.modelName = null;
            this.inputTokens = inputTokens;
            this.outputTokens = outputTokens;
            this.responseId = responseId;
            this.startTime = startTime;
            this.endTime = endTime;
            this.duration = (endTime != null && startTime != null) 
                ? Duration.between(startTime, endTime) : null;
            this.status = status;
            this.extraAttributes = new HashMap<>();
        }

        public ParsedSpan withModel(String model) {
            ParsedSpan copy = new ParsedSpan(traceId, spanId, operationName,
                    inputTokens, outputTokens, responseId, startTime, endTime, status);
            copy.modelName = model;
            return copy;
        }

        public ParsedSpan withExtraAttr(String key, Object value) {
            ParsedSpan copy = new ParsedSpan(traceId, spanId, operationName,
                    inputTokens, outputTokens, responseId, startTime, endTime, status);
            copy.extraAttributes.put(key, value);
            return copy;
        }

        @Override
        public String toString() {
            StringBuilder sb = new StringBuilder();
            sb.append("ParsedSpan{trace=").append(traceId).append(", span=").append(spanId)
              .append(", op=").append(operationName);
            if (modelName != null) sb.append(", model=").append(modelName);
            sb.append(", in=").append(inputTokens).append("/out=").append(outputTokens)
              .append(", id=").append(responseId);
            if (duration != null) sb.append(", dur=").append(duration.toMillis()).append("ms");
            sb.append(", status=").append(status);
            return sb.toString();
        }

        public String getTraceId() { return traceId; }
        public String getSpanId() { return spanId; }
        public String getOperationName() { return operationName; }
        public String getModelName() { return modelName; }
        public long getInputTokens() { return inputTokens; }
        public long getOutputTokens() { return outputTokens; }
        public String getResponseId() { return responseId; }
        public Instant getStartTime() { return startTime; }
        public Instant getEndTime() { return endTime; }
        public Duration getDuration() { return duration; }
        public String getStatus() { return status; }
        public Map<String, Object> getExtraAttributes() { return extraAttributes; }

        @Override
        public int hashCode() {
            int h = 31 * traceId.hashCode();
            h = 31 * h + spanId.hashCode();
            h = 31 * h + operationName.hashCode();
            h = 31 * h + (modelName != null ? modelName.hashCode() : 0);
            h = 31 * h + Long.hashCode(inputTokens);
            h = 31 * h + Long.hashCode(outputTokens);
            h = 31 * h + (responseId != null ? responseId.hashCode() : 0);
            return h;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof ParsedSpan)) return false;
            ParsedSpan other = (ParsedSpan) o;
            return Objects.equals(traceId, other.traceId) &&
                   Objects.equals(spanId, other.spanId) &&
                   Objects.equals(operationName, other.operationName) &&
                   inputTokens == other.inputTokens &&
                   outputTokens == other.outputTokens &&
                   Objects.equals(responseId, other.responseId);
        }
    }

    /**
     * Represents a complete OTel session (trace root) with all child spans.
     */
    public static class ParsedSession {
        private final String traceId;
        private final List<ParsedSpan> spans;
        private final Instant startTime;
        private final Instant endTime;

        public ParsedSession(String traceId, List<ParsedSpan> spans) {
            this.traceId = traceId;
            this.spans = spans;
            this.startTime = spans.stream()
                .map(ParsedSpan::getStartTime)
                .filter(Objects::nonNull)
                .min(Instant::compareTo).orElse(null);
            this.endTime = spans.stream()
                .map(ParsedSpan::getEndTime)
                .filter(Objects::nonNull)
                .max(Instant::compareTo).orElse(null);
        }

        public ParsedSession withExtraAttr(String key, Object value) {
            ParsedSession copy = new ParsedSession(traceId, spans);
            // Store session-level attributes separately if needed
            return copy;
        }

        @Override
        public String toString() {
            StringBuilder sb = new StringBuilder();
            sb.append("ParsedSession{trace=").append(traceId).append(", spans=").append(spans.size());
            if (startTime != null) sb.append(", start=").append(startTime);
            if (endTime != null) sb.append(", end=").append(endTime);
            return sb.toString();
        }

        public String getTraceId() { return traceId; }
        public List<ParsedSpan> getSpans() { return spans; }
        public Instant getStartTime() { return startTime; }
        public Instant getEndTime() { return endTime; }

        @Override
        public int hashCode() {
            return Objects.hash(traceId, spans);
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof ParsedSession)) return false;
            ParsedSession other = (ParsedSession) o;
            return Objects.equals(traceId, other.traceId) &&
                   Objects.equals(spans, other.spans);
        }
    }

    /**
     * Configuration for semantic parsing behavior.
     */
    public static class ParserConfig {
        private boolean strictMode = false;
        private Set<String> allowedOperationTypes = KNOWN_OPERATION_TYPES;
        private long maxTokenThreshold = 10_000_000; // 10M tokens

        public ParserConfig withStrict(boolean strict) {
            this.strictMode = strict;
            return this;
        }

        public ParserConfig withMaxTokens(long max) {
            this.maxTokenThreshold = max;
            return this;
        }

        public boolean isStrict() { return strictMode; }
        public long getMaxTokenThreshold() { return maxTokenThreshold; }
    }

    /**
     * Main parser class.
     */
    public static class Parser {

        private final ParserConfig config;
        private final Map<String, String> operationTypeMap = new HashMap<>();

        public Parser(ParserConfig config) {
            this.config = Objects.requireNonNull(config);
            // Build reverse map from operation name to type
            for (String type : KNOWN_OPERATION_TYPES) {
                operationTypeMap.put(type.toLowerCase(), type);
            }
        }

        /**
         * Parse a single span string and return ParsedSpan.
         */
        public ParsedSpan parse(String spanJson, String parentId) {
            if (spanJson == null || spanJson.trim().isEmpty()) {
                throw new IllegalArgumentException("Null or empty span JSON");
            }

            try {
                SpanData data = extractSpanData(spanJson);
                
                // Extract semantic attributes
                String operationName = extractOperationName(data, parentId);
                long inputTokens = extractInputTokens(data);
                long outputTokens = extractOutputTokens(data);
                String responseId = extractResponseId(data);
                Instant startTime = extractStartTime(data);
                Instant endTime = extractEndTime(data);
                String status = extractStatus(data);

                // Build ParsedSpan with defaults for missing fields
                return new ParsedSpan(
                    data.getTraceId(),
                    data.getSpanId(),
                    operationName,
                    inputTokens,
                    outputTokens,
                    responseId,
                    startTime,
                    endTime,
                    status
                ).withModel(data.getModel());

            } catch (Exception e) {
                if (config.isStrict()) {
                    throw new SemanticParseError("Failed to parse span: " + e.getMessage(), e);
                }
                // Return a best-effort partial result
                return createFallbackSpan(spanJson, parentId, e.getMessage());
            }
        }

        /**
         * Parse multiple spans and group them into sessions.
         */
        public List<ParsedSession> parseAll(String... spanStrings) {
            if (spanStrings == null || spanStrings.length == 0) {
                return Collections.emptyList();
            }

            List<ParsedSpan> allSpans = new ArrayList<>();
            
            for (String json : spanStrings) {
                try {
                    SpanData data = extractSpanData(json);
                    
                    // Group by trace ID
                    String traceId = data.getTraceId();
                    ParsedSpan span = parse(json, data.getParentId());
                    
                    if (!traceId.isEmpty()) {
                        allSpans.add(span);
                        
                        // Auto-group into sessions as we go
                        addToSession(traceId, span);
                    } else {
                        // Unknown trace ID - create a temporary session
                        ParsedSpan temp = new ParsedSpan(
                            "unknown-" + System.currentTimeMillis(),
                            data.getSpanId(),
                            "unknown", 0, 0, null,
                            Instant.now(), null, "unknown"
                        );
                        allSpans.add(temp);
                    }

                } catch (Exception e) {
                    if (config.isStrict()) {
                        throw new SemanticParseError("Failed to parse span: " + json.substring(0, 100), e);
                    }
                    // Add fallback
                    ParsedSpan fallback = createFallbackSpan(json, null, e.getMessage());
                    allSpans.add(fallback);
                }
            }

            return buildSessions(allSpans);
        }

        /**
         * Parse from a file.
         */
        public List<ParsedSession> parseFile(String path) throws IOException {
            if (!new File(path).exists()) {
                throw new FileNotFoundException("OTel span file not found: " + path);
            }

            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(new FileInputStream(path), StandardCharsets.UTF_8))) {
                
                List<String> lines = reader.lines().collect(Collectors.toList());
                return parseAll(lines.toArray(String[]::new));
            }
        }

        /**
         * Parse from an InputStream.
         */
        public List<ParsedSession> parse(InputStream inputStream) throws IOException {
            if (inputStream == null || !inputStream.available() > 0) {
                return Collections.emptyList();
            }

            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(inputStream, StandardCharsets.UTF_8))) {
                
                List<String> lines = reader.lines().collect(Collectors.toList());
                return parseAll(lines.toArray(String[]::new));
            }
        }

        /**
         * Generate an audit report from parsed sessions.
         */
        public AuditReport generateAuditReport(List<ParsedSession> sessions) {
            if (sessions == null || sessions.isEmpty()) {
                return new AuditReport(0, 0, 0L, 0L, Collections.emptyList());
            }

            long totalInputTokens = sessions.stream()
                .flatMap(s -> s.getSpans().stream())
                .mapToLong(ParsedSpan::getInputTokens)
                .sum();

            long totalOutputTokens = sessions.stream()
                .flatMap(s -> s.getSpans().stream())
                .mapToLong(ParsedSpan::getOutputTokens)
                .sum();

            // Find unique models used
            Set<String> models = new HashSet<>();
            for (ParsedSession session : sessions) {
                for (ParsedSpan span : session.getSpans()) {
                    if (span.getModelName() != null) {
                        models.add(span.getModelName());
                    }
                }
            }

            // Find unique operations
            Set<String> operations = new HashSet<>();
            for (ParsedSession session : sessions) {
                for (ParsedSpan span : session.getSpans()) {
                    if (span.getOperationName() != null && !span.getOperationName().isEmpty()) {
                        operations.add(span.getOperationName());
                    }
                }
            }

            return new AuditReport(
                sessions.size(),
                totalInputTokens,
                totalOutputTokens,
                models,
                operations
            );
        }

        /**
         * Check if a span is within token limits.
         */
        public boolean checkTokenLimits(ParsedSpan span) {
            long combined = span.getInputTokens() + span.getOutputTokens();
            return combined <= config.getMaxTokenThreshold();
        }

        /**
         * Filter spans by operation type.
         */
        public List<ParsedSession> filterByOperation(String operationType, 
                                                     List<ParsedSession> sessions) {
            if (operationType == null || operationType.isEmpty()) {
                return sessions;
            }

            Set<String> types = new HashSet<>(Arrays.asList(operationType.split(",")));
            
            return sessions.stream()
                .filter(s -> s.getSpans().stream()
                    .anyMatch(span -> types.contains(span.getOperationName())))
                .collect(Collectors.toList());
        }

        /**
         * Filter spans by model.
         */
        public List<ParsedSession> filterByModel(String modelName, 
                                                 List<ParsedSession> sessions) {
            if (modelName == null || modelName.isEmpty()) {
                return sessions;
            }

            return sessions.stream()
                .filter(s -> s.getSpans().stream()
                    .anyMatch(span -> span.getModelName() != null && 
                                     span.getModelName().equalsIgnoreCase(modelName)))
                .collect(Collectors.toList());
        }

        /**
         * Sort spans by start time.
         */
        public List<ParsedSession> sortByTime(List<ParsedSession> sessions) {
            return sessions.stream()
                .sorted(Comparator.comparingLong(s -> 
                    s.getStartTime() != null ? s.getStartTime().toEpochMilli() : 0))
                .collect(Collectors.toList());
        }

        /**
         * Export parsed data to JSON.
         */
        public String exportToJSON(List<ParsedSession> sessions) {
            if (sessions == null || sessions.isEmpty()) {
                return "[]";
            }

            StringBuilder json = new StringBuilder();
            json.append("[\n");
            
            for (int i = 0; i < sessions.size(); i++) {
                ParsedSession session = sessions.get(i);
                
                // Build span objects
                List<Map<String, Object>> spansJson = new ArrayList<>();
                for (ParsedSpan span : session.getSpans()) {
                    Map<String, Object> spanMap = new HashMap<>();
                    spanMap.put("traceId", span.getTraceId());
                    spanMap.put("spanId", span.getSpanId());
                    spanMap.put("operationName", span.getOperationName());
                    if (span.getModelName() != null) {
                        spanMap.put("model", span.getModelName());
                    }
                    spanMap.put("inputTokens", span.getInputTokens());
                    spanMap.put("outputTokens", span.getOutputTokens());
                    spanMap.put("responseId", span.getResponseId());
                    
                    if (span.getStartTime() != null) {
                        spanMap.put("startTime", 
                            span.getStartTime().toString());
                    }
                    if (span.getEndTime() != null) {
                        spanMap.put("endTime", 
                            span.getEndTime().toString());
                    }
                    if (span.getDuration() != null) {
                        spanMap.put("durationMs", span.getDuration().toMillis());
                    }
                    
                    spansJson.add(spanMap);
                }

                Map<String, Object> sessionMap =