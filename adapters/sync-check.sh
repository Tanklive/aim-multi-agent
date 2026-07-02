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
    
    shared_md5=$(python3 -c "import hashlib; print(hashlib.md5(open('$shared_file','rb').read()).hexdigest())")
    deploy_md5=$(python3 -c "import hashlib; print(hashlib.md5(open('$deploy_file','rb').read()).hexdigest())")
    
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
echo "=== services.api 声明检查 ==="
for agent in ZS0001 ZS0002 ZS0003; do
    config_file="$DEPLOY_DIR/$agent/config.json"
    if [ ! -f "$config_file" ]; then
        echo "[$agent] ⚠️ config.json 不存在"
        continue
    fi
    api_url=$(python3 -c "
import json
with open('$config_file') as f:
    d = json.load(f)
svc = d.get('services', {}).get('api', {})
print(svc.get('url', 'NOT SET'))
" 2>/dev/null)
    api_cred=$(python3 -c "
import json
with open('$config_file') as f:
    d = json.load(f)
auth = d.get('services', {}).get('api', {}).get('auth', {})
print(auth.get('credential', 'NOT SET'))
" 2>/dev/null)
    required=$(python3 -c "
import json
with open('$config_file') as f:
    d = json.load(f)
print(d.get('services', {}).get('api', {}).get('required', 'NOT SET'))
" 2>/dev/null)
    if [ "$api_url" = "NOT SET" ]; then
        echo "[$agent] ⚠️ services.api 未声明（CLI only）"
    elif [ "$api_cred" = "NOT SET" ]; then
        echo "[$agent] ❌ services.api.url=$api_url 但 credential 未设置"
        ISSUES=$((ISSUES + 1))
    else
        echo "[$agent] ✅ url=$api_url cred=${api_cred:0:10}... required=$required"
    fi
done

echo ""
echo "=== Adapter info JSON 标准检查（P1-001）==="
INFO_REQUIRED_FIELDS="provider version execution_model"
for agent in ZS0001 ZS0002 ZS0003; do
    adapter="$DEPLOY_DIR/$agent/adapter.sh"
    if [ ! -x "$adapter" ]; then
        echo "[$agent] ⚠️ adapter.sh 不可执行"
        ISSUES=$((ISSUES + 1))
        continue
    fi
    raw=$(bash "$adapter" info 2>/dev/null || echo '{}')
    if ! echo "$raw" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "[$agent] ❌ info 返回非法 JSON: ${raw:0:80}..."
        ISSUES=$((ISSUES + 1))
        continue
    fi
    missing=""
    for f in $INFO_REQUIRED_FIELDS; do
        val=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$f',''))" 2>/dev/null)
        if [ -z "$val" ]; then
            missing="$missing $f"
        fi
    done
    if [ -n "$missing" ]; then
        echo "[$agent] ❌ info 缺少字段: $missing"
        ISSUES=$((ISSUES + 1))
    else
        prov=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('provider','?'))")
        ver=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'))")
        echo "[$agent] ✅ provider=$prov ver=$ver"
    fi
done

echo ""
if [ $ISSUES -eq 0 ]; then
    echo "结果: ✅ 全部一致"
else
    echo "结果: ❌ $ISSUES 项不一致（用 --fix 自动修复）"
fi

exit $ISSUES
