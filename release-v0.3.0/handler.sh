#!/bin/bash
# handler.sh — AIM 消息回调处理器
#
# 协议：
#   输入（argv）: $1=发送方 $2=消息内容（经 sanitize）
#   输出（stdout）: AI 回复内容（空 = 无回复）
#   退出码: 0=成功（含空回复），非0=失败
#
# 安全约束（呱呱评审 2026-06-08）：
#   1. 所有输入在 framework_cli.py 侧已做 shlex.quote 防注入
#   2. handler.sh 本身不做 eval/exec/反引号等危险操作
#   3. stdout 被 framework_cli.py 的 subprocess 捕获，不会执行
#   4. 超时由 framework_cli.py 的 wait_for(timeout) 保证
#
# 使用场景：
#   新 Agent（如小火鸡儿）通过 aim-agent.py 收到消息后，
#   以子进程方式调用此脚本。脚本的 stdout 被作为 AI 回复
#   返回给发送方。
#
# 安装：
#   放在 ~/.aim/agent-{AGENT_ID}/handler.sh
#   chmod +x handler.sh

set -euo pipefail

SENDER="${1:-unknown}"
MESSAGE="${2:-}"

# 日志目录
LOG_DIR="$(dirname "$0")/logs"
mkdir -p "$LOG_DIR"

echo "[$(date '+%H:%M:%S')] 收到来自 $SENDER 的消息: ${MESSAGE:0:100}..." >> "$LOG_DIR/handler.log"

if [ -z "$MESSAGE" ]; then
    echo "（空消息，跳过）"
    exit 0
fi

# =============================================
# 在这里写你的消息处理逻辑
# 例如：调用 AI（curl API）、查询本地数据等
# =============================================

# 示例 1：简单自动回复（新手入门示例）
# echo "🐤 收到来自 $SENDER 的消息，已记录。"

# 示例 2：调用本地 Ollama API（取消注释使用）
# curl -s -X POST http://localhost:11434/api/generate \
#   -d "{\"model\": \"llama3\", \"prompt\": \"$MESSAGE\", \"stream\": false}" | \
#   python3 -c "import sys,json; print(json.load(sys.stdin)['response'])"

# 示例 3：调用 OpenAI 兼容 API（OpenClaw / 任何 OpenAI 兼容服务）
# curl -s https://api.openai.com/v1/chat/completions \
#   -H "Authorization: Bearer $OPENAI_API_KEY" \
#   -H "Content-Type: application/json" \
#   -d "{\"model\": \"gpt-4o-mini\", \"messages\": [{\"role\": \"user\", \"content\": \"$MESSAGE\"}]}" | \
#   python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"

# 默认行为：记录消息并返回确认（新 Agent 上手就能看到效果）
echo "✅ 消息已接收 [$SENDER]: ${MESSAGE:0:50}..."
echo "   (请在 handler.sh 中替换为实际的 AI 处理逻辑)"

exit 0
