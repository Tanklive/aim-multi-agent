#!/bin/bash
# Letta stdout 噪声过滤器 v1.1
# 用法: echo "$letta_output" | filter_letta_output.sh
#       或 filter_letta_output.sh "$letta_output"
#
# 独立脚本，方便单独测试和版本升级。
# adapter.sh 优先使用此脚本，不存在时回退到内置 grep。

# 统一从 stdin 或参数读取
if [ $# -gt 0 ]; then
    INPUT="$1"
else
    INPUT="$(cat)"
fi

# 过滤已知噪声模式，同时做兜底 box-drawing 检测
echo "$INPUT" | awk '
BEGIN {
    lines = 0; box = 0
    # 噪声黑名单（正则）
    noise["^Connected"] = 1
    noise["^Loading"] = 1
    noise["^Error saving"] = 1
    noise["^ENOENT"] = 1
    noise["^/Users/"] = 1
    noise["^[[:space:]]+at "] = 1
    noise["^Session:"] = 1
    noise["^Duration:"] = 1
    noise["^Messages:"] = 1
}
{
    # 检查是否匹配噪声模式
    skip = 0
    for (pat in noise) {
        if ($0 ~ pat) { skip = 1; break }
    }
    if (skip) next

    lines++
    if ($0 ~ /[╭─╰│]/) box++
    print
}
END {
    if (box > 2 && lines > 10) exit 1
}
'
