#!/bin/bash
# AIM P3-1 测试后清理脚本
# 清理离线队列、残留数据，恢复 pf 防火墙
# 用法: ./cleanup-test-data.sh [--all]

echo "🧹 AIM P3-1 测试数据清理"
echo "=========================="

AIM_DATA_DIR="$HOME/.hermes/aim/data"

# 清理离线队列
echo ""
echo "📁 离线队列文件:"
for f in "$AIM_DATA_DIR"/offline_*.jsonl; do
  if [ -f "$f" ]; then
    count=$(wc -l < "$f")
    echo "  🗑️  $f ($count 行)"
    rm "$f"
  fi
done

# 清理测试消息（可选 --all）
if [ "$1" = "--all" ]; then
  echo ""
  echo "📁 messages.jsonl:"
  if [ -f "$AIM_DATA_DIR/messages.jsonl" ]; then
    total=$(wc -l < "$AIM_DATA_DIR/messages.jsonl")
    echo "  📊 $total 行（保留，不做清理）"
    echo "  ℹ️  如需清理请手动操作"
  fi

  # 清理旧轮转文件
  for f in "$AIM_DATA_DIR"/*.old; do
    if [ -f "$f" ]; then
      echo "  🗑️  轮转备份: $f"
      rm "$f"
    fi
  done
fi

# 清理 pf 防火墙规则
echo ""
echo "🔓 pf 防火墙:"
if sudo pfctl -a "aim-test" -sr 2>/dev/null | grep -q .; then
  sudo pfctl -a "aim-test" -F rules 2>/dev/null
  echo "  ✅ aim-test 锚点已清理"
else
  echo "  ℹ️  aim-test 锚点无规则"
fi

echo ""
echo "✅ 清理完成"
