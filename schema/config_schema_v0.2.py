#!/usr/bin/env python3
"""Config Schema v0.2 — A/B/C 三层校验
用法: config_schema_v0.2.py -f config.json
exit 0=OK, 1=A层拒绝, 0+B=通过+警告
"""

import json, sys, re
from pathlib import Path
from typing import Dict, Any, Tuple, List

A_LAYER = {
    "agent_id": {"type": str, "pattern": r"^ZS\d{4}$"},
    "adapter": {"type": dict, "keys": {
        "cmd": {"type": str, "required": True},
        "timeout": {"type": int, "min": 10},
    }},
    "nats": {"type": dict, "keys": {
        "subject_prefix": {"type": str, "required": True}
    }},
    "security": {"type": dict, "keys": {
        "auth": {"type": dict, "keys": {
            "chain": {"type": list, "required": True},
            "registered_agents": {"type": list, "required": True}
        }}
    }},
    "version": {"type": str, "required": True}
}

B_DEFAULTS = {
    "queue.max_age_ms": 3600000,
    "queue.ack_timeout_ms": 300000,
    "heartbeat.interval_ms": 30000,
    "heartbeat.timeout_ms": 120000,
    "log.level": "info"
}

def check_a(cfg):
    errs = []
    for key, spec in A_LAYER.items():
        if key not in cfg:
            if spec.get("required"): errs.append(f"A层缺失: {key}")
            continue
        val = cfg[key]
        if spec["type"] == dict and "keys" in spec:
            for sk, ss in spec["keys"].items():
                if sk not in val:
                    if ss.get("required"): errs.append(f"A层缺失: {key}.{sk}")
                    continue
                sv = val[sk]
                if isinstance(ss["type"], type) and not isinstance(sv, ss["type"]):
                    errs.append(f"A层类型错误: {key}.{sk} 期望{ss['type'].__name__} 实际{type(sv).__name__}")
                if "min" in ss and isinstance(sv, (int,float)) and sv < ss["min"]:
                    errs.append(f"A层值错误: {key}.{sk}={sv} < min={ss['min']}")
        elif isinstance(spec["type"], type) and not isinstance(val, spec["type"]):
            errs.append(f"A层类型错误: {key} 期望{spec['type'].__name__}")
        if "pattern" in spec and isinstance(val, str):
            if not re.match(spec["pattern"], val):
                errs.append(f"A层格式不符: {key}={val}")
    return len(errs) == 0, errs

def check_b(cfg):
    warns = []
    for key_path, default_val in B_DEFAULTS.items():
        keys = key_path.split(".")
        val = cfg; found = True
        for k in keys:
            if isinstance(val, dict) and k in val: val = val[k]
            else: found = False; break
        if not found:
            warns.append(f"B层缺失: {key_path} (默认{default_val})")
    return warns

def validate(path):
    try: cfg = json.loads(Path(path).read_text())
    except Exception as e: print(f"❌ 解析失败: {e}", file=sys.stderr); return 1
    a_ok, a_errs = check_a(cfg)
    if not a_ok:
        for e in a_errs: print(f"❌ {e}", file=sys.stderr)
        return 1
    for w in check_b(cfg): print(f"⚠️  {w}", file=sys.stderr)
    print(f"✅ config schema v0.2 OK ({cfg.get('agent_id','?')})")
    return 0

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "-f": sys.exit(1)
    sys.exit(validate(sys.argv[2]))
