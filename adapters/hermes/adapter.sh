#!/bin/bash
# Hermes AIM Adapter — v1.6 (2026-06-24: API Server via curl)
# v1.5: +context-card L1+L2 注入, +进程锁, +persona

: ${HERMES_API_URL:="http://127.0.0.1:8642"}
: ${HERMES_API_KEY:=""}
: ${HERMES_BIN:="/Users/yangzs/.local/bin/hermes"}
: ${ADAPTER_TIMEOUT:=60}
: ${HERMES_HOME:="$HOME/.hermes"}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_BIN:="$HOME/.aim/bin"}

MODE="$1"
shift || true

case "$MODE" in
    process)
        while [[ $# -gt 0 ]]; do
            case $1 in
                --message) MESSAGE="$2"; shift 2 ;;
                --from) FROM_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done

        [ -z "$MESSAGE" ] && { echo "缺少 --message" >&2; exit 3; }

        # 进程锁
        LOCKDIR="/tmp/aim-adapter-ZS0002.lock"
        if ! mkdir "$LOCKDIR" 2>/dev/null; then
            echo "已有 adapter 在运行" >&2; exit 0
        fi
        trap "rmdir '$LOCKDIR' 2>/dev/null" EXIT

        # 构建系统提示
        CONTEXT=""
        [ -f "$AIM_SHARED/PROJECT/context-card.md" ] && CONTEXT="$(head -30 "$AIM_SHARED/PROJECT/context-card.md")"
        [ -f "$AIM_SHARED/PROJECT/context-live.md" ] && CONTEXT="${CONTEXT}
$(head -10 "$AIM_SHARED/PROJECT/context-live.md")"

        SP="你是吉量  AIM ZS0002 Hermes端 研究运营。"
        [ -n "$CONTEXT" ] && SP="${SP} 项目上下文：${CONTEXT}"
        SP="${SP} 直接回复20-80字，不要加前缀后缀。"

        # ── API Server（优先，带自恢复）──
        api_ok=false
        if [ -n "$HERMES_API_KEY" ]; then
            # 第1次：直接测
            curl -s --max-time 2 "$HERMES_API_URL/health" >/dev/null 2>&1 && api_ok=true
            # 失败 → 重启 Gateway 再测
            if ! $api_ok; then
                "$HERMES_BIN" gateway start 2>/dev/null
                sleep 4
                curl -s --max-time 2 "$HERMES_API_URL/health" >/dev/null 2>&1 && api_ok=true
            fi
        fi
        if $api_ok; then
            # 通过临时文件传 JSON，避开 shell 引号转义问题
            TMPJSON=$(mktemp /tmp/aim-adapter-json.XXXXXX)
            "$AIM_BIN"/aim_hermes_req.py "$MESSAGE" "$SP" > "$TMPJSON" 2>/dev/null
            if [ -s "$TMPJSON" ]; then
                REPLY=$(curl -s --max-time "$ADAPTER_TIMEOUT" \
                    -X POST "$HERMES_API_URL/v1/chat/completions" \
                    -H "Authorization: Bearer $HERMES_API_KEY" \
                    -H "Content-Type: application/json" \
                    -d "@$TMPJSON" 2>/dev/null)
                rm -f "$TMPJSON"
                TEXT=$(echo "$REPLY" | python3.13 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
                [ -n "$TEXT" ] && { echo "$TEXT"; exit 0; }
            else
                rm -f "$TMPJSON"
            fi
        fi

        # ── CLI 最后降级（API Server 自恢复失败才走）──
        command -v "$HERMES_BIN" >/dev/null 2>&1 || { echo "CLI 不可用" >&2; exit 3; }
        output=$(timeout "$ADAPTER_TIMEOUT" "$HERMES_BIN" -z "${SP}\n${MESSAGE}" \
            --ignore-user-config --ignore-rules \
            --model deepseek-v4-pro --provider deepseek 2>/dev/null)
        rc=$?
        [ $rc -eq 124 ] && { echo "超时" >&2; exit 1; }
        [ $rc -ne 0 ] && { echo "失败 exit=$rc" >&2; exit 1; }
        echo "$output" | head -1
        exit 0
        ;;

    health)
        curl -s --max-time 2 "$HERMES_API_URL/health" >/dev/null 2>&1 \
            && { echo '{"status":"healthy","active_sessions":1}'; exit 0; }
        command -v "$HERMES_BIN" >/dev/null 2>&1 || { echo '{"status":"unhealthy","active_sessions":0}'; exit 2; }
        "$HERMES_BIN" --version >/dev/null 2>&1 \
            && echo '{"status":"healthy","active_sessions":1}' \
            || echo '{"status":"degraded","active_sessions":0}'
        ;;

    info)
        v=$("$HERMES_BIN" --version 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        echo "{\"provider\":\"hermes\",\"version\":\"${v}\",\"execution_model\":\"realtime\",\"max_concurrency\":1}"
        ;;

    cancel)
        echo '{"status":"not_supported","detail":"realtime 无法取消"}'; exit 3
        ;;

    trim)
        echo '{"status":"trimmed","detail":"no-op"}'; exit 0
        ;;

    *)
        echo "未知模式: $MODE" >&2; exit 3
        ;;
esac
