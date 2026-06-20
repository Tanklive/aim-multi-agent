#!/bin/bash
# AIM 日志轮转 — 项4
# 按天轮转 agent.out.log / agent.err.log / main.log
# 保留 7 天

set -e
LOG_DIR="${1:-$HOME/.aim/agents/ZS0001/logs}"
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d)

cd "$LOG_DIR" || exit 1

for base in agent.out agent.err main registry.out registry.err services.out; do
    logfile="${base}.log"
    if [ -f "$logfile" ] && [ -s "$logfile" ]; then
        # 轮转：当前文件 → 带日期后缀
        rotated="${base}-${TIMESTAMP}.log"
        cp "$logfile" "$rotated"
        : > "$logfile"  # 清空当前文件（进程用 >> 追加不受影响）
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] rotated $logfile → $rotated ($(wc -c < "$rotated") bytes)"
    fi
done

# 清理超过 7 天的旧日志
find "$LOG_DIR" -name "*.log-*" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true
find "$LOG_DIR" -name "agent.out-*" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true
echo "[$(date '+%Y-%m-%d %H:%M:%S')] cleanup done, retention=${RETENTION_DAYS}d"
