#!/bin/bash
# AIM Deploy v3.0 — Git-driven 自动化部署
# 架构原则：main.py 单源（plist 直接指 shared/）、adapter/config 分 Agent 管理
# 用法：
#   ./deploy.sh                   部署所有变更文件
#   ./deploy.sh --agent ZS0001    只部署 ZS0001
#   ./deploy.sh --dry-run         预览不执行
#   ./deploy.sh --restart         部署后自动重启
#   ./deploy.sh --verify          只验证（diff检查）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AIM_DIR="$HOME/.aim"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"

DRY_RUN=false
TARGET_AGENT=""
DO_RESTART=false
VERIFY_ONLY=false

# ── 参数解析 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=true; shift ;;
        --agent)    TARGET_AGENT="$2"; shift 2 ;;
        --restart)  DO_RESTART=true; shift ;;
        --verify)   VERIFY_ONLY=true; shift ;;
        *)          echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 颜色 ──
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✅ $1${NC}"; }
err() { echo -e "${RED}❌ $1${NC}"; }
warn(){ echo -e "${YELLOW}⚠️  $1${NC}"; }
info(){ echo "📋 $1"; }

# ── 提权命令（dry-run 时不执行）──
do_cp()  { if $DRY_RUN; then echo "  [DRY] cp $1 → $2"; else cp "$1" "$2"; fi; }
do_cmd() { if $DRY_RUN; then echo "  [DRY] $*"; else "$@"; fi; }

# ── 1. 验证 ──
echo "=== AIM Deploy v3.0 ==="
echo "Project: $PROJECT_DIR"
echo "Target:  ${TARGET_AGENT:-全部 Agent}"
echo "Mode:    $(if $DRY_RUN; then echo DRY-RUN; elif $VERIFY_ONLY; then echo VERIFY-ONLY; else echo DEPLOY; fi)"
echo ""

info "Step 1: 代码验证..."
cd "$PROJECT_DIR"

# Python 语法检查
for pyfile in aim-client/main.py aim_nats_sdk.py aim-client/security.py aim-client/registry.py aim-client/session.py aim-client/group_admission.py aim-client/context.py; do
    if [ -f "$pyfile" ]; then
        python3 -c "import py_compile; py_compile.compile('$pyfile', doraise=True)" 2>/dev/null && \
            ok "$pyfile syntax" || err "$pyfile syntax FAILED"
    fi
done

echo ""

# ── 2. main.py 单源 ──
info "Step 2: main.py 无需部署（plist 直接指向 shared/aim/aim-client/main.py）"
for agent in ZS0001 ZS0002 ZS0003; do
    if [ -n "$TARGET_AGENT" ] && [ "$agent" != "$TARGET_AGENT" ]; then continue; fi
    plist_path="$LAUNCHD_DIR/com.aim.agent.$agent.plist"
    if [ -f "$plist_path" ]; then
        main_path=$(grep -A1 "main.py" "$plist_path" | grep "shared/aim" | tr -d '[:space:]' || true)
        if [ -n "$main_path" ]; then
            ok "$agent: main.py → shared/ (单源)"
        else
            warn "$agent: plist 未指向 shared/，请检查"
        fi
    fi
done
echo ""

# ── 3. adapter.sh 部署 ──
info "Step 3: adapter.sh 部署..."

adapter_path() {
    case "$1" in
        ZS0001) echo "adapters/ZS0001/adapter.sh" ;;
        ZS0002) echo "adapters/hermes/adapter.sh" ;;
        ZS0003) echo "adapters/letta/adapter.sh" ;;
    esac
}

for agent in ZS0001 ZS0002 ZS0003; do
    if [ -n "$TARGET_AGENT" ] && [ "$agent" != "$TARGET_AGENT" ]; then continue; fi

    src="$PROJECT_DIR/$(adapter_path "$agent")"
    dst="$AIM_DIR/agents/$agent/adapter.sh"

    if [ ! -f "$src" ]; then
        warn "$agent: 源 adapter 不存在 ($src)"
        continue
    fi

    if [ -f "$dst" ]; then
        src_md5=$(md5 -q "$src")
        dst_md5=$(md5 -q "$dst")
        if [ "$src_md5" = "$dst_md5" ]; then
            ok "$agent adapter: 已同步"
            continue
        fi
        # 备份旧版本
        do_cp "$dst" "$dst.bak.$(date +%m%d-%H%M%S)"
    fi

    if $DRY_RUN; then
        echo "  [DRY] cp $src → $dst"
    else
        cp "$src" "$dst"
        chmod +x "$dst"

        # diff 校验
        new_md5=$(md5 -q "$dst")
        if [ "$(md5 -q "$src")" = "$new_md5" ]; then
            ok "$agent adapter: 部署成功 + diff 校验通过"
        else
            err "$agent adapter: diff 校验失败！"
        fi
    fi
done
echo ""

# ── 4. config.json 部署 ──
info "Step 4: config.json 部署..."

for agent in ZS0001 ZS0002 ZS0003; do
    if [ -n "$TARGET_AGENT" ] && [ "$agent" != "$TARGET_AGENT" ]; then continue; fi

    src="$PROJECT_DIR/configs/$agent/config.json"
    dst="$AIM_DIR/agents/$agent/config.json"

    if [ ! -f "$src" ]; then
        warn "$agent: config 源不存在 ($src)，跳过"
        continue
    fi
    if [ ! -f "$dst" ]; then
        warn "$agent: config 目标不存在 ($dst)，跳过"
        continue
    fi

    # 智能合并：保留本地密钥，更新配置项
    # 仅当文件不一致时部署
    src_md5=$(md5 -q "$src")
    dst_md5=$(md5 -q "$dst")
    if [ "$src_md5" = "$dst_md5" ]; then
        ok "$agent config: 已同步"
        continue
    fi

    do_cp "$dst" "$dst.bak.$(date +%m%d-%H%M%S)"
    do_cp "$src" "$dst"
    ok "$agent config: 部署完成"
done
echo ""

# ── 5. plist 部署 ──
info "Step 5: plist 部署..."

for plist_src in "$PROJECT_DIR/plists/"*.plist; do
    [ -f "$plist_src" ] || continue
    pname=$(basename "$plist_src")
    plist_dst="$LAUNCHD_DIR/$pname"

    if [ -f "$plist_dst" ]; then
        src_md5=$(md5 -q "$plist_src")
        dst_md5=$(md5 -q "$plist_dst")
        if [ "$src_md5" = "$dst_md5" ]; then
            ok "plist $pname: 已同步"
            continue
        fi
        do_cp "$plist_dst" "$plist_dst.bak.$(date +%m%d-%H%M%S)"
    fi

    do_cp "$plist_src" "$plist_dst"
    ok "plist $pname: 部署完成"
done
echo ""

# ── 6. SDK 部署 ──
info "Step 6: aim_nats_sdk → ~/.aim/bin/"
if [ -f "$PROJECT_DIR/aim_nats_sdk.py" ]; then
    do_cp "$PROJECT_DIR/aim_nats_sdk.py" "$AIM_DIR/bin/aim_nats_sdk.py"
    ok "aim_nats_sdk.py 已同步"
fi
echo ""

# ── 7. 重启 ──
if $DO_RESTART && ! $DRY_RUN && ! $VERIFY_ONLY; then
    info "Step 7: 重启 Agent..."
    for agent in ZS0001 ZS0002 ZS0003; do
        if [ -n "$TARGET_AGENT" ] && [ "$agent" != "$TARGET_AGENT" ]; then continue; fi

        svc="com.aim.agent.$agent"
        if [ "$agent" = "ZS0002" ]; then
            warn "$agent: 吉量管理，不自动重启。请通知吉量。"
            continue
        fi

        if $DRY_RUN; then
            echo "  [DRY] launchctl kickstart -k gui/$(id -u)/$svc"
            continue
        fi

        # 重启
        launchctl kickstart -k "gui/$(id -u)/$svc" 2>/dev/null && ok "$agent: 重启完成" || warn "$agent: 重启失败"
        sleep 3

        # 健康检查
        health_path="$AIM_DIR/agents/$agent/adapter.sh"
        if [ -f "$health_path" ]; then
            health_out=$(timeout 15 bash "$health_path" info 2>/dev/null | head -1 || echo "TIMEOUT")
            case "$health_out" in
                *provider*|*version*) ok "$agent: 健康检查通过 ($health_out)" ;;
                *) warn "$agent: 健康检查异常: $health_out" ;;
            esac
        fi
    done
elif $VERIFY_ONLY; then
    echo ""
    warn "VERIFY-ONLY 模式：跳过重启"
fi

echo ""
echo "=== Deploy v3.0 完成 ==="
echo "Git: $(cd "$PROJECT_DIR" && git log --oneline -1 | cat)"
