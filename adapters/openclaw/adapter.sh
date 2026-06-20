#!/bin/bash
# adapter-version: v2.0  (项目 1.3.3 | OpenClaw adapter | ZS0001 呱呱)
# OpenClaw AIM Adapter — v1.5 (2026-06-20)
# process: 直接调用 OpenClaw CLI，调用方阻塞等回复（≤30s）
# 退出码: 0=正常, 1=可重试, 2=挂了, 3=需人工介入

: ${AIM_HOME:="$HOME/.aim"}
: ${AIM_AGENT_ID:="ZS0001"}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_WORKSPACE:="$HOME/.openclaw/workspace"}
OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.npm-global/bin/openclaw}"

MODE="${1:-process}"
shift 2>/dev/null || true

# ── health ──
if [ "$MODE" = "health" ]; then
    PID=$(ps aux | grep -v grep | grep "openclaw.*gateway" | awk '{print $2}' | head -1)
    if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
        echo '{"status":"unhealthy"}' >&2; exit 2
    fi
    echo '{"status":"healthy","active_sessions":1}'; exit 0
fi

# ── info ──
if [ "$MODE" = "info" ]; then
    printf '{"provider":"openclaw","execution_model":"realtime","version":"v2.0","project":"%s"}\n' "$(cat ~/shared/aim/VERSION 2>/dev/null || echo unknown)"; exit 0
fi

# ── cancel ──
if [ "$MODE" = "cancel" ]; then
    printf '{"status":"cancelled"}\n'; exit 0
fi

# ── trim ── (620 L3: StallWatchdog 自愈，清理卡死 session)
if [ "$MODE" = "trim" ]; then
    printf '{"status":"trimmed","detail":"openclaw runtime no-op — StallWatchdog acknowledged"}\n'; exit 0
fi

# ── process ──
while [[ $# -gt 0 ]]; do
    case $1 in
        process) shift ;;
        --message) MESSAGE="$2"; shift 2 ;;
        --from) FROM_ID="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[ -z "$MESSAGE" ] && { echo "缺少 --message" >&2; exit 2; }
FROM_ID="${FROM_ID:-unknown}"

# 直接调 OpenClaw agent 生成回复
REPLY=$("$OPENCLAW_BIN" agent \
    --agent main \
    --message "你是呱呱🐸，来自 ${FROM_ID} 的消息。直接回复(20-80字)以🐸开头：${MESSAGE}" \
    --json --timeout 25 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    t=d.get('result',{}).get('payloads',[{}])[0].get('text','')
    print(t)
except: pass
" 2>/dev/null)

if [ -n "$REPLY" ]; then
    echo "$REPLY"; exit 0
else
    echo "OpenClaw 无回复" >&2; exit 1
fi
