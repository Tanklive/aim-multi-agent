#!/bin/bash
# ────────────────────────────────────────────────────────────
# AIM Agent Setup — 新 Agent 标准化接入脚本
# 用法: bash ~/.aim/setup-agent.sh <AGENT_ID> <AGENT_NAME> <FRAMEWORK>
# 示例: bash ~/.aim/setup-agent.sh ZS0004 "新Agent" openclaw
# ────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; exit 1; }

AIM_HOME="${AIM_HOME:-$HOME/.aim}"
AGENT_ID="${1:?Usage: $0 <AGENT_ID> <AGENT_NAME> <FRAMEWORK>}"
AGENT_NAME="${2:?Missing AGENT_NAME}"
FRAMEWORK="${3:?Missing FRAMEWORK (openclaw|hermes|letta)}"
AGENT_DIR="$AIM_HOME/agents/$AGENT_ID"
SHARED_AIM="${SHARED_AIM:-$HOME/shared/aim}"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   AIM Agent Setup — $AGENT_ID ($AGENT_NAME)  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 0. 预检 ──
[ -d "$AGENT_DIR" ] && err "Agent $AGENT_ID 已存在: $AGENT_DIR"
[ -d "$SHARED_AIM" ] || err "shared/aim 不存在: $SHARED_AIM"

NATS_SERVER="${NATS_SERVER:-nats://127.0.0.1:4222}"
case "$FRAMEWORK" in
    openclaw)  EXEC_MODEL="realtime" ;;
    hermes)    EXEC_MODEL="realtime" ;;
    letta)     EXEC_MODEL="deferred" ;;
    *)         EXEC_MODEL="custom"   ;;  # 自定义框架
esac

# ── 1. 创建标准目录 ──
info "创建目录结构…"
mkdir -p "$AGENT_DIR"/{logs,secrets,data,aim-client}

# ── 2. 凭据 ──
info "生成凭据…"

# nkey (如果 NATS 需要)
if [ ! -f "$AGENT_DIR/secrets/nkey.seed" ]; then
    python3 -c "
import secrets
seed = 'SA' + secrets.token_hex(32)[:56]
with open('$AGENT_DIR/secrets/nkey.seed', 'w') as f:
    f.write(seed)
" 2>/dev/null || touch "$AGENT_DIR/secrets/nkey.seed"
    chmod 600 "$AGENT_DIR/secrets/nkey.seed"
fi

# aim.creds
if [ ! -f "$AGENT_DIR/aim.creds" ]; then
    cat > "$AGENT_DIR/aim.creds" <<CREOF
{
  "agent_id": "$AGENT_ID",
  "agent_name": "$AGENT_NAME",
  "nats_server": "$NATS_SERVER",
  "_note": "AIM credentials — keep secret"
}
CREOF
    chmod 600 "$AGENT_DIR/aim.creds"
fi

# identity.json
cat > "$AGENT_DIR/identity.json" <<IDEOF
{
  "agent_id": "$AGENT_ID",
  "agent_name": "$AGENT_NAME",
  "framework": "$FRAMEWORK",
  "execution_model": "$EXEC_MODEL",
  "version": "1.5.0"
}
IDEOF

# VERSION
echo "1.5.0" > "$AGENT_DIR/VERSION"

# ── 3. 配置 ──
info "生成配置…"
TEMPLATE="$AIM_HOME/config/agent-template.json"
if [ ! -f "$TEMPLATE" ]; then
    warn "模板不存在, 使用内置配置"
fi

python3 << PEOF
import json

AGENT_ID = "$AGENT_ID"
AGENT_NAME = "$AGENT_NAME"
FRAMEWORK = "$FRAMEWORK"
EXEC_MODEL = "$EXEC_MODEL"

cfg = {
    "agent_id": AGENT_ID,
    "agent_name": AGENT_NAME,
    "protocol_version": "",
    "version": "1.5.0",
    "nats_server": "$NATS_SERVER",
    "creds_path": f"~/.aim/agents/{AGENT_ID}/aim.creds",
    "framework": FRAMEWORK,
    "execution_model": EXEC_MODEL,
    "adapter_cmd": f"~/.aim/agents/{AGENT_ID}/adapter.sh",
    "adapter_timeout": 300,
    "mention_names": [AGENT_ID, AGENT_NAME],
    "security": {
        "allowlist": ["ZS0001", "ZS0002", "ZS0003", AGENT_ID],
        "allow_grp": ["grp_trio"],
    },
    "heartbeat": {"interval_ms": 30000, "timeout_ms": 120000},
    "launchd": {"auto_manage": True, "keep_alive": True},
    "healthd": {"auto_ensure": True, "check_interval_s": 30},
    "notification": {"channel": ["file", "system_event"], "watch_interval_s": 5},
    "log": {"level": "info"},
    "queue": {"max_age_ms": 3600000, "ack_timeout_ms": 300000, "capacity": 1000, "max_retries": 3},
    "queue_persist_path": f"~/.aim/agents/{AGENT_ID}/queue.jsonl",
    "queue_processor": {"enabled": True, "poll_interval_s": 1},
    "max_msg_age_sec": 900,
    "stall_watchdog_sec": 60,
    "grp_reply_cooldown_sec": 30,
}

with open(f"$AGENT_DIR/config.json", "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print("config.json written")
PEOF

# ── 4. 适配器模板 ──
info "生成 adapter.sh 模板…"
ADAPTER_TEMPLATE="$SHARED_AIM/adapters/$FRAMEWORK/adapter.sh"
if [ -f "$ADAPTER_TEMPLATE" ]; then
    cp "$ADAPTER_TEMPLATE" "$AGENT_DIR/adapter.sh"
    chmod 755 "$AGENT_DIR/adapter.sh"
    info "adapter.sh 已复制 ($FRAMEWORK)"
else
    # 没有模板 → 生成最小适配器
    cat > "$AGENT_DIR/adapter.sh" <<ADAPEOF
#!/bin/bash
# $AGENT_ID Adapter — $FRAMEWORK framework
# TODO: 实现你的框架调用逻辑
MODE="\${1:-}"; shift || true
case "\$MODE" in
    health) echo '{"status":"healthy","detail":"$FRAMEWORK adapter"}' ;;
    info)   echo '{"framework":"$FRAMEWORK","version":"1.0.0"}' ;;
    process)
        echo '{"status":"ok","reply":"$AGENT_ID 收到了！(未实现 adapter 逻辑)"}'
        ;;
    *)      echo "unknown mode: \$MODE" >&2; exit 3 ;;
esac
ADAPEOF
    chmod 755 "$AGENT_DIR/adapter.sh"
    warn "无适配器模板, 生成最小 stub (需自行实现)"
fi

# ── 5. launchd plist ──
info "生成 launchd plist…"
PLIST_FILE="$AGENT_DIR/com.aim.agent.$AGENT_ID.plist"
cat > "$PLIST_FILE" <<PLEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aim.agent.$AGENT_ID</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>aim-client/main.py</string>
        <string>--mode</string>
        <string>direct</string>
        <string>--agent</string>
        <string>$AGENT_ID</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$AGENT_DIR</string>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$AGENT_DIR/logs/agent.out.log</string>
    <key>StandardErrorPath</key>
    <string>$AGENT_DIR/logs/agent.err.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:$HOME/.local/bin</string>
        <key>AIM_AGENT_ID</key>
        <string>$AGENT_ID</string>
    </dict>
</dict>
</plist>
PLEOF

info "plist: $PLIST_FILE"

# ── 6. aim-client 软链接 → shared ──
info "链接 aim-client → shared/aim_client..."
for src_py in "$SHARED_AIM/aim_client"/*.py; do
    bn="$(basename "$src_py")"
    ln -sf "$src_py" "$AGENT_DIR/aim-client/$bn" 2>/dev/null || true
done

# 注册到 NATS allowlist (如果需要)
info "注册 $AGENT_ID 到安全性配置…"
for existing in "$AIM_HOME/agents"/*/config.json; do
    [ -f "$existing" ] || continue
    python3 -c "
import json
with open('$existing') as f: c = json.load(f)
sec = c.get('security', {})
wl = sec.get('allowlist', [])
if '$AGENT_ID' not in wl:
    wl.append('$AGENT_ID')
    sec['allowlist'] = wl
    c['security'] = sec
    with open('$existing', 'w') as f: json.dump(c, f, indent=2, ensure_ascii=False)
    print(f'  + $AGENT_ID added to {c[\"agent_id\"]} allowlist')
" 2>/dev/null || true
done

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ $AGENT_ID ($AGENT_NAME) 接入完成     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  📁 目录: $AGENT_DIR"
echo "  🔧 配置: $AGENT_DIR/config.json"
echo "  🔌 适配器: $AGENT_DIR/adapter.sh ($FRAMEWORK)"
echo "  🚀 启动: launchctl load $PLIST_FILE"
echo ""
echo "  ⚠️  别忘了:"
echo "  1. 实现 adapter.sh 中的框架调用逻辑"
echo "  2. 如果有框架专属配置, 加到 config.json"
echo "  3. 加载 plist 启动 Agent"
echo ""
