require 'json'
require 'time'

module AgentLog
  # ============== DATA MODELS ==============

  # Represents parsed semantic attributes from an OTel span
  SpanAttributes = Struct.new(
    :operation_name,       # e.g., "completion", "chat"
    :system,               # e.g., "gpt-4o", "claude-3-5-sonnet"
    :model_id,             # normalized model identifier
    :input_text,           # concatenated input prompts
    :output_text,          # concatenated completions
    :input_tokens,         # integer or nil
    :output_tokens,        # integer or nil
    :total_tokens,         # integer or nil
    :response_id,          # unique response identifier
    :latency_ms,           # total latency in milliseconds
    :tool_calls,           # array of tool/function calls
    :metadata              # any additional context
  )

  # ============== CONFIGURATION ==============

  GENAI_ATTR_PREFIX = 'gen_ai.'
  
  OPERATIONS = {
    'completion' => 'Completion',
    'chat' => 'Chat',
    'embedding' => 'Embedding',
    'classification' => 'Classification',
    'summarization' => 'Summarization',
    'translation' => 'Translation',
    'transcription' => 'Transcription'
  }

  # ============== PARSER IMPLEMENTATION ==============

  class SemanticParser
    def self.parse(span_attributes)
      new.parse(span_attributes)
    end

    def initialize
      @input_parts = []
      @output_parts = []
      @tool_calls = []
    end

    def parse(raw_attrs)
      # Extract operation name with default fallback
      operation_name = extract_operation(raw_attrs)
      
      # Extract system/provider info
      system = extract_system(raw_attrs)
      
      # Extract model identifier and normalize it
      model_id = extract_model(raw_attrs)
      
      # Extract token usage metrics
      input_tokens = extract_token_count('input', raw_attrs)
      output_tokens = extract_token_count('output', raw_attrs)
      total_tokens = extract_token_count('total', raw_attrs)
      
      # Extract response identifier if present
      response_id = extract_response_id(raw_attrs)
      
      # Extract latency if available
      latency_ms = extract_latency(raw_attrs)
      
      # Build input/output text from message arrays or single prompt/response
      @input_parts.concat(extract_inputs(raw_attrs))
      @output_parts.concat(extract_outputs(raw_attrs))
      
      # Parse tool/function calls
      @tool_calls.concat(extract_tool_calls(raw_attrs))
      
      # Build final structured result
      SpanAttributes.new(
        operation_name: operation_name,
        system: system,
        model_id: model_id,
        input_text: build_combined_text(@input_parts),
        output_text: build_combined_text(@output_parts),
        input_tokens: input_tokens,
        output_tokens: output_tokens,
        total_tokens: total_tokens,
        response_id: response_id,
        latency_ms: latency_ms,
        tool_calls: @tool_calls,
        metadata: extract_metadata(raw_attrs)
      )
    end

    private

    def extract_operation(attrs)
      operation = attrs['gen_ai.operation.name'] || 'unknown'
      
      # Normalize to title case with proper formatting
      normalized = OPERATIONS[operation] || operation.capitalize
      
      # Handle compound operations (e.g., "chat.completion")
      if compound_op = normalize_compound_operation(operation)
        return "#{compound_op}::#{normalized}"
      end
      
      normalized
    end

    def extract_system(attrs)
      system = attrs['gen_ai.system'] || 
               attrs['attributes.gen_ai.system'] ||
               'unknown'
      
      # Normalize common provider prefixes
      case system.downcase
      when /claude/ then "Anthropic::#{system}"
      when /gemini|google/ then "Google::#{system}"
      when /dall-e|vision/ then "OpenAI Vision::#{system}"
      else system
      end
    end

    def extract_model(attrs)
      # Priority: explicit model field, then request.model, then infer from system
      if attrs['gen_ai.request.model']
        return normalize_model_name(attrs['gen_ai.request.model'])
      end
      
      # Try to infer from common patterns
      inferred = infer_model_from_context(attrs)
      
      inferred || 'unknown'
    end

    def extract_token_count(type, attrs)
      key = "attributes.gen_ai.usage.#{type}_tokens"
      value = attrs[key] || attrs["gen_ai.usage.#{type}_tokens"]
      
      return Integer(value) if value
      
      # Calculate from total_tokens and one known type
      if attrs['gen_ai.usage.total_tokens'] && 
         (attrs['gen_ai.usage.input_tokens'].nil? || attrs['gen_ai.usage.output_tokens'].nil?)
        total = Integer(attrs['gen_ai.usage.total_tokens'])
        input = attrs['gen_ai.usage.input_tokens']
        output = attrs['gen_ai.usage.output_tokens']
        
        if input && !output.nil?
          return total - (Integer(input) || 0)
        elsif output && !input.nil?
          return total - (Integer(output) || 0)
        end
      end
      
      nil
    end

    def extract_response_id(attrs)
      attrs['gen_ai.response.id'] || 
      attrs['attributes.gen_ai.response.id'] ||
      attrs['response_id'] ||
      'unknown'
    end

    def extract_latency(attrs)
      # Try multiple possible latency fields
      candidates = [
        'attributes.latency',
        'latency_ms',
        'duration_ms',
        'attributes.duration'
      ]
      
      candidates.each do |candidate|
        value = attrs[candidate]
        return Integer(value) if value && !value.to_s.empty?
      end
      
      nil
    end

    def extract_inputs(attrs)
      # Handle message arrays (chat format)
      messages = attrs['attributes.gen_ai.messages'] || 
                 attrs['messages'] || []
      
      return messages.map { |m| m.is_a?(Hash) ? m['content'] : m.to_s } if messages
      
      # Single prompt/response pair
      if attrs['prompt'] && !attrs['response'].nil?
        [attrs['prompt'], attrs['response']]
      elsif attrs['input']
        [attrs['input']]
      else
        []
      end
    end

    def extract_outputs(attrs)
      # Handle message arrays (chat format)
      messages = attrs['attributes.gen_ai.messages'] || 
                 attrs['messages'] || []
      
      return messages.map { |m| m.is_a?(Hash) ? m['content'] : m.to_s } if messages
      
      # Single response
      [attrs['response'] || attrs['output']]
    end

    def build_combined_text(parts)
      parts.compact.join("\n\n---\n\n")
    end

    def extract_tool_calls(attrs)
      tool_fields = [
        'attributes.gen_ai.tool_calls',
        'tool_calls',
        'function_calls'
      ]
      
      return [] if attrs['gen_ai.operation.name'] == 'embedding' || 
                   attrs['gen_ai.operation.name'] == 'completion'
      
      tools = tool_fields.map { |f| attrs[f] }.compact
      
      # Parse tool call objects into structured format
      parsed_tools = tools.flat_map do |tool_data|
        next [] unless tool_data.is_a?(Array)
        
        tool_data.map do |t|
          {
            name: t['name'] || t['function']['name'],
            arguments: t['arguments'] || t['function']['arguments'],
            id: t['id'] || t['call_id'] || 'unknown'
          }
        end
      end
      
      parsed_tools.flatten(1)
    end

    def extract_metadata(attrs)
      # Extract any custom metadata fields
      {
        version: attrs['attributes.version'] || ENV['AGENTLOG_VERSION'],
        timestamp: Time.now.iso8601,
        span_id: attrs['span_id'] || 'unknown',
        trace_id: attrs['trace_id'] || 'unknown'
      }
    end

    def normalize_model_name(name)
      # Normalize common model name patterns
      normalized = name.to_s.strip
      
      # Handle version suffixes
      if match = normalized.match(/(gpt-|claude-|gemini-)([^0-9]+)?(\d+)?/)
        base, prefix, version = match.captures
        "#{prefix}#{version}"
      else
        normalized
      end
    rescue
      name.to_s.strip
    end

    def infer_model_from_context(attrs)
      # Infer model from system/provider hints
      case attrs['gen_ai.system']&.downcase
      when /claude/ then "claude-3-5-sonnet"
      when /gemini|google.*vision/ then "gemini-1.5-flash"
      when /dall-e/ then "gpt-4o-dalle"
      else nil
      end
    end

    def normalize_compound_operation(operation)
      # Split compound operations like "chat.completion"
      parts = operation.split('.')
      
      return parts.last if parts.length > 1
      
      operation
    end
  end

  # ============== CONVENIENCE METHODS ==============

  class << self
    def parse_span(span_attrs)
      SemanticParser.parse(span_attrs)
    end

    def format_attributes(attrs, pretty: true)
      result = attrs.to_h.transform_values do |v|
        v.is_a?(Time) ? v.iso8601 : (v.is_a?(Integer) ? v : v.to_s)
      end
      
      pretty ? JSON.pretty_generate(result) : JSON.generate(result)
    end

    def create_sample_span(attrs = {})
      default_attrs = {
        'gen_ai.operation.name' => 'chat',
        'gen_ai.system' => 'gpt-4o',
        'gen_ai.request.model' => 'gpt-4o-2024-11-20',
        'gen_ai.usage.input_tokens' => 156,
        'gen_ai.usage.output_tokens' => 89,
        'gen_ai.usage.total_tokens' => 245,
        'gen_ai.response.id' => 'resp_abc123',
        'attributes.latency' => 1250,
        'messages' => [
          { role: 'user', content: 'Hello, how are you?' },
          { role: 'assistant', content: 'I am doing well!' }
        ],
        'tool_calls' => [
          { id: 'call_1', name: 'search_web', arguments: { query: 'weather' } },
          { id: 'call_2', name: 'get_weather', arguments: { location: 'SF' } }
        ]
      }.merge(attrs)

      SemanticParser.parse(default_attrs)
    end
  end

  # ============== DEMO / ENTRY POINT ==============

  if __FILE__ == $0
    puts "=== AgentLog OTel Semantic Parser Demo ==="
    puts ""

    # Sample 1: Basic chat completion with messages
    sample_1 = {
      'gen_ai.operation.name' => 'chat',
      'gen_ai.system' => 'gpt-4o',
      'gen_ai.request.model' => 'gpt-4o-2024-05-13',
      'gen_ai.usage.input_tokens' => 98,
      'gen_ai.usage.output_tokens' => 47,
      'gen_ai.response.id' => 'resp_demo_001',
      'attributes.latency' => 892,
      'messages' => [
        { role: 'user', content: 'What is the capital of France?' },
        { role: 'assistant', content: 'The capital of France is Paris.' }
      ]
    }

    puts "Sample 1 - Basic Chat:"
    attrs_1 = SemanticParser.parse(sample_1)
    puts "  Operation: #{attrs_1.operation_name}"
    puts "  Model:     #{attrs_1.model_id}"
    puts "  Tokens:    I=#{attrs_1.input_tokens}, O=#{attrs_1.output_tokens}, T=#{attrs_1.total_tokens}"
    puts "  Latency:   #{attrs_1.latency_ms}ms"
    puts "  Response:  #{attrs_1.response_id}"
    puts ""

    # Sample 2: With tool calls
    sample_2 = {
      'gen_ai.operation.name' => 'chat',
      'gen_ai.system' => 'claude-3.5-sonnet',
      'messages' => [
        { role: 'user', content: 'Search for the latest news about AI.' },
        { role: 'assistant', content: '', tool_calls: [
          { id: 'call_123', name: 'search_web', arguments: { query: 'AI news 2024' } }
        ]},
        { role: 'tool', content: 'Found 5 articles...', tool_call_id: 'call_123' },
        { role: 'assistant', content: 'Here are the top results...' }
      ],
      'gen_ai.usage.input_tokens' => 203,
      'gen_ai.usage.output_tokens' => 147,
      'attributes.latency' => 2156,
      'gen_ai.response.id' => 'resp_demo_002'
    }

    puts "Sample 2 - With Tool Calls:"
    attrs_2 = SemanticParser.parse(sample_2)
    puts "  Operation: #{attrs_2.operation_name}"
    puts "  System:    #{attrs_2.system}"
    puts "  Model:     #{attrs_2.model_id}"
    puts "  Tokens:    I=#{attrs_2.input_tokens}, O=#{attrs_2.output_tokens}"
    puts "  Latency:   #{attrs_2.latency_ms}ms"
    if attrs_2.tool_calls.any?
      puts "  Tools:     #{attrs_2.tool_calls.size} calls detected"
      attrs_2.tool_calls.each_with_index do |tc, i|
        puts "    [#{i+1}] #{tc[:name]} - #{tc[:arguments].to_s}"
      end
    else
      puts "  Tools:     No tool calls found"
    end
    puts ""

    # Sample 3: Embedding operation (no messages)
    sample_3 = {
      'gen_ai.operation.name' => 'embedding',
      'gen_ai.system' => 'text-embedding-3-small',
      'gen_ai.request.model' => 'text-embedding-3-small',
      'gen_ai.usage.input_tokens' => 12,
      'gen_ai.usage.output_tokens' => 768,
      'attributes.latency' => 45,
      'input': 'Hello world test embedding',
      'response_id': 'embed_resp_003'
    }

    puts "Sample 3 - Embedding:"
    attrs_3 = SemanticParser.parse(sample_3)
    puts "  Operation: #{attrs_3.operation_name}"
    puts "  Model:     #{attrs_3.model_id}"
    puts "  Tokens:    I=#{attrs_3.input_tokens}, O=#{attrs_3.output_tokens}"
    puts "  Latency:   #{attrs_3.latency_ms}ms"
    puts "  Input:     '#{attrs_3.input_text[0..50]}...'" if attrs_3.input_text.length > 50
    puts ""

    # Summary statistics
    puts "=== Summary Statistics ==="
    total_tokens = [attrs_1, attrs_2, attrs_3].sum { |a| a.total_tokens || 0 }
    total_latency = [attrs_1, attrs_2, attrs_3].sum {