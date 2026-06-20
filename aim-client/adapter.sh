#!/bin/bash
set -euo pipefail
# AIM Letta adapter — AIM Client v1.2 标准接口
# VERSION: 1.6.1
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
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.json"

# 优先环境变量，回退到 config.json
LETTA_BIN="${LETTA_BIN:-/Users/yangzs/.npm-global/bin/letta}"
LETTA_AGENT_ID="${LETTA_AGENT_ID:-}"

if [ -z "$LETTA_BIN" ] || [ -z "$LETTA_AGENT_ID" ]; then
    if [ -f "$CONFIG_FILE" ]; then
        [ -z "$LETTA_BIN" ] && LETTA_BIN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('letta_bin',''))" 2>/dev/null || true)
        [ -z "$LETTA_AGENT_ID" ] && LETTA_AGENT_ID=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('letta_agent_id',''))" 2>/dev/null || true)
    fi
fi

# 最终默认值
LETTA_BIN="${LETTA_BIN:-$HOME/.npm-global/bin/letta}"
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
    # v1.7: 磁盘持久化检查，替代 letta agents list
    #       letta -p "ping" --agent 会发起完整 LLM 对话(>10s)，不适合 health check
    #       memfs 目录存在 → agent 数据完好 → letta -p 可加载
    if [ -n "$LETTA_AGENT_ID" ]; then
        local memfs_dir="${HOME}/.letta/lc-local-backend/memfs/${LETTA_AGENT_ID}/memory"
        if [ -d "$memfs_dir" ]; then
            : # Agent 持久化数据存在
        else
            echo "[letta-adapter] Agent 数据不存在: $memfs_dir" >&2
            return 1
        fi
    fi
    return 0
}

# ═══════════════════════════════════════
# MODE: health — 健康探针
# ═══════════════════════════════════════
if [ "$MODE" = "health" ]; then
    _detect_letta || exit 3
    _verify_agent_id || exit 4

    # v1.7: 磁盘持久化检查（memfs/ 目录）
    #       移除 agents list 依赖（主 agent 不在子 agent 列表中）
    #       不用 -p "ping" --agent（会发起完整 LLM 对话 >10s，不适合 health check）
    _verify_agent_id && echo '{"status":"healthy","detail":"letta CLI reachable"}' && exit 0
    echo '{"status":"unhealthy","detail":"agent data not found on disk"}' && exit 4
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
_verify_agent_id || exit 4

# ═══ 并发会话策略 ═══
# v1.5: 使用固定 dispatch conversation 复用，替代 v1.4 的 --new
#   - --conversation <固定ID>: 复用同一会话，TUI 开着也能并发，零磁盘增长
#   - --new 问题: 每条消息 +1 会话 (80KB)，50条/天=4MB/天
#   - 固定会话: 消息历史积累在同一会话，debug 可追溯，不膨胀
#   - 15s 超时兜底（正常 3-8s 返回）
PROBE_TIMEOUT=15
DISPATCH_CONV="${LETTA_DISPATCH_CONV:-local-conv-1422}"

# v1.5.1: prompt 加约束前缀，防止 conversation 历史导致多次回复
#   问题: --conversation 复用带入历史上下文，Letta 看到之前的 "收到" 也会跟着回
#   修复: 前缀明确指令「只回一次，不回历史」，去 [[:space:]] 首尾空白
PROMPT="[AIM dispatch - 仅回复本条消息，不要回复历史] ${MESSAGE}"

# v1.6.1: 去掉 --conversation（conversation 漂移/清理会导致永久降级）
# letta v0.27.11 默认行为复用最后活跃会话，无需显式指定
set +e
RAW_OUTPUT=$(timeout "$PROBE_TIMEOUT" "$LETTA_BIN" \
    -p "$PROMPT" 2>/dev/null)
RC=$?
set -e

if [ $RC -eq 124 ]; then
    echo "[letta-adapter] 处理超时 (${PROBE_TIMEOUT}s)，可重试" >&2
    exit 1
elif [ $RC -ne 0 ]; then
    echo "[letta-adapter] 调用失败 rc=$RC" >&2
    exit 2
fi

# ── 输出处理 ─────────────────
REPLIES_DIR="$SCRIPT_DIR/.aim-replies"
mkdir -p "$REPLIES_DIR"

if [ -n "$RAW_OUTPUT" ]; then
    if [ -x "$FILTER_SCRIPT" ]; then
        REPLY=$("$FILTER_SCRIPT" "$RAW_OUTPUT")
    else
        REPLY="$RAW_OUTPUT"
    fi
    if [ -n "$REPLY" ]; then
        echo "$REPLY"
        # v1.6: 记录回复到 .aim-replies/
        TIMESTAMP=$(date +%s)
        BODY_JSON=$(python3 -c "import json; print(json.dumps('''$REPLY'''.strip()))" 2>/dev/null || echo "\"$REPLY\"")
        printf '{"ts":%s,"from":"%s","reply":%s}\n' "$TIMESTAMP" "${FROM_ID:-unknown}" "$BODY_JSON" >> "$REPLIES_DIR/replies.jsonl"
    fi
else
    # v1.6: 无回复时也记录（避免静默丢回复）
    TIMESTAMP=$(date +%s)
    printf '{"ts":%s,"from":"%s","reply":null,"note":"empty output"}\n' "$TIMESTAMP" "${FROM_ID:-unknown}" >> "$REPLIES_DIR/replies.jsonl"
fi

# v1.6: Trim dispatch conversation history（每10条消息触发一次 trim）
TRIM_COUNTER_FILE="$REPLIES_DIR/.trim_counter"
TRIM_INTERVAL=10
COUNT=$(cat "$TRIM_COUNTER_FILE" 2>/dev/null || echo 0)
COUNT=$((COUNT + 1))
echo "$COUNT" > "$TRIM_COUNTER_FILE"

if [ "$COUNT" -ge "$TRIM_INTERVAL" ]; then
    echo "0" > "$TRIM_COUNTER_FILE"
    # trim: 用 letta messages list 获取 conversation 消息数，超过阈值则用 letta conversations trim
    MSG_COUNT=$("$LETTA_BIN" messages list --conversation "$DISPATCH_CONV" 2>/dev/null | wc -l || echo 0)
    if [ "$MSG_COUNT" -gt 100 ]; then
        TRIM_TO=20
        "$LETTA_BIN" conversations trim "$DISPATCH_CONV" --keep-last "$TRIM_TO" 2>/dev/null || \
            echo "[letta-adapter] trim failed (non-fatal)" >&2
        printf '{"ts":%s,"event":"trim","msg_count":%s,"trim_to":%s}\n' "$(date +%s)" "$MSG_COUNT" "$TRIM_TO" >> "$REPLIES_DIR/replies.jsonl"
    fi
fi

exit 0
