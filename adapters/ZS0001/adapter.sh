#!/bin/bash
# adapter-version: v2.2  (项目 1.4.0 | OpenClaw adapter | ZS0001 呱呱)
# 构建: 2026-06-23 +context-card + --session-key 隔离
# process: 直接调用 OpenClaw CLI，调用方阻塞等回复（≤30s）
# 退出码: 0=正常, 1=可重试, 2=挂了, 3=需人工介入

: ${AIM_HOME:="$HOME/.aim"}
: ${AIM_AGENT_ID:="ZS0001"}
: ${AIM_SHARED:="$HOME/shared/aim"}
: ${AIM_WORKSPACE:="$HOME/.openclaw/workspace"}
OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.npm-global/bin/openclaw}"

MODE="${1:-process}"
shift 2>/dev/null || true

# ── health ──
if [ "$MODE" = "health" ]; then
    PID=$(ps aux | grep -v grep | grep "openclaw.*gateway" | awk '{print $2}' | head -1)
    if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
        echo '{"status":"unhealthy"}' >&2; exit 2
    fi
    echo '{"status":"healthy","active_sessions":1}'; exit 0
fi

# ── info ──
if [ "$MODE" = "info" ]; then
    printf '{"provider":"openclaw","execution_model":"realtime","version":"v2.0","project":"%s"}\n' "$(cat ~/shared/aim/VERSION 2>/dev/null || echo unknown)"; exit 0
fi

# ── cancel ──
if [ "$MODE" = "cancel" ]; then
    printf '{"status":"cancelled"}\n'; exit 0
fi

# ── trim ── (620 L3: StallWatchdog 自愈，清理卡死 session)
if [ "$MODE" = "trim" ]; then
    printf '{"status":"trimmed","detail":"openclaw runtime no-op — StallWatchdog acknowledged"}\n'; exit 0
fi

# ── process ──
while [[ $# -gt 0 ]]; do
    case $1 in
        process) shift ;;
        --message) MESSAGE="$2"; shift 2 ;;
        --from) FROM_ID="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[ -z "$MESSAGE" ] && { echo "缺少 --message" >&2; exit 2; }
FROM_ID="${FROM_ID:-unknown}"

# ── 群操作快速通道（v1.0 2026-07-15）──
# 识别群操作关键词，不走 OpenClaw，直接调 NATS API
GRP_OPS="$AIM_SHARED/scripts/aim_group_ops.sh"
if [ -x "$GRP_OPS" ]; then
    # 建群 / 拉群（含指定名称，解析交给 Python 处理 UTF-8）
    if echo "$MESSAGE" | grep -qiE '(建.*群|创建.*群|拉.*群|新.*群|帮.*建群|帮.*拉群)'; then
        GRP_NAME=$(echo "$MESSAGE" | python3 -c "
import sys, re
text = sys.stdin.read().strip()
m = re.search(r'[叫|名称|名字|命名][:：\s]*[\"\x27]?([^\"\x27，,，。.\s]{1,30})', text)
if m:
    g = m.group(1).strip()
    print(g if len(g) < 50 else 'NO_MATCH')
else:
    print('NO_MATCH')
" 2>/dev/null)
        [ "$GRP_NAME" = "NO_MATCH" ] && GRP_NAME=""
        [ -z "$GRP_NAME" ] && GRP_NAME="群聊($(date '+%Y-%m-%d %H:%M'))"
        env OWNER=$FROM_ID "$GRP_OPS" create "$GRP_NAME"
        exit 0
    fi
    # 加群 / 入群
    if echo "$MESSAGE" | grep -qiE '(加.*群|入群|我要.*加.*群|让我.*进.*群|申请.*入群)'; then
        GRP_ID=$(echo "$MESSAGE" | grep -oE 'grp_[a-f0-9-]+' | head -1)
        if [ -n "$GRP_ID" ]; then
            "$GRP_OPS" join "$GRP_ID" "$FROM_ID"
        else
            echo "🐸 请提供群 ID（格式 grp_<uuid>），例如：加入群 grp_xxx"
        fi
        exit 0
    fi
    # 退群
    if echo "$MESSAGE" | grep -qiE '(退.*群|离开.*群|我要.*退)'; then
        GRP_ID=$(echo "$MESSAGE" | grep -oE 'grp_[a-f0-9-]+' | head -1)
        if [ -n "$GRP_ID" ]; then
            "$GRP_OPS" leave "$GRP_ID" "$FROM_ID"
        else
            echo "🐸 请提供群 ID（格式 grp_<uuid>），例如：退出群 grp_xxx"
        fi
        exit 0
    fi
    # 群公告（必须在群列表之前，避免"查看群公告"被群列表匹配）
    if echo "$MESSAGE" | grep -qiE '(群公告|群规则|发布公告|设置公告|查看公告|看公告)'; then
        if echo "$MESSAGE" | grep -qiE '(发布|设置|写|改|更新)'; then
            GRP_ID=$(echo "$MESSAGE" | grep -oE 'grp_[a-f0-9-]+' | head -1)
            [ -z "$GRP_ID" ] && GRP_ID="grp_trio"
            CONTENT=$(echo "$MESSAGE" | python3 -c "
import sys, re
text = sys.stdin.read().strip()
m = re.search(r'(?:公告|规则|内容)[:：是]\\s*[\"\\x27]?([^\"\\x27]{1,500})', text)
if m:
    print(m.group(1).strip())
else:
    print('NO_MATCH')
" 2>/dev/null)
            [ "$CONTENT" = "NO_MATCH" ] && CONTENT=""
            if [ -n "$CONTENT" ]; then
                env OPERATOR=$FROM_ID "$GRP_OPS" announce set "$GRP_ID" "$CONTENT"
            else
                echo "🐸 请提供公告内容，例如：发布群公告：本群仅限协作沟通，请勿闲聊"
            fi
        else
            GRP_ID=$(echo "$MESSAGE" | grep -oE 'grp_[a-f0-9-]+' | head -1)
            [ -z "$GRP_ID" ] && GRP_ID="grp_trio"
            "$GRP_OPS" announce get "$GRP_ID"
        fi
        exit 0
    fi
    # 群列表
    if echo "$MESSAGE" | grep -qiE '(所有群|群列表|列出.*群|有哪些群|群.*有哪些|查看.*群)'; then
        "$GRP_OPS" list
        exit 0
    fi
    # 我的群
    if echo "$MESSAGE" | grep -qiE '(我的群|我在.*群|我.*在.*哪些群)'; then
        "$GRP_OPS" my "$FROM_ID"
        exit 0
    fi
    # 查成员
    if echo "$MESSAGE" | grep -qiE '(群成员|成员列表|谁.*在.*群|群里有谁)'; then
        GRP_ID=$(echo "$MESSAGE" | grep -oE 'grp_[a-f0-9-]+' | head -1)
        if [ -n "$GRP_ID" ]; then
            "$GRP_OPS" members "$GRP_ID"
        else
            echo "🐸 请提供群 ID（格式 grp_<uuid>）"
        fi
        exit 0
    fi
fi

# 注入性格 + 项目上下文（L1 骨架 + L2 即时）
PERSONALITY=""
if [ -f "$AIM_WORKSPACE/SOUL.md" ]; then
    PERSONALITY="$(sed -n '/^### 性格/,/^## /p' "$AIM_WORKSPACE/SOUL.md" | head -15)"
fi
CONTEXT=""
if [ -f "$AIM_SHARED/PROJECT/context-card.md" ]; then
    CONTEXT="$(head -30 "$AIM_SHARED/PROJECT/context-card.md")"
fi
if [ -f "$AIM_SHARED/PROJECT/context-live.md" ]; then
    CONTEXT="${CONTEXT}
$(head -10 "$AIM_SHARED/PROJECT/context-live.md")"
fi

# 构建 prompt
BASE="你是呱呱🐸，来自 ${FROM_ID} 的消息"
if [ -n "$PERSONALITY" ]; then
    BASE="${BASE}。你的性格：${PERSONALITY}"
fi
if [ -n "$CONTEXT" ]; then
    PROMPT="${BASE}。项目上下文：${CONTEXT}。直接回复(20-80字)以🐸开头：${MESSAGE}"
else
    PROMPT="${BASE}。直接回复(20-80字)以🐸开头：${MESSAGE}"
fi

# 独立 session key 隔离，不阻塞主会话（等同 hermes chat -q 新进程）
SESSION_KEY="agent:main:aim-reply-$(date +%s)-$$"

# 错误日志路径
ERR_LOG="${AIM_HOME}/aim-adapter-errors.log"

# 调用 OpenClaw，stderr 重定向到日志文件方便排障
REPLY=$("$OPENCLAW_BIN" agent \
    --session-key "$SESSION_KEY" \
    --message "${PROMPT}" \
    --json --timeout 25 2>>"$ERR_LOG" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    t=d.get('result',{}).get('payloads',[{}])[0].get('text','')
    print(t)
except Exception as e:
    print(f'JSON_PARSE_ERROR: {type(e).__name__}: {e}', file=sys.stderr)
except KeyboardInterrupt:
    pass
" 2>/dev/null)

# 检测 OpenClaw 返回的错误消息（LLM 超时等），让调度器退避重试
if echo "$REPLY" | grep -qE '^(LLM request failed\.|Request timed out|LLM request timed out)' 2>/dev/null; then
    echo "$REPLY" >&2; exit 1  # 退避重试
fi

if [ -n "$REPLY" ]; then
    echo "$REPLY"; exit 0
else
    # 输出最近错误供 main.py 日志使用
    LAST_ERR=$(tail -3 "$ERR_LOG" 2>/dev/null | tr '\n' ' | ')
    echo "OpenClaw 无回复 (last_err=${LAST_ERR})" >&2; exit 1
fi
