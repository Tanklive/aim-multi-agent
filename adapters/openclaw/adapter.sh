#!/bin/bash
# OpenClaw AIM Adapter — v1.3
# 模式:
#   process --message "<内容>" --from "<发送方>"  处理消息
#   health                                           健康探针 (无需 AIM_AGENT_ID)
#   info                                              Runtime 信息
#   cancel --task-id <id>                            取消任务
# 退出码: 0=正常, 1=可重试/降级, 2=挂了, 3=需人工介入

# ── 变量声明块（环境变量 > 默认值）──
# AIM_AGENT_ID: health 模式无需; process/info/cancel 需要
: ${AIM_HOME:="$HOME/.aim"}
: ${AIM_AGENT_ID:="ZS0001"}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_WORKSPACE:="$HOME/.openclaw/workspace"}

ADAPTER_TIMEOUT="${ADAPTER_TIMEOUT:-30}"
IDENTITY_FILE="${AIM_HOME}/agents/${AIM_AGENT_ID}/identity.json"
CONFIG_FILE="${AIM_HOME}/agents/${AIM_AGENT_ID}/config.json"
WORKSPACE="${AIM_WORKSPACE}"
QUEUE_DIR="${WORKSPACE}/.aim-queue"
REPLY_DIR="${WORKSPACE}/.aim-replies"
TRIGGER="${WORKSPACE}/.aim-trigger"

# ── 模式分发 ──────────────────────────────────────────
MODE="${1:-process}"
shift 2>/dev/null || true

# ── health 模式 ─────────────────────────────────────────
if [ "$MODE" = "health" ]; then
    # 检查 OpenClaw 主进程是否存活
    OPENCLAW_PID=$(ps aux | grep -v grep | grep "openclaw.*gateway" | awk '{print $2}' | head -1)
    if [ -z "$OPENCLAW_PID" ]; then
        echo '{"status":"unhealthy","active_sessions":0}' >&2
        exit 2
    fi
    # 检查进程是否响应（简单 ps 检查）
    if ! kill -0 "$OPENCLAW_PID" 2>/dev/null; then
        echo '{"status":"unhealthy","active_sessions":0}' >&2
        exit 2
    fi
    # 返回健康状态
    echo '{"status":"healthy","active_sessions":1,"provider":"openclaw","detail":"gateway running"}'
    exit 0
fi

# ── info 模式 ───────────────────────────────────────────
if [ "$MODE" = "info" ]; then
    # 从 identity.json 动态读取 execution_model
    EXEC_MODEL="deferred"
    IDENTITY_FILE="${AIM_HOME}/agents/${AIM_AGENT_ID}/identity.json"
    if [ -f "$IDENTITY_FILE" ]; then
        EXEC_MODEL=$(python3 -c "import json,sys; d=json.load(open('$IDENTITY_FILE')); print(d.get('execution_model','deferred'))" 2>/dev/null || echo "deferred")
    fi
    printf '{"provider":"openclaw","version":"24.15.0","execution_model":"%s","max_concurrency":1,"supports_streaming":false}\n' "$EXEC_MODEL"
    exit 0
fi


# ── cancel 模式 ───────────────────────────────────────────
if [ "$MODE" = "cancel" ]; then
    while [[ $# -gt 0 ]]; do
        case $1 in
            --task-id) TASK_ID="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    # OpenClaw 无独立任务系统，cancel = no-op，任务已投递到会话无法撤回
    # exit 0 而非 exit 2：消息写入文件队列前可拦截（cancel 有意义），
    # 写入后无法撤回（no-op 是合理语义）。Adater exit code 规范允许此特例
    # (2026-06-17 吉量+火鸡儿审查确认)
    printf '{"status":"cancelled","task_id":"%s","detail":"OpenClaw deferred — cancel is no-op"}\n' "${TASK_ID:-unknown}"
    exit 0
fi

# ── generate-reply 模式 ──────────────────────────────────
if [ "$MODE" = "generate-reply" ]; then
    while [[ $# -gt 0 ]]; do
        case $1 in
            --msg-id) MSG_ID="$2"; shift 2 ;;
            --from) FROM_ID="$2"; shift 2 ;;
            --content) CONTENT="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    QUEUE_FILE="${QUEUE_DIR}/${MSG_ID}.json"
    if [ -f "$QUEUE_FILE" ]; then
        FROM_ID=$(python3 -c "import json,sys; print(json.load(open('${QUEUE_FILE}')).get('from',''))" 2>/dev/null || echo "${FROM_ID:-unknown}")
    fi
    FROM_ID="${FROM_ID:-unknown}"

    # 构建 prompt：让 AI 直接生成回复
    PROMPT="你是呱呱(🐸)，收到来自 ${FROM_ID} 的一条消息。请直接输出回复内容(20-80字)，以🐸开头，不要加前缀、解释或工具调用。消息内容：${CONTENT}"

    OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.npm-global/bin/openclaw}"
    SESSION_KEY="agent:main:aim-reply-${MSG_ID}"

    REPLY=$("$OPENCLAW_BIN" agent \
        --session-key "$SESSION_KEY" \
        --local \
        --message "$PROMPT" \
        --json \
        --timeout 30 \
        2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('payloads',[{}])[0].get('text',''))" 2>/dev/null)

    if [ -n "$REPLY" ]; then
        echo "$REPLY"
        exit 0
    else
        echo ""
        exit 1
    fi
fi

# ── process 模式 ────────────────────────────────────────
# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        process) shift ;;
        --message) MESSAGE="$2"; shift 2 ;;
        --from) FROM_ID="$2"; shift 2 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$MESSAGE" ]; then
    echo "缺少 --message 参数" >&2
    exit 2
fi

FROM_ID="${FROM_ID:-unknown}"
MSG_ID="adapter-$(date +%s)-$$"

# 确保目录存在
mkdir -p "$QUEUE_DIR" "$REPLY_DIR"

# 写入消息到队列（格式对齐 process_aim_queue.py）
cat > "${QUEUE_DIR}/${MSG_ID}.json" << EOF
{"msg_id": "${MSG_ID}", "from": "${FROM_ID}", "to": "${AIM_AGENT_ID}", "content": "${MESSAGE}", "ts": $(date +%s), "type": "dm"}
EOF

# 触发处理
touch "$TRIGGER"

# 轮询等待回复（短间隔，适配器级超时控制）
# aim-client _call_adapter() 根据 execution_model 决定阻塞/非阻塞策略；
# adapter 本身统一使用阻塞轮询模式，由调用方控制超时和取消（2026-06-17 审查）
POLL_COUNT=0
MAX_POLL=$((ADAPTER_TIMEOUT / 2))  # 每2秒一次

while [ $POLL_COUNT -lt $MAX_POLL ]; do
    REPLY_FILE="${REPLY_DIR}/${MSG_ID}.txt"
    if [ -f "$REPLY_FILE" ]; then
        REPLY=$(cat "$REPLY_FILE" 2>/dev/null)
        rm -f "$REPLY_FILE"
        
        if [ -n "$REPLY" ]; then
            # OK = ACK 回执，不发消息（避免回音循环）
            if [ "$REPLY" = "OK" ]; then
                rm -f "$REPLY_FILE"
                exit 0  # 返回空成功
            fi
            echo "$REPLY"
            exit 0
        fi
    fi
    
    # 也检查 JSON 格式的回复
    shopt -s nullglob
    for f in "${REPLY_DIR}"/*.json; do
        if grep -q "$MSG_ID" "$f" 2>/dev/null; then
            REPLY=$(python3 -c "import json; print(json.load(open('$f')).get('reply',''))" 2>/dev/null)
            rm -f "$f"
            if [ -n "$REPLY" ]; then
                echo "$REPLY"
                exit 0
            fi
        fi
    done
    shopt -u nullglob
    
    sleep 2
    POLL_COUNT=$((POLL_COUNT + 1))
done

# 超时 → 清理队列消息，走降级
# exit 2 = 降级（不重试，入 dead 队列）。
# OpenClaw 文件队列模式下，超时表示 framework 未在窗口内响应，
# 重复投递可能堆积 → 选择降级而非重试（2026-06-17 审查确认合理）
rm -f "${QUEUE_DIR}/${MSG_ID}.json"
echo "OpenClaw 处理超时 (${ADAPTER_TIMEOUT}s)" >&2
exit 2
