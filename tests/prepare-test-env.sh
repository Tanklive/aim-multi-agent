#!/bin/bash
# AIM P3-1 测试环境准备脚本
# ===========================
# 检查所有前置条件是否满足，输出环境就绪报告
# 用法: ./prepare-test-env.sh
#
# 返回值:
#   0 = 全部就绪
#   1 = 有警告（可继续，但建议修复）
#   2 = 有错误（必须修复后才能测试）

set -e

# ── 颜色 ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

PASS="${GREEN}✅${NC}"
WARN="${YELLOW}⚠️${NC}"
FAIL="${RED}❌${NC}"
INFO="${CYAN}ℹ️${NC}"

# ── 路径 ──
AIM_HOME="$HOME/.hermes/aim"
AIM_DATA="$AIM_HOME/data"
AIM_TESTS="$AIM_HOME/tests"
SHARED_TESTS="$HOME/shared/aim/tests"
# AIM Server 是内嵌在 aim-agent.py 中的，不是独立目录
# 用 aim-agent.py 路径代替
AIM_AGENT_DIR="$HOME/.hermes/hermes-agent/apps/aim-agent"
AIM_CONFIG="$AIM_HOME/config.json"
REGISTRY="$AIM_HOME/registry.py"
AIM_AGENT_PY="$HOME/.hermes/hermes-agent/apps/aim-agent/aim-agent.py"

ERRORS=0
WARNINGS=0

echo ""
echo "╔════════════════════════════════════════╗"
echo "║   AIM P3-1 测试环境准备检查            ║"
echo "╚════════════════════════════════════════╝"
echo ""

# ── 1. 目录结构检查 ──
echo "━━━ 1. 目录结构 ━━━"
for d in "$AIM_HOME" "$AIM_DATA" "$AIM_TESTS" "$SHARED_TESTS" "$AIM_AGENT_DIR"; do
  if [ -d "$d" ]; then
    echo -e "  $PASS $d"
  else
    echo -e "  $FAIL $d (缺失)"
    ERRORS=$((ERRORS + 1))
  fi
done

# ── 2. 配置文件 ──
echo ""
echo "━━━ 2. 配置文件 ━━━"
if [ -f "$AIM_CONFIG" ]; then
  echo -e "  $PASS config.json"
  # 检查必要字段
  AGENTS=$(python3 -c "import json; c=json.load(open('$AIM_CONFIG')); print(len(c.get('agents',{})))" 2>/dev/null || echo "0")
  if [ "$AGENTS" -ge 3 ]; then
    echo -e "  $PASS Agent 配置: $AGENTS 个"
  else
    echo -e "  $WARN Agent 配置: $AGENTS 个 (至少应有 ZS0001/ZS0002/ZS0003)"
    WARNINGS=$((WARNINGS + 1))
  fi
else
  echo -e "  $FAIL config.json 缺失"
  ERRORS=$((ERRORS + 1))
fi

if [ -f "$REGISTRY" ]; then
  echo -e "  $PASS registry.py"
else
  echo -e "  $WARN registry.py 缺失 (T10 需要)"
  WARNINGS=$((WARNINGS + 1))
fi

# ── 3. 环境依赖 ──
echo ""
echo "━━━ 3. 环境依赖 ━━━"
# Python 版本
PY_VER=$(python3 --version 2>/dev/null || echo "missing")
if echo "$PY_VER" | grep -q "Python 3"; then
  echo -e "  $PASS Python: $PY_VER"
else
  echo -e "  $FAIL Python 3 未安装"
  ERRORS=$((ERRORS + 1))
fi

# websockets 库
if python3 -c "import websockets" 2>/dev/null; then
  WS_VER=$(python3 -c "import websockets; print(websockets.__version__)" 2>/dev/null || echo "?")
  echo -e "  $PASS websockets==$WS_VER"
else
  echo -e "  $FAIL websockets 库未安装 (pip install websockets)"
  ERRORS=$((ERRORS + 1))
fi

# sudo 权限 (pf-isolate.sh 需要)
if sudo -n true 2>/dev/null; then
  echo -e "  $PASS sudo 权限（无需密码）"
else
  echo -e "  $WARN sudo 需要密码（pf-isolate.sh 运行时会提示输入密码）"
  WARNINGS=$((WARNINGS + 1))
fi

# ── 4. 进程状态 ──
echo ""
echo "━━━ 4. 进程状态 ━━━"

# AIM Server 进程（用 lsof 确认实际监听进程，避免 PID 文件过时）
echo -e "  $INFO 根据 :18900 端口查找实际 Server 进程..."

# 端口监听
if command -v lsof &>/dev/null; then
  PORT_18900=$(lsof -i :18900 -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -3)
  if [ -n "$PORT_18900" ]; then
    SERVER_INFO=$(lsof -i :18900 -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)
    SERVER_PID=$(echo "$SERVER_INFO" | awk '{print $2}')
    SERVER_CMD=$(echo "$SERVER_INFO" | awk '{print $1}')
    echo -e "  $PASS AIM Server (PID $SERVER_PID, $SERVER_CMD) — 端口 :18900 (WS) 已监听"
  else
    echo -e "  $FAIL 端口 :18900 (WS) 未监听 — AIM Server 未运行"
    ERRORS=$((ERRORS + 1))
  fi
  PORT_18901=$(lsof -i :18901 -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)
  if [ -n "$PORT_18901" ]; then
    echo -e "  $INFO 端口 :18901 (WSS) 已监听"
  fi
else
  echo -e "  $WARN lsof 不可用，跳过端口检查"
  WARNINGS=$((WARNINGS + 1))
fi

# 查找实际运行的 aim-agent.py 进程（精确匹配，排除 hermes chat 进程）
AGENT_PIDS=$(pgrep -f "aim-agent" 2>/dev/null | grep -v "$(pgrep -f 'hermes chat' 2>/dev/null | tr '\n' '|' | sed 's/|$//')" | head -5)
AGENT_COUNT=$(echo "$AGENT_PIDS" | wc -w | tr -d ' ')
echo -e "  $INFO 发现 aim-agent 进程: $AGENT_COUNT"
if [ "$AGENT_COUNT" -gt 0 ]; then
  for pid in $AGENT_PIDS; do
    COMM=$(ps -p "$pid" -o comm= 2>/dev/null || echo "?")
    echo -e "        PID $pid — $COMM"
  done
else
  echo -e "  $WARN 未发现 aim-agent 进程"
  WARNINGS=$((WARNINGS + 1))
fi

# ── 5. Agent 数据 ──
echo ""
echo "━━━ 5. 测试工具 ━━━"

TOOLS_OK=0
TOOLS_MISSING=0

for tool in "ws_test_client.py" "pf-isolate.sh" "cleanup-test-data.sh"; do
  if [ -f "$AIM_TESTS/$tool" ]; then
    echo -e "  $PASS $AIM_TESTS/$tool"
    TOOLS_OK=$((TOOLS_OK + 1))
  elif [ -f "$SHARED_TESTS/$tool" ]; then
    echo -e "  $PASS $SHARED_TESTS/$tool (shared 副本)"
    TOOLS_OK=$((TOOLS_OK + 1))
  else
    echo -e "  $FAIL $tool 缺失"
    TOOLS_MISSING=$((TOOLS_MISSING + 1))
    ERRORS=$((ERRORS + 1))
  fi
done

# 测试计划
for plan in "P3-1-test-plan.md" "P3-1-ready-checklist.md"; do
  if [ -f "$SHARED_TESTS/$plan" ]; then
    echo -e "  $PASS $SHARED_TESTS/$plan"
  else
    echo -e "  $WARN $plan 缺失"
    WARNINGS=$((WARNINGS + 1))
  fi
done

# ── 6. ws_test_client.py 快速验证 ──
echo ""
echo "━━━ 6. 客户端认证快速验证 ━━━"
if [ -f "$AIM_TESTS/ws_test_client.py" ] && [ -n "${AIM_AGENT_SECRET:-}" ]; then
  echo -e "  $INFO AIM_AGENT_SECRET 已设置，尝试快速连接..."
  python3 -c "
import asyncio, json, os, sys
sys.path.insert(0, '$AIM_TESTS')
from ws_test_client import TestWsClient
async def test():
    c = TestWsClient(
        agent_id=os.environ.get('AIM_AGENT_ID', 'ZS0003'),
        channel=os.environ.get('AIM_CHANNEL', 'script'),
        secret=os.environ.get('AIM_AGENT_SECRET', ''),
        server_url=os.environ.get('AIM_SERVER_URL', 'ws://localhost:18900')
    )
    ok = await c.connect()
    if ok:
        print('连接认证成功')
        await c.close()
    else:
        print('连接认证失败')
        sys.exit(1)
asyncio.run(test())
" 2>&1 && echo -e "  $PASS 快速连接测试通过" || echo -e "  $WARN 快速连接测试失败（可跳过，运行时再认证）"
elif [ -z "${AIM_AGENT_SECRET:-}" ]; then
  echo -e "  $WARN AIM_AGENT_SECRET 未设置（跳过认证测试，需在 .env 中配置）"
  WARNINGS=$((WARNINGS + 1))
else
  echo -e "  $WARN ws_test_client.py 不可用（跳过认证测试）"
  WARNINGS=$((WARNINGS + 1))
fi

# ── 7. 数据目录清理建议 ──
echo ""
echo "━━━ 7. 数据目录状态 ━━━"
if [ -d "$AIM_DATA" ]; then
  MSG_COUNT=$(wc -l < "$AIM_DATA/messages.jsonl" 2>/dev/null || echo "0")
  OFFLINE_COUNT=$(ls "$AIM_DATA"/offline_*.jsonl "$AIM_DATA"/offline_*.json 2>/dev/null | wc -l || echo "0")
  echo -e "  $INFO messages.jsonl: $MSG_COUNT 行"
  echo -e "  $INFO 离线队列文件: $OFFLINE_COUNT 个"
  if [ "$OFFLINE_COUNT" -gt 4 ]; then
    echo -e "  $WARN 离线队列文件较多，建议运行 cleanup-test-data.sh"
    WARNINGS=$((WARNINGS + 1))
  fi
fi

# ── 汇总报告 ──
echo ""
echo "╔════════════════════════════════════════╗"
echo "║   检查完成                             ║"
echo "╚════════════════════════════════════════╝"
echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo -e "${RED}❌ $ERRORS 个错误 — 必须修复后才能测试${NC}"
  exit 2
elif [ "$WARNINGS" -gt 0 ]; then
  echo -e "${YELLOW}⚠️  $WARNINGS 个警告 — 可继续测试，建议修复${NC}"
  exit 1
else
  echo -e "${GREEN}✅ 全部就绪 — 可以开始 P3-1 测试！${NC}"
  exit 0
fi
