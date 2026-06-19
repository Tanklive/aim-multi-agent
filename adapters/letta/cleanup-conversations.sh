#!/bin/bash
set -euo pipefail
# cleanup-conversations.sh — Letta dispatch conv 磁盘维护
# VERSION: 1.0
#
# 背景:
#   Letta -p 裸跑每次创建新 conversation 目录 (~80KB/个)
#   历史累积 400+ 目录 / 43MB，不清理会持续增长
#
# 策略:
#   - 清理: 所有 old conversation 目录 (base64 编码，早期 -p 裸跑/废弃 agent conv)
#     - conversation:local-conv-* (早期 adapter 裸跑产物)
#     - default:agent-local-<other-uuid> (其他废弃 agent 的 conv)
#   - 安全: 
#     - 不删除当前 agent (agent-local-f763730a) 的 conv 目录 (如果存在的话)
#     - 支持干运行模式 (--dry-run) 只统计不删除
#   - ZS0003 主 agent conv 不在磁盘上（活跃 session 中），所以全删安全
#
# 用法:
#   cleanup-conversations.sh              # 交互模式，统计并提示
#   cleanup-conversations.sh --execute    # 直接执行清理
#   cleanup-conversations.sh --dry-run    # 只统计不删除
#
# cron:
#   每天凌晨 4 点自动清理 (磁盘 > 100MB 时触发)
#   0 4 * * * /bin/bash ~/.aim/agents/ZS0003/cleanup-conversations.sh --execute >> ~/.aim/agents/ZS0003/logs/cleanup.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONV_BASE="${HOME}/.letta/lc-local-backend/conversations"
THRESHOLD_MB="${CLEANUP_THRESHOLD_MB:-100}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

MODE="${1:-}"

# ── 获取磁盘用量 (MB) ─────────────────
get_disk_usage_mb() {
    if [ -d "$CONV_BASE" ]; then
        du -sm "$CONV_BASE" 2>/dev/null | cut -f1 || echo 0
    else
        echo 0
    fi
}

# ── 统计 ──────────────────────────────
USAGE_MB=$(get_disk_usage_mb)
TOTAL_DIRS=$(ls "$CONV_BASE" 2>/dev/null | wc -l | tr -d ' ')

echo "=== Letta Conversation 磁盘维护 ==="
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  路径: $CONV_BASE"
echo "  目录数: $TOTAL_DIRS"
echo "  磁盘占用: ${USAGE_MB}MB"
echo "  阈值: ${THRESHOLD_MB}MB"
echo ""

if [ "$MODE" = "--dry-run" ]; then
    echo "[干运行] 不执行删除，仅统计。"
    echo ""

    # 按类型分组统计
    AGENT_CONVS=$(ls "$CONV_BASE" 2>/dev/null | grep -c '^default:agent-local-' || true)
    LOCAL_CONVS=$(ls "$CONV_BASE" 2>/dev/null | grep -c '^Y29udmVyc2F0aW9uOmxvY2FsLWNvbnYt' || true)
    OTHER_CONVS=$((TOTAL_DIRS - AGENT_CONVS - LOCAL_CONVS))

    echo "  agent convs (default:agent-local-*): $AGENT_CONVS"
    echo "  local convs (conversation:local-conv-*): $LOCAL_CONVS"
    echo "  其他 convs: $OTHER_CONVS"
    echo ""
    echo "[干运行结束] 未执行任何删除。"
    exit 0
fi

if [ "$MODE" != "--execute" ]; then
    echo "用法: cleanup-conversations.sh {--dry-run|--execute}"
    echo ""
    echo "  --dry-run  只统计不删除"
    echo "  --execute  执行清理 (需磁盘 > ${THRESHOLD_MB}MB)"
    echo ""
    echo "当前磁盘: ${USAGE_MB}MB / 阈值: ${THRESHOLD_MB}MB"
    exit 0
fi

# ── 执行清理 ──────────────────────────
if [ "$USAGE_MB" -lt "$THRESHOLD_MB" ]; then
    echo "[跳过] 磁盘 ${USAGE_MB}MB < 阈值 ${THRESHOLD_MB}MB，无需清理。"
    exit 0
fi

echo "[执行] 开始清理..."

DELETED=0
FAILED=0

# 遍历所有目录
for conv_dir in "$CONV_BASE"/*/; do
    [ -d "$conv_dir" ] || continue
    conv_name=$(basename "$conv_dir")

    # 跳过当前活跃的 dispatch conversation（通过检查是否有 letta 进程引用它）
    # 安全策略：
    #   保留当前 agent (agent-local-f763730a) 的 conv（目录名含 f763730a）
    #   删除所有其他旧 conv

    case "$conv_name" in
        *f763730a*)
            # 保留当前 agent 的 conv
            ;;
        *)
            rm -rf "$conv_dir" 2>/dev/null && DELETED=$((DELETED + 1)) || FAILED=$((FAILED + 1))
            ;;
    esac
done

NEW_USAGE=$(get_disk_usage_mb)
SAVED=$((USAGE_MB - NEW_USAGE))

echo "[完成] 删除: $DELETED 个, 失败: $FAILED 个"
echo "[结果] 磁盘: ${USAGE_MB}MB → ${NEW_USAGE}MB (节省 ${SAVED}MB)"
echo ""

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
