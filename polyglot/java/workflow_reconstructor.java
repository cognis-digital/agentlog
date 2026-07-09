package agentlog.java;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Workflow Reconstructor for agentlog tool.
 * 
 * Parses OpenTelemetry GenAI semantic conventions from spans/logs and
 * reconstructs the execution flow, dependencies, and causal relationships.
 */
public class workflow_reconstructor {

    // =====================================================================
    // Core Data Structures
    // =====================================================================

    /**
     * Represents a single span/operation in the agentic workflow.
     * Built to match OpenTelemetry GenAI semantic conventions.
     */
    public static final class WorkflowSpan implements Comparable<WorkflowSpan> {
        private final String traceId;
        private final String spanId;
        private final String parentSpanId;
        private final long startTimeNanos;
        private final long endTimeNanos;
        private final String operationName;
        private final Map<String, Object> attributes;

        public WorkflowSpan(String traceId, String spanId, String parentSpanId,
                           long startTimeNanos, long endTimeNanos,
                           String operationName, Map<String, Object> attributes) {
            this.traceId = traceId;
            this.spanId = spanId;
            this.parentSpanId = parentSpanId;
            this.startTimeNanos = startTimeNanos;
            this.endTimeNanos = endTimeNanos;
            this.operationName = operationName;
            this.attributes = attributes != null ? new HashMap<>(attributes) : new HashMap<>();
        }

        public String getTraceId() { return traceId; }
        public String getSpanId() { return spanId; }
        public String getParentSpanId() { return parentSpanId; }
        public long getStartTimeNanos() { return startTimeNanos; }
        public long getEndTimeNanos() { return endTimeNanos; }
        public long getDurationNanos() { return endTimeNanos - startTimeNanos; }
        public String getOperationName() { return operationName; }

        /** Extracts GenAI-specific attributes with defaults. */
        public GenAiContext getGenAiContext() {
            String model = getStringAttr("gen_ai.request.model", "unknown");
            String requestId = getStringAttr("gen_ai.response.id", null);
            long inputTokens = longAttr("gen_ai.usage.input_tokens", 0L);
            long outputTokens = longAttr("gen_ai.usage.output_tokens", 0L);
            double totalCost = doubleAttr("gen_ai.usage.total_cost", 0.0);

            return new GenAiContext(model, requestId, inputTokens, outputTokens, totalCost);
        }

        public Map<String, Object> getAttributes() { return attributes; }

        @Override
        public int compareTo(WorkflowSpan other) {
            long timeDiff = this.startTimeNanos - other.startTimeNanos;
            if (timeDiff != 0) return Long.compare(timeDiff, 0);
            return this.spanId.compareTo(other.spanId);
        }
    }

    /** GenAI-specific context extracted from spans. */
    public static final class GenAiContext {
        private final String model;
        private final String requestId;
        private final long inputTokens;
        private final long outputTokens;
        private final double totalCost;

        public GenAiContext(String model, String requestId, long inputTokens,
                           long outputTokens, double totalCost) {
            this.model = model;
            this.requestId = requestId;
            this.inputTokens = inputTokens;
            this.outputTokens = outputTokens;
            this.totalCost = totalCost;
        }

        public String getModel() { return model; }
        public String getRequestId() { return requestId; }
        public long getInputTokens() { return inputTokens; }
        public long getOutputTokens() { return outputTokens; }
        public double getTotalCost() { return totalCost; }

        @Override
        public String toString() {
            return "GenAiContext{" +
                    "model='" + model + '\'' +
                    ", requestId='" + requestId + '\'' +
                    ", inputTokens=" + inputTokens +
                    ", outputTokens=" + outputTokens +
                    ", totalCost=" + totalCost +
                    '}';
        }
    }

    /** Metadata about the overall workflow run. */
    public static final class WorkflowContext {
        private final String traceId;
        private final long startTimeNanos;
        private final long endTimeNanos;
        private final Map<String, Object> attributes;

        public WorkflowContext(String traceId, long startTimeNanos, long endTimeNanos,
                              Map<String, Object> attributes) {
            this.traceId = traceId;
            this.startTimeNanos = startTimeNanos;
            this.endTimeNanos = endTimeNanos;
            this.attributes = attributes != null ? new HashMap<>(attributes) : new HashMap<>();
        }

        public String getTraceId() { return traceId; }
        public long getStartTimeNanos() { return startTimeNanos; }
        public long getEndTimeNanos() { return endTimeNanos; }
        public long getDurationNanos() { return endTimeNanos - startTimeNanos; }

        @Override
        public String toString() {
            return "WorkflowContext{" +
                    "traceId='" + traceId + '\'' +
                    ", duration=" + formatDuration(getDurationNanos()) +
                    '}';
        }

        private static String formatDuration(long nanos) {
            if (nanos < 1_000) return "< 1ms";
            double ms = nanos / 1_000_000.0;
            if (ms < 60_000) return String.format("%.2f ms", ms);
            double sec = ms / 1_000.0;
            if (sec < 3_600) return String.format("%.2f s", sec);
            double min = sec / 60.0;
            if (min < 60) return String.format("%.1f m", min);
            double hr = min / 60.0;
            return String.format("%.1f h", hr);
        }
    }

    /** Result of the reconstruction process. */
    public static final class ReconstructionResult {
        private final WorkflowContext context;
        private final List<WorkflowSpan> spans;
        private final Map<String, List<WorkflowSpan>> byParentId;
        private final Map<String, List<WorkflowSpan>> childrenByRoot;
        private final List<List<WorkflowSpan>> executionPaths;

        public ReconstructionResult(WorkflowContext context, List<WorkflowSpan> spans) {
            this.context = context;
            this.spans = spans != null ? new ArrayList<>(spans) : new ArrayList<>();
            
            // Build indexes for fast lookups
            byParentId = buildByParentIndex();
            childrenByRoot = buildChildrenByRoot();
            executionPaths = computeExecutionPaths();
        }

        private Map<String, List<WorkflowSpan>> buildByParentIndex() {
            var index = new HashMap<String, List<WorkflowSpan>>();
            for (var span : spans) {
                String parentId = span.getParentSpanId();
                if (parentId == null || parentId.isEmpty()) {
                    // Root span - no parent
                    continue;
                }
                index.computeIfAbsent(parentId, k -> new ArrayList<>()).add(span);
            }
            return index;
        }

        private Map<String, List<WorkflowSpan>> buildChildrenByRoot() {
            var rootSpans = spans.stream()
                    .filter(s -> s.getParentSpanId() == null || 
                                 s.getParentSpanId().isEmpty())
                    .collect(Collectors.toSet());
            
            var result = new HashMap<String, List<WorkflowSpan>>();
            for (var root : rootSpans) {
                var children = findAllDescendants(root);
                if (!children.isEmpty()) {
                    result.put(root.getTraceId(), children);
                }
            }
            return result;
        }

        private List<WorkflowSpan> findAllDescendants(WorkflowSpan root) {
            var descendants = new ArrayList<WorkflowSpan>();
            var queue = new LinkedList<>(Collections.singletonList(root));
            
            while (!queue.isEmpty()) {
                var current = queue.poll();
                if (current.getParentSpanId() != null && 
                    !current.getParentSpanId().isEmpty()) {
                    // Find parent and add to queue
                    var parent = spans.stream()
                            .filter(s -> s.getSpanId().equals(current.getParentSpanId()))
                            .findFirst();
                    if (parent.isPresent()) {
                        queue.add(parent.get());
                    }
                } else {
                    descendants.add(current);
                }
            }
            
            // Sort by start time for readability
            return descendants.stream()
                    .sorted(Comparator.comparingLong(s -> s.getStartTimeNanos()))
                    .collect(Collectors.toList());
        }

        private List<List<WorkflowSpan>> computeExecutionPaths() {
            var paths = new ArrayList<List<WorkflowSpan>>();
            
            // Find all root spans (no parent or parent not in our set)
            var roots = new HashSet<String>();
            for (var span : spans) {
                if (span.getParentSpanId() == null || 
                    !spans.stream().anyMatch(p -> p.getSpanId().equals(span.getParentSpanId()))) {
                    roots.add(span.getTraceId());
                }
            }

            // For each root, find all descendant spans in order
            for (var root : roots) {
                var path = new ArrayList<WorkflowSpan>();
                
                // BFS to collect spans in execution order
                var queue = new LinkedList<>(spans.stream()
                        .filter(s -> s.getTraceId().equals(root))
                        .sorted(Comparator.comparingLong(s -> s.getStartTimeNanos()))
                        .collect(Collectors.toList()));
                
                while (!queue.isEmpty()) {
                    path.add(queue.poll());
                }
                
                if (!path.isEmpty()) {
                    paths.add(path);
                }
            }

            return paths;
        }

        public WorkflowContext getContext() { return context; }
        public List<WorkflowSpan> getSpans() { return spans; }
        public Map<String, List<WorkflowSpan>> getByParentId() { return byParentId; }
        public Map<String, List<WorkflowSpan>> getChildrenByRoot() { return childrenByRoot; }
        public List<List<WorkflowSpan>> getExecutionPaths() { return executionPaths; }

        /** Generate a human-readable report. */
        public String generateReport() {
            StringBuilder sb = new StringBuilder();
            
            // Header
            sb.append("=".repeat(80)).append("\n");
            sb.append("WORKFLOW RECONSTRUCTION REPORT\n");
            sb.append("=".repeat(80)).append("\n\n");

            // Context summary
            sb.append(context).append("\n\n");

            // Summary statistics
            var totalSpans = spans.size();
            var genAiSpans = (long) spans.stream()
                    .filter(s -> isGenAiSpan(s.getOperationName()))
                    .count();
            
            long totalInputTokens = 0;
            long totalOutputTokens = 0;
            double totalCost = 0.0;

            for (var span : spans) {
                var ctx = span.getGenAiContext();
                totalInputTokens += ctx.getInputTokens();
                totalOutputTokens += ctx.getOutputTokens();
                totalCost += ctx.getTotalCost();
            }

            sb.append("SUMMARY\n");
            sb.append("-".repeat(40)).append("\n");
            sb.append(String.format("  Total spans: %d\n", totalSpans));
            sb.append(String.format("  GenAI operations: %d\n", genAiSpans));
            sb.append(String.format("  Total input tokens: %d\n", totalInputTokens));
            sb.append(String.format("  Total output tokens: %d\n", totalOutputTokens));
            sb.append(String.format("  Estimated cost: $%.6f\n", totalCost));
            sb.append("\n");

            // Execution timeline
            if (!executionPaths.isEmpty()) {
                sb.append("EXECUTION TIMELINE\n");
                sb.append("-".repeat(40)).append("\n\n");
                
                for (int i = 0; i < executionPaths.size(); i++) {
                    var path = executionPaths.get(i);
                    if (path.isEmpty()) continue;

                    sb.append("Path ").append(i + 1).append(": ").append(path.size()).append(" spans\n");
                    sb.append("-".repeat(40)).append("\n");

                    for (var span : path) {
                        var ctx = span.getGenAiContext();
                        String typeIndicator = isGenAiSpan(span.getOperationName()) ? "[GENAI]" : "";
                        
                        sb.append(String.format("  [%s] %s\n", 
                            formatTime(span.getStartTimeNanos()),
                            span.getOperationName() + " " + typeIndicator));

                        if (isGenAiSpan(span.getOperationName())) {
                            var c = ctx;
                            sb.append(String.format("    Model: %s\n", c.getModel()));
                            sb.append(String.format("    Request ID: %s\n", c.getRequestId() != null ? c.getRequestId() : "N/A"));
                            sb.append(String.format("    Tokens: in=%d, out=%d\n", c.getInputTokens(), c.getOutputTokens()));
                            if (c.getTotalCost() > 0) {
                                sb.append(String.format("    Cost: $%.6f\n", c.getTotalCost()));
                            }
                        }

                        // Show parent relationship
                        if (!span.getParentSpanId().isEmpty()) {
                            var parent = spans.stream()
                                    .filter(s -> s.getSpanId().equals(span.getParentSpanId()))
                                    .findFirst();
                            if (parent.isPresent()) {
                                sb.append(String.format("    Parent: %s\n", parent.get().getOperationName()));
                            }
                        }

                        // Show duration
                        long durMs = span.getDurationNanos() / 1_000_000;
                        if (durMs >= 1) {
                            sb.append(String.format("    Duration: %d ms\n", durMs));
                        }
                    }
                    
                    sb.append("\n");
                }
            }

            // Dependency graph summary
            var rootCount = childrenByRoot.size();
            if (rootCount > 0) {
                sb.append("DEPENDENCY STRUCTURE\n");
                sb.append("-".repeat(40)).append("\n\n");
                
                for (var entry : childrenByRoot.entrySet()) {
                    var rootId = entry.getKey();
                    var descendants = entry.getValue();
                    
                    // Find the root span name
                    var rootSpan = spans.stream()
                            .filter(s -> s.getTraceId().equals(rootId))
                            .findFirst();
                    
                    sb.append(String.format("Root: %s\n", 
                        rootSpan.map(s -> s.getOperationName()).orElse("unknown"));
                    sb.append(String.format("  Descendants: %d spans\n", descendants.size()));
                }
                
                sb.append("\n");
            }

            // Critical paths / bottlenecks
            var criticalPaths = findCriticalPaths();
            if (!criticalPaths.isEmpty()) {
                sb.append("CRITICAL PATHS (Longest Duration)\n");
                sb.append("-".repeat(40)).append("\n\n");
                
                for (var path : criticalPaths) {
                    long totalDurMs = 0;
                    for (var span : path) {
                        totalDurMs += span.getDurationNanos() / 1_000_000;
                    }
                    
                    sb.append(String.format("Path: %d spans, Total: %.2f ms\n", 
                        path.size(), totalDurMs));
                }
                
                sb.append("\n");
            }

            // Footer
            sb.append("=".repeat(80)).append("\n");
            sb.append("END OF REPORT").append("=".repeat(80)).append("\n");

            return sb.toString();
        }

        private static boolean isGenAiSpan(String operationName) {
            if (operationName == null || operationName.isEmpty()) return false;
            
            // Common GenAI operations from OTel semantic conventions
            var genAiOps = Set.of(
                "gen_ai.ingest",
                "gen_ai.chat.completion.create",
                "gen_ai.embeddings.embedding.create",
                "gen_ai.classification.create",
                "gen_ai.summary.create"
            );
            
            return genAiOps.contains(operationName);
        }

        private static String formatTime(long nanos) {
            if (nanos < 1