#!/usr/bin/env python3
"""
AIM Watch v3.1 — 启动回放历史 + 实时消息
用法: aim-watch --agent-id ZS0001 --agent-name 呱呱

特性:
- 启动时从 NATS JetStream 回放最近 50 条消息（无需 Observer，无需 launchd）
- 实时订阅 aim.dm.{agent_id} + aim.grp.> + aim.obs.{agent_id}
- 关闭后消息仍在 JetStream 中，下次打开可回放
"""
import asyncio, json, os, sys, time, argparse, signal
from datetime import datetime, timezone, timedelta
from pathlib import Path

SDK_DIR = Path.home() / ".aim" / "bin"
sys.path.insert(0, str(SDK_DIR))
from aim_nats_sdk import AIMObserverClient

VERSION = "3.1.0"
HISTORY_LIMIT = 50
LOCAL_TZ = timezone(timedelta(hours=8))

def fmt_ts(ts):
    try:
        if isinstance(ts, str):
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            # 如果是 naive datetime（无时区），当作 UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LOCAL_TZ).strftime("%H:%M:%S")
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(ts)[:8]

NAME_MAP = {"ZS0001": "呱呱", "ZS0002": "吉量", "ZS0003": "小火鸡儿"}

def agent_name(aid):
    n = NAME_MAP.get(aid, aid)
    return f"{n}({aid})" if n != aid else aid

def parse_msg(raw: str):
    """解析消息 JSON，兼容两种格式"""
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # 格式1: AIM v1.0 信封
    frm = d.get("from", "")
    pl = d.get("payload", {}) if isinstance(d.get("payload"), dict) else {}
    txt = pl.get("text", "") or d.get("content", "") or d.get("text", "")
    grp = d.get("group", "") or d.get("to", "")
    ts = d.get("ts", int(time.time()))
    tp = d.get("type", "dm")
    return {"from": frm, "text": txt, "group": grp, "ts": ts, "type": tp}

def extract_dm_grp(d):
    """从消息 dict 提取显示信息"""
    frm = d.get("from", "?")
    txt = d.get("payload", {}).get("text", "") if isinstance(d.get("payload"), dict) else d.get("content", d.get("text", ""))
    grp = d.get("group", "") or d.get("to", "")
    ts = d.get("ts", 0)
    return frm, txt, grp, ts

class Display:
    def __init__(self, agent_id, agent_name_):
        self.aid = agent_id
        self.aname = agent_name_
        self._shown_ids = set()  # 去重

    def banner(self, server, hist=0):
        t = self.aname or self.aid
        print(f"┌─ AIM Watch v{VERSION} ─── {t} ({self.aid}) ─── {datetime.now(LOCAL_TZ):%m-%d %H:%M:%S} ─┐")
        print(f"│ 📡 {server}  │ 📜 历史 {hist} 条")
        print("├" + "─" * 66 + "┤")

    def _dedup(self, msg_id):
        if msg_id in self._shown_ids:
            return True
        self._shown_ids.add(msg_id)
        if len(self._shown_ids) > 500:
            self._shown_ids.clear()
        return False

    ACK_PREFIXES = ("👂 收到", "👂收到")

    def _is_ack(self, text):
        """ACK 消息: 👂 收到，稍等... → 不显示"""
        t = (text or "").strip()
        if not t:
            return True
        return t.startswith(self.ACK_PREFIXES) and len(t) < 30

    def dm(self, from_id, to_id, text, ts, msg_id=""):
        if to_id != self.aid and from_id != self.aid:
            return
        if self._is_ack(text):
            return
        if self._dedup(msg_id):
            return
        print(f"{fmt_ts(ts)} ▶ {agent_name(from_id)}: {text}")

    def grp(self, from_id, group, text, ts, msg_id=""):
        if self._is_ack(text):
            return
        if self._dedup(msg_id):
            return
        g = group or "?"
        print(f"{fmt_ts(ts)} ▶ {agent_name(from_id)} @{g}: {text}")

    def obs_completed(self, text, ts):
        print(f"{fmt_ts(ts)} ✅ {text}")

    def obs_error(self, text, ts):
        print(f"{fmt_ts(ts)} ❌ {text[:150]}")

    def footer(self, n):
        print(f"├──────────────────────────────────────────────────────────────┤")
        print(f"│ 📊 {n} events  │  Ctrl+C 退出")
        print("└──────────────────────────────────────────────────────────────┘")

async def replay_history(dsp, server, creds):
    """从 JetStream aim-messages 回放最近消息"""
    try:
        from nats.aio.client import Client as NATS
        nc = NATS()
        await nc.connect(
            servers=[server],
            user_credentials=creds if (creds and os.path.exists(creds)) else "",
            connect_timeout=5
        )
        js = nc.jetstream()
        # 检查 stream 是否存在
        try:
            await js.stream_info("aim-messages")
        except Exception:
            await nc.close()
            return 0
        # 用 ephemeral consumer 拉最新消息
        sub = await js.pull_subscribe("", "aim-watch-replay", stream="aim-messages")
        msgs = await sub.fetch(HISTORY_LIMIT, timeout=5)

        shown = 0
        for m in msgs:
            raw = m.data.decode()
            parsed = parse_msg(raw)
            if not parsed:
                continue
            frm, txt, grp, ts = parsed["from"], parsed["text"], parsed["group"], parsed["ts"]
            mid = ""
            try: mid = json.loads(raw).get("id", "")
            except: pass

            if grp and grp != dsp.aid:
                dsp.grp(frm, grp, txt, ts, mid)
            elif not grp or grp == dsp.aid:
                to_id = ""
                try: to_id = json.loads(raw).get("to", "")
                except: pass
                dsp.dm(frm, to_id or dsp.aid, txt, ts, mid)
            shown += 1
            await m.ack()
        await sub.unsubscribe()
        await nc.close()
        return shown
    except Exception as e:
        print(f"│ ⚠️ 历史回放跳过: {e}")
        return 0

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agent-id", default="")
    p.add_argument("--agent-name", default="")
    p.add_argument("--all", action="store_true", help="观察所有 Agent（不按 ID 过滤）")
    a = p.parse_args()
    if not a.agent_id and not a.all:
        print("Usage: aim-watch --agent-id ZS0001 --agent-name 呱呱")
        print("       aim-watch --all（观察所有 Agent）")
        sys.exit(1)

    cfg = {}
    cp = Path.home()/".aim"/"config"/"aim.json"
    if cp.exists():
        cfg = json.loads(cp.read_text())
    url = cfg.get("nats_server","nats://127.0.0.1:4222")

    if a.all:
        # --all 模式：用 admin-watch creds
        creds = cfg.get("agents",{}).get("admin-watch",{}).get("creds_path","")
        if creds:
            creds = os.path.expanduser(creds)
        a.agent_id = "all"
        a.agent_name = "All Agents"
    else:
        creds = cfg.get("agents",{}).get(a.agent_id,{}).get("creds_path","")
        if creds:
            creds = os.path.expanduser(creds)

    dsp = Display(a.agent_id, a.agent_name)

    # 先回放历史
    hist_count = await replay_history(dsp, url, creds)

    dsp.banner(url, hist_count)

    obs = AIMObserverClient(server=url, credentials=creds if (creds and os.path.exists(creds)) else "")
    await obs.connect()

    nc = obs.nc
    total = 0

    def _skip_ack(text):
        """过虑 ACK 消息: 👂 收到，稍等..."""
        t = (text or "").strip()
        if not t:
            return True
        if t.startswith("👂") and ("收到" in t or "稍等" in t):
            return True
        return False

    async def on_dm(msg):
        nonlocal total
        try:
            d = json.loads(msg.data)
            frm, txt, grp, ts = extract_dm_grp(d)
            if _skip_ack(txt):
                return
            mid = d.get("id","")
            to_id = d.get("to",a.agent_id)
            dsp.dm(frm, to_id, txt, ts, mid)
            total += 1
        except: pass

    async def on_grp(msg):
        nonlocal total
        try:
            d = json.loads(msg.data)
            frm, txt, grp, ts = extract_dm_grp(d)
            if _skip_ack(txt):
                return
            mid = d.get("id","")
            dsp.grp(frm, grp, txt, ts, mid)
            total += 1
        except: pass

    async def on_obs(msg):
        nonlocal total
        try:
            d = json.loads(msg.data)
            s = d.get("status","")
            aid = d.get("agent_id","")
            if aid != a.agent_id:
                return
            detail = d.get("detail","")
            ts = d.get("ts", time.time())

            # StateReport 扩展信息
            active = d.get("active_sessions")
            qd = d.get("queue_depth")
            lat = d.get("avg_latency_ms")

            extra = ""
            if active is not None or qd is not None:
                parts = []
                if active is not None:
                    parts.append(f"active={active}")
                if qd is not None:
                    parts.append(f"queue={qd}")
                if lat is not None:
                    parts.append(f"lat={lat:.0f}ms")
                extra = f" [{', '.join(parts)}]"

            if s in ("heartbeat","received","processing"):
                return
            if s == "completed":
                dsp.obs_completed(f"{detail}{extra}", ts)
            elif s == "error":
                dsp.obs_error(f"{detail}{extra}", ts)
            elif s == "healthy":
                dsp.obs_completed(f"🟢 {detail or '健康'}{extra}", ts)
            elif s == "degraded":
                dsp.obs_error(f"🟡 降级{extra}", ts)
            elif s == "unhealthy":
                dsp.obs_error(f"🔴 离线{extra}", ts)
            total += 1
        except: pass

    if a.all:
        # --all 模式：订阅所有 observer + 所有 dm + 所有 grp
        await nc.subscribe("aim.obs.>", cb=on_obs)
        await nc.subscribe("aim.dm.>", cb=on_dm)
        await nc.subscribe("aim.grp.>", cb=on_grp)
    else:
        await nc.subscribe(f"aim.dm.{a.agent_id}", cb=on_dm)
        await nc.subscribe("aim.grp.>", cb=on_grp)
        await nc.subscribe(f"aim.obs.{a.agent_id}", cb=on_obs)

    stop = asyncio.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    await stop.wait()
    dsp.footer(total)
    await obs.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
