#!/bin/bash
# P0-004 冷归档脚本 — 2026-06-24 三方通过
# 规则：30天未访问的文件 → archive/cold/YYYY/
# 范围：issues/ proposals/ docs/ backups/ logs/
# 排除：PROJECT/ config/ adapters/ aim-client/ scripts/

set -euo pipefail

SHARED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COLD_DIR="$SHARED_DIR/archive/cold"
INDEX_FILE="$COLD_DIR/INDEX.md"
DAYS=30
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

YEAR=$(date +%Y)
YEAR_DIR="$COLD_DIR/$YEAR"

mkdir -p "$YEAR_DIR"

# 扫描目标
TARGETS=("issues" "proposals" "docs" "backups" "logs")

MOVED=0

for dir in "${TARGETS[@]}"; do
    TARGET="$SHARED_DIR/$dir"
    [ -d "$TARGET" ] || continue

    while IFS= read -r -d '' file; do
        rel="${file#$SHARED_DIR/}"
        dest="$YEAR_DIR/$(basename "$file")"

        # 去重：目标已存在则跳过
        [ -e "$dest" ] && continue

        year=$(stat -f '%Sm' -t '%Y' "$file" 2>/dev/null || echo "$YEAR")

        if $DRY_RUN; then
            echo "[DRY-RUN] $rel → archive/cold/$year/"
        else
            git -C "$SHARED_DIR" mv "$file" "$COLD_DIR/$year/" 2>/dev/null && {
                echo "  ✅ $rel → cold/$year/"
                MOVED=$((MOVED + 1))
            } || {
                echo "  ⚠️  git mv 失败，用 mv: $rel"
                mv "$file" "$COLD_DIR/$year/"
                MOVED=$((MOVED + 1))
            }
        fi
    done < <(find "$TARGET" -type f -atime +"$DAYS" -print0 2>/dev/null)
done

# 更新 INDEX
if ! $DRY_RUN && [ "$MOVED" -gt 0 ]; then
    {
        echo "# 冷归档索引"
        echo ""
        echo "> 最后更新: $(date '+%Y-%m-%d %H:%M')"
        echo "> 归档数量: $MOVED 个文件"
        echo ""
        echo "## $YEAR"
        echo ""
        ls -1 "$YEAR_DIR/" 2>/dev/null | while read f; do
            echo "- \`$f\` — 归档于 $(date -r "$YEAR_DIR/$f" '+%Y-%m-%d')"
        done
    } > "$INDEX_FILE"

    git -C "$SHARED_DIR" add "$COLD_DIR" 2>/dev/null || true
    git -C "$SHARED_DIR" commit -m "P0-004: cold archive $MOVED files → archive/cold/$YEAR/" 2>/dev/null || true

    echo "📦 完成: $MOVED 文件归档 → archive/cold/$YEAR/"
elif $DRY_RUN; then
    echo "🔍 预览完成（--dry-run，未实际移动）"
else
    echo "📭 无需归档（30天内无未访问文件）"
fi
