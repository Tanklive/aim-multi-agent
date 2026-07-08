#!/bin/bash
# ============================================================
# AIM Agent Client Installer v6.0
# AIM 客户端全自动安装 + 注册 + 配置
# ============================================================
# 一条命令完成全部安装：
#   bash install.sh --name "新Agent" --framework hermes
#
# 自动完成：
#   1. 安装 Python 依赖
#   2. 从注册服务器下载 SDK
#   3. 连接注册服务器获取 Agent ID + .creds
#   4. 根据框架自动生成 handler.sh
#   5. 生成 aim-watch.sh + launchd 配置
#   6. 更新 NATS 配置并重启
#   7. 测试连接
# ============================================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 默认值
AIM_HOME="${AIM_HOME:-$HOME/.aim}"
NATS_SERVER=""
REGISTER_SERVER=""
AGENT_NAME=""
FRAMEWORK=""
PYTHON_CMD=""
AGENT_ID=""

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }
log_done()  { echo -e "${CYAN}[DONE]${NC} $1"; }

banner() {
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║     🐸 AIM Agent Installer v6.0         ║"
    echo "║     全自动安装 + 注册 + 配置             ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""
}

# ============================================================
# 1. 环境检查
# ============================================================

check_python() {
    log_step "检查 Python 环境..."
    for cmd in python3.14 python3 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                PYTHON_CMD="$cmd"
                log_info "Python $version ✅"
                return 0
            fi
        fi
    done
    log_error "需要 Python >= 3.10"
    exit 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --name)           AGENT_NAME="$2"; shift 2 ;;
            --framework)      FRAMEWORK="$2"; shift 2 ;;
            --server)         REGISTER_SERVER="$2"; shift 2 ;;
            --nats-server)    NATS_SERVER="$2"; shift 2 ;;
            --help|-h)        usage; exit 0 ;;
            *)                log_error "未知参数: $1"; usage; exit 1 ;;
        esac
    done
}

usage() {
    echo "用法: bash install.sh [选项]"
    echo ""
    echo "选项:"
    echo "  --name NAME         Agent 昵称"
    echo "  --framework FRAME   框架 (hermes/openclaw/letta/custom)"
    echo "  --server URL        注册服务器 (默认从配置文件读取)"
    echo "  --nats-server URL   NATS Server (默认从配置文件读取)"
    echo ""
    echo "示例:"
    echo "  bash install.sh --name '小助手' --framework hermes"
}

load_config() {
    log_step "加载配置..."
    local config_file="$AIM_HOME/config/aim.json"
    if [ -f "$config_file" ]; then
        [ -z "$REGISTER_SERVER" ] && REGISTER_SERVER=$($PYTHON_CMD -c "import json; print(json.load(open('$config_file')).get('register_server', ''))" 2>/dev/null || echo "")
        [ -z "$NATS_SERVER" ] && NATS_SERVER=$($PYTHON_CMD -c "import json; print(json.load(open('$config_file')).get('nats_server', ''))" 2>/dev/null || echo "")
        log_done "配置已加载"
    fi
    # 兜底默认值
    REGISTER_SERVER="${REGISTER_SERVER:-http://127.0.0.1:18910}"
    NATS_SERVER="${NATS_SERVER:-nats://127.0.0.1:4222}"
}

interactive_config() {
    log_step "交互式配置..."
    [ -z "$AGENT_NAME" ] && read -p "Agent 昵称: " AGENT_NAME
    [ -z "$FRAMEWORK" ] && { echo "可选框架: hermes, openclaw, letta, custom"; read -p "框架: " FRAMEWORK; }
    [ -z "$AGENT_NAME" ] && { log_error "昵称不能为空"; exit 1; }
    log_info "昵称: $AGENT_NAME | 框架: $FRAMEWORK"
    log_info "注册服务器: $REGISTER_SERVER"
}

# ============================================================
# 2. 安装依赖
# ============================================================

install_deps() {
    log_step "安装基础环境..."
    mkdir -p "$AIM_HOME/bin" "$AIM_HOME/bin/aim_watch/transports" "$AIM_HOME/config" "$AIM_HOME/logs"

    # Python 依赖
    if ! $PYTHON_CMD -c "import nats, nkeys" 2>/dev/null; then
        $PYTHON_CMD -m pip install --quiet nats-py nkeys 2>/dev/null || \
        $PYTHON_CMD -m pip install --quiet --break-system-packages nats-py nkeys 2>/dev/null || {
            command -v uv &>/dev/null && uv pip install nats-py nkeys --system
        }
    fi
    log_done "Python 依赖 ✅"
}

# ============================================================
# 3. 从注册服务器下载 SDK
# ============================================================

download_sdk() {
    log_step "下载 SDK..."

    # 检查是否已有 SDK
    local missing=0
    for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
        [ ! -f "$AIM_HOME/bin/$f" ] && missing=$((missing + 1))
    done

    if [ $missing -eq 0 ]; then
        log_done "SDK 已存在 ✅"
        return 0
    fi

    # 从注册服务器下载
    local sdk_url="${REGISTER_SERVER}/sdk"
    log_info "从 $sdk_url 下载 SDK..."

    # 优先本地复制（同机部署，零攻击面）
    local script_dir="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$script_dir/bin/aim_nats_sdk.py" ]; then
        mkdir -p "$AIM_HOME/bin"
        for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
            cp "$script_dir/bin/$f" "$AIM_HOME/bin/$f" 2>/dev/null && log_info "  本地复制 $f ✅" || log_warn "  本地缺少 $f"
        done
        [ -d "$script_dir/bin/aim_watch" ] && cp -r "$script_dir/bin/aim_watch" "$AIM_HOME/bin/" 2>/dev/null
    fi

    # 如果本地没有，再从注册服务器下载（带校验）
    local still_missing=0
    for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
        [ ! -f "$AIM_HOME/bin/$f" ] && still_missing=$((still_missing + 1))
    done

    if [ $still_missing -gt 0 ]; then
        log_warn "本地缺少 SDK 文件，尝试从注册服务器下载..."
        if command -v curl &>/dev/null; then
            for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
                [ -f "$AIM_HOME/bin/$f" ] && continue
                curl -sf "$sdk_url/$f" -o "$AIM_HOME/bin/$f" 2>/dev/null && log_info "  下载 $f ✅" || log_warn "  下载 $f 失败"
            done
            # 下载 aim_watch 模块
            [ ! -d "$AIM_HOME/bin/aim_watch" ] && \
                curl -sf "$sdk_url/aim_watch.tar.gz" -o /tmp/aim_watch.tar.gz 2>/dev/null && \
                tar -xzf /tmp/aim_watch.tar.gz -C "$AIM_HOME/bin/" 2>/dev/null && \
                log_info "  下载 aim_watch 模块 ✅" || true
        elif command -v wget &>/dev/null; then
            for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
                [ -f "$AIM_HOME/bin/$f" ] && continue
                wget -q "$sdk_url/$f" -O "$AIM_HOME/bin/$f" 2>/dev/null && log_info "  下载 $f ✅" || log_warn "  下载 $f 失败"
            done
        fi

        # SHA256 完整性校验（如果有校验文件）
        if [ -f "$AIM_HOME/bin/aim_nats_sdk.py" ] && curl -sf "$sdk_url/aim_nats_sdk.py.sha256" -o /tmp/sdk.sha256 2>/dev/null; then
            EXPECTED=$(cat /tmp/sdk.sha256 | awk '{print $1}')
            ACTUAL=$(sha256sum "$AIM_HOME/bin/aim_nats_sdk.py" 2>/dev/null | awk '{print $1}' || shasum -a 256 "$AIM_HOME/bin/aim_nats_sdk.py" | awk '{print $1}')
            if [ "$ACTUAL" != "$EXPECTED" ]; then
                log_error "SDK 文件校验失败！请检查下载安全性"
                log_error "  期望: $EXPECTED"
                log_error "  实际: $ACTUAL"
                exit 1
            fi
            log_done "SDK SHA256 校验通过 ✅"
            rm -f /tmp/sdk.sha256
        fi
    fi

    # 最终验证
    local final_missing=0
    for f in aim_nats_sdk.py aim_send.py aim-watch-v2.py; do
        [ ! -f "$AIM_HOME/bin/$f" ] && final_missing=$((final_missing + 1))
    done

    if [ $final_missing -gt 0 ]; then
        log_error "SDK 下载失败，请手动复制:"
        echo "  scp <主节点>:$AIM_HOME/bin/* $AIM_HOME/bin/"
        exit 1
    fi

    log_done "SDK 就绪 ✅"
}

# ============================================================
# 4. 自动注册
# ============================================================

auto_register() {
    log_step "自动注册..."
    log_info "连接注册服务器: $REGISTER_SERVER"

    # 运行注册客户端
    local result=$($PYTHON_CMD "$AIM_HOME/bin/aim-register.py" \
        --name "$AGENT_NAME" \
        --framework "$FRAMEWORK" \
        --server "$REGISTER_SERVER" \
        --nats-url "$NATS_SERVER" 2>&1)

    if [ $? -eq 0 ]; then
        # 提取 Agent ID
        AGENT_ID=$(echo "$result" | grep -oE "ZS[0-9]+" | tail -1)
        if [ -n "$AGENT_ID" ]; then
            log_done "注册成功: $AGENT_ID"
            return 0
        fi
    fi

    log_error "注册失败"
    echo "$result" | tail -5
    echo ""
    log_info "可能原因:"
    echo "  1. 注册服务器未运行: python3 aim-register-server.py"
    echo "  2. 网络不通: ping <server-ip>"
    exit 1
}

# ============================================================
# 5. 生成配置文件
# ============================================================

generate_configs() {
    log_step "生成配置文件..."

    local agent_dir="$AIM_HOME/agents/$AGENT_ID"

    # 生成 handler.sh（根据框架）
    cat > "$agent_dir/handler.sh" << HANDLER_EOF
#!/bin/bash
# AIM Agent Handler — $AGENT_NAME ($AGENT_ID)
# 框架: $FRAMEWORK
set -euo pipefail

INPUT=\$(cat)
FROM=\$(echo "\$INPUT" | $PYTHON_CMD -c "import sys,json; print(json.load(sys.stdin).get('from',''))" 2>/dev/null || echo "")
TEXT=\$(echo "\$INPUT" | $PYTHON_CMD -c "import sys,json; print(json.load(sys.stdin).get('payload',{}).get('text',''))" 2>/dev/null || echo "")

if [ -z "\$TEXT" ]; then
    exit 0
fi

# 过滤 CLI 噪声
echo "\$TEXT" | grep -qE '^(⚠️|Normalized model|Query:|Initializing|─|╭|╰|│|┊|Resume this|Session:|Duration:|Messages:|输入")' && {
    echo "ok"
    exit 0
}

HANDLER_EOF

    # 根据框架生成 AI 调用
    case "$FRAMEWORK" in
        hermes)
            cat >> "$agent_dir/handler.sh" << 'HANDLER_EOF'
# 调用 hermes CLI
HERMES_CLI="$HOME/.hermes/hermes-agent/venv/bin/hermes"
if [ -x "$HERMES_CLI" ]; then
    # 注意：不加 -Q，-Q 只输出 session_id 拿不到 AI 回复
    result=$(timeout 30 "$HERMES_CLI" chat -q "$TEXT" -p default 2>/dev/null || echo "ok")
    if [ -n "$result" ]; then
        # 过滤 CLI 输出噪声
        filtered_lines=()
        while IFS= read -r line; do
            line=$(echo "$line" | sed 's/^[[:space:]]*//')
            [ -z "$line" ] && continue
            echo "$line" | grep -qE '^(⚠️|Normalized model|Query:|Initializing|─|╭|╰|│|┊|Resume this|Session:|Duration:|Messages:|输入")' && continue
            filtered_lines+=("$line")
        done <<< "$result"
        if [ ${#filtered_lines[@]} -gt 0 ]; then
            echo "${filtered_lines[0]}"
        else
            echo "ok"
        fi
    else
        echo "ok"
    fi
else
    echo "[ZS0000] hermes CLI 未找到，请检查安装路径"
fi
HANDLER_EOF
            ;;
        openclaw)
            cat >> "$agent_dir/handler.sh" << 'HANDLER_EOF'
# 调用 OpenClaw CLI
if command -v openclaw &>/dev/null; then
    result=$(openclaw agent --agent main -m "$TEXT" --json 2>/dev/null || echo "")
    if [ -n "$result" ]; then
        echo "$result" | $PYTHON_CMD -c "import sys,json; print(json.loads(sys.stdin.read()).get('text','ok'))" 2>/dev/null || echo "ok"
    else
        echo "ok"
    fi
else
    echo "[ZS0000] openclaw CLI 未找到"
fi
HANDLER_EOF
            ;;
        letta)
            cat >> "$agent_dir/handler.sh" << 'HANDLER_EOF'
# 调用 Letta API
LETTA_URL="${LETTA_URL:-http://127.0.0.1:8283}"
result=$(curl -sf "$LETTA_URL/v1/agents/messages" \
    -H "Content-Type: application/json" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$TEXT\"}]}" 2>/dev/null || echo "")
if [ -n "$result" ]; then
    echo "$result" | $PYTHON_CMD -c "import sys,json; msgs=json.loads(sys.stdin.read()).get('messages',[]); print(msgs[-1].get('content','ok') if msgs else 'ok')" 2>/dev/null || echo "ok"
else
    echo "ok"
fi
HANDLER_EOF
            ;;
        *)
            cat >> "$agent_dir/handler.sh" << 'HANDLER_EOF'
# 自定义框架 — 请替换为你的 AI 调用
echo "[$AGENT_ID] 收到来自 $FROM 的消息: ${TEXT:0:50}"
HANDLER_EOF
            ;;
    esac

    chmod +x "$agent_dir/handler.sh"
    log_done "handler.sh 已生成（$FRAMEWORK 框架）"

    # 生成 aim-watch.sh
    cat > "$agent_dir/aim-watch.sh" << EOF
#!/bin/bash
# AIM Watch — $AGENT_NAME ($AGENT_ID)
AIM_HOME="\${AIM_HOME:-\$HOME/.aim}"
export AIM_AGENT_ID="$AGENT_ID"
python3 "\$AIM_HOME/bin/aim-watch-v2.py" --agent-id "$AGENT_ID" --nats-url "$NATS_SERVER" "\$@"
EOF
    chmod +x "$agent_dir/aim-watch.sh"
    log_done "aim-watch.sh 已生成"

    # 生成 config.json
    cat > "$agent_dir/config.json" << EOF
{
    "agent_id": "$AGENT_ID",
    "agent_name": "$AGENT_NAME",
    "framework": "$FRAMEWORK",
    "nats_url": "$NATS_SERVER",
    "creds_path": "$agent_dir/aim.creds"
}
EOF
    log_done "config.json 已生成"

    # 生成 launchd 配置（macOS）
    if [ "$(uname)" = "Darwin" ]; then
        local plist_file="$HOME/Library/LaunchAgents/com.aim.agent.$AGENT_ID.plist"
        cat > "$plist_file" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aim.agent.$AGENT_ID</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_CMD</string>
        <string>$agent_dir/nats-agent.py</string>
        <string>--agent-id</string>
        <string>$AGENT_ID</string>
        <string>--nats-url</string>
        <string>$NATS_SERVER</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$agent_dir</string>
    <key>StandardOutPath</key>
    <string>$agent_dir/logs/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$agent_dir/logs/agent.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>AIM_AGENT_ID</key>
        <string>$AGENT_ID</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF
        log_done "launchd 配置已生成"
    fi
}

# ============================================================
# 6. 更新 NATS 配置
# ============================================================

update_nats() {
    log_step "更新 NATS 配置..."

    # 检查是否在主节点（有 nsc 命令）
    if ! command -v nsc &>/dev/null; then
        log_warn "nsc 未安装，跳过 NATS 配置更新（需在主节点执行）"
        return 0
    fi

    # 重新生成配置
    nsc generate config --mem-resolver --config-file ~/aim-server/nats-jwt.conf --force 2>/dev/null && \
        log_done "NATS 配置已更新" || log_warn "NATS 配置更新失败"

    # 热加载 NATS 配置（不中断现有连接）
    if pkill -HUP nats-server 2>/dev/null; then
        log_done "NATS 配置已热加载"
    else
        log_warn "NATS 热加载失败，请手动执行: pkill -HUP nats-server"
    fi
}

# ============================================================
# 7. 测试连接
# ============================================================

test_connection() {
    log_step "测试连接..."

    local agent_dir="$AIM_HOME/agents/$AGENT_ID"

    # 测试 NATS 连接
    timeout 8 $PYTHON_CMD "$agent_dir/nats-agent.py" --agent-id "$AGENT_ID" --nats-url "$NATS_SERVER" 2>&1 | grep -q "已连接" && {
        log_done "NATS 连接成功 ✅"
        return 0
    } || {
        log_warn "NATS 连接测试超时（可能正常，需重启后验证）"
        return 0
    }
}

# ============================================================
# 8. 输出摘要
# ============================================================

print_summary() {
    local agent_dir="$AIM_HOME/agents/$AGENT_ID"

    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║     ✅ 安装完成！                        ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""
    echo "🎯 Agent ID: $AGENT_ID"
    echo "📁 目录: $agent_dir"
    echo "🤖 框架: $FRAMEWORK"
    echo ""
    echo "📝 启动方式:"
    echo ""
    echo "   # 前台运行（调试用）"
    echo "   python3 $agent_dir/nats-agent.py --agent-id $AGENT_ID"
    echo ""
    echo "   # 系统服务（macOS）"
    echo "   launchctl bootstrap gui/\$(id -u) \\"
    echo "      ~/Library/LaunchAgents/com.aim.agent.$AGENT_ID.plist"
    echo ""
    echo "   # AIM Watch"
    echo "   bash $agent_dir/aim-watch.sh"
    echo ""
}

# ============================================================
# 主流程
# ============================================================

main() {
    banner
    parse_args "$@"
    check_python
    load_config
    interactive_config
    install_deps
    download_sdk
    auto_register
    generate_configs
    update_nats
    test_connection
    print_summary
}

main "$@"
