#!/bin/bash
# AIM adapter 版本同步检查
# 用法: bash sync-check.sh [--fix]
#   无参数: 检查 shared↔部署 MD5 是否一致
#   --fix:   不一致时自动 cp shared → 部署

set -e

SHARED_DIR="$HOME/shared/aim/adapters"
DEPLOY_DIR="$HOME/.aim/agents"

MODE="${1:-check}"
ISSUES=0

echo "=== AIM Adapter 版本同步检查 ==="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "项目版本: $(cat "$SHARED_DIR/../VERSION" 2>/dev/null || echo '?')"
echo ""

for agent in ZS0001 ZS0002 ZS0003; do
    case $agent in
        ZS0001) shared_subdir="openclaw" ;;
        ZS0002) shared_subdir="hermes" ;;
        ZS0003) shared_subdir="letta" ;;
    esac
    shared_file="$SHARED_DIR/$shared_subdir/adapter.sh"
    deploy_file="$DEPLOY_DIR/$agent/adapter.sh"
    
    if [ ! -f "$shared_file" ]; then
        echo "[$agent] ❌ shared 版不存在: $shared_file"
        ISSUES=$((ISSUES + 1))
        continue
    fi
    if [ ! -f "$deploy_file" ]; then
        echo "[$agent] ❌ 部署版不存在: $deploy_file"
        ISSUES=$((ISSUES + 1))
        continue
    fi
    
    shared_md5=$(md5 -q "$shared_file")
    deploy_md5=$(md5 -q "$deploy_file")
    
    # 提取版本注释
    shared_ver=$(grep -m1 "^# .*v[0-9]" "$shared_file" | sed 's/^# //' | xargs)
    deploy_ver=$(grep -m1 "^# .*v[0-9]" "$deploy_file" | sed 's/^# //' | xargs)
    
    if [ "$shared_md5" = "$deploy_md5" ]; then
        echo "[$agent] ✅ $shared_ver"
    else
        echo "[$agent] ❌ 不一致!"
        echo "  shared:  $shared_md5  $shared_ver"
        echo "  deploy:  $deploy_md5  $deploy_ver"
        ISSUES=$((ISSUES + 1))
        
        if [ "$MODE" = "--fix" ]; then
            cp "$shared_file" "$deploy_file"
            echo "  → 已同步 shared → deploy"
            ISSUES=$((ISSUES - 1))
        fi
    fi
done

echo ""
if [ $ISSUES -eq 0 ]; then
    echo "结果: ✅ 全部一致"
else
    echo "结果: ❌ $ISSUES 项不一致（用 --fix 自动修复）"
fi

exit $ISSUES
