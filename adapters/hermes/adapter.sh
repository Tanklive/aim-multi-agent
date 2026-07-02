#!/bin/bash
# Hermes AIM Adapter v2.0 — API Server 优先 + CLI fallback
# 标准接口: process/health/info/cancel/trim
# 退出码: 0=SUCCESS, 1=RETRY, 2=DEGRADE, 3=FATAL
# v2.0: services.api 服务发现 → AIM_API_URL/AIM_API_CREDENTIAL → curl API → CLI fallback

: ${AIM_API_URL:=""}
: ${AIM_API_CREDENTIAL:=""}
: ${AGENT_BIN:="$HOME/.local/bin/hermes"}
: ${ADAPTER_TIMEOUT:=300}

MODE="$1"; shift || true

case "$MODE" in
    process)
        while [[ $# -gt 0 ]]; do
            case $1 in
                --message) MESSAGE="$2"; shift 2 ;;
                --from) FROM_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        [ -z "$MESSAGE" ] && { echo "missing --message" >&2; exit 3; }

        # ── API Server 优先 ──
        if [ -n "$AIM_API_URL" ] && [ -n "$AIM_API_CREDENTIAL" ]; then
            if curl -s --max-time 3 "$AIM_API_URL/health" >/dev/null 2>&1; then
                PROMPT="回复以下内容，仅输出回复文本，不要前缀后缀说明或操作描述。来自${FROM_ID}的消息：${MESSAGE}"
                PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'model': 'deepseek-v4-pro',
    'messages': [{'role': 'user', 'content': sys.argv[1]}],
    'max_tokens': 200
}))
" "$PROMPT" 2>/dev/null)

                if [ -n "$PAYLOAD" ]; then
                    AUTH_HDR=$(printf 'Authorization: Bearer %s' "$AIM_API_CREDENTIAL")
                    REPLY=$(curl -s --max-time "$ADAPTER_TIMEOUT" \
                        -X POST "$AIM_API_URL/v1/chat/completions" \
                        -H "$AUTH_HDR" \
                        -H "Content-Type: application/json" \
                        -d "$PAYLOAD" 2>/dev/null)
                    TEXT=$(echo "$REPLY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
                    [ -n "$TEXT" ] && { echo "$TEXT"; exit 0; }
                fi
                echo "[adapter] API failed, fallback to CLI" >&2
            fi
        fi

        # ── CLI fallback ──
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo "hermes CLI not found" >&2; exit 3; }
        PROMPT="回复以下内容，仅输出你对该消息的回复文本，不要加任何前缀后缀说明或操作描述：${MESSAGE}"
        output=$(timeout "$ADAPTER_TIMEOUT" "$AGENT_BIN" chat -q "$PROMPT" -Q --source aim-adapter 2>/dev/null)
        rc=$?
        [ $rc -eq 124 ] && { echo "timeout" >&2; exit 1; }
        [ $rc -ne 0 ] && { echo "cli exit=$rc" >&2; exit 1; }
        cleaned=$(echo "$output" | sed '/Normalized model/{N;d;}' | LC_ALL=en_US.UTF-8 grep -v '^session_id:' | grep -v '^Restored session:' | grep -v '^Saving session' | grep -v '^\.\.\.' | grep -v '^$')
        first_line=$(echo "$cleaned" | head -1)
        [ -n "$first_line" ] && { echo "$first_line"; exit 0; }
        echo "empty reply" >&2
        exit 0
        ;;

    health)
        if [ -n "$AIM_API_URL" ]; then
            curl -s --max-time 2 "$AIM_API_URL/health" >/dev/null 2>&1 && { echo '{"status":"healthy","active_sessions":1}'; exit 0; }
        fi
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo '{"status":"unhealthy","active_sessions":0}'; exit 2; }
        "$AGENT_BIN" --version >/dev/null 2>&1 && echo '{"status":"healthy","active_sessions":1}' || { echo '{"status":"degraded","active_sessions":0}'; exit 1; }
        exit 0
        ;;

    info)
        v=$("$AGENT_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        printf '{"provider":"hermes","version":"%s","execution_model":"realtime","max_concurrency":1}\n' "$v"
        exit 0
        ;;

    cancel) echo '{"status":"not_supported","detail":"Hermes realtime, cannot cancel"}'; exit 3 ;;
    trim)   echo '{"status":"trimmed","detail":"no-op"}'; exit 0 ;;
    *)      echo "unknown mode: $MODE" >&2; exit 3 ;;
esac
