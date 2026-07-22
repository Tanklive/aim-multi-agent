#!/usr/bin/env python3
"""
AIM Agent 会话健康监控 — 实时轮询本地日志/队列，显示三 Agent 状态

用法:
  python3 aim_health_monitor.py              # 快照一次
  python3 aim_health_monitor.py --watch 5    # 每 5 秒刷新
  python3 aim_health_monitor.py --watch      # 每 3 秒刷新（默认）

维护: ZS0003 (小火鸡儿) | 2026-07-09
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AIM_DIR = os.path.expanduser("~/.aim")
SHARED_AIM_DIR = os.path.expanduser("~/shared/aim")
AGENTS = {
    "ZS0001": "呱呱",
    "ZS0002": "吉量",
    "ZS0003": "小火鸡儿",
}

# 积压预警阈值（2026-07-17 🐤）
QUEUE_WARN = 80       # ⚠️ 黄色预警：开始关注
QUEUE_CRITICAL = 100  # 🔴 红色告警：自动群聊通知
ALERT_COOLDOWN_SEC = 300  # 同一 agent 同级别告警冷却 5 分钟
_last_alert: dict[str, tuple[float, str]] = {}  # agent_id -> (timestamp, level)

GRP_TRIO = "grp_dc738fc1-c85c-4440-b3ad-31192284a6b2"

def send_grp_alert(agent_id: str, name: str, stuck: int, level: str):
    """向 trio 群聊发送积压告警"""
    now_ts = time.time()
    last = _last_alert.get(agent_id)
    if last and (now_ts - last[0] < ALERT_COOLDOWN_SEC) and last[1] == level:
        return  # 冷却中，不重复发

    emoji = "🚨" if level == "critical" else "⚠️"
    msg = f"{emoji} [健康监控] {name}({agent_id}) 积压 {stuck} 条，超过{'临界' if level == 'critical' else '预警'}阈值"
    send_script = os.path.join(SHARED_AIM_DIR, "aim_send_nats.py")
    try:
        subprocess.run(
            [sys.executable, send_script, GRP_TRIO, msg, "--group", "--from", "ZS0003"],
            capture_output=True, timeout=10,
        )
        _last_alert[agent_id] = (now_ts, level)
    except Exception:
        pass  # 发告警失败不阻塞监控主循环


def check_launchctl(agent_id: str) -> str:
    """检查 launchctl 进程状态"""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2] == f"com.aim.agent.{agent_id}":
                pid, status = parts[0], parts[1]
                if status == "0":
                    return f"✅ alive (PID {pid})"
                return f"⚠️  exit={status} (PID {pid})"
        return "❌ not in launchd"
    except Exception:
        return "⚠️  check failed"


def check_queue(agent_id: str) -> dict:
    """检查队列积压"""
    path = Path(AIM_DIR) / "agents" / agent_id / "queue.jsonl"
    if not path.exists():
        return {"total": 0, "stuck": 0, "error": None}

    try:
        lines = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    lines.append({"op": "corrupt"})

        enqueued = [l for l in lines if l.get("op") == "enqueue"]
        stuck = [e for e in enqueued if e.get("data", {}).get("dequeued_at", 0) == 0]
        corrupt = [l for l in lines if l.get("op") == "corrupt"]
        return {
            "total": len(lines),
            "stuck": len(stuck),
            "corrupt": len(corrupt),
            "error": None,
        }
    except Exception as e:
        return {"total": 0, "stuck": 0, "error": str(e)}


def check_log(agent_id: str) -> dict:
    """检查最近日志活跃度"""
    # 优先读 shared/aim/logs/（main.py 实际写入路径），fallback 到 ~/.aim/logs/
    log_path = Path(SHARED_AIM_DIR) / "logs" / f"aim-client-{agent_id}.log"
    if not log_path.exists():
        log_path = Path(AIM_DIR) / "logs" / f"aim-client-{agent_id}.log"
    if not log_path.exists():
        return {"age_sec": None, "last_line": "no log"}

    try:
        st = os.stat(log_path)
        age_sec = time.time() - st.st_mtime
        with open(log_path) as f:
            lines = f.readlines()
        last = lines[-1].strip()[:80] if lines else "(empty)"
        # check for errors in last 10 lines
        recent = "".join(lines[-10:])
        has_error = "ERROR" in recent or "FATAL" in recent
        has_warning = "WARNING" in recent or "WARN" in recent
        has_dispatch = "adapter OK" in recent or "投递:" in recent
        return {
            "age_sec": age_sec,
            "last_line": last,
            "has_error": has_error,
            "has_warning": has_warning,
            "has_dispatch": has_dispatch,
        }
    except Exception as e:
        return {"age_sec": None, "last_line": str(e)}


def render(clear: bool = True):
    """渲染一帧监控画面"""
    if clear:
        print("\033[2J\033[H", end="")  # 清屏 + 光标复位

    now = datetime.now().strftime("%H:%M:%S")
    print(f"╔══════════════════════════════════════════╗")
    print(f"║   AIM Agent 会话健康监控  {now}  ║")
    print(f"╚══════════════════════════════════════════╝")
    print()

    # 收集数据
    rows = []
    for agent_id, name in AGENTS.items():
        q = check_queue(agent_id)
        log = check_log(agent_id)
        status = check_launchctl(agent_id)
        alive = "✅" if "alive" in status else "⚠️" if "exit=" in status else "❌"
        acked = q.get("total", 0) - q.get("stuck", 0) if not q.get("error") else "?"

        # 日志状态
        if log["age_sec"] is None:
            log_status = "—"
        elif log["has_error"]:
            log_status = "🔴"
        elif log["has_dispatch"]:
            log_status = "🟢"
        else:
            log_status = "🟡"

        rows.append({
            "agent": agent_id,
            "name": name,
            "alive": alive,
            "status": status.split("(")[0].strip() if "(" in status else status,
            "total": q.get("total", "?"),
            "stuck": q.get("stuck", "?"),
            "acked": acked,
            "corrupt": q.get("corrupt", 0),
            "log_age": f"{log['age_sec']:.0f}s" if log["age_sec"] and log["age_sec"] < 120 else f"{log['age_sec']/60:.0f}m" if log["age_sec"] else "—",
            "log_icon": log_status,
            "has_dispatch": log.get("has_dispatch", False),
            "has_error": log.get("has_error", False),
            "has_warning": log.get("has_warning", False),
        })

    # 表格
    print("┌────────┬──────┬───────┬───────┬──────┬───────┬──────────┬────────────┐")
    print("│ Agent  │ 进程 │ 总量  │ 积压  │ 已ACK│ 损坏  │ 日志活跃 │ dispatch   │")
    print("├────────┼──────┼───────┼───────┼──────┼───────┼──────────┼────────────┤")
    for r in rows:
        stuck_mark = "🔴" if isinstance(r["stuck"], int) and r["stuck"] > 5 else "🟡" if isinstance(r["stuck"], int) and r["stuck"] > 0 else "🟢"
        corrupt_str = f"⚠️{r['corrupt']}" if r["corrupt"] > 0 else "0"
        dispatch_str = "🟢 OK" if r["has_dispatch"] else "🟡 idle" if r["has_error"] else "🟢 idle"
        wflag = "⚠️" if r.get("has_warning") else " "
        print(f"│ {r['agent']:6} │ {r['alive']:4} │ {str(r['total']):5} │ {stuck_mark}{str(r['stuck']):4} │ {str(r['acked']):4} │ {corrupt_str:5} │ {r['log_age']:8} │ {dispatch_str:10} │")
    print("└────────┴──────┴───────┴───────┴──────┴───────┴──────────┴────────────┘")
    print()

    # 积压预警 + 自动群聊告警
    alert_lines = []
    for r in rows:
        stuck = r.get("stuck")
        if not isinstance(stuck, int) or stuck == 0:
            continue
        if stuck >= QUEUE_CRITICAL:
            label = f"🔴 {r['agent']}({r['name']}) 积压 {stuck} 条 ≥ {QUEUE_CRITICAL}"
            alert_lines.append(label)
            send_grp_alert(r["agent"], r["name"], stuck, "critical")
        elif stuck >= QUEUE_WARN:
            label = f"🟡 {r['agent']}({r['name']}) 积压 {stuck} 条 ≥ {QUEUE_WARN}"
            alert_lines.append(label)
            send_grp_alert(r["agent"], r["name"], stuck, "warn")
    if alert_lines:
        print("📊 积压预警：")
        for line in alert_lines:
            print(f"  {line}")
        print()

    # 只有真正的 ERROR/FATAL 才在底部显示
    has_errors = [r for r in rows if r.get("has_error")]
    if has_errors:
        print("🔴 检测到 ERROR/FATAL：")
        for r in has_errors:
            log = check_log(r["agent"])
            # 找最近一条含 ERROR 或 FATAL 的行
            try:
                log_path = Path(SHARED_AIM_DIR) / "logs" / f"aim-client-{r['agent']}.log"
                if not log_path.exists():
                    log_path = Path(AIM_DIR) / "logs" / f"aim-client-{r['agent']}.log"
                with open(log_path) as f:
                    err_lines = [l.strip() for l in f.readlines() if "ERROR" in l or "FATAL" in l]
                last_err = err_lines[-1][:120] if err_lines else log.get("last_line", "")[:120]
                print(f"  {r['agent']}: {last_err}")
            except:
                print(f"  {r['agent']}: {log.get('last_line', '')[:120]}")


def main():
    parser = argparse.ArgumentParser(description="AIM Agent 会话健康监控")
    parser.add_argument("--watch", "-w", nargs="?", const=3, type=int,
                        help="持续监控间隔（秒），默认 3")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                render(clear=True)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\n退出。")
    else:
        render(clear=False)


if __name__ == "__main__":
    main()
