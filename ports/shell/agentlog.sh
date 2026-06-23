#!/usr/bin/env sh
# POSIX-shell port of the agentlog audit core, built on `jq`.
#
# Mirrors the reference Python CLI's `audit` surface: loads OTel GenAI spans
# (JSON array or {"spans":[...]}), scans each span's string attributes for
# secret/PII leaks, prompt-injection markers, and high-blast-radius tool
# calls, then prints metrics + findings as JSON on stdout.
#
#   ./agentlog.sh ../../demos/01-basic/spans.json
#   cat spans.json | ./agentlog.sh -
#
# Exit code: 1 if any critical/high finding is present, else 0; 2 on bad input.
# Offline only: reads local files / stdin, never the network.
#
# Requires: jq (https://stedolan.github.io/jq/). On Debian/Ubuntu: apt-get install jq
set -eu

VERSION="1.2.5"

SRC="${1:--}"
if [ "$SRC" = "-" ]; then
  INPUT="$(cat)"
else
  if [ ! -f "$SRC" ]; then
    echo "error: file not found: $SRC" >&2
    exit 2
  fi
  INPUT="$(cat "$SRC")"
fi

if [ -z "$(printf '%s' "$INPUT" | tr -d '[:space:]')" ]; then
  echo "error: empty input: no spans to load" >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required for the shell port" >&2
  exit 2
fi

# The whole audit is one jq program. It is delivered via a quoted heredoc so
# the shell never touches its contents (the program contains apostrophes and
# braces that would otherwise break shell quoting).
JQ_PROG="$(cat <<'JQ'
  def is_error($s): ($s|tostring|ascii_upcase) as $u
      | ($u != "OK" and $u != "UNSET" and $u != "" and $u != "0" and $u != "1");
  def blob: (.attributes // {}) | [ to_entries[]
        | if (.value|type)=="string" then .value else (.value|tostring) end ] | join("\n");

  (if type == "array" then . elif has("spans") then .spans else [.] end) as $spans
  | ($spans | map(.span_id // .spanId // .id)) as $ids
  | ["shell","bash","exec","execute_command","run_command","delete_file","rm",
     "write_file","http_request","send_email","transfer_funds","execute_sql",
     "sql","kubectl","terraform_apply"] as $danger
  | ["ignore previous instructions","ignore all previous","disregard the above",
     "system prompt","you are now","new instructions:","reveal your",
     "exfiltrate"] as $markers

  | ( [ $spans[]
      | . as $s
      | ($s.span_id // $s.spanId // $s.id // "") as $sid
      | (($s.attributes // {})["gen_ai.tool.name"] // "") as $tool
      | ($s | blob) as $b
      | ($b | ascii_downcase) as $low
      | [
          ( if is_error($s.status) then
              {severity:"high",code:"span_error",span_id:$sid,
               message:("span ended with status " + ($s.status|tostring))}
            else empty end ),
          ( ($s.parent_span_id // $s.parentSpanId) as $p
            | if ($p != null and $p != "" and ([$ids[]|select(.==$p)]|length==0)) then
                {severity:"medium",code:"broken_trace",span_id:$sid,
                 message:("parent_span_id " + ($p|tojson) + " not present in trace")}
              else empty end ),
          ( if ($b | test("AKIA[0-9A-Z]{16}")) then
              {severity:"critical",code:"secret_leak",span_id:$sid,
               message:"possible aws_access_key exposed in span attributes"} else empty end ),
          ( if ($b | test("-----BEGIN (RSA |EC )?PRIVATE KEY-----")) then
              {severity:"critical",code:"secret_leak",span_id:$sid,
               message:"possible private_key exposed in span attributes"} else empty end ),
          ( if ($b | test("(?i)bearer\\s+[A-Za-z0-9._-]{20,}")) then
              {severity:"critical",code:"secret_leak",span_id:$sid,
               message:"possible bearer_token exposed in span attributes"} else empty end ),
          ( if ($b | test("(?i)(api[_-]?key|secret|password)\\s*[=:]\\s*[A-Za-z0-9._-]{8,}")) then
              {severity:"critical",code:"secret_leak",span_id:$sid,
               message:"possible api_key_assign exposed in span attributes"} else empty end ),
          ( if ($b | test("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")) then
              {severity:"critical",code:"secret_leak",span_id:$sid,
               message:"possible email exposed in span attributes"} else empty end ),
          ( if ($tool != "" and ([$danger[]|select(.==($tool|ascii_downcase))]|length>0)) then
              {severity:"high",code:"dangerous_tool",span_id:$sid,
               message:("high-blast-radius tool " + ($tool|tojson) + " invoked")} else empty end ),
          ( ([$markers[]|select(. as $m | $low|contains($m))]|.[0]) as $hit
            | if $hit != null then
                {severity:"high",code:"prompt_injection",span_id:$sid,
                 message:("prompt-injection marker " + ($hit|tojson) + " found in span content")}
              else empty end )
        ]
      ] | add | map(select(. != null)) ) as $findings

  | {critical:0,high:1,medium:2,low:3,info:4} as $rank
  | ($findings | sort_by($rank[.severity], .code)) as $sorted

  | {
      tool: "agentlog",
      version: $ver,
      metrics: {
        spans: ($spans|length),
        tool_calls: ([ $spans[]
          | (((.attributes // {})["gen_ai.operation.name"]) == "execute_tool")
            or (((.attributes // {})["gen_ai.tool.name"]) // "" | length > 0)
          | select(.) ] | length),
        llm_calls: ([ $spans[]
          | (((.attributes // {})["gen_ai.operation.name"]) // "")
          | select(. == "chat" or . == "text_completion" or . == "generate_content") ] | length),
        errors: ([ $spans[] | select(is_error(.status)) ] | length)
      },
      findings: $sorted,
      failing: ([ $sorted[] | select(.severity=="critical" or .severity=="high") ] | length > 0)
    }
JQ
)"

RESULT="$(printf '%s' "$INPUT" | jq --arg ver "$VERSION" "$JQ_PROG")"

printf '%s\n' "$RESULT"

# Exit non-zero when blocking findings are present.
if printf '%s' "$RESULT" | jq -e '.failing' >/dev/null; then
  exit 1
fi
exit 0
