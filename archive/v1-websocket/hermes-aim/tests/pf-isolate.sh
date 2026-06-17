#!/bin/bash
# AIM P3-1 T4 测试用 pf 防火墙规则
# 用于模拟网络断连（不 kill 进程）
# 用法:
#   ./pf-isolate.sh start [port]   # 阻断指定端口
#   ./pf-isolate.sh stop           # 恢复规则
#   ./pf-isolate.sh status         # 查看状态

PORT="${2:-18900}"
ANCHOR="aim-test"

case "${1:-status}" in
  start)
    echo "🔒 阻断端口 $PORT (anchor=$ANCHOR)"
    # 创建阻断规则（优先于默认规则）
    echo "block drop proto tcp from any to any port $PORT" | \
      sudo pfctl -a "$ANCHOR" -f - 2>/dev/null
    # 确保 pf 已启用
    sudo pfctl -e 2>/dev/null || true
    echo "✅ $PORT 已阻断"
    sudo pfctl -a "$ANCHOR" -sr 2>/dev/null
    ;;

  stop)
    echo "🔓 恢复端口 $PORT"
    # 清除测试锚点规则
    sudo pfctl -a "$ANCHOR" -F rules 2>/dev/null || true
    # 恢复默认规则
    sudo pfctl -f /etc/pf.conf 2>/dev/null || true
    # 如果原来没开 pf 就关闭
    # （不自动关闭，避免影响其他规则）
    echo "✅ $PORT 已恢复"
    ;;

  status)
    echo "📊 pf 状态:"
    sudo pfctl -si 2>/dev/null | head -5
    echo ""
    echo "🔍 锚点 $ANCHOR 规则:"
    sudo pfctl -a "$ANCHOR" -sr 2>/dev/null || echo "  （无规则）"
    echo ""
    echo "🔍 默认规则:"
    sudo pfctl -sr 2>/dev/null | head -5 || echo "  pf 未启用"
    ;;

  *)
    echo "用法: $0 {start|stop|status} [port]"
    exit 1
    ;;
esac
