#!/bin/bash
# AIM 健康检查脚本 — 每日运行
# 检查 NATS Server、Agent 状态、日志异常

set -euo pipefail

echo "=== AIM 健康检查 $(date '+%Y-%m-%d %H:%M:%S') ==="
echo ""

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0

# 1. 检查 NATS Server
echo "1. NATS Server 状态"
if pgrep -f "nats-server" > /dev/null; then
    PID=$(pgrep -f "nats-server")
    echo -e "   ${GREEN}✅ 运行中${NC} (PID: $PID)"
else
    echo -e "   ${RED}❌ 未运行${NC}"
    ((ERRORS++))
fi

# 2. 检查端口监听
echo ""
echo "2. 端口监听"
if lsof -i :4222 | grep LISTEN > /dev/null 2>&1; then
    echo -e "   ${GREEN}✅ 端口 4222 正常监听${NC}"
else
    echo -e "   ${RED}❌ 端口 4222 未监听${NC}"
    ((ERRORS++))
fi

# 3. 检查连接数
echo ""
echo "3. NATS 连接数"
CONNECTIONS=$(lsof -i :4222 | grep ESTABLISHED | wc -l)
echo -e "   ${GREEN}✅ $CONNECTIONS 个连接${NC}"

# 4. 检查 Agent 进程
echo ""
echo "4. Agent 进程状态"
for agent in ZS0001 ZS0002 ZS0003; do
    if pgrep -f "$agent" > /dev/null; then
        PID=$(pgrep -f "$agent" | head -1)
        echo -e "   ${GREEN}✅ $agent 运行中${NC} (PID: $PID)"
    else
        echo -e "   ${YELLOW}⚠️  $agent 未运行${NC}"
    fi
done

# 5. 检查日志目录
echo ""
echo "5. 日志目录"
if [ -d "$HOME/aim-server/logs" ]; then
    LOG_COUNT=$(ls -1 "$HOME/aim-server/logs" 2>/dev/null | wc -l)
    echo -e "   ${GREEN}✅ Server 日志: $LOG_COUNT 个文件${NC}"
else
    echo -e "   ${YELLOW}⚠️  Server 日志目录不存在${NC}"
fi

for agent in ZS0001 ZS0002 ZS0003; do
    LOG_DIR="$HOME/.aim/agents/$agent/logs"
    if [ -d "$LOG_DIR" ]; then
        LOG_COUNT=$(ls -1 "$LOG_DIR" 2>/dev/null | wc -l)
        echo -e "   ${GREEN}✅ $agent 日志: $LOG_COUNT 个文件${NC}"
    else
        echo -e "   ${YELLOW}⚠️  $agent 日志目录不存在${NC}"
    fi
done

# 6. 检查磁盘空间
echo ""
echo "6. 磁盘空间"
JETSTREAM_DIR="$HOME/aim-server/data/jetstream"
if [ -d "$JETSTREAM_DIR" ]; then
    SIZE=$(du -sh "$JETSTREAM_DIR" | cut -f1)
    echo -e "   ${GREEN}✅ JetStream 数据: $SIZE${NC}"
else
    echo -e "   ${YELLOW}⚠️  JetStream 目录不存在${NC}"
fi

# 7. 检查最近错误
echo ""
echo "7. 最近日志错误"
SERVER_LOG="$HOME/aim-server/logs/nats-server.log"
if [ -f "$SERVER_LOG" ]; then
    ERRORS=$(tail -100 "$SERVER_LOG" | grep -i "error\|warn" | wc -l)
    if [ $ERRORS -eq 0 ]; then
        echo -e "   ${GREEN}✅ 无错误${NC}"
    else
        echo -e "   ${YELLOW}⚠️  发现 $ERRORS 条警告/错误${NC}"
    fi
fi

# 总结
echo ""
echo "═══════════════════════════════════════════════════════════"
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✅ 健康检查通过${NC}"
else
    echo -e "${RED}❌ 发现 $ERRORS 个问题${NC}"
fi
echo "═══════════════════════════════════════════════════════════"

exit $ERRORS
