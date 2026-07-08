#!/bin/bash
# SDK 部署脚本 — 从 shared/aim/src → 各 Agent bin 目录
# 维护: ZS0001 (呱呱) | 2026-06-19
set -euo pipefail

SHARED_SRC="$(cd "$(dirname "$0")/src" && pwd)"
SDK_FILE="aim_nats_sdk.py"
AGENTS=("ZS0001" "ZS0002" "ZS0003")

echo "=== AIM SDK 部署 ==="
echo "源: $SHARED_SRC/$SDK_FILE"
echo "版本: $(grep 'VERSION =' "$SHARED_SRC/$SDK_FILE" | head -1)"

for agent in "${AGENTS[@]}"; do
    target="$HOME/.aim/bin"
    mkdir -p "$target"
    cp "$SHARED_SRC/$SDK_FILE" "$target/$SDK_FILE"
    chmod +x "$target/$SDK_FILE"
    echo "✅ $agent: $target/$SDK_FILE"
done

echo ""
echo "⚠️  部署完成。请重启所有 Agent 加载新 SDK。"
