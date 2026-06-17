#!/bin/bash
# AIM Letta 适配器 — 队列消费者 (v1.1)
# 标准组件：扫描队列 → Letta Code 处理 → 写回复
# 由 watcher 事件驱动调用，也支持手动执行
#
# 路径优先级: 环境变量 > 默认 AIM 标准路径 > 兼容旧路径
#   AIM_QUEUE_DIR  — 消息队列目录
#   AIM_REPLY_DIR  — 回复目录

# ── 路径解析 ───────────────────────────
if [ -n "${AIM_QUEUE_DIR:-}" ]; then
    QUEUE_DIR="$AIM_QUEUE_DIR"
elif [ -d "$HOME/.aim/agents/${AIM_AGENT_ID:-ZS0003}/queue" ]; then
    QUEUE_DIR="$HOME/.aim/agents/${AIM_AGENT_ID:-ZS0003}/queue"
else
    # 兼容 nats-agent V2 当前写入路径
    QUEUE_DIR="$HOME/.openclaw/workspace/.aim-queue"
fi

if [ -n "${AIM_REPLY_DIR:-}" ]; then
    REPLY_DIR="$AIM_REPLY_DIR"
elif [ -d "$HOME/.aim/agents/${AIM_AGENT_ID:-ZS0003}/replies" ]; then
    REPLY_DIR="$HOME/.aim/agents/${AIM_AGENT_ID:-ZS0003}/replies"
else
    REPLY_DIR="$HOME/.openclaw/workspace/.aim-replies"
fi

AGENT_ID="${AIM_AGENT_ID:-ZS0003}"
LOG_FILE="$HOME/.aim/agents/$AGENT_ID/logs/letta-consumer.log"
LETTA_BIN="${LETTA_BIN:-$HOME/.npm-global/bin/letta}"
LOCK_DIR="/tmp/aim-letta-consumer.${AGENT_ID}.lock"
MAX_PER_RUN=3
AI_TIMEOUT=45

export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.npm-global/bin:$PATH"
mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG_FILE"; }

# ── 目录锁 ─────────────────────────────
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

[ -d "$QUEUE_DIR" ] || exit 0
mkdir -p "$REPLY_DIR"

QUEUED=$(ls -t "$QUEUE_DIR"/*.json 2>/dev/null | head -$MAX_PER_RUN)
[ -z "$QUEUED" ] && exit 0

COUNT=0
for QFILE in $QUEUED; do
    COUNT=$((COUNT + 1))
    MSG_ID=$(basename "$QFILE" .json)
    RAW=$(cat "$QFILE" 2>/dev/null) || { rm -f "$QFILE"; continue; }

    FROM=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('from','unknown'))" 2>/dev/null)
    CONTENT=$(echo "$RAW" | python3 -c "import sys,json; print(json.load(sys.stdin).get('content',''))" 2>/dev/null)
    [ -z "$CONTENT" ] && { rm -f "$QFILE"; continue; }

    # 跳过自己的消息（避免循环）
    [ "$FROM" = "$AGENT_ID" ] && { rm -f "$QFILE"; continue; }

    log "msg=$MSG_ID from=$FROM content=${CONTENT:0:60}"

    # ── 调 Letta Code 处理 ──────────────
    TMPFILE=$(mktemp)
    PROMPT="[AIM消息] 收到来自 ${FROM} 的消息：${CONTENT}"

    /usr/bin/script -q /dev/null "$LETTA_BIN" -p "$PROMPT" > "$TMPFILE" 2>/dev/null &
    LPID=$!
    ( sleep $AI_TIMEOUT; kill $LPID 2>/dev/null ) &
    WPID=$!
    wait $LPID 2>/dev/null
    kill $WPID 2>/dev/null
    wait $WPID 2>/dev/null

    RAW_OUTPUT=$(cat "$TMPFILE" 2>/dev/null || echo "")
    rm -f "$TMPFILE"

    # ── 过滤 Letta CLI 噪声 ──────────────
    REPLY=$(LETTA_OUTPUT="$RAW_OUTPUT" python3 << 'PYEOF'
import sys, re, os
raw = os.environ.get("LETTA_OUTPUT","")
raw = re.sub(r"^(?:\^D|[\x00-\x1f])+", "", raw)
noise = ("connected","loading","error saving","error:","enoent","session:","duration:","messages:","resume this")
clean = []
for l in raw.strip().split("\n"):
    s = l.strip()
    if not s: continue
    low = s.lower()
    if any(low.startswith(n) for n in noise): continue
    if s.startswith(("/users/", "    at ", "at mkdirsync", "at mkdir (", "file://", "at ")): continue
    if "node:fs" in low or "mkdirsync" in low: continue
    clean.append(s)
print("\n".join(clean).strip())
PYEOF
    )

    # ── 写回复 ───────────────────────────
    REPLY_FILE="$REPLY_DIR/${MSG_ID}.txt"
    if [ -n "$REPLY" ]; then
        echo "$REPLY" > "$REPLY_FILE"
        log "  -> reply: ${REPLY:0:60}"
    else
        echo "NO_REPLY" > "$REPLY_FILE"
        log "  -> (no reply)"
    fi
    rm -f "$QFILE"
done

log "done ($COUNT msgs)"
