#!/bin/bash
set -euo pipefail
# AIM Letta adapter — AIM Client v1.2 标准接口
# VERSION = "1.3.0"  (adapter 独立版本号，对应项目级 1.3.0)
# adapter version: v1.7  (标准注释标记)
#
# 4 个标准模式:
#   adapter.sh process --message "..." --from "ZSxxxx"   处理消息
#   adapter.sh health                                    健康探针
#   adapter.sh info                                      返回 Runtime 元信息
#   adapter.sh cancel --task-id "..."                    取消任务
#
# 返回码:
#   process: 0=正常回复, 1=可重试, 2=降级, 3=人工介入
#   health:  0=健康,     1=降级,   2=挂
#   info:    0=正常
#   cancel:  0=已取消,   1=任务不存在, 2=无法取消

MODE="${1:-}"
MESSAGE=""
FROM_ID=""
TASK_ID=""
TIMEOUT="${ADAPTER_TIMEOUT:-120}"
LETTA_BIN="${LETTA_BIN:-$HOME/.npm-global/bin/letta}"
LETTA_AGENT_ID="${LETTA_AGENT_ID:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FILTER_SCRIPT="$SCRIPT_DIR/filter_letta_output.sh"

shift
while [[ $# -gt 0 ]]; do
    case "$1" in
        --message) MESSAGE="$2"; shift 2 ;;
        --from)    FROM_ID="$2"; shift 2 ;;
        --task-id) TASK_ID="$2"; shift 2 ;;
        *) shift ;;
    esac
done

export PATH="$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# ── 检测 letta CLI ─────────────────────
_detect_letta() {
    if [ ! -x "$LETTA_BIN" ]; then
        LETTA_BIN=$(which letta 2>/dev/null || echo "")
    fi
    if [ -z "$LETTA_BIN" ] || [ ! -x "$LETTA_BIN" ]; then
        echo "[letta-adapter] letta CLI 不可用" >&2
        return 1
    fi
    return 0
}

# ── 验证 Agent ID ──────────────────────
_verify_agent_id() {
    if [ -n "$LETTA_AGENT_ID" ]; then
        AGENT_CHECK=$(timeout 5 "$LETTA_BIN" agents list 2>/dev/null | grep -c "$LETTA_AGENT_ID" || echo "0" || true)
        if [ "$AGENT_CHECK" -eq 0 ]; then
            echo "[letta-adapter] Agent ID 不存在或已漂移: $LETTA_AGENT_ID" >&2
            return 1
        fi
    fi
    return 0
}

# ═══════════════════════════════════════
# MODE: health — 健康探针
# ═══════════════════════════════════════
if [ "$MODE" = "health" ]; then
    _detect_letta || exit 2
    _verify_agent_id || exit 2

    # v1.6: 改用 letta agents list（JSON API，不受 TUI session 阻塞）
    # 原理: agents list 走独立后端，TUI 活跃对话中也秒回。
    # health 只回答 "letta CLI 还可不可以用"，session 忙不忙交给 Scheduler 的超时处理。
    AGENT_CHECK=$(timeout 10 "$LETTA_BIN" agents list 2>/dev/null | grep -c "$LETTA_AGENT_ID" || true)

    # 防御：AGENT_CHECK 可能为空（timeout 10 超时 + grep -c 无匹配），默认为 0
    AGENT_CHECK="${AGENT_CHECK:-0}"

    if [ "$AGENT_CHECK" -gt 0 ]; then
        echo '{"status":"healthy","detail":"letta CLI reachable"}'
        exit 0
    else
        echo '{"status":"unhealthy","active_sessions":-1,"detail":"letta agents list failed or agent ID not found"}'
        exit 2
    fi
fi

# ═══════════════════════════════════════
# MODE: info — Runtime 元信息
# ═══════════════════════════════════════
if [ "$MODE" = "info" ]; then
    _detect_letta || exit 2

    LETTA_VERSION=$("$LETTA_BIN" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")

    cat <<EOF
{
  "provider": "letta",
  "version": "${LETTA_VERSION}",
  "execution_model": "deferred",
  "max_concurrency": 1,
  "agent_id": "${LETTA_AGENT_ID:-null}"
}
EOF
    exit 0
fi

# ═══════════════════════════════════════
# MODE: cancel — 取消任务
# ═══════════════════════════════════════
if [ "$MODE" = "cancel" ]; then
    # Letta 当前架构不支持取消排队的 subprocess
    # 返回"无法取消"，由 Scheduler 层面做超时管理
    echo "[letta-adapter] Letta deferred 模式不支持取消排队中的任务 (task_id=${TASK_ID})" >&2
    exit 2
fi

# ═══════════════════════════════════════
# MODE: process — 处理消息
# ═══════════════════════════════════════
if [ "$MODE" != "process" ]; then
    echo "用法: adapter.sh {process|health|info|cancel} [--message ...] [--from ...] [--task-id ...]" >&2
    exit 3
fi

[ -n "$MESSAGE" ] || { echo "缺少 --message" >&2; exit 3; }
[ -n "$FROM_ID" ] || FROM_ID="unknown"

_detect_letta || exit 3
_verify_agent_id || exit 3

# ═══ 分层超时策略 ═══
# Letta Code 是单 session 架构 + TTY 限制：
#   1. 非 TTY 环境：letta -p stdout 为空 → 必须用 script -q 模拟 TTY
#   2. 单 session：当前对话中时 subprocess 阻塞排队
# 分层策略：
#   第 1 层 (PROBE_TIMEOUT=30s): script -q letta -p，给 session 排队机会
#      - 空闲时通常 3-5s 就回
#      - 30s 超时 → exit 1 → call_adapter 返回 RETRY → 降级文件队列重试
#   第 2 层 (ADAPTER_TIMEOUT=120s): call_adapter 层兜底，防止进程僵尸
PROBE_TIMEOUT=30
PROMPT="[AIM消息] 收到来自 ${FROM_ID} 的消息：${MESSAGE}"

# set -e 下 timeout 124 会触发脚本退出，先关再开
set +e
RAW_OUTPUT=$(timeout "$PROBE_TIMEOUT" /usr/bin/script -q /dev/null "$LETTA_BIN" \
    --agent "$LETTA_AGENT_ID" \
    -p "$PROMPT" </dev/null 2>/dev/null)
RC=$?
set -e

if [ $RC -eq 124 ]; then
    # 30s 超时 → session 忙（TUI 对话中） → 可重试
    echo "[letta-adapter] 处理超时 (${PROBE_TIMEOUT}s)，session 可能忙，可重试" >&2
    echo "DEADLINE_HINT: retry_after_idle" >&2
    exit 1
elif [ $RC -ne 0 ]; then
    # 非 timeout 的错误 → letta CLI 本身挂了 → 不可用
    echo "[letta-adapter] 调用失败 rc=$RC" >&2
    exit 2
fi

# 噪声过滤 + 输出
if [ -n "$RAW_OUTPUT" ]; then
    # 先清理 script -q 输出的控制字符
    # script -q 输出开头有 ^D、退格等控制字符，需要清理
    CLEAN_OUTPUT=$(echo "$RAW_OUTPUT" | sed 's/^\^D//' | tr -d '\010' | sed 's/^[[:space:]]*//')

    if [ -x "$FILTER_SCRIPT" ]; then
        REPLY=$("$FILTER_SCRIPT" "$CLEAN_OUTPUT")
    else
        REPLY=$(echo "$CLEAN_OUTPUT" | grep -v -E \
            '^Connected|^Loading|^Error saving|^ENOENT|^/Users/|^\s+at |^Session:|^Duration:|^Messages:')
    fi
    if [ -n "$REPLY" ]; then
        echo "$REPLY"
    fi
fi
exit 0
