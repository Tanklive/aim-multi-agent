#!/bin/bash
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
    printf '{"provider":"openclaw","execution_model":"realtime"}\n'; exit 0
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

# ── 纯确认过滤（防 ping-pong 循环）──
# 这些消息不需要回复：收到、1、👍、👌、✅ 等短确认
_filter_ack() {
    local msg="$1"
    # 去掉 emoji/空白后的纯文本
    local stripped
    stripped=$(echo "$msg" | sed 's/[✨🐴🐸👂👍👌✅⏸️🟢🔌🤝💪👀🧠]/ /g' | sed 's/  */ /g' | xargs)
    # 纯数字/单字
    [[ "$stripped" =~ ^[0-9]+$ ]] && return 0
    # 纯收到
    [[ "$stripped" =~ ^收到 ]] && return 0
    # 纯好/OK/行/知道了
    [[ "$stripped" =~ ^(好的|知道了|OK|行|好|嗯|哦) ]] && return 0
    # 纯表情回复
    [[ -z "$stripped" ]] && return 0
    return 1
}

if _filter_ack "$MESSAGE"; then
    echo "[纯确认，跳过]" >&2
    exit 0  # 空回复 = 静默ack，不发消息，不触发重试
fi

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
