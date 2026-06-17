#!/bin/bash
# AIM Deploy — 从开发仓库同步到运行目录
# 版本：v2.0
# 日期：2026-06-09
# 负责人：呱呱 🐸
set -e

SHARED_DIR="$HOME/shared/aim"
SERVER_DIR="$HOME/aim-server"
AIM_DIR="$HOME/.aim"

echo "=== AIM Deploy v2.0 ==="
echo "Source: $SHARED_DIR"
echo "Server: $SERVER_DIR"
echo "Agent:  $AIM_DIR"
echo ""

# 1. 同步 Server 代码
echo "[1/5] Syncing Server..."
if [ -d "$SHARED_DIR/src/server" ]; then
    for f in registry.py aim_server.py aim_observer.py; do
        if [ -f "$SHARED_DIR/src/server/$f" ]; then
            cp "$SHARED_DIR/src/server/$f" "$SERVER_DIR/"
            echo "  ✅ $f"
        fi
    done
else
    echo "  ⚠️  src/server/ not found, skipping"
fi

# 2. 同步共享工具
echo "[2/5] Syncing shared tools..."
if [ -d "$SHARED_DIR/src/bin" ]; then
    for f in aim_nats_sdk.py aim_send.py aim-watch.py aim-observe.py; do
        if [ -f "$SHARED_DIR/src/bin/$f" ]; then
            cp "$SHARED_DIR/src/bin/$f" "$AIM_DIR/bin/"
            echo "  ✅ $f"
        fi
    done
else
    echo "  ⚠️  src/bin/ not found, skipping"
fi

# 3. 同步通用模块
echo "[3/5] Syncing common modules..."
if [ -d "$SHARED_DIR/src/common" ]; then
    mkdir -p "$AIM_DIR/common"
    for f in aim_pin.py aim_retry.py; do
        if [ -f "$SHARED_DIR/src/common/$f" ]; then
            cp "$SHARED_DIR/src/common/$f" "$AIM_DIR/common/"
            echo "  ✅ $f"
        fi
    done
else
    echo "  ⚠️  src/common/ not found, skipping"
fi

# 4. 同步 Agent 模板
echo "[4/5] Syncing agent templates..."
if [ -d "$SHARED_DIR/src/agents" ]; then
    for agent in ZS0001 ZS0002 ZS0003; do
        if [ -d "$AIM_DIR/agents/$agent" ]; then
            if [ -f "$SHARED_DIR/src/agents/nats-agent.py" ]; then
                cp "$SHARED_DIR/src/agents/nats-agent.py" "$AIM_DIR/agents/$agent/"
                echo "  ✅ $agent/nats-agent.py"
            fi
        fi
    done
else
    echo "  ⚠️  src/agents/ not found, skipping"
fi

# 5. 同步配置模板
echo "[5/5] Syncing config templates..."
if [ -d "$SHARED_DIR/config" ]; then
    for f in nats.conf.template aim-config.template.json; do
        if [ -f "$SHARED_DIR/config/$f" ]; then
            cp "$SHARED_DIR/config/$f" "$AIM_DIR/config/"
            echo "  ✅ $f"
        fi
    done
else
    echo "  ⚠️  config/ not found, skipping"
fi

echo ""
echo "=== Deploy complete ==="
echo "Next: restart services to pick up changes"
