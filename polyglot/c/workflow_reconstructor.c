#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <ctype.h>

#define MAX_EVENTS 1024
#define MAX_ATTRS_PER_EVENT 32
#define MAX_ATTR_VALUE_LEN 64
#define MAX_WORKFLOW_NAME 128

typedef struct {
    char name[MAX_WORKFLOW_NAME];
    time_t timestamp;
    int is_start;
    int is_end;
} WorkflowEvent;

typedef struct {
    WorkflowEvent events[MAX_EVENTS];
    size_t count;
    char workflow_id[64];
    double total_tokens;
    char model_name[128];
} ReconstructedWorkflow;

static void parse_otel_timestamp(const char *ts, time_t *out) {
    if (!ts || !*ts) return;
    
    // Try ISO 8601 format: "2024-01-15T10:30:45.123Z" or similar
    struct tm tm = {0};
    int year, month, day, hour, min, sec;
    
    if (sscanf(ts, "%d-%d-%dT%d:%d:%d", 
               &year, &month, &day, &hour, &min, &sec) == 6) {
        tm.tm_year = year - 1900;
        tm.tm_mon = month - 1;
        tm.tm_mday = day;
        tm.tm_hour = hour;
        tm.tm_min = min;
        tm.tm_sec = sec;
        *out = mktime(&tm);
    } else if (sscanf(ts, "%d-%d-%dT%d:%d", 
                     &year, &month, &day, &hour, &min) == 5) {
        // No seconds - use current
        tm.tm_year = year - 1900;
        tm.tm_mon = month - 1;
        tm.tm_mday = day;
        tm.tm_hour = hour;
        tm.tm_min = min;
        tm.tm_sec = 0;
        *out = mktime(&tm);
    }
}

static int extract_operation_name(const char *attrs, size_t len, char *name, size_t max) {
    // Look for gen_ai.operation.name attribute
    const char *search = "gen_ai.operation.name";
    
    if (strstr(attrs, search)) {
        // Extract value after the key
        const char *val_start = strstr(attrs, search);
        val_start += strlen(search);
        
        while (*val_start && isspace(*val_start)) val_start++;
        
        int i = 0;
        while (*val_start && i < max - 1) {
            if (!isalnum(*val_start) && !*val_start == '_' && *val_start != '-' && *val_start != '.') break;
            name[i++] = *val_start++;
        }
        name[i] = '\0';
        return 1;
    }
    
    // Fallback: extract any string attribute value as operation name
    const char *first_val = strstr(attrs, "=");
    if (first_val) {
        first_val += 2;
        while (*first_val && isspace(*first_val)) first_val++;
        
        int i = 0;
        while (*first_val && i < max - 1) {
            if (!isalnum(*first_val) && !*first_val == '_' && *first_val != '-' && *first_val != '.') break;
            name[i++] = *first_val++;
        }
        name[i] = '\0';
        return 1;
    }
    
    return 0;
}

static int extract_model_name(const char *attrs, size_t len, char *name, size_t max) {
    const char *search = "gen_ai.request.model";
    
    if (strstr(attrs, search)) {
        const char *val_start = strstr(attrs, search);
        val_start += strlen(search);
        
        while (*val_start && isspace(*val_start)) val_start++;
        
        int i = 0;
        while (*val_start && i < max - 1) {
            if (!isalnum(*val_start) && !*val_start == '_' && *val_start != '-' && *val_start != '.') break;
            name[i++] = *val_start++;
        }
        name[i] = '\0';
        return 1;
    }
    
    return 0;
}

static int extract_token_usage(const char *attrs, size_t len, double *prompt_tokens, double *completion_tokens) {
    const char *search_prompt = "gen_ai.usage.prompt_tokens";
    const char *search_completion = "gen_ai.usage.completion_tokens";
    
    if (strstr(attrs, search_prompt)) {
        const char *val_start = strstr(attrs, search_prompt);
        val_start += strlen(search_prompt);
        
        while (*val_start && isspace(*val_start)) val_start++;
        
        int i = 0;
        while (*isdigit(*val_start) || *val_start == '.') {
            if (i < 64) name[i++] = *val_start++;
        }
        *prompt_tokens = atof(&attrs[search_prompt - attrs]);
    }
    
    return 1;
}

static void init_workflow(ReconstructedWorkflow *wf, const char *id) {
    memset(wf, 0, sizeof(*wf));
    if (id) strncpy(wf->workflow_id, id, sizeof(wf->workflow_id) - 1);
    wf->total_tokens = 0.0;
}

static int add_event(ReconstructedWorkflow *wf, const char *attrs, size_t len) {
    if (!wf || !attrs || wf->count >= MAX_EVENTS) return 0;
    
    WorkflowEvent *ev = &wf->events[wf->count];
    time_t ts = 0;
    
    // Extract timestamp (use current if not present)
    const char *ts_str = strstr(attrs, "timestamp");
    if (ts_str && strlen(ts_str) > 10) {
        parse_otel_timestamp(ts_str + 9, &ts);
    } else {
        ts = time(NULL);
    }
    
    ev->timestamp = ts;
    ev->is_start = 0;
    ev->is_end = 0;
    
    // Extract operation name
    if (extract_operation_name(attrs, len, ev->name, MAX_WORKFLOW_NAME)) {
        ev->is_start = 1;
    } else {
        strncpy(ev->name, "unknown", sizeof(ev->name) - 1);
    }
    
    wf->count++;
    return 1;
}

static void extract_token_totals(ReconstructedWorkflow *wf, const char *attrs, size_t len) {
    double pt = 0.0, ct = 0.0;
    
    if (extract_token_usage(attrs, len, &pt, &ct)) {
        wf->total_tokens += pt + ct;
    }
}

static void extract_model(ReconstructedWorkflow *wf, const char *attrs, size_t len) {
    if (extract_model_name(attrs, len, wf->model_name, sizeof(wf->model_name))) {
        // Keep first model found
    }
}

static int parse_event_attrs(const char *line, ReconstructedWorkflow *wf, size_t idx) {
    if (!line || !*line) return 0;
    
    // Simple attribute parsing: key=value pairs separated by space or comma
    const char *p = line;
    while (*p && wf->count < MAX_EVENTS - 1) {
        // Find next key-value pair
        const char *eq = strchr(p, '=');
        if (!eq) break;
        
        // Extract key
        size_t key_len = eq - p;
        char key[MAX_ATTR_VALUE_LEN];
        strncpy(key, p, key_len);
        key[key_len] = '\0';
        
        // Skip to value
        const char *val_start = eq + 1;
        while (*val_start && isspace(*val_start)) val_start++;
        
        if (!*val_start) break;
        
        // Extract value (until next space, comma, or end)
        size_t val_len = 0;
        const char *next_sep = strchr(val_start, ' ');
        if (!next_sep) {
            next_sep = strchr(val_start, ',');
        }
        if (!next_sep) next_sep = val_start + strlen(val_start);
        
        size_t dist = next_sep - val_start;
        if (dist < MAX_ATTR_VALUE_LEN) {
            char value[MAX_ATTR_VALUE_LEN];
            strncpy(value, val_start, dist);
            value[dist] = '\0';
            
            // Process based on key type
            if (strcmp(key, "timestamp") == 0 || strcmp(key, "@timestamp") == 0) {
                parse_otel_timestamp(value, &wf->events[wf->count].timestamp);
            } else if (strcmp(key, "gen_ai.operation.name") == 0) {
                strncpy(wf->events[wf->count].name, value + 1, MAX_WORKFLOW_NAME - 1);
                wf->events[wf->count].is_start = 1;
            } else if (strcmp(key, "gen_ai.request.model") == 0) {
                strncpy(wf->model_name, value + 1, sizeof(wf->model_name) - 1);
            } else if (strcmp(key, "gen_ai.usage.prompt_tokens") == 0 || 
                     strcmp(key, "gen_ai.usage.completion_tokens") == 0) {
                double tokens = atof(value);
                wf->total_tokens += tokens;
            }
            
            // Add as event anyway for timeline reconstruction
            if (strcmp(key, "timestamp") != 0 && strcmp(key, "@timestamp") != 0) {
                WorkflowEvent *ev = &wf->events[wf->count];
                ev->name[0] = '\0';
                ev->is_start = 1;
                wf->count++;
            }
        }
        
        // Move to next pair
        if (next_sep) {
            p = next_sep + 1;
            while (*p && isspace(*p)) p++;
        } else {
            break;
        }
    }
    
    return wf->count > 0;
}

static int reconstruct_from_file(const char *filename, ReconstructedWorkflow *wf) {
    if (!filename || !*filename) return 0;
    
    FILE *f = fopen(filename, "r");
    if (!f) {
        fprintf(stderr, "Error: cannot open file '%s'\n", filename);
        return 0;
    }
    
    char line[4096];
    int first_line = 1;
    
    while (fgets(line, sizeof(line), f)) {
        if (!first_line) {
            // First non-empty line is the event data
            parse_event_attrs(line, wf, 0);
        } else {
            parse_event_attrs(line, wf, wf->count);
        }
        
        first_line = 0;
    }
    
    fclose(f);
    return wf->count > 0;
}

static void print_workflow(ReconstructedWorkflow *wf) {
    if (!wf || !wf->count) {
        printf("Empty or uninitialized workflow\n");
        return;
    }
    
    printf("\n=== RECONSTRUCTED WORKFLOW ===\n");
    printf("ID: %s\n", wf->workflow_id[0] ? wf->workflow_id : "(unknown)");
    printf("Model: %s\n", wf->model_name[0] ? wf->model_name : "(not specified)");
    printf("Total Tokens: %.2f\n", wf->total_tokens);
    printf("Events: %zu\n", wf->count);
    
    printf("\n--- Timeline ---\n");
    for (size_t i = 0; i < wf->count; i++) {
        WorkflowEvent *ev = &wf->events[i];
        
        char time_str[64];
        if (ev->timestamp) {
            strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S", 
                    localtime(&ev->timestamp));
        } else {
            strcpy(time_str, "unknown");
        }
        
        printf("[%s] [%s]", time_str, ev->is_start ? "START" : (ev->is_end ? "END" : "EVENT"));
        
        if (ev->name[0]) {
            printf(" - %s", ev->name);
        }
        printf("\n");
    }
    
    printf("=========================\n\n");
}

static void print_summary(ReconstructedWorkflow *wf) {
    printf("\n=== SUMMARY ===\n");
    printf("Events parsed: %zu\n", wf ? wf->count : 0);
    printf("Total tokens processed: %.2f\n", wf ? wf->total_tokens : 0.0);
    printf("Model detected: %s\n", wf && wf->model_name[0] ? wf->model_name : "none");
}

static void demo_reconstructor(void) {
    // Sample OTel GenAI log line format
    const char *sample_log = 
        "@timestamp=2024-01-15T10:30:45.123Z "
        "gen_ai.operation.name=chat "
        "gen_ai.request.model=gpt-4o "
        "gen_ai.usage.prompt_tokens=128 "
        "gen_ai.usage.completion_tokens=64";
    
    ReconstructedWorkflow wf;
    init_workflow(&wf, "demo-workflow-001");
    
    printf("Testing workflow reconstructor with sample data:\n\n");
    printf("Input log line:\n%s\n", sample_log);
    
    parse_event_attrs(sample_log, &wf, 0);
    
    print_summary(&wf);
    print_workflow(&wf);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        printf("Usage: %s <logfile> [--demo]\n", argv[0]);
        printf("\nReconstructs agentic workflows from OTel GenAI semantic conventions.\n");
        
        // Run demo if no args or --demo flag
        if (argc == 1 || strcmp(argv[1], "--demo") == 0) {
            demo_reconstructor();
        } else {
            ReconstructedWorkflow wf;
            init_workflow(&wf, "auto-detected");
            
            int success = reconstruct_from_file(argv[1], &wf);
            
            if (success) {
                print_summary(&wf);
                print_workflow(&wf);
            } else {
                printf("Failed to parse workflow from file.\n");
            }
        }
    }
    
    return 0;
}