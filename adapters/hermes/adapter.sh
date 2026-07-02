#!/bin/bash
# Hermes AIM Adapter v2.1 — protocol v1.0 JSON stdin + CLI args + API Server
# 标准接口: process/health/info/cancel/trim
# 退出码: 0=SUCCESS, 1=RETRY, 2=DEGRADE, 3=FATAL

: ${AIM_API_URL:=""}
: ${AIM_API_CREDENTIAL:=""}
: ${AGENT_BIN:="$HOME/.local/bin/hermes"}
: ${ADAPTER_TIMEOUT:=300}

RAW=""

# ── 模式检测 ──
case "${1:-}" in
    process|health|info|cancel|trim)
        MODE="$1"; shift || true
        ;;
    *)
        # 非已知子命令 → 检查 JSON stdin
        if [ ! -t 0 ]; then
            RAW=$(cat 2>/dev/null)
            if [ -n "$RAW" ]; then
                MODE=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('action',''))" 2>/dev/null)
                [ -z "$MODE" ] && { echo '{"error":"missing action in JSON"}' >&2; exit 3; }
            else
                echo "unknown mode: ${1:-}" >&2; exit 3
            fi
        else
            echo "usage: adapter.sh {process|health|info|cancel|trim}" >&2; exit 3
        fi
        ;;
esac

# ── JSON stdin 参数提取 ──
if [ -n "$RAW" ]; then
    MESSAGE=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
    CTX=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('context',''))" 2>/dev/null)
    FROM_ID=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('from_id',''))" 2>/dev/null)
    SESSION_ID=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
fi

case "$MODE" in
    process)
        # CLI args 解析（backward compat）
        if [ -z "$MESSAGE" ]; then
            while [[ $# -gt 0 ]]; do
                case $1 in
                    --message) MESSAGE="$2"; shift 2 ;;
                    --from) FROM_ID="$2"; shift 2 ;;
                    *) shift ;;
                esac
            done
        fi
        [ -z "$MESSAGE" ] && { echo '{"error":"missing message"}' >&2; exit 3; }

        # ── API Server 优先 ──
        if [ -n "$AIM_API_URL" ] && [ -n "$AIM_API_CREDENTIAL" ]; then
            if curl -s --max-time 3 "$AIM_API_URL/health" >/dev/null 2>&1; then
                FULL_PROMPT="${CTX:+${CTX}
}${MESSAGE}"
                PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'model': 'deepseek-v4-pro',
    'messages': [{'role': 'user', 'content': sys.argv[1]}],
    'max_tokens': 200
}))
" "$FULL_PROMPT" 2>/dev/null)

                if [ -n "$PAYLOAD" ]; then
                    AUTH_HDR=$(printf 'Authorization: Bearer %s' "$AIM_API_CREDENTIAL")
                    REPLY=$(curl -s --max-time "$ADAPTER_TIMEOUT" \
                        -X POST "$AIM_API_URL/v1/chat/completions" \
                        -H "$AUTH_HDR" \
                        -H "Content-Type: application/json" \
                        -d "$PAYLOAD" 2>/dev/null)
                    TEXT=$(echo "$REPLY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
                    if [ -n "$TEXT" ]; then
                        if [ -n "$RAW" ]; then
                            ESCAPED=$(echo "$TEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip()))')
                            printf '{"reply":%s,"status":"ok"}\n' "$ESCAPED"
                        else
                            echo "$TEXT"
                        fi
                        exit 0
                    fi
                fi
                echo "[adapter] API failed, fallback to CLI" >&2
            fi
        fi

        # ── CLI fallback ──
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo '{"error":"hermes CLI not found"}' >&2; exit 3; }
        PROMPT="回复以下内容，仅输出你对该消息的回复文本，不要加任何前缀后缀说明或操作描述：${FULL_PROMPT:-$MESSAGE}"
        output=$(timeout "$ADAPTER_TIMEOUT" "$AGENT_BIN" chat -q "$PROMPT" -Q --source aim-adapter 2>/dev/null)
        rc=$?
        [ $rc -eq 124 ] && { echo '{"error":"timeout"}' >&2; exit 1; }
        [ $rc -ne 0 ] && { echo "{\"error\":\"cli exit=$rc\"}" >&2; exit 1; }
        cleaned=$(echo "$output" | sed '/Normalized model/{N;d;}' | LC_ALL=en_US.UTF-8 grep -v '^session_id:' | grep -v '^Restored session:' | grep -v '^Saving session' | grep -v '^\.\.\.' | grep -v '^$')
        first_line=$(echo "$cleaned" | head -1)
        if [ -n "$first_line" ]; then
            if [ -n "$RAW" ]; then
                ESCAPED=$(echo "$first_line" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip()))')
                printf '{"reply":%s,"status":"ok"}\n' "$ESCAPED"
            else
                echo "$first_line"
            fi
            exit 0
        fi
        echo '{"error":"empty reply"}' >&2
        exit 0
        ;;

    health)
        if [ -n "$AIM_API_URL" ]; then
            if curl -s --max-time 2 "$AIM_API_URL/health" >/dev/null 2>&1; then
                echo '{"status":"healthy","active_sessions":1}'
                exit 0
            fi
        fi
        command -v "$AGENT_BIN" >/dev/null 2>&1 || { echo '{"status":"unhealthy"}'; exit 2; }
        echo '{"status":"healthy","active_sessions":1}'
        exit 0
        ;;

    info)
        v=$("$AGENT_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        printf '{"provider":"hermes","version":"%s","execution_model":"realtime","max_concurrency":1}\n' "$v"
        exit 0
        ;;

    cancel) echo '{"status":"not_supported","detail":"Hermes realtime"}'; exit 3 ;;
    trim)   echo '{"status":"trimmed","detail":"no-op"}'; exit 0 ;;
    *)      echo "{\"error\":\"unknown mode: $MODE\"}" >&2; exit 3 ;;
esac
