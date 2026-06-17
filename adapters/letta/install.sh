#!/bin/bash
# AIM Letta 适配器 — 一键安装 (v1.1)
# 支持自动检测和手动指定，路径可配置
#
# 用法:
#   bash install.sh                                    # 自动检测
#   bash install.sh --agent-id ZS0003                  # 指定 agent
#   bash install.sh --check-only                       # 只检测，不安装
#   bash install.sh --agent-id ZS0003 --letta-agent-id agent-local-xxx
#   bash install.sh --queue-dir ~/.aim/queue --reply-dir ~/.aim/replies

set -e

# ── 默认值 ─────────────────────────────
AGENT_ID="${AIM_AGENT_ID:-}"
LETTA_AGENT_ID=""
LETTA_BIN="${HOME}/.npm-global/bin/letta"
AIM_DIR="${HOME}/.aim"
QUEUE_DIR="${AIM_QUEUE_DIR:-}"
REPLY_DIR="${AIM_REPLY_DIR:-}"
CHECK_ONLY=false
ADAPTER_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTO_MODE=true

# ── 参数解析 ───────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --agent-id)       AGENT_ID="$2"; AUTO_MODE=false; shift 2 ;;
        --letta-agent-id) LETTA_AGENT_ID="$2"; AUTO_MODE=false; shift 2 ;;
        --letta-bin)      LETTA_BIN="$2"; shift 2 ;;
        --queue-dir)      QUEUE_DIR="$2"; shift 2 ;;
        --reply-dir)      REPLY_DIR="$2"; shift 2 ;;
        --check-only)     CHECK_ONLY=true; shift ;;
        -h|--help)
            echo "AIM Letta 适配器 — 安装脚本 v1.1"
            echo ""
            echo "选项:"
            echo "  --agent-id ID          AIM Agent ID"
            echo "  --letta-agent-id ID    Letta Agent ID"
            echo "  --queue-dir PATH       队列目录 (默认 ~/.aim/agents/{id}/queue)"
            echo "  --reply-dir PATH       回复目录 (默认 ~/.aim/agents/{id}/replies)"
            echo "  --check-only           只检测，不安装"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}✅${NC} $*"; }
fail() { echo -e "${RED}❌${NC} $*"; }
warn() { echo -e "${YELLOW}⚠️${NC}  $*"; }
info() { echo "   $*"; }

echo ""
echo "╔══════════════════════════════════╗"
echo "║  AIM Letta 适配器 v1.1           ║"
echo "╚══════════════════════════════════╝"
echo ""

FAILS=0

# ── 1. 检测 Letta CLI ──────────────────
echo "── [1/6] Letta CLI ──"
if [ -x "$LETTA_BIN" ]; then
    pass "Letta CLI: $LETTA_BIN"
else
    LETTA_BIN=$(which letta 2>/dev/null || echo "")
    if [ -n "$LETTA_BIN" ]; then
        pass "Letta CLI (PATH): $LETTA_BIN"
    else
        fail "Letta CLI 未找到"
        info "npm install -g @letta-ai/letta-code"
        FAILS=$((FAILS + 1))
    fi
fi
echo ""

# ── 2. 检测 AIM Agent ──────────────────
echo "── [2/6] AIM Agent ──"
if [ -z "$AGENT_ID" ]; then
    for cfg in "$AIM_DIR"/agents/ZS*/config.json; do
        [ -f "$cfg" ] || continue
        fw=$(python3 -c "import json; print(json.load(open('$cfg')).get('framework',''))" 2>/dev/null)
        [ "$fw" = "letta" ] && AGENT_ID=$(python3 -c "import json; print(json.load(open('$cfg')).get('agent_id',''))" 2>/dev/null) && break
    done
fi
if [ -n "$AGENT_ID" ]; then
    AGENT_DIR="$AIM_DIR/agents/$AGENT_ID"
    [ -d "$AGENT_DIR" ] && pass "AIM Agent: $AGENT_ID" || { fail "$AGENT_DIR 不存在"; FAILS=$((FAILS + 1)); }
else
    fail "未找到 letta 框架的 AIM Agent"
    FAILS=$((FAILS + 1))
fi
echo ""

# ── 3. 检测 Letta Agent ────────────────
echo "── [3/6] Letta Agent ──"
if [ -z "$LETTA_AGENT_ID" ] && [ -f "$AGENT_DIR/config.json" ]; then
    LETTA_AGENT_ID=$(python3 -c "import json; print(json.load(open('$AGENT_DIR/config.json')).get('letta_agent_id',''))" 2>/dev/null)
fi
if [ -n "$LETTA_AGENT_ID" ]; then
    MEMFS="${HOME}/.letta/lc-local-backend/memfs/${LETTA_AGENT_ID}"
    [ -d "$MEMFS" ] && pass "Letta Agent: $LETTA_AGENT_ID" || warn "未确认: $LETTA_AGENT_ID"
else
    warn "未指定 Letta Agent ID"
fi
echo ""

# ── 4. 检测队列路径 ────────────────────
echo "── [4/6] 队列路径 ──"
if [ -z "$QUEUE_DIR" ]; then
    QUEUE_DIR="$AGENT_DIR/queue"
fi
if [ -z "$REPLY_DIR" ]; then
    REPLY_DIR="$AGENT_DIR/replies"
fi
mkdir -p "$QUEUE_DIR" 2>/dev/null && pass "队列: $QUEUE_DIR" || { fail "无法创建"; FAILS=$((FAILS + 1)); }
mkdir -p "$REPLY_DIR" 2>/dev/null && pass "回复: $REPLY_DIR" || { fail "无法创建"; FAILS=$((FAILS + 1)); }

# 兼容：如果 nats-agent 当前写入的是 ~/.openclaw/workspace/.aim-queue/
OLD_QUEUE="$HOME/.openclaw/workspace/.aim-queue"
if [ -d "$OLD_QUEUE" ] && [ "$QUEUE_DIR" != "$OLD_QUEUE" ]; then
    warn "nats-agent 当前写入: $OLD_QUEUE"
    info "建议用 --queue-dir $OLD_QUEUE 或等 nats-agent 迁移到标准路径"
fi
echo ""

# ── 5. 检测 nats-agent ────────────────
echo "── [5/6] nats-agent ──"
if pgrep -f "$AGENT_ID.*nats-agent" > /dev/null 2>&1; then
    pass "nats-agent 运行中"
else
    warn "nats-agent 未运行"
fi
echo ""

# ── 6. 框架兼容性分析 ──────────────────
echo "── [6/6] 框架分析 ──"
echo "   类型: Letta Code (本地模式)"
echo "   调用: letta -p (script TTY)"
echo "   消费: watcher 2s poll (launchd)"
echo "   空闲: 2-5s 响应 | 对话中: 排队等待"
echo ""

# ── 结果 ───────────────────────────────
if [ "$CHECK_ONLY" = true ]; then
    if [ $FAILS -eq 0 ]; then
        echo -e "${GREEN}✅ 自检全部通过${NC}"
    else
        echo -e "${RED}❌ 发现 $FAILS 个问题${NC}"
    fi
    exit $FAILS
fi
[ $FAILS -gt 0 ] && { echo -e "${RED}❌ $FAILS 个问题，中止${NC}"; exit 1; }

# ── 安装 ───────────────────────────────
echo "═══════════════════════════════════"
echo "  安装中..."
echo ""

mkdir -p "$AGENT_DIR/logs"

cp "$ADAPTER_DIR/aim-letta-consumer.sh" "$AGENT_DIR/"
chmod +x "$AGENT_DIR/aim-letta-consumer.sh"
pass "consumer → $AGENT_DIR/aim-letta-consumer.sh"

cp "$ADAPTER_DIR/aim-letta-watcher.py" "$AGENT_DIR/"
chmod +x "$AGENT_DIR/aim-letta-watcher.py"
pass "watcher → $AGENT_DIR/aim-letta-watcher.py"

# 更新 config.json
if [ -f "$AGENT_DIR/config.json" ]; then
    python3 -c "
import json
cfg = json.load(open('$AGENT_DIR/config.json'))
cfg['letta_bin'] = '$LETTA_BIN'
cfg['letta_agent_id'] = '$LETTA_AGENT_ID'
cfg['queue_dir'] = '$QUEUE_DIR'
cfg['reply_dir'] = '$REPLY_DIR'
cfg['adapter_version'] = '1.1'
json.dump(cfg, open('$AGENT_DIR/config.json','w'), indent=2, ensure_ascii=False)
" 2>/dev/null
    pass "config.json 已更新"
fi

# launchd
PLIST="$HOME/Library/LaunchAgents/com.aim.letta-watcher.${AGENT_ID}.plist"
cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aim.letta-watcher.${AGENT_ID}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>-B</string>
        <string>${AGENT_DIR}/aim-letta-watcher.py</string>
        <string>--agent-id</string>
        <string>${AGENT_ID}</string>
        <string>--queue-dir</string>
        <string>${QUEUE_DIR}</string>
        <string>--reply-dir</string>
        <string>${REPLY_DIR}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${AGENT_DIR}/logs/letta-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>${AGENT_DIR}/logs/letta-watcher.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:${HOME}/.npm-global/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
pass "launchd → $PLIST"

echo ""
echo -e "${GREEN}✅ 安装完成${NC}"
echo "  日志: tail -f $AGENT_DIR/logs/letta-watcher.log"
echo "  卸载: launchctl unload $PLIST"
