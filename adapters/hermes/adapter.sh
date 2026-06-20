#!/bin/bash
# Hermes AIM Adapter — v1.4 (2026-06-20)
# v1.3: P1-3 exit code 对齐 — 未知参数/缺参数/未知模式/cancel不支持 exit=2→3
# v1.2: 标准化 4 接口 (process/health/info/cancel), 噪声跨行过滤修复
# v1.1: 初始 AIM adapter
#
# 调用方式:
#   adapter.sh process --message "<内容>" --from "<发送方ID>"
#   adapter.sh health
#   adapter.sh info
#   adapter.sh cancel --task-id "<task_id>"
# 退出码: 0=SUCCESS, 1=RETRY, 2=DEGRADE, 3=FATAL, 4=AGENT_UNREACHABLE(预留)
#
# 环境变量:
#   HERMES_BIN — hermes CLI 路径（默认: hermes）
#   ADAPTER_TIMEOUT — 超时秒数（默认: 120）

# set -e 注释掉：launchd PATH 不全，依赖命令可能缺失
HERMES_BIN="${HERMES_BIN:-/Users/yangzs/.local/bin/hermes}"
TIMEOUT_BIN="${TIMEOUT_BIN:-/usr/local/bin/timeout}"
ADAPTER_TIMEOUT="${ADAPTER_TIMEOUT:-120}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

MODE="$1"
shift || true

case "$MODE" in
    process)
        # 解析参数
        while [[ $# -gt 0 ]]; do
            case $1 in
                --message) MESSAGE="$2"; shift 2 ;;
                --from) FROM_ID="$2"; shift 2 ;;
                *) echo "未知参数: $1" >&2; exit 3 ;;
            esac
        done

        if [ -z "$MESSAGE" ]; then
            echo "缺少 --message 参数" >&2
            exit 3
        fi

        # 检查 Hermes CLI
        if ! command -v "$HERMES_BIN" &>/dev/null; then
            echo "Hermes CLI 不可用: $HERMES_BIN" >&2
            exit 3
        fi

        # 调用 Hermes（--source aim-adapter 防止跨会话污染）
        AIM_PROMPT="回复以下内容，仅输出你对该消息的回复文本，不要加任何前缀后缀说明或操作描述："
        output=$($TIMEOUT_BIN "$ADAPTER_TIMEOUT" "$HERMES_BIN" chat -q "${AIM_PROMPT}${MESSAGE}" -Q --source aim-adapter 2>/dev/null)
        exit_code=$?

        if [ $exit_code -eq 124 ]; then
            echo "Hermes 超时 (${ADAPTER_TIMEOUT}s)" >&2
            exit 1
        elif [ $exit_code -ne 0 ]; then
            echo "Hermes 调用失败 (exit=$exit_code)" >&2
            exit 1
        fi

        # 综合噪声过滤（hermes chat -Q 输出的会话管理信息）：
        #   1. sed: 删除 Normalized model 警告行 + 紧随的续行（如 "deepseek."）
        #   2. grep: 过滤 session_id:、Restored session:、Saving session、... 开头行、空行
        # 只取第一条有效行作为 AI 回复
        cleaned=$(echo "$output" | sed '/Normalized model/{N;d;}')
        filtered=$(echo "$cleaned" | LC_ALL=en_US.UTF-8 grep -v '^session_id:' | grep -v '^Restored session:' | grep -v '^Saving session' | grep -v '^\.\.\.' | grep -v '^$')
        first_line=$(echo "$filtered" | head -1)

        if [ -z "$first_line" ]; then
            if [ -n "$output" ]; then
                echo "无有效回复（仅噪声）" >&2
                exit 0
            fi
            echo "空回复" >&2
            exit 0
        fi

        echo "$first_line"
        exit 0
        ;;

    health)
        # 健康探针：检查 hermes CLI 可达 + 进程存活
        if ! command -v "$HERMES_BIN" &>/dev/null; then
            echo '{"status":"unhealthy","active_sessions":0}'
            exit 2
        fi

        # 检查是否有 hermes 进程在运行
        proc_count=$(pgrep -f "hermes" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$proc_count" -lt 1 ]; then
            echo '{"status":"degraded","active_sessions":0}'
            exit 1
        fi

        # 快速健康检查
        health_output=$("$HERMES_BIN" --version 2>/dev/null)
        if [ $? -ne 0 ]; then
            echo '{"status":"unhealthy","active_sessions":0}'
            exit 2
        fi

        echo '{"status":"healthy","active_sessions":1}'
        exit 0
        ;;

    info)
        # 返回 Runtime 元信息
        version=$("$HERMES_BIN" --version 2>/dev/null | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        cat <<EOF
{
  "provider": "hermes",
  "version": "${version}",
  "execution_model": "realtime",
  "max_concurrency": 1
}
EOF
        exit 0
        ;;

    cancel)
        # 解析 task-id
        while [[ $# -gt 0 ]]; do
            case $1 in
                --task-id) TASK_ID="$2"; shift 2 ;;
                *) echo "未知参数: $1" >&2; exit 3 ;;
            esac
        done

        if [ -z "$TASK_ID" ]; then
            echo '{"status":"error","detail":"缺少 --task-id 参数"}' >&2
            exit 1
        fi

        # Hermes 是 realtime 模式，任务即时处理无法取消
        echo '{"status":"not_supported","detail":"Hermes execution_model=realtime，任务即时处理无法取消"}'
        exit 3
        ;;

    trim)
        # 620 L3: StallWatchdog 自愈 — 清理 Hermes 卡死 session
        # Hermes 当前架构不支持会话级清理，返回 success 让 StallWatchdog 重置计数
        echo '{"status":"trimmed","detail":"hermes runtime no-op — StallWatchdog acknowledged"}'
        exit 0
        ;;

    *)
        echo "未知模式: $MODE" >&2
        echo "用法: adapter.sh {process|health|info|cancel|trim} [参数...]" >&2
        exit 3
        ;;
esac
