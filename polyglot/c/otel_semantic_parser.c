/*
 * polyglot/c/otel_semantic_parser.c
 * 
 * OpenTelemetry GenAI Semantic Conventions Parser for AgentLog Tool
 * 
 * Parses OTel spans containing LLM/Agent telemetry and normalizes them
 * into queryable structures. Supports JSON format (most common in practice).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdbool.h>

/* ==================== CONFIGURATION ==================== */

#define MAX_ATTR_NAME_LEN 256
#define MAX_ATTR_VALUE_LEN 4096
#define MAX_SPAN_COUNT 1024
#define MAX_WORKFLOW_DEPTH 32

/* ==================== DATA STRUCTURES ==================== */

typedef enum {
    OTEL_TYPE_STRING,
    OTEL_TYPE_INT64,
    OTEL_TYPE_DOUBLE,
    OTEL_TYPE_BOOL,
    OTEL_TYPE_ARRAY,
    OTEL_TYPE_OBJECT
} otel_value_type_t;

typedef struct {
    char name[MAX_ATTR_NAME_LEN];
    union {
        char str_val[MAX_ATTR_VALUE_LEN];
        int64_t int_val;
        double dbl_val;
        bool bool_val;
        size_t arr_len;
        void *obj_ptr;
    };
    otel_value_type_t type;
} otel_attribute_t;

typedef struct {
    char name[MAX_ATTR_NAME_LEN];
    otel_attribute_t value;
} otel_key_value_pair_t;

typedef enum {
    GENAI_OP_UNKNOWN,
    GENAI_OP_CHAT,
    GENAI_OP_COMPLETION,
    GENAI_OP_EMBEDDING,
    GENAI_OP_RERANK,
    GENAI_OP_SUMMARIZATION,
    GENAI_OP_TRANSLATION,
    GENAI_OP_CLASSIFICATION,
    GENAI_OP_OTHER
} genai_operation_type_t;

typedef struct {
    char name[MAX_ATTR_NAME_LEN];
    otel_attribute_t value;
} genai_usage_field_t;

typedef enum {
    AGENT_ROLE_UNKNOWN,
    AGENT_ROLE_USER,
    AGENT_ROLE_SYSTEM,
    AGENT_ROLE_AGENT,
    AGENT_ROLE_TOOL,
    AGENT_ROLE_OBSERVER
} agent_role_t;

/* ==================== PARSER CONTEXT ==================== */

typedef struct {
    char *json_buffer;
    size_t buffer_len;
    size_t pos;
    bool eof;
    
    /* Accumulated parsed data */
    otel_key_value_pair_t attributes[MAX_SPAN_COUNT];
    int attr_count;
    genai_usage_field_t usage_fields[16];
    int usage_count;
    
    /* Extracted metadata */
    char operation_name[MAX_ATTR_NAME_LEN];
    char model_id[MAX_ATTR_VALUE_LEN];
    char response_id[MAX_ATTR_VALUE_LEN];
    double completion_tokens;
    double prompt_tokens;
    double total_tokens;
    genai_operation_type_t op_type;
    
    /* Agent-specific */
    agent_role_t role;
    char workflow_run_id[MAX_ATTR_NAME_LEN];
    char tool_name[MAX_ATTR_NAME_LEN];
    int64_t tool_call_index;
} otel_parser_context_t;

/* ==================== UTILITY FUNCTIONS ==================== */

static inline void ctx_init(otel_parser_context_t *ctx) {
    memset(ctx, 0, sizeof(*ctx));
    ctx->buffer_len = 0;
    ctx->pos = 0;
    ctx->eof = false;
    ctx->attr_count = 0;
    ctx->usage_count = 0;
}

static inline void ctx_reset(otel_parser_context_t *ctx) {
    memset(ctx, 0, sizeof(*ctx));
    ctx->buffer_len = 0;
    ctx->pos = 0;
    ctx->eof = false;
    ctx->attr_count = 0;
    ctx->usage_count = 0;
}

static inline int peek_char(otel_parser_context_t *ctx) {
    if (ctx->pos >= ctx->buffer_len || ctx->eof) return -1;
    return (unsigned char)ctx->json_buffer[ctx->pos];
}

static inline void advance(otel_parser_context_t *ctx, size_t n) {
    ctx->pos += n;
    if (ctx->pos >= ctx->buffer_len) ctx->eof = true;
}

static inline int skip_whitespace(otel_parser_context_t *ctx) {
    while (!ctx->eof && isspace((unsigned char)peek_char(ctx))) {
        advance(ctx, 1);
    }
    return peek_char(ctx);
}

/* ==================== STRING PARSING ==================== */

static inline int parse_string_value(otel_parser_context_t *ctx, 
                                      otel_attribute_t *attr,
                                      bool allow_empty) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) != '"') {
        return -1; /* Not a string */
    }
    
    advance(ctx, 1); /* Skip opening quote */
    
    char *start = ctx->json_buffer + ctx->pos;
    size_t len = 0;
    
    while (!ctx->eof) {
        unsigned char c = peek_char(ctx);
        
        if (c == '"') {
            advance(ctx, 1); /* Skip closing quote */
            break;
        } else if (c == '\\' && !ctx->eof) {
            advance(ctx, 1);
            if (!ctx->eof) {
                unsigned char esc = peek_char(ctx);
                switch (esc) {
                    case '"': advance(ctx, 1); c = '"'; break;
                    case '\\': advance(ctx, 1); c = '\\'; break;
                    case '/': advance(ctx, 1); c = '/'; break;
                    case 'b': advance(ctx, 1); c = '\b'; break;
                    case 'f': advance(ctx, 1); c = '\f'; break;
                    case 'n': advance(ctx, 1); c = '\n'; break;
                    case 'r': advance(ctx, 1); c = '\r'; break;
                    case 't': advance(ctx, 1); c = '\t'; break;
                    default: 
                        if (esc >= '0' && esc <= '7') {
                            /* Octal escape */
                            int val = 0;
                            for (int i = 0; i < 3 && !ctx->eof; i++) {
                                unsigned char d = peek_char(ctx);
                                if (d >= '0' && d <= '7') {
                                    val = val * 8 + (d - '0');
                                    advance(ctx, 1);
                                } else break;
                            }
                            c = val;
                        } else {
                            /* Unknown escape, pass as-is */
                            c = esc;
                        }
                        break;
                }
            }
        } else if (c == '\0') {
            /* Null terminator in buffer */
            advance(ctx, 1);
            break;
        } else {
            if (!allow_empty && len == 0) return -1;
            if (len < MAX_ATTR_VALUE_LEN - 1) {
                attr->str_val[len++] = c;
            }
            advance(ctx, 1);
        }
    }
    
    attr->type = OTEL_TYPE_STRING;
    strncpy(attr->str_val, start, len);
    attr->str_val[len] = '\0';
    return (int)len;
}

static inline int parse_integer_value(otel_parser_context_t *ctx, 
                                       otel_attribute_t *attr) {
    skip_whitespace(ctx);
    
    if (!isdigit((unsigned char)peek_char(ctx)) && peek_char(ctx) != '-') {
        return -1; /* Not an integer */
    }
    
    int64_t value = 0;
    bool negative = false;
    
    if (peek_char(ctx) == '-') {
        negative = true;
        advance(ctx, 1);
    }
    
    while (!ctx->eof && isdigit((unsigned char)peek_char(ctx))) {
        value = value * 10 + (peek_char(ctx) - '0');
        advance(ctx, 1);
    }
    
    attr->type = OTEL_TYPE_INT64;
    attr->int_val = negative ? -value : value;
    return 1;
}

static inline int parse_double_value(otel_parser_context_t *ctx, 
                                      otel_attribute_t *attr) {
    skip_whitespace(ctx);
    
    if (!isdigit((unsigned char)peek_char(ctx)) && peek_char(ctx) != '-' && 
        peek_char(ctx) != '.' && peek_char(ctx) != 'e' && peek_char(ctx) != 'E') {
        return -1; /* Not a double */
    }
    
    char buf[64];
    size_t i = 0;
    
    while (!ctx->eof && (isdigit((unsigned char)peek_char(ctx)) || 
                         peek_char(ctx) == '.' || peek_char(ctx) == 'e' || 
                         peek_char(ctx) == 'E' || peek_char(ctx) == '-' || 
                         peek_char(ctx) == '+' || peek_char(ctx) == '_')) {
        if (i < 63) buf[i++] = peek_char(ctx);
        advance(ctx, 1);
    }
    
    buf[i] = '\0';
    attr->type = OTEL_TYPE_DOUBLE;
    attr->dbl_val = strtod(buf, NULL);
    return 1;
}

static inline int parse_boolean_value(otel_parser_context_t *ctx, 
                                       otel_attribute_t *attr) {
    skip_whitespace(ctx);
    
    if (strncmp(ctx->json_buffer + ctx->pos, "true", 4) == 0) {
        attr->type = OTEL_TYPE_BOOL;
        attr->bool_val = true;
        advance(ctx, 4);
        return 1;
    } else if (strncmp(ctx->json_buffer + ctx->pos, "false", 5) == 0) {
        attr->type = OTEL_TYPE_BOOL;
        attr->bool_val = false;
        advance(ctx, 5);
        return 1;
    }
    
    return -1;
}

/* ==================== ATTRIBUTE PARSING ==================== */

static int parse_attribute_value(otel_parser_context_t *ctx, 
                                  otel_attribute_t *attr) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) == '"') {
        return parse_string_value(ctx, attr, true);
    } else if (isdigit((unsigned char)peek_char(ctx)) || peek_char(ctx) == '-') {
        /* Try int first */
        if (parse_integer_value(ctx, attr) > 0) {
            return 1;
        }
        /* Fall back to double for floats */
        if (parse_double_value(ctx, attr) > 0) {
            return 1;
        }
    } else if (peek_char(ctx) == 't' || peek_char(ctx) == 'f') {
        return parse_boolean_value(ctx, attr);
    } else if (peek_char(ctx) == '{' || peek_char(ctx) == '[') {
        /* Complex types - store as string for now */
        char buf[MAX_ATTR_VALUE_LEN];
        size_t i = 0;
        
        while (!ctx->eof && !isspace((unsigned char)peek_char(ctx))) {
            if (i < MAX_ATTR_VALUE_LEN - 1) {
                buf[i++] = peek_char(ctx);
            }
            advance(ctx, 1);
        }
        
        attr->type = OTEL_TYPE_OBJECT;
        strncpy(attr->str_val, buf, i);
        return 1;
    }
    
    return -1;
}

static int parse_attribute_name(otel_parser_context_t *ctx, 
                                 otel_attribute_t *attr) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) == '"') {
        return parse_string_value(ctx, attr, false);
    } else if (isdigit((unsigned char)peek_char(ctx)) || peek_char(ctx) == '-' ||
               peek_char(ctx) == '_' || peek_char(ctx) == '.') {
        /* Unquoted key - common in some OTel formats */
        size_t i = 0;
        
        while (!ctx->eof && (isalnum((unsigned char)peek_char(ctx)) || 
                             peek_char(ctx) == '.' || peek_char(ctx) == '_' ||
                             peek_char(ctx) == '-' || peek_char(ctx) == ':')) {
            if (i < MAX_ATTR_NAME_LEN - 1) {
                attr->name[i] = peek_char(ctx);
                i++;
            }
            advance(ctx, 1);
        }
        
        attr->name[i] = '\0';
        return 1;
    }
    
    return -1;
}

/* ==================== JSON OBJECT PARSING ==================== */

static int parse_json_object(otel_parser_context_t *ctx) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) != '{') {
        return -1; /* Not an object start */
    }
    
    advance(ctx, 1); /* Skip opening brace */
    
    while (!ctx->eof) {
        skip_whitespace(ctx);
        
        if (peek_char(ctx) == '}') {
            advance(ctx, 1);
            return 0; /* End of object */
        } else if (peek_char(ctx) == ',') {
            advance(ctx, 1);
            continue;
        }
        
        /* Parse key-value pair */
        otel_attribute_t kv_attr;
        
        if (parse_attribute_name(ctx, &kv_attr)) {
            skip_whitespace(ctx);
            
            if (peek_char(ctx) == ':') {
                advance(ctx, 1);
                
                otel_attribute_t val_attr;
                memset(&val_attr, 0, sizeof(val_attr));
                
                if (parse_attribute_value(ctx, &val_attr)) {
                    /* Store as key-value pair */
                    strncpy(kv_attr.name, ctx->json_buffer + ctx->pos - 
                            strlen("key") - 1, MAX_ATTR_NAME_LEN);
                    
                    /* For now, store value in main attr slot */
                    memcpy(&kv_attr.value, &val_attr, sizeof(val_attr));
                } else {
                    return -1;
                }
            }
        } else {
            return -1;
        }
    }
    
    return 0;
}

/* ==================== GENAI SEMANTIC PARSING ==================== */

static int parse_genai_operation_name(otel_parser_context_t *ctx) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) != '"') {
        return -1;
    }
    
    advance(ctx, 1); /* Skip opening quote */
    
    char start_pos = ctx->pos;
    size_t len = 0;
    
    while (!ctx->eof && peek_char(ctx) != '"') {
        if (len < MAX_ATTR_NAME_LEN - 1) {
            ctx->operation_name[len] = peek_char(ctx);
            len++;
        }
        advance(ctx, 1);
    }
    
    if (peek_char(ctx) == '"') {
        advance(ctx, 1); /* Skip closing quote */
    }
    
    ctx->operation_name[len] = '\0';
    return 1;
}

static int parse_genai_model_id(otel_parser_context_t *ctx) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) != '"') {
        return -1;
    }
    
    advance(ctx, 1); /* Skip opening quote */
    
    char start_pos = ctx->pos;
    size_t len = 0;
    
    while (!ctx->eof && peek_char(ctx) != '"') {
        if (len < MAX_ATTR_VALUE_LEN - 1) {
            ctx->model_id[len] = peek_char(ctx);
            len++;
        }
        advance(ctx, 1);
    }
    
    if (peek_char(ctx) == '"') {
        advance(ctx, 1); /* Skip closing quote */
    }
    
    ctx->model_id[len] = '\0';
    return 1;
}

static int parse_genai_response_id(otel_parser_context_t *ctx) {
    skip_whitespace(ctx);
    
    if (peek_char(ctx) != '"') {
        return -1;
    }
    
    advance(ctx, 1); /* Skip opening quote */
    
    char start_pos = ctx->pos;
    size_t len = 0;
    
    while (!ctx->eof && peek_char(ctx) != '"') {
        if (len < MAX_ATTR_VALUE_LEN - 1) {
            ctx->response_id[len] = peek_char(ctx