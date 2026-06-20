#!/bin/bash
# AIM Deploy — 从开发仓库同步到运行目录
# 版本：v2.1 | 日期：2026-06-19 | 维护：ZS0001 (呱呱)
# 619+ 修复：5 条路径 3 条已废弃，重写为 3 步同步
set -e

SHARED_DIR="$HOME/shared/aim"
AIM_DIR="$HOME/.aim"
SERVER_DIR="$HOME/aim-server"
VERSION=$(cat "$SHARED_DIR/VERSION" 2>/dev/null || echo "unknown")

echo "=== AIM Deploy v2.1 ==="
echo "Source: $SHARED_DIR"
echo "Version: $VERSION"
echo ""

# 1. SDK 同步 → ~/.aim/bin/（P0-4 修复，原 src/bin/ 已废弃）
echo "[1/3] SDK → ~/.aim/bin/"
mkdir -p "$AIM_DIR/bin"
cp "$SHARED_DIR/src/aim_nats_sdk.py" "$AIM_DIR/bin/aim_nats_sdk.py"
chmod +x "$AIM_DIR/bin/aim_nats_sdk.py"
echo "  ✅ aim_nats_sdk.py → $AIM_DIR/bin/ (v$VERSION)"

# 2. aim-client → 各 Agent 目录（原 src/agents/nats-agent.py 已退役）
echo "[2/3] aim-client → agents/"
for agent in ZS0001 ZS0002 ZS0003; do
    agent_dir="$AIM_DIR/agents/$agent"
    if [ -d "$agent_dir" ]; then
        mkdir -p "$agent_dir/aim-client"
        rsync -a --delete "$SHARED_DIR/aim-client/" "$agent_dir/aim-client/"
        echo "  ✅ $agent/aim-client/"
    else
        echo "  ⚠️  $agent 目录不存在，跳过"
    fi
done

# 3. Server 组件（如存在）
echo "[3/3] Server 组件"
if [ -f "$SHARED_DIR/aim-server/registry.py" ]; then
    cp "$SHARED_DIR/aim-server/registry.py" "$SERVER_DIR/"
    echo "  ✅ registry.py"
fi
if [ -f "$SHARED_DIR/aim-server/aim_server.py" ]; then
    cp "$SHARED_DIR/aim-server/aim_server.py" "$SERVER_DIR/"
    echo "  ✅ aim_server.py"
fi

echo ""
echo "=== Deploy complete ==="
echo "⚠️  必须重启所有 Agent 加载新代码"
echo "   ZS0001: launchctl stop com.aim.agent.zs0001 && launchctl start com.aim.agent.zs0001"
echo "   ZS0002/ZS0003: 各自重启"
