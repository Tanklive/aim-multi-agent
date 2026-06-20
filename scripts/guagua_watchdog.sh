#!/bin/bash
# 呱呱(OpenClaw)深度监控 v3 — 只追踪新事件，5秒采样
# v3: 修复旧日志重复告警、基于时间戳过滤、增加稳定性
set -euo pipefail

# ── 路径常量 ──
AIM_CLIENT_LOG="$HOME/.aim/logs/aim-client-ZS0001.log"
GATEWAY_LOG="/tmp/openclaw/openclaw-2026-06-19.log"
QUEUE_DIR="$HOME/.openclaw/workspace/.aim-queue"
REPLY_DIR="$HOME/.openclaw/workspace/.aim-replies"
TRIGGER_FILE="$HOME/.openclaw/workspace/.aim-trigger"
PROGRESS_FILE="$HOME/.openclaw/workspace/.guagua-progress.md"
SEND_TOOL="$HOME/shared/aim/aim_send_nats.py"
LOG_FILE="$HOME/.aim/logs/guagua_watchdog.log"
INTERVAL="${1:-5}"

# ── 安全工具 ──
safe_grep_count() {
    grep -c "$2" "$1" 2>/dev/null || echo 0
}
safe_wc() {
    ls -1 "$1"/*."$2" 2>/dev/null | wc -l | tr -d ' ' || echo 0
}
ts_now() { date +%s; }
ts_str() { date '+%H:%M:%S'; }

# ── 状态 ──
LAST_DOWN_ALERT=0;    LAST_QUEUE_ALERT=0
LAST_TRIGGER_MTIME=0; LAST_PROGRESS_MD5=""
PREV_TOOL_TOTAL=0;    PREV_MODEL_TOTAL=0
PREV_TIMEOUT_TOTAL=0; LAST_ALERT_TS=0
STATE=0; STATE_COUNTER=0; CYCLE_COUNT=0
GW_LOG_MARKER=0       # 上次读到 gateway log 的行位

alert() {
    local msg="$1"
    local now
    now=$(ts_now)
    # 防抖：同类型告警 30s 内不重复
    if [ $((now - LAST_ALERT_TS)) -le 30 ]; then
        echo "$(ts_str) [DEBOUNCED] $msg" >> "$LOG_FILE"
        return
    fi
    LAST_ALERT_TS=$now
    echo "$(ts_str) [ALERT] $msg" | tee -a "$LOG_FILE"
    python3 "$SEND_TOOL" grp_trio "🐤 [监控] $msg" --group 2>/dev/null || true
}

log_info()  { echo "$(ts_str) [INFO]  $1" >> "$LOG_FILE"; }
log_task()  { echo "$(ts_str) [TASK]  $1" >> "$LOG_FILE"; }
log_debug() { echo "$(ts_str) [DEBUG] $1" >> "$LOG_FILE"; }

# ── 获取自上次标记后 gateway 日志新增行 ──
read_gw_new_lines() {
    if [ ! -f "$GATEWAY_LOG" ]; then return 0; fi
    local total_lines
    total_lines=$(wc -l < "$GATEWAY_LOG" | tr -d ' ')
    if [ "$total_lines" -gt "$GW_LOG_MARKER" ]; then
        tail -n $((total_lines - GW_LOG_MARKER)) "$GATEWAY_LOG"
        GW_LOG_MARKER=$total_lines
    fi
}

# ── 启动 ──
> "$LOG_FILE"  # 清空旧日志
echo "========================================================" | tee -a "$LOG_FILE"
echo "$(date): 呱呱深度监控 v3 启动，采样 ${INTERVAL}s" | tee -a "$LOG_FILE"
echo "========================================================" | tee -a "$LOG_FILE"

# 初始化基线 — 只记录当前总量，后续用增量
GW_LOG_MARKER=$(wc -l < "$GATEWAY_LOG" | tr -d ' ')
PREV_TIMEOUT_TOTAL=$(safe_grep_count "$AIM_CLIENT_LOG" 'adapter stderr: OpenClaw 处理超时')
PREV_TOOL_TOTAL=$(safe_grep_count "$GATEWAY_LOG" 'tool_call\|tool-result\|bash run')
PREV_MODEL_TOTAL=$(safe_grep_count "$GATEWAY_LOG" 'embedded run agent')
LAST_TRIGGER_MTIME=$(stat -f %m "$TRIGGER_FILE" 2>/dev/null || echo 0)
LAST_PROGRESS_MD5=$(md5 -q "$PROGRESS_FILE" 2>/dev/null || echo "none")
log_info "初始化: GW行=$GW_LOG_MARKER 超时=$PREV_TIMEOUT_TOTAL 工具=$PREV_TOOL_TOTAL 模型=$PREV_MODEL_TOTAL"

alert "✅ 呱呱监控 v3 已启动 — 等待任务开始"

# ════════════════════ 主循环 ════════════════════
while true; do
    sleep "$INTERVAL"
    CYCLE_COUNT=$((CYCLE_COUNT + 1))

    # ── 0. 进程存活 ──
    TUI_PID=$(ps aux | awk '/openclaw-tui/ && !/grep/ {print $2; exit}' || echo "")
    GW_PID=$(ps aux | awk '/openclaw.*gateway/ && !/grep/ {print $2; exit}' || echo "")
    AIM_PID=$(ps aux | awk '/aim-client\/main.py.*ZS0001/ && !/grep/ {print $2; exit}' || echo "")
    CONSUMER_PID=$(ps aux | awk '/aim.*consumer.*openclaw/ && !/grep/ {print $2; exit}' || echo "")

    if [ -z "$TUI_PID" ]; then DOWN_TUI=1; else DOWN_TUI=0; fi
    if [ -z "$GW_PID" ];  then DOWN_GW=1;  else DOWN_GW=0; fi
    if [ -z "$AIM_PID" ]; then DOWN_AIM=1; else DOWN_AIM=0; fi
    ANY_DOWN=$((DOWN_TUI + DOWN_GW + DOWN_AIM))

    if [ "$ANY_DOWN" -gt 0 ] && [ $(( $(ts_now) - LAST_DOWN_ALERT )) -gt 30 ]; then
        DOWN_DETAIL=""
        [ "$DOWN_TUI" -eq 1 ] && DOWN_DETAIL="$DOWN_DETAIL TUI"
        [ "$DOWN_GW" -eq 1 ]  && DOWN_DETAIL="$DOWN_DETAIL GW"
        [ "$DOWN_AIM" -eq 1 ] && DOWN_DETAIL="$DOWN_DETAIL AIM"
        alert "🔴 进程异常:${DOWN_DETAIL}"
        LAST_DOWN_ALERT=$(ts_now)
    fi

    # ── 1. AIM 消息链路 ──
    # 超时增量
    CUR_TIMEOUT_TOTAL=$(safe_grep_count "$AIM_CLIENT_LOG" 'adapter stderr: OpenClaw 处理超时')
    TIMEOUT_DELTA=$((CUR_TIMEOUT_TOTAL - PREV_TIMEOUT_TOTAL))
    if [ "$TIMEOUT_DELTA" -gt 0 ]; then
        PREV_TIMEOUT_TOTAL=$CUR_TIMEOUT_TOTAL
        LAST_DELIVERY=$(grep '投递:' "$AIM_CLIENT_LOG" 2>/dev/null | tail -2 | tr '\n' ' | ' || echo "无")
        alert "🟡 adapter超时 +${TIMEOUT_DELTA} (累计${CUR_TIMEOUT_TOTAL}) | 投递: ${LAST_DELIVERY}"
    fi

    # trigger 变化
    CURR_TRIGGER_MTIME=$(stat -f %m "$TRIGGER_FILE" 2>/dev/null || echo 0)
    if [ "$CURR_TRIGGER_MTIME" -gt "$LAST_TRIGGER_MTIME" ]; then
        QUEUE_JSONS=$(safe_wc "$QUEUE_DIR" "json")
        REPLY_TXTS=$(safe_wc "$REPLY_DIR" "txt")
        log_task "📨 AIM消息活动: trigger=${CURR_TRIGGER_MTIME} queue=${QUEUE_JSONS} reply=${REPLY_TXTS}"
        LAST_TRIGGER_MTIME=$CURR_TRIGGER_MTIME
    fi

    # 队列积压
    QUEUE_COUNT=$(safe_wc "$QUEUE_DIR" "json")
    if [ "$QUEUE_COUNT" -gt 0 ] && [ $(( $(ts_now) - LAST_QUEUE_ALERT )) -gt 30 ]; then
        alert "⚠️ 队列积压: ${QUEUE_COUNT}条消息未处理"
        LAST_QUEUE_ALERT=$(ts_now)
    fi

    # ── 2. Gateway 日志 — 增量分析 ──
    GW_NEW=$(read_gw_new_lines || echo "")
    if [ -n "$GW_NEW" ]; then
        NEW_LINES=$(echo "$GW_NEW" | wc -l | tr -d ' ')
        log_debug "GW +${NEW_LINES}行"

        # 2a. 模型推理开始
        MODEL_STARTS=$(echo "$GW_NEW" | grep -c 'embedded run agent start' 2>/dev/null || echo 0)
        if [ "$MODEL_STARTS" -gt 0 ]; then
            log_task "🧠 模型推理开始: ${MODEL_STARTS}次"
        fi

        # 2b. 模型推理结束
        MODEL_ENDS=$(echo "$GW_NEW" | grep 'embedded run agent end' 2>/dev/null || echo "")
        if [ -n "$MODEL_ENDS" ]; then
            if echo "$MODEL_ENDS" | grep -q 'isError=true'; then
                ERR=$(echo "$MODEL_ENDS" | grep 'isError=true' | tail -1 | sed 's/.*error=//; s/ rawError.*//; s/request id.*//')
                alert "🔴 模型推理失败: ${ERR:0:150}"
            else
                DURATION=$(echo "$MODEL_ENDS" | grep -oE 'durationMs=[0-9]+' | tail -1 || echo "?")
                log_task "✅ 模型推理完成: ${DURATION}"
            fi
        fi

        # 2c. 401 认证错误
        NEW_401_INC=$(echo "$GW_NEW" | grep -c '401 The API key' 2>/dev/null || echo 0)
        if [ "$NEW_401_INC" -gt 0 ]; then
            CTX=$(echo "$GW_NEW" | grep '401 The API key' | grep -oE 'provider=[a-z0-9-]+|model=[a-z0-9.-]+' | tr '\n' ' ' || echo "")
            alert "🔴 API认证失败(401): ${CTX:-未知provider/model}"
        fi

        # 2d. model-fallback
        FB=$(echo "$GW_NEW" | grep 'model-fallback/decision' 2>/dev/null || echo "")
        if [ -n "$FB" ]; then
            DETAIL=$(echo "$FB" | grep -oE 'reason=\S+|next=\S+' | tr '\n' ' ' || echo "?")
            log_task "🔄 模型降级: ${DETAIL}"
        fi

        # 2e. compaction 失败
        CF=$(echo "$GW_NEW" | grep -c 'compaction.*failed' 2>/dev/null || echo 0)
        [ "$CF" -gt 0 ] && log_task "⚠️ 上下文压缩失败: ${CF}次"

        # 2f. lane errors
        LE=$(echo "$GW_NEW" | grep 'lane task error' 2>/dev/null || echo "")
        if [ -n "$LE" ]; then
            LANES=$(echo "$LE" | grep -oE 'lane=\S+' | sort -u | tr '\n' ',' | sed 's/,$//')
            log_task "⚠️ 通道错误: ${LANES}"
        fi

        # 2g. 工具调用
        TOOLS_INC=$(echo "$GW_NEW" | grep -cE 'tool_call|tool-result|bash run|skills/.*called' 2>/dev/null || echo 0)
        if [ "$TOOLS_INC" -gt 0 ]; then
            CUR_TOOL_TOTAL=$(safe_grep_count "$GATEWAY_LOG" 'tool_call\|tool-result\|bash run')
            log_task "🔧 工具调用 +${TOOLS_INC} (累计${CUR_TOOL_TOTAL})"
            PREV_TOOL_TOTAL=$CUR_TOOL_TOTAL
        fi
    fi

    # ── 3. 进度文件 ──
    if [ -f "$PROGRESS_FILE" ]; then
        CURR_MD5=$(md5 -q "$PROGRESS_FILE" 2>/dev/null || echo "none")
        if [ "$CURR_MD5" != "$LAST_PROGRESS_MD5" ]; then
            CONTENT=$(head -c 200 "$PROGRESS_FILE" 2>/dev/null | tr '\n' ' ')
            log_task "📝 进度更新: ${CONTENT}"
            LAST_PROGRESS_MD5=$CURR_MD5
        fi
    fi

    # ── 4. 中断诊断 ──
    if [ -n "$TUI_PID" ] && [ "$TIMEOUT_DELTA" -gt 0 ]; then
        TUI_CPU=$(ps -o %cpu= -p "$TUI_PID" 2>/dev/null | tr -d ' ' || echo "0")
        if [ "${TUI_CPU%.*}" -lt 1 ]; then
            log_task "⏸️ TUI空闲: CPU=${TUI_CPU}% (有超时→可能阻塞)"
        fi
    fi

    # ── 5. 心跳（每30秒）──
    STATE_COUNTER=$((STATE_COUNTER + INTERVAL))
    if [ $STATE_COUNTER -ge 30 ]; then
        Q=$(safe_wc "$QUEUE_DIR" "json")
        R=$(safe_wc "$REPLY_DIR" "txt")
        if [ "$ANY_DOWN" -gt 0 ]; then S="🔴DOWN"
        elif [ "$TIMEOUT_DELTA" -gt 0 ]; then S="🟡TIMEOUT"
        else S="🟢OK"; fi
        log_info "心跳[${S}] CYCLE=${CYCLE_COUNT} | TUI=${TUI_PID:-无} GW=${GW_PID:-无} AIM=${AIM_PID:-无} | Q=${Q} R=${R} | TO=${CUR_TIMEOUT_TOTAL}"
        STATE_COUNTER=0
    fi
done
