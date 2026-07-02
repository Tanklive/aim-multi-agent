#!/bin/bash
# Hermes AIM Adapter v2.0
: ${AIM_API_URL:=""}
: ${AIM_API_CREDENTIAL:=""}
: ${HERMES_API_URL:="${AIM_API_URL}"}
: ${HERMES_API_KEY:="${AIM_API_CREDENTIAL}"}
: ${AGENT_BIN:="$HOME/.local/bin/hermes"}
: ${ADAPTER_TIMEOUT:=300}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_BIN:="$HOME/.aim/bin"}
MODE="$1"; shift || true

API_URL="${AIM_API_URL:-${HERMES_API_URL:-}}"
API_TOKEN="${AIM_API_CREDENTIAL:-${HERMES_API_KEY:-}}"

case "$MODE" in
    process)
        while [[ $# -gt 0 ]]; do
            case $1 in
                --message) MESSAGE="$2"; shift 2 ;;
                --from) FROM_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        [ -z "$MESSAGE" ] && { echo missing >&2; exit 3; }
        if [ -n "$API_URL" ] && [ -n "$API_TOKEN" ]; then
            if curl -s --max-time 3 "$API_URL/health" >/dev/null 2>&1; then
                TMPJSON=$(mktemp /tmp/aim-adapter-json.XXXXXX)
                python3 "$AIM_BIN"/aim_hermes_req.py "$MESSAGE" "$SP" > "$TMPJSON" 2>/dev/null
                if [ -s "$TMPJSON" ]; then
                    AUTH=*** $API_TOKEN"
                    REPLY=$(curl -s --max-time "$ADAPTER_TIMEOUT" -X POST "$API_URL/v1/chat/completions" -H "$AUTH" -H "Content-Type: application/json" -d "@$TMPJSON" 2>/dev/null)
                    rm -f "$TMPJSON"
                    TEXT=$(echo "$REPLY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
                    [ -n "$TEXT" ] && { echo "$TEXT"; exit 0; }
                else
                    rm -f "$TMPJSON"
                fi
                echo fail >&2; exit 1
            fi
        fi
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo nocli >&2; exit 3; }
        output=$(timeout "$ADAPTER_TIMEOUT" "$AGENT_BIN" -z "${SP}
${MESSAGE}" --ignore-user-config --ignore-rules --model deepseek-v4-pro --provider deepseek 2>/dev/null)
        rc=$?
        [ $rc -eq 124 ] && { echo timeout >&2; exit 1; }
        [ $rc -ne 0 ] && { echo cli=$rc >&2; exit 1; }
        echo "$output" | head -1
        exit 0
        ;;
    health)
        curl -s --max-time 2 "$API_URL/health" >/dev/null 2>&1 && { echo ok; exit 0; }
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo dead; exit 2; }
        "$AGENT_BIN" --version >/dev/null 2>&1 && echo ok || echo degraded
        ;;
    info)
        v=$("$AGENT_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo ?)
        printf '{"provider":"hermes","version":"%s","execution_model":"realtime","max_concurrency":1}
' "$v"
        ;;
    cancel) echo '{"status":"not_supported"}'; exit 3 ;;
    trim)   echo '{"status":"trimmed","detail":"no-op"}'; exit 0 ;;
    *)      echo "unknown: $MODE" >&2; exit 3 ;;
esac
