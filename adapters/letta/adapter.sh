#!/bin/bash
set -eu
# v1.12.0: 移除 pipefail — 在 $() 嵌套调用场景下 pipefail 导致子进程继承破损 pipe fd 触发 SIGPIPE (141)
# 脚本内无数据管道（所有管道已在 v1.12.0 中改为临时文件或 python3），pipefail 无保护价值
# AIM Letta adapter — AIM Client v1.2 标准接口
# VERSION: 1.11.0
#
# 6 个标准模式:
#   adapter.sh process --message "..." --from "ZSxxxx"   处理消息
#   adapter.sh health                                    健康探针
#   adapter.sh info                                      返回 Runtime 元信息
#   adapter.sh cancel --task-id "..."                    取消任务
#   adapter.sh recover                                   自修复（620 L3）
#   adapter.sh trim                                      清理 dispatch history
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

# Python 版本变量化（2026-06-20 P0-002）: 环境变量 > config.json > 系统默认 python3
PYTHON_BIN="${PYTHON_BIN:-}"
# 优先环境变量，回退到 config.json
LETTA_BIN="${LETTA_BIN:-}"
LETTA_AGENT_ID="${LETTA_AGENT_ID:-}"

if [ -z "$LETTA_BIN" ] || [ -z "$LETTA_AGENT_ID" ] || [ -z "$PYTHON_BIN" ]; then
    if [ -f "$CONFIG_FILE" ]; then
        [ -z "$PYTHON_BIN" ] && PYTHON_BIN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('python_bin',''))" 2>/dev/null || true)
        [ -z "$LETTA_BIN" ] && LETTA_BIN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('letta_bin',''))" 2>/dev/null || true)
        [ -z "$LETTA_AGENT_ID" ] && LETTA_AGENT_ID=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('letta_agent_id',''))" 2>/dev/null || true)
    fi
fi

# Python 最终默认值
PYTHON_BIN="${PYTHON_BIN:-python3}"

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
    # v2.0.1: 不再回退到 which letta — 必须从环境变量或 config.json 精确解析
    #         which 兜底会绕过 LETTA_BIN 校验，掩盖 npm 包丢失问题
    if [ -z "$LETTA_BIN" ] || [ ! -x "$LETTA_BIN" ]; then
        echo "[letta-adapter] letta CLI 不可用 (LETTA_BIN=$LETTA_BIN)" >&2
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
    # v1.9.0: 也验证 dispatch_conv_ids.txt 可写（池化依赖此文件）
    DISPATCH_IDS_FILE="$SCRIPT_DIR/dispatch_conv_ids.txt"
    touch "$DISPATCH_IDS_FILE" 2>/dev/null || { echo '{"status":"degraded","detail":"dispatch_conv_ids.txt not writable"}'; exit 1; }
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
  "max_concurrency": "pooled(--new)",
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

    # v2.1: recover 不再走 LLM (letta -p "ping" → 12K+ tokens)
    #       改为纯进程/磁盘探活 — 零 token 消耗
    #       AIM 自身运行监控走服务/模块/代码，不走 token

    LETTA_PID=$(pgrep -f "node.*letta" 2>/dev/null | head -1 || echo "")
    DISK_OK=0

    if [ -n "$LETTA_PID" ] && kill -0 "$LETTA_PID" 2>/dev/null; then
        # Letta 进程活着 → 验证磁盘数据
        if [ -d "${HOME}/.letta/lc-local-backend/memfs/${LETTA_AGENT_ID}/memory" ]; then
            DISK_OK=1
        fi
    fi

    if [ "$DISK_OK" -eq 1 ]; then
        echo "{\"status\":\"recovered\",\"detail\":\"letta process alive (PID=$LETTA_PID) + memfs intact\"}"
        exit 0
    fi

    # 恢复失败 → 可重试（Scheduler 护栏 N=3 控制重试次数）
    echo "{\"status\":\"failed\",\"detail\":\"letta process not found or memfs missing\"}"
    exit 1
fi

# ═══════════════════════════════════════
# MODE: trim — 清理 dispatch conversations（620 L3，v1.9.0 池化）
# ═══════════════════════════════════════
# v1.9.0: 从 dispatch_conv_ids.txt 读取所有池内 conv → 逐个 truncate messages.jsonl
#         不再依赖写死的 local-conv-1422
if [ "$MODE" = "trim" ]; then
    _detect_letta || exit 2
    _load_pool_size

    CONV_BASE="${HOME}/.letta/lc-local-backend/conversations"
    TOTAL_BEFORE=0
    TOTAL_AFTER=0
    COUNT=0

    if [ -f "$DISPATCH_IDS_FILE" ]; then
        while IFS= read -r conv_id; do
            [ -z "$conv_id" ] && continue
            ENCODED_NAME=$(echo -n "conversation:${conv_id}" | base64)
            CONV_DIR="${CONV_BASE}/${ENCODED_NAME}"
            MSG_FILE="${CONV_DIR}/messages.jsonl"

            if [ -f "$MSG_FILE" ]; then
                BEFORE=$(wc -l < "$MSG_FILE" | tr -d ' ')
                : > "$MSG_FILE"
                AFTER=$(wc -l < "$MSG_FILE" | tr -d ' ')
                TOTAL_BEFORE=$((TOTAL_BEFORE + BEFORE))
                TOTAL_AFTER=$((TOTAL_AFTER + AFTER))
                COUNT=$((COUNT + 1))
            fi
        done < "$DISPATCH_IDS_FILE"
    fi

    echo "{\"status\":\"trimmed\",\"convs_trimmed\":$COUNT,\"total_lines_before\":$TOTAL_BEFORE,\"total_lines_after\":$TOTAL_AFTER}"
    exit 0
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
# Dispatch conv 池化机制 — 变量化 + 跨环境可迁移（v1.9.0）
# ══════════════════════════════════════════════════════════════
#
# 设计理由:
#   1. Letta --new 支持多 conversation 并发，但 --conversation <固定ID> 内部串行
#   2. TUI 活跃时复用固定 conv 会排队超时 → 用 --new 每次新 conv 解耦并发
#   3. conv ID 是全局自增计数器，不能硬编码 (1422/1423 换环境会断裂)
#   4. 用 dispatch_conv_ids.txt 持久化映射真实 ID → 跨环境可迁移
#
# 机制:
#   1. 每次 process 调用 —> letta --new -p "..."  → 创建新 conv → 回复
#   2. 新 conv ID 写入 dispatch_conv_ids.txt（去重 append）
#   3. health 验证 dispatch_conv_ids.txt 可写 + letta CLI 可用
#   4. trim 通过 dispatch_conv_ids.txt 找到所有池内 conv → truncate messages.jsonl
#   5. cleanup cron 通过 dispatch_conv_ids.txt 判定保护白名单
#
# 变量化路径:
#   config.json → dispatch_conv_pool_size (默认 2)
#   dispatch_conv_ids.txt → 持久化真实 conv ID 映射
#   LETTA_DISPATCH_CONV → 环境变量覆盖（可选，向后兼容）
#
# @see [[reference/aim/adapter-dispatch-session.md]]  完整说明
# @see [[reference/aim/gotchas.md]]                   相关陷阱
# ══════════════════════════════════════════════════════════════

PROBE_TIMEOUT=25
DISPATCH_IDS_FILE="$SCRIPT_DIR/dispatch_conv_ids.txt"
POOL_SIZE="${DISPATCH_CONV_POOL_SIZE:-2}"
PROMPT="[AIM dispatch - 仅回复本条消息，不要回复历史] ${MESSAGE}"

# ── 读取 config.json 获取 pool_size ─────
_load_pool_size() {
    local cfg="${DISPATCH_CONV_POOL_SIZE:-}"
    if [ -z "$cfg" ] && [ -f "$CONFIG_FILE" ]; then
        cfg=$(${PYTHON_BIN} -c "import json; print(json.load(open('$CONFIG_FILE')).get('dispatch_conv_pool_size',''))" 2>/dev/null || true)
    fi
    POOL_SIZE="${cfg:-2}"
}

# ── 用 --new 创建新 dispatch conv，记录 ID ─────
# v1.12.0: 用临时文件做 I/O，根除 bash pipefail nested subprocess SIGPILE
_dispatch_with_new_conv() {
    local raw_output="" rc=0 _tmp_out after_latest new_conv_id

    _tmp_out=$(mktemp /tmp/aim-dispatch.XXXXXX)
    before_latest=$(ls -t "${HOME}/.letta/lc-local-backend/conversations/" 2>/dev/null | head -1)

    set +e
    timeout "$PROBE_TIMEOUT" "$LETTA_BIN" \
        --agent "$LETTA_AGENT_ID" \
        --new \
        -p "$PROMPT" > "$_tmp_out" 2>/dev/null
    rc=$?
    raw_output=$(cat "$_tmp_out")
    rm -f "$_tmp_out"
    set -e

    # 冷启动重试
    if [ $rc -eq 124 ] || [ $rc -eq 141 ]; then
        _tmp_out=$(mktemp /tmp/aim-dispatch.XXXXXX)
        set +e
        timeout "$PROBE_TIMEOUT" "$LETTA_BIN" \
            --agent "$LETTA_AGENT_ID" \
            --new \
            -p "$PROMPT" > "$_tmp_out" 2>/dev/null
        rc=$?
        raw_output=$(cat "$_tmp_out")
        rm -f "$_tmp_out"
        set -e
    fi

    # conv ID 追踪（echo 之前完成）
    if [ $rc -eq 0 ] && [ -n "$raw_output" ]; then
        after_latest=$(ls -t "${HOME}/.letta/lc-local-backend/conversations/" 2>/dev/null | head -1)
        if [ "$after_latest" != "$before_latest" ]; then
            new_conv_id=$(${PYTHON_BIN} -c "
import base64, sys
try:
    raw = sys.argv[1]
    decoded = base64.b64decode(raw).decode()
    if decoded.startswith('conversation:'):
        print(decoded.split(':')[1])
except:
    pass
" "$after_latest" 2>/dev/null)
            if [ -n "$new_conv_id" ]; then
                _track_conv_id "$new_conv_id"
            fi
        fi
    fi

    printf '%s\n' "$raw_output"
    return $rc
}

# ── 记录新 conv ID 到 ids 文件 ─────
_track_conv_id() {
    local conv_id="$1"
    touch "$DISPATCH_IDS_FILE" 2>/dev/null
    if ! grep -qxF "$conv_id" "$DISPATCH_IDS_FILE" 2>/dev/null; then
        echo "$conv_id" >> "$DISPATCH_IDS_FILE"
    fi
    # LRU 淘汰: 保留最近 pool_size * 2 个 ID
    local keep=$((POOL_SIZE * 2 + 5))
    if [ "$(wc -l < "$DISPATCH_IDS_FILE" | tr -d ' ')" -gt "$keep" ]; then
        tail -n "$keep" "$DISPATCH_IDS_FILE" > "${DISPATCH_IDS_FILE}.tmp" && mv "${DISPATCH_IDS_FILE}.tmp" "$DISPATCH_IDS_FILE"
    fi
}

# ── 确保 dispatch conv 存在（仅用于 trim/旧兼容） ──
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
    ${PYTHON_BIN} -c "
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

# ── process 主流程：--new 每次新 conv，避免复用串行 ──
_load_pool_size

# 尝试用 --new 创建新 conversation 处理消息
# v1.12.0: redirect 到临时文件，不用 $() 捕获函数输出（规避 pipefail SIGPIPE）
ADAPTER_TMP=$(mktemp /tmp/aim-letta-adapter.XXXXXX)
set +e
_dispatch_with_new_conv > "$ADAPTER_TMP" 2>/dev/null
RC=$?
set -e
RAW_OUTPUT=$(cat "$ADAPTER_TMP" 2>/dev/null || true)
rm -f "$ADAPTER_TMP"

if [ $RC -eq 124 ]; then
    echo "[letta-adapter] 处理超时 (${PROBE_TIMEOUT}s)，可重试" >&2
    exit 1
elif [ $RC -eq 141 ]; then
    # rc=141=SIGPIPE: 管道竞争瞬态 → 透传给 main.py 做退避重试，不降级
    echo "[letta-adapter] SIGPIPE rc=141，透传重试" >&2
    exit 141
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
        BODY_JSON=$(${PYTHON_BIN} -c "import json; print(json.dumps('''$REPLY'''.strip()))" 2>/dev/null || echo "\"$REPLY\"")
        printf '{"ts":%s,"from":"%s","reply":%s}\n' "$TIMESTAMP" "${FROM_ID:-unknown}" "$BODY_JSON" >> "$REPLIES_DIR/replies.jsonl"
    fi
else
    # v1.6: 无回复时也记录（避免静默丢回复）
    TIMESTAMP=$(date +%s)
    printf '{"ts":%s,"from":"%s","reply":null,"note":"empty output"}\n' "$TIMESTAMP" "${FROM_ID:-unknown}" >> "$REPLIES_DIR/replies.jsonl"
fi

exit 0
