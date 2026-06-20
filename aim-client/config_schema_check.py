#!/usr/bin/env python3
"""启动前 config schema quick check — 调用 schema 库做 A/B 层校验

要求 ~/shared/aim/schema/config_schema_v0.2.py 已部署
返回 0=OK, 1=A层拒绝
"""

import sys, importlib.util
from pathlib import Path

def check(config_path: str) -> int:
    schema_file = Path.home() / "shared/aim/schema/config_schema_v0.2.py"
    
    if not schema_file.exists():
        print(f"⚠️  schema 脚本不存在: {schema_file}", file=sys.stderr)
        return 0  # 不阻塞
    
    # importlib 导入带点的文件名
    spec = importlib.util.spec_from_file_location("config_schema_v0_2", str(schema_file))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"⚠️  schema 加载失败: {e}", file=sys.stderr)
        return 0
    
    return mod.validate(config_path)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    sys.exit(check(sys.argv[1]))
