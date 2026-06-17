#!/bin/bash
# AIM Letta adapter — Phase 2 直通架构
# 接口: adapter.sh process --message "..." --from "ZSxxxx"
#
# 返回码:
#   0 = 正常（stdout 为回复内容，空也 OK，表示 AI 决定不回复）
#   2 = 降级到文件队列（letta 超时/不可用/阻塞）
#   3 = 需人工介入（letta CLI 不存在/配置错误）

set -euo pipefail

MODE="${1:-}"
MESSAGE=""
FROM_ID=""
TIMEOUT="${AIM_ADAPTER_TIMEOUT:-45}"
LETTA_BIN="${LETTA_BIN:-$HOME/.npm-global/bin/letta}"
LETTA_AGENT_ID="${LETTA_AGENT_ID:-}"

# ── 参数解析 ───────────────────────────
shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --message) MESSAGE="$2"; shift 2 ;;
        --from)    FROM_ID="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[ "$MODE" = "process" ] || { echo "用法: adapter.sh process --message ... --from ..."; exit 3; }
[ -n "$MESSAGE" ] || { echo "缺少 --message"; exit 3; }
[ -n "$FROM_ID" ] || FROM_ID="unknown"

export PATH="$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# ── 检测 letta CLI ─────────────────────
if [ ! -x "$LETTA_BIN" ]; then
    LETTA_BIN=$(which letta 2>/dev/null || echo "")
fi
if [ -z "$LETTA_BIN" ] || [ ! -x "$LETTA_BIN" ]; then
    echo "[letta-adapter] letta CLI 不可用" >&2
    exit 3
fi

# ── 构造 prompt ────────────────────────
PROMPT="[AIM消息] 收到来自 ${FROM_ID} 的消息：${MESSAGE}"

# ── 调用 Letta ─────────────────────────
TMPFILE=$(mktemp)
(
    "$LETTA_BIN" --agent "$LETTA_AGENT_ID" -p "$PROMPT" > "$TMPFILE" 2>/dev/null
) &
LPID=$!

( sleep "$TIMEOUT"; kill "$LPID" 2>/dev/null ) &
WPID=$!

wait "$LPID" 2>/dev/null
RC=$?
kill "$WPID" 2>/dev/null
wait "$WPID" 2>/dev/null

RAW_OUTPUT=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"

# ── 判断结果 ───────────────────────────
if [ $RC -eq 0 ]; then
    if [ -n "$RAW_OUTPUT" ]; then
        # 过滤 Letta CLI 噪声
        REPLY=$(echo "$RAW_OUTPUT" | grep -v -E \
            '^Connected|^Loading|^Error saving|^ENOENT|^/Users/|^\s+at |^Session:|^Duration:|^Messages:')
        if [ -n "$REPLY" ]; then
            echo "$REPLY"
        fi
        exit 0
    else
        # 空输出 = AI 决定不回复，正常
        exit 0
    fi
elif [ $RC -eq 143 ] || [ $RC -eq 137 ]; then
    # SIGTERM/SIGKILL = 超时被 kill → 降级
    echo "[letta-adapter] 超时 (${TIMEOUT}s)" >&2
    exit 2
else
    # 其他错误 → 降级
    echo "[letta-adapter] 调用失败 rc=$RC" >&2
    exit 2
fi
