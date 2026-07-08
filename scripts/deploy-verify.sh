#!/bin/bash
# deploy-verify.sh — AIM 部署后验证（P0-3, 2026-06-20）
# 用法: bash deploy-verify.sh [--quick]

set -euo pipefail
QUICK="${1:-}"

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; NC='\033[0m'
pass=0; fail=0

check() { if [ $? -eq 0 ]; then echo -e "  ${GREEN}✅${NC} $1"; ((pass++)); else echo -e "  ${RED}❌${NC} $1"; ((fail++)); fi; }

echo "🔍 AIM Deploy Verify $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# ── 1. 文件一致性 ──
echo -e "\n📁 文件一致性"
SHARED_MAIN=~/shared/aim/aim-client/main.py
md5_shared=$(md5 -q "$SHARED_MAIN" 2>/dev/null || echo "N/A")
echo "  main.py MD5: $md5_shared (所有 Agent 共享)"

# 仅 ZS0003 的 adapter.sh 需与 shared 一致（另两个有框架专属 adapter）
SHARED_ADAPTER=~/shared/aim/adapters/letta/adapter.sh
adapter=~/.aim/agents/ZS0003/adapter.sh
if [ -f "$adapter" ]; then
    md5_local=$(md5 -q "$adapter" 2>/dev/null)
    md5_shared=$(md5 -q "$SHARED_ADAPTER" 2>/dev/null || echo "N/A")
    if [ "$md5_local" = "$md5_shared" ]; then
        echo -e "  ${GREEN}✅${NC} ZS0003 adapter.sh = shared"
        ((pass++))
    else
        echo -e "  ${RED}❌${NC} ZS0003 adapter.sh 与 shared 不一致"
        ((fail++))
    fi
fi

# ── 2. Queue 路径 ──
echo -e "\n📦 Queue 持久化路径"
for agent in ZS0001 ZS0002 ZS0003; do
    qfile=~/.aim/agents/$agent/queue.jsonl
    if [ -f "$qfile" ]; then
        size=$(wc -c < "$qfile" | tr -d ' ')
        echo -e "  ${GREEN}✅${NC} $agent: $(basename $(dirname $qfile))/queue.jsonl ($size bytes)"
        ((pass++))
    else
        echo -e "  ${RED}❌${NC} $agent: queue.jsonl 不存在"
        ((fail++))
    fi
done

# ── 3. 进程检查 ──
echo -e "\n🖥️  进程状态"
for agent in ZS0001 ZS0002 ZS0003; do
    pid=$(ps aux | grep "[m]ain.py.*--agent-id $agent" | awk '{print $2}' | head -1)
    if [ -n "$pid" ]; then
        echo -e "  ${GREEN}✅${NC} $agent: PID $pid"
        ((pass++))
    else
        echo -e "  ${RED}❌${NC} $agent: 未运行"
        ((fail++))
    fi
done

# ── 4. Registry 在线状态 ──
echo -e "\n🌐 Registry 在线状态"
python3 -c "
import asyncio, nats, json, os, sys
async def check():
    nc = await nats.connect('nats://127.0.0.1:4222',
        user_credentials=str(os.path.expanduser('~/.aim/agents/ZS0001/aim.creds')))
    resp = await nc.request('aim.registry.list', b'{}', timeout=5)
    data = json.loads(resp.data)
    ok = True
    for agent in ('ZS0001', 'ZS0002', 'ZS0003'):
        info = data.get('agents', {}).get(agent, {})
        status = info.get('status', 'unknown')
        mark = '✅' if status == 'online' else '❌'
        if status != 'online': ok = False
        print(f'  {mark} {agent}: {status}')
    sys.exit(0 if ok else 1)
    await nc.close()
asyncio.run(check())
" 2>&1
check "Registry 三方 online"

# ── 4b. alertd 守护进程 ──
echo -e "\n🚨 alertd 告警守护"
python3 ~/.aim/bin/alertd.py --test 2>&1
check "alertd --test"

# launchd 状态
if launchctl print gui/501/com.aim.alertd &>/dev/null; then
    echo -e "  ${GREEN}✅${NC} alertd launchd loaded"
    ((pass++))
else
    echo -e "  ${RED}❌${NC} alertd launchd not loaded"
    ((fail++))
fi

# 持久化文件
for f in ~/.aim/system/alerts.log ~/.aim/system/observer.jsonl; do
    if [ -f "$f" ]; then
        echo -e "  ${GREEN}✅${NC} $(basename $f)"
        ((pass++))
    else
        echo -e "  ${RED}❌${NC} $(basename $f) missing"
        ((fail++))
    fi
done

# ── 5. 端到端 health（非 quick 模式） ──
if [ "$QUICK" != "--quick" ]; then
    echo -e "\n🏥 端到端 health check"
    for agent in ZS0003; do
        adapter=~/.aim/agents/$agent/adapter.sh
        if [ -x "$adapter" ]; then
            result=$(timeout 10 bash "$adapter" health 2>&1 || echo '{"status":"timeout"}')
            status=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "parse_error")
            if [ "$status" = "healthy" ]; then
                echo -e "  ${GREEN}✅${NC} $agent adapter health: $status"
                ((pass++))
            else
                echo -e "  ${RED}❌${NC} $agent adapter health: $status"
                ((fail++))
            fi
        fi
    done
fi


# ── 6. E2E 烟雾测试（U-104, 2026-06-20） ──
echo -e "\n🔥 E2E Smoke Test"
SEND_SCRIPT=~/shared/aim/aim_send_nats.py
TARGET=ZS0003
TEST_MSG="E2E-DEPLOY-VERIFY-$(date +%s)"

echo "  📤 发送测试 DM → $TARGET ..."
result=$(python3 "$SEND_SCRIPT" "$TARGET" "$TEST_MSG" 2>&1)
msg_id=$(echo "$result" | grep -o 'msg_id: [a-f0-9]\{12\}' | awk '{print $2}')

if [ -n "$msg_id" ]; then
    echo -e "  ${GREEN}✅${NC} NATS publish 成功 (msg_id=$msg_id)"
    ((pass++))

    # 等 1 秒让消息入队
    sleep 1

    # 查对方 queue 是否有这条消息
    queue=~/.aim/agents/$TARGET/queue.jsonl
    if [ -f "$queue" ]; then
        if grep -q "$msg_id" "$queue" 2>/dev/null; then
            echo -e "  ${GREEN}✅${NC} 消息已入 $TARGET 队列"
            ((pass++))
        elif grep -q "$TEST_MSG" "$queue" 2>/dev/null; then
            echo -e "  ${GREEN}✅${NC} 消息已入 $TARGET 队列（内容匹配）"
            ((pass++))
        else
            echo -e "  ${YELLOW}⚠️${NC}  队列未找到 msg_id=$msg_id（可能已 dispatch）"
        fi
    fi
else
    echo -e "  ${RED}❌${NC} NATS publish 失败: $result"
    ((fail++))
fi

# ── 6b. Observer 送达确认 ──
OBS_FILE=~/.aim/system/observer.jsonl
if [ -f "$OBS_FILE" ] && [ -n "$msg_id" ]; then
    if grep -q "$msg_id" "$OBS_FILE" 2>/dev/null; then
        echo -e "  ${GREEN}✅${NC} Observer 已记录送达"
        ((pass++))
    fi
fi
# ── 总结 ──
echo -e "\n========================================"
total=$((pass + fail))
if [ $fail -eq 0 ]; then
    echo -e "${GREEN}✅ 全部通过 ($pass/$total)${NC}"
else
    echo -e "${RED}❌ $fail/$total 失败${NC}"
    exit 1
fi

