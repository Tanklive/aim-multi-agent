#!/bin/bash
set -euo pipefail
# AIM Letta adapter — AIM Client v1.2 标准接口
# VERSION: 1.8.1
#
# 6 个标准模式:
#   adapter.sh process --message "..." --from "ZSxxxx"   处理消息
#   adapter.sh health                                    健康探针
#   adapter.sh info                                      返回 Runtime 元信息
#   adapter.sh cancel --task-id "..."                    取消任务
#   adapter.sh recover                                   自修复（620 L3）
#
# 返回码:
#   process: 0=正常回复, 1=可重试, 2=降级, 3=人工介入
#   health:  0=健康,     1=降级,   2=挂
#   info:    0=正常
#   cancel:  0=已取消,   1=任务不存在, 2=无法取消
#   recover: 0=恢复成功, 1=恢复失败可重试, 2=恢复失败需人工, 4=不可恢复(数据丢失)

MODE="${1:-}"
MESSAGE=""
FROM_ID=""
TASK_ID=""
TIMEOUT="${ADAPTER_TIMEOUT:-120}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.json"

# 优先环境变量，回退到 config.json
LETTA_BIN="${LETTA_BIN:-}"
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
# MODE: recover — L3 自修复（620）
# ═══════════════════════════════════════
if [ "$MODE" = "recover" ]; then
    _detect_letta || exit 2
    _verify_agent_id || exit 4

    RECOVER_TIMEOUT=10
    MAX_RETRIES=2
    RETRY=0
    RECOVER_OK=0

    while [ "$RETRY" -lt "$MAX_RETRIES" ]; do
        RETRY=$((RETRY + 1))

        # Step 1: ping 唤醒 agent（v1.6.1: 不加 --conversation，复用最后活跃会话）
        set +e
        PING_OUTPUT=$(timeout "$RECOVER_TIMEOUT" "$LETTA_BIN" -p "ping" 2>/dev/null)
        PING_RC=$?
        set -e

        if [ $PING_RC -eq 0 ]; then
            # Step 2: 验证恢复——再发一条 ping 确认不是侥幸
            set +e
            VERIFY_OUTPUT=$(timeout 5 "$LETTA_BIN" -p "ping" 2>/dev/null)
            VERIFY_RC=$?
            set -e

            if [ $VERIFY_RC -eq 0 ]; then
                echo "{\"status\":\"recovered\",\"retries\":$RETRY,\"detail\":\"agent responsive after recovery\"}"
                RECOVER_OK=1
                break
            fi
        fi

        if [ $PING_RC -eq 124 ]; then
            echo "[letta-adapter] recover ping 超时 (attempt $RETRY/$MAX_RETRIES)" >&2
        else
            echo "[letta-adapter] recover ping 失败 rc=$PING_RC (attempt $RETRY/$MAX_RETRIES)" >&2
        fi

        # 退避：2s / 4s / 8s
        DELAY=$([ "$RETRY" -eq 1 ] && echo 2 || ([ "$RETRY" -eq 2 ] && echo 4 || echo 8))
        sleep "$DELAY"
    done

    if [ "$RECOVER_OK" -eq 1 ]; then
        exit 0
    fi

    # 恢复失败 → 可重试（Scheduler 护栏 N=3 控制重试次数）
    echo "{\"status\":\"failed\",\"retries\":$MAX_RETRIES,\"detail\":\"agent unresponsive after $MAX_RETRIES recovery attempts\"}"
    exit 1
fi

# ═══════════════════════════════════════
# MODE: trim — 清理 dispatch conversation（620 L3）
# ═══════════════════════════════════════
if [ "$MODE" = "trim" ]; then
    _detect_letta || exit 2

    TRIM_CONV="${LETTA_DISPATCH_CONV:-local-conv-1422}"
    KEEP="${TRIM_KEEP:-10}"

    # 获取当前 conversation 消息数
    # Letta 当前架构不支持 conversations trim 子命令 (v0.27.11)
    # 清理方式: 删除 conversation 对应磁盘目录 → 下次 process 新建
    MSG_COUNT=0
    set +e
    MSG_COUNT=$("$LETTA_BIN" messages list --conversation "$TRIM_CONV" 2>/dev/null | wc -l | tr -d '[:space:]')
    set -e
    MSG_COUNT="${MSG_COUNT:-0}"
    [ -n "$MSG_COUNT" ] || MSG_COUNT=0

    if [ "$MSG_COUNT" -gt "$KEEP" ]; then
        # 清理 dispatch conv 磁盘目录
        CONV_DIR="${HOME}/.letta/lc-local-backend/conversations"
        DELETED=0
        for d in "$CONV_DIR"/*/; do
            [ -d "$d" ] || continue
            DIRNAME=$(basename "$d")
            DECODED=$(echo "$DIRNAME" | base64 -d 2>/dev/null || echo "")
            if echo "$DECODED" | grep -q "conversation:${TRIM_CONV}\$"; then
                rm -rf "$d" 2>/dev/null && DELETED=$((DELETED + 1))
            fi
        done

        echo "{\"status\":\"trimmed\",\"conv\":\"$TRIM_CONV\",\"msg_count_before\":$MSG_COUNT,\"keep\":$KEEP,\"dirs_deleted\":$DELETED}"
        exit 0
    else
        echo "{\"status\":\"skipped\",\"conv\":\"$TRIM_CONV\",\"msg_count\":$MSG_COUNT,\"reason\":\"below threshold ($KEEP)\"}"
        exit 0
    fi
fi

# ═══════════════════════════════════════
# MODE: process — 处理消息
# ═══════════════════════════════════════
if [ "$MODE" != "process" ]; then
    echo "用法: adapter.sh {process|health|info|cancel|recover|trim} [--message ...] [--from ...] [--task-id ...]" >&2
    exit 3
fi

[ -n "$MESSAGE" ] || { echo "缺少 --message" >&2; exit 3; }
[ -n "$FROM_ID" ] || FROM_ID="unknown"

_detect_letta || exit 3
_verify_agent_id || exit 4

# ══════════════════════════════════════════════════════════════
# 双会话隔离机制 — Letta 架构的 AIM Client 适配方案
# ══════════════════════════════════════════════════════════════
#
# 设计理由:
#   Letta 是 deferred 模型 (max_concurrency=1)，主会话被 TUI 占用时
#   adapter process 不能抢主会话。必须用独立 dispatch 会话处理 AIM 消息。
#
# 机制:
#   1. DISPATCH_CONV 从环境变量 LETTA_DISPATCH_CONV 读取（可动态配置）
#      默认值 ref: [[reference/aim/adapter-dispatch-session.md]]
#   2. process 模式自动检测并初始化 dispatch conv:
#      a) 检查磁盘目录 (conversation.json + manifest.json + messages.jsonl)
#      b) 目录缺失 → 用 ensure_dispatch_conv() 通过 letta 创建
#      c) 目录存在 → 直接 --conversation 复用
#   3. health 模式也验证 dispatch conv 存活（目录存在 = healthy）
#   4. cleanup-conversations.sh 永久排除 dispatch conv（不清理）
#   5. 存活监控: adapter health 探针检测 dispatch conv 目录存在性
#
# @see [[reference/aim/adapter-dispatch-session.md]]  完整说明
# @see [[reference/aim/gotchas.md]]                   相关陷阱
# ══════════════════════════════════════════════════════════════

PROBE_TIMEOUT=15
DISPATCH_CONV="${LETTA_DISPATCH_CONV:-local-conv-1422}"
PROMPT="[AIM dispatch - 仅回复本条消息，不要回复历史] ${MESSAGE}"

# ── 确保 dispatch conv 存在 ──────────────────
ensure_dispatch_conv() {
    local conv_id="$1"
    local base_dir="${HOME}/.letta/lc-local-backend/conversations"
    local encoded_name
    encoded_name=$(echo -n "conversation:${conv_id}" | base64)
    local conv_dir="${base_dir}/${encoded_name}"

    # 检查磁盘目录是否完整
    if [ -d "$conv_dir" ] && [ -f "$conv_dir/conversation.json" ] && [ -f "$conv_dir/manifest.json" ]; then
        return 0
    fi

    # 目录不存在或不完整 → 通过 letta 创建
    echo "[letta-adapter] 初始化 dispatch 会话: $conv_id" >&2
    mkdir -p "$conv_dir" 2>/dev/null || true

    # 写 conversation.json（Letta 通过此文件识别 conv）
    python3 -c "
import json, os
from datetime import datetime, timezone
now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
conv = {
    'id': '$conv_id',
    'agent_id': '$LETTA_AGENT_ID',
    'archived': False, 'archived_at': None,
    'created_at': now, 'updated_at': now, 'last_message_at': now,
    'summary': None, 'in_context_message_ids': []
}
manifest = {
    'schema_version': 2,
    'message_format': 'pi-session-entry-jsonl',
    'provider_stack': 'pi-ai',
    'created_at': now
}
with open(os.path.join('$conv_dir', 'conversation.json'), 'w') as f: json.dump(conv, f)
with open(os.path.join('$conv_dir', 'manifest.json'), 'w') as f: json.dump(manifest, f)
# 确保 messages.jsonl 存在（空文件）
open(os.path.join('$conv_dir', 'messages.jsonl'), 'a').close()
" 2>/dev/null

    return 0
}

# ── 初始化 + 调用 dispatch ────────────────
ensure_dispatch_conv "$DISPATCH_CONV"

set +e
RAW_OUTPUT=$(timeout "$PROBE_TIMEOUT" "$LETTA_BIN" \
    --conversation "$DISPATCH_CONV" \
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

exit 0
