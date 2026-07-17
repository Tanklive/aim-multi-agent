#!/bin/bash
# aim_group_ops — AIM 群操作 CLI 工具（供适配器调用）
# 版本: v1.0 | ZS0001 呱呱 | 2026-07-15
#
# 用法:
#   aim_group_ops.sh create [群名]             → 建群，返回 group_id
#   aim_group_ops.sh join <group_id> <agent_id> → 入群申请
#   aim_group_ops.sh approve <group_id> <agent_id> → 审批入群
#   aim_group_ops.sh leave <group_id> [agent_id]   → 退群/踢人
#   aim_group_ops.sh members <group_id>            → 查成员
#   aim_group_ops.sh list                          → 全部群
#   aim_group_ops.sh my <agent_id>                 → 我的群

set -euo pipefail

: ${NATS_URL:="nats://127.0.0.1:4222"}
: ${NATS_CREDS:="$HOME/.aim/registry.creds"}

NATS="nats --server $NATS_URL --creds $NATS_CREDS"

_cmd() { shift; echo "$@"; }

OP="${1:-}"
shift 2>/dev/null || true

case "$OP" in
    create)
        NAME="${1:-}"
        if [ -z "$NAME" ]; then
            # 自动生成默认名
            NAME="群聊($(date '+%Y-%m-%d %H:%M'))"
        fi
        OWNER="${OWNER:-ZS0001}"
        $NATS request aim.groups.create "{\"owner\":\"$OWNER\",\"name\":\"$NAME\"}" 2>/dev/null \
            | python3 -c "
import sys,json
try:
    for line in sys.stdin:
        if line.startswith('{'):
            d=json.loads(line)
            if d.get('status')=='created':
                print(f'✅ 群已创建: {d[\"group_id\"]} — {d[\"name\"]}')
            else:
                print(json.dumps(d,ensure_ascii=False))
            break
except: pass
"
        ;;
    join)
        GRP_ID="$1"; AGT_ID="${2:-$OWNER}"
        [ -z "$GRP_ID" ] && { echo "用法: $0 join <group_id> [agent_id]"; exit 1; }
        $NATS request aim.groups.join "{\"group_id\":\"$GRP_ID\",\"agent_id\":\"$AGT_ID\"}" 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        s=d.get('status','?')
        print(f'✅ 已加入 {d.get(\"group_id\",\"?\")}' if s in ('joined','pending','already_member') else json.dumps(d,ensure_ascii=False))
        break
"
        ;;
    approve)
        GRP_ID="$1"; AGT_ID="$2"
        [ -z "$GRP_ID" ] || [ -z "$AGT_ID" ] && { echo "用法: $0 approve <group_id> <agent_id>"; exit 1; }
        REQ="${REQ:-ZS0001}"
        $NATS request aim.groups.approve "{\"group_id\":\"$GRP_ID\",\"agent_id\":\"$AGT_ID\",\"action\":\"approve\",\"requester\":\"$REQ\"}" 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        print(f'✅ 已批准 {d.get(\"agent_id\",\"?\")} 加入 {d.get(\"group_id\",\"?\")}' if d.get('status')=='approved' else json.dumps(d,ensure_ascii=False))
        break
"
        ;;
    leave)
        GRP_ID="$1"; AGT_ID="${2:-ZS0001}"; REQ="${REQ:-ZS0001}"
        [ -z "$GRP_ID" ] && { echo "用法: $0 leave <group_id> [agent_id]"; exit 1; }
        $NATS request aim.groups.leave "{\"group_id\":\"$GRP_ID\",\"agent_id\":\"$AGT_ID\",\"requester\":\"$REQ\"}" 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        print(f'✅ {d.get(\"agent_id\",\"?\")} {d.get(\"action\",\"已离开\")} {d.get(\"group_id\",\"?\")}')
        break
"
        ;;
    members)
        GRP_ID="$1"
        [ -z "$GRP_ID" ] && { echo "用法: $0 members <group_id>"; exit 1; }
        $NATS request aim.groups.members "{\"group_id\":\"$GRP_ID\"}" 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        print(f'群 {d.get(\"group_id\",\"?\")}: 成员={d.get(\"members\",[])}, 待审批={list(d.get(\"pending\",{}).keys())}, 群主={d.get(\"owner\",\"?\")}')
        break
"
        ;;
    list|ls)
        $NATS request aim.groups.list '{}' 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        gs=d.get('groups',{})
        for gid,info in sorted(gs.items()):
            print(f'  {gid}  \"{info.get(\"name\",\"?\")}\"  {info.get(\"members\",0)}人  群主:{info.get(\"owner\",\"?\")}')
        break
"
        ;;
    my)
        AGT_ID="${1:-ZS0001}"
        $NATS request aim.groups.my "{\"agent_id\":\"$AGT_ID\"}" 2>/dev/null \
            | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        gs=d.get('groups',{})
        for gid,info in sorted(gs.items()):
            print(f'  {gid}  \"{info.get(\"name\",\"?\")}\"  {info.get(\"member_count\",0)}人')
        break
"
        ;;
    announce)
        SUB="${1:-}"
        shift 2>/dev/null || true
        case "$SUB" in
            set)
                GRP_ID="$1"; shift 2>/dev/null || true
                CONTENT="${*:-}"
                [ -z "$GRP_ID" ] && { echo "用法: $0 announce set <group_id> <公告内容>"; exit 1; }
                [ -z "$CONTENT" ] && { echo "用法: $0 announce set <group_id> <公告内容>"; exit 1; }
                OPR="${OPERATOR:-ZS0001}"
                $NATS request aim.groups.announce "{\"action\":\"set\",\"group_id\":\"$GRP_ID\",\"operator\":\"$OPR\",\"content\":$(echo "$CONTENT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")}" 2>/dev/null \
                    | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        print(f'✅ 公告已设置 {d.get(\"group_id\",\"?\")}' if d.get('status')=='set' else json.dumps(d,ensure_ascii=False))
        break
"
                ;;
            get)
                GRP_ID="$1"
                [ -z "$GRP_ID" ] && { echo "用法: $0 announce get <group_id>"; exit 1; }
                $NATS request aim.groups.announce "{\"action\":\"get\",\"group_id\":\"$GRP_ID\"}" 2>/dev/null \
                    | python3 -c "
import sys,json
for line in sys.stdin:
    if line.startswith('{'):
        d=json.loads(line)
        a=d.get('announcement')
        if a:
            print(f'📢 [{d.get(\"group_id\",\"?\")}] {a[\"content\"]}\\n  — {a[\"set_by\"]} {__import__(\"time\").strftime(\"%Y-%m-%d %H:%M\",__import__(\"time\").localtime(a[\"set_at\"]))}')
        else:
            print(f'📢 [{d.get(\"group_id\",\"?\")}] 暂无公告')
        break
"
                ;;
            *)
                echo "用法: $0 announce <set|get> <group_id> [公告内容]"
                ;;
        esac
        ;;
    *)
        echo "AIM 群操作工具 v1.0"
        echo ""
        echo "用法: $0 <操作> [参数...]"
        echo ""
        echo "操作:"
        echo "  create [群名]                       建群"
        echo "  join <group_id> [agent_id]          入群"
        echo "  approve <group_id> <agent_id>       审批"
        echo "  leave <group_id> [agent_id]         退群"
        echo "  members <group_id>                  查成员"
        echo "  list                                全部群"
        echo "  my [agent_id]                       我的群"
        echo "  announce set <group_id> <内容>      设置公告（群主）"
        echo "  announce get <group_id>             查看公告"
        ;;
esac
