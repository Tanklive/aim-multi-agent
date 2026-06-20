"""
AIM Registry Service v1.2 — Phase 2 Query API

Changelog v1.2:
  + Query API: aim.registry.health_query / aim.registry.event_query
  + P2: non-blocking recover (create_task + _recover_task guard)

Changelog v1.1:
  + AgentRecord: framework_version, adapter_version, capabilities
  + Health snapshot: aim.registry.health_report → KV health_{agent_id}
  + Event log: aim.registry.event → KV event_{event_type}-{ts}
  + Client methods: report_health(), report_event()

启动:
  python3 registry.py --nats-url nats://127.0.0.1:4222
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aim.registry")

# ── 数据模型 ─────────────────────────────────────────────────

@dataclass
class AgentRecord:
    """Registry 中的 Agent 记录"""
    serial: str               # ZS0001
    global_id: str            # UUID v4
    name: str
    execution_model: str      # realtime | deferred | batch
    registered_at: float      # unix timestamp
    last_seen: float          # unix timestamp
    status: str               # online | offline | stalled | retired
    card_version: str         # Agent Card version
    nkey_pub: str = ""        # NKEY 公钥 (P2+)
    # P1: 版本 + 能力
    framework_version: str = ""    # main.py version (e.g. "1.3.1")
    adapter_version: str = ""      # adapter.sh version (e.g. "1.8")
    capabilities: list[str] = field(default_factory=list)  # ["process","health","trim","recover"]
    metadata: dict = field(default_factory=dict)
    # P1-3: 累积计数 + stalled 检测
    _offline_count: int = 0           # 累积离线次数（永不清零）
    offline_since: float = 0.0        # 离线开始时间戳
    stalled_since: float = 0.0        # stalled 开始时间戳
    last_queue_size: int = 0          # 上次健康报告中的 queue_size
    last_queue_at: float = 0.0        # 上次 queue_size 更新时间

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.capabilities is None:
            self.capabilities = []


# ── Registry Service ─────────────────────────────────────────

class Registry:
    """Registry 服务端 — NATS 微服务"""

    # NATS subjects
    SUBJ_REGISTER    = "aim.registry.register"
    SUBJ_LOOKUP      = "aim.registry.lookup"
    SUBJ_LIST        = "aim.registry.list"
    SUBJ_HEARTBEAT   = "aim.registry.heartbeat"
    SUBJ_HEALTH_RPT  = "aim.registry.health_report"   # P1: 健康快照
    SUBJ_EVENT       = "aim.registry.event"           # P1: 事件日志
    SUBJ_HEALTH_QRY  = "aim.registry.health_query"     # P2: 健康查询
    SUBJ_EVENT_QRY   = "aim.registry.event_query"      # P2: 事件查询
    SUBJ_OBS         = "aim.obs.registry"             # Observer 告警通道

    HEARTBEAT_TIMEOUT = 120   # 秒，超时判离线
    HEALTH_INTERVAL    = 30   # 秒，巡检间隔
    EVENT_RETENTION    = 100  # 每种事件最多保留条数
    STALLED_QUEUE_THRESHOLD = 5      # queue_size >= 此值才可能判 stalled
    STALLED_TIME_THRESHOLD   = 90    # 秒，queue 持续增长超此时间 → stalled

    def __init__(self, nats_url: str = "nats://127.0.0.1:4222", credentials: str = ""):
        self.nats_url = nats_url
        self.credentials = credentials
        self.nc = None
        self.js = None
        self.kv = None
        self._agents: dict[str, AgentRecord] = {}
        self._next_serial = 0
        self._running = False
        self._health_task = None
        self._startup_ts = 0.0

    async def start(self):
        """启动 Registry — 连接 NATS + 加载 KV + 订阅"""
        from nats import connect as nats_connect

        opts = {}
        if self.credentials:
            opts["user_credentials"] = self.credentials

        self.nc = await nats_connect(self.nats_url, **opts)
        self.js = self.nc.jetstream()

        # 初始化主 KV 存储
        try:
            self.kv = await self.js.create_key_value(
                bucket="aim-kv-registry",
                description="AIM Agent Registry",
            )
        except Exception:
            self.kv = await self.js.key_value("aim-kv-registry")

        await self._load_from_kv()

        # 订阅核心
        await self.nc.subscribe(self.SUBJ_REGISTER, cb=self._handle_register)
        await self.nc.subscribe(self.SUBJ_LOOKUP, cb=self._handle_lookup)
        await self.nc.subscribe(self.SUBJ_LIST, cb=self._handle_list)
        await self.nc.subscribe(self.SUBJ_HEARTBEAT, cb=self._handle_heartbeat)
        # P1 新增
        await self.nc.subscribe(self.SUBJ_HEALTH_RPT, cb=self._handle_health_report)
        await self.nc.subscribe(self.SUBJ_EVENT, cb=self._handle_event)
        # P2 查询
        await self.nc.subscribe(self.SUBJ_HEALTH_QRY, cb=self._handle_health_query)
        await self.nc.subscribe(self.SUBJ_EVENT_QRY, cb=self._handle_event_query)

        self._running = True
        self._startup_ts = time.time()
        self._health_task = asyncio.create_task(self._health_monitor())
        logger.info(f"Registry v1.2 启动完成 — {len(self._agents)} agent(s), 健康巡检 {self.HEALTH_INTERVAL}s/次, 超时 {self.HEARTBEAT_TIMEOUT}s")

    async def stop(self):
        self._running = False
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None
        if self.nc:
            await self.nc.drain()

    async def _get_client_nc(self):
        """获取 NATS 连接"""
        from nats import connect as _connect
        if self.nc and self.nc.is_connected:
            return self.nc
        opts = {}
        if self.credentials:
            opts["user_credentials"] = self.credentials
        return await _connect(self.nats_url, **opts)

    # ── 客户端方法 ────────────────────────────────────────

    async def register(self, agent_id: str, card: dict) -> dict:
        """客户端注册"""
        import json as _json
        nc = await self._get_client_nc()
        owned = nc is not self.nc
        try:
            resp = await nc.request(self.SUBJ_REGISTER, _json.dumps({
                "action": "register", "agent_id": agent_id, "card": card,
            }).encode(), timeout=5)
            return _json.loads(resp.data)
        finally:
            if owned:
                await nc.drain()

    async def lookup(self, agent_id: str) -> dict:
        """客户端查询"""
        import json as _json
        nc = await self._get_client_nc()
        owned = nc is not self.nc
        try:
            resp = await nc.request(self.SUBJ_LOOKUP, _json.dumps({
                "action": "lookup", "agent_id": agent_id,
            }).encode(), timeout=5)
            return _json.loads(resp.data)
        finally:
            if owned:
                await nc.drain()

    async def report_health(self, agent_id: str, health: dict) -> dict:
        """P1: 客户端上报健康快照"""
        import json as _json
        nc = await self._get_client_nc()
        owned = nc is not self.nc
        try:
            resp = await nc.request(self.SUBJ_HEALTH_RPT, _json.dumps({
                "agent_id": agent_id, "health": health,
            }).encode(), timeout=5)
            return _json.loads(resp.data)
        finally:
            if owned:
                await nc.drain()

    async def report_event(self, agent_id: str, event_type: str, detail: dict = None) -> dict:
        """P1: 客户端上报事件 (trim/recover/dead_letter)"""
        import json as _json
        nc = await self._get_client_nc()
        owned = nc is not self.nc
        try:
            resp = await nc.request(self.SUBJ_EVENT, _json.dumps({
                "agent_id": agent_id,
                "event_type": event_type,
                "detail": detail or {},
            }).encode(), timeout=5)
            return _json.loads(resp.data)
        finally:
            if owned:
                await nc.drain()

    # ── KV 加载 ──────────────────────────────────────────

    async def _load_from_kv(self):
        """从 KV 恢复注册数据"""
        try:
            keys = await self.kv.keys()
            for key in keys:
                # 跳过 health/event 前缀
                if key.startswith("health_") or key.startswith("event_"):
                    continue
                try:
                    entry = await self.kv.get(key)
                    data = json.loads(entry.value.decode())
                    record = AgentRecord(**data)
                    record.status = "online"
                    self._agents[record.serial] = record
                    num = int(record.serial[2:])
                    if num > self._next_serial:
                        self._next_serial = num
                except Exception as e:
                    logger.warning(f"KV 记录解析失败 {key}: {e}")
        except Exception as e:
            logger.warning(f"KV 加载失败: {e}")

    def _alloc_serial(self) -> str:
        self._next_serial += 1
        return f"ZS{self._next_serial:04d}"

    # ── 消息处理 ──────────────────────────────────────────

    async def _handle_register(self, msg):
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
            card = req.get("card", {})
        except Exception:
            await msg.respond(json.dumps({"status": "error", "error": "invalid json"}).encode())
            return

        now = time.time()

        if agent_id and agent_id in self._agents:
            existing = self._agents[agent_id]
            was_offline = existing.status != "online"
            existing.last_seen = now
            existing.status = "online"
            # P1: 更新版本+能力
            existing.framework_version = card.get("framework_version", existing.framework_version)
            existing.adapter_version = card.get("adapter_version", existing.adapter_version)
            existing.capabilities = card.get("capabilities", existing.capabilities)
            await self._save_to_kv(agent_id, existing)
            if was_offline:
                logger.info(f"Agent 恢复: {agent_id}")
                await self._publish_alert("agent_online", agent_id,
                    f"{agent_id} ({existing.name}) 恢复在线")
            await msg.respond(json.dumps({
                "status": "ok", "action": "reregister", "serial": agent_id,
            }).encode())
            return

        serial = agent_id if agent_id else self._alloc_serial()
        record = AgentRecord(
            serial=serial,
            global_id=req.get("global_id", str(uuid.uuid4())),
            name=card.get("name", serial),
            execution_model=card.get("execution_model", "deferred"),
            registered_at=now, last_seen=now, status="online",
            card_version=card.get("protocol_version", "1.0"),
            nkey_pub=card.get("nkey_pub", ""),
            framework_version=card.get("framework_version", ""),
            adapter_version=card.get("adapter_version", ""),
            capabilities=card.get("capabilities", []),
            metadata=card.get("metadata", {}),
        )
        self._agents[serial] = record
        await self._save_to_kv(serial, record)
        logger.info(f"注册: {serial} ({record.name}) v={record.framework_version} caps={record.capabilities}")
        await msg.respond(json.dumps({
            "status": "ok", "action": "registered", "serial": serial,
            "global_id": record.global_id,
        }).encode())

    async def _handle_lookup(self, msg):
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
        except Exception:
            await msg.respond(json.dumps({"status": "error"}).encode())
            return

        if agent_id in self._agents:
            record = self._agents[agent_id]
            await msg.respond(json.dumps({
                "status": "ok", "serial": agent_id,
                "agent": asdict(record),
            }, default=str).encode())
        else:
            await msg.respond(json.dumps({
                "status": "not_found", "serial": agent_id,
            }).encode())

    async def _handle_list(self, msg):
        agents = {
            serial: {
                "name": r.name, "execution_model": r.execution_model,
                "status": r.status, "last_seen": r.last_seen,
                "registered_at": r.registered_at,
                "framework_version": r.framework_version,
                "adapter_version": r.adapter_version,
                "capabilities": r.capabilities,
                "offline_count": r._offline_count,
                "offline_since": r.offline_since,
                "stalled_since": r.stalled_since,
                "last_queue_size": r.last_queue_size,
            }
            for serial, r in self._agents.items()
        }
        await msg.respond(json.dumps({
            "status": "ok", "count": len(agents), "agents": agents,
        }).encode())

    async def _handle_heartbeat(self, msg):
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
        except Exception:
            return

        now = time.time()
        if agent_id in self._agents:
            was_offline = self._agents[agent_id].status != "online"
            self._agents[agent_id].last_seen = now
            self._agents[agent_id].status = "online"
            if was_offline:
                logger.info(f"Agent 恢复: {agent_id}")
                await self._publish_alert("agent_online", agent_id,
                    f"{agent_id} ({self._agents[agent_id].name}) 恢复在线")

    async def _handle_health_report(self, msg):
        """P1: 接收健康快照 → 存 KV health_{agent_id}"""
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
            health = req.get("health", {})
        except Exception:
            await msg.respond(json.dumps({"status": "error"}).encode())
            return

        # 追加时间戳 + 更新 AgentRecord 中的 queue 快照
        now = time.time()
        health["reported_at"] = now

        # P1-3: 更新 AgentRecord 中的 queue 信息（用于 stalled 检测）
        if agent_id in self._agents:
            record = self._agents[agent_id]
            qs = health.get('queue_size', 0)
            record.last_queue_size = qs
            record.last_queue_at = now
            # queue 清空 → stalled 自动恢复
            if qs == 0 and record.status == "stalled":
                record.status = "online"
                record.stalled_since = 0.0
                await self._save_to_kv(agent_id, record)
                logger.info(f"🟢 Agent 恢复 (stalled→online): {agent_id} queue 已清空")
                await self._publish_alert("agent_online", agent_id,
                    f"{agent_id} ({record.name}) stalled 恢复，queue 已清空")

        try:
            key = f"health_{agent_id}"
            await self.kv.put(key, json.dumps(health).encode())
            await msg.respond(json.dumps({"status": "ok"}).encode())
            logger.debug(f"健康快照: {agent_id} q={health.get('queue_size',0)} ok={health.get('adapter_ok',False)}")
        except Exception as e:
            logger.error(f"健康快照保存失败 {agent_id}: {e}")
            await msg.respond(json.dumps({"status": "error", "error": str(e)}).encode())

    async def _handle_event(self, msg):
        """P1: 事件日志 → KV event_{type}-{ts}"""
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
            event_type = req.get("event_type", "unknown")
            detail = req.get("detail", {})
        except Exception:
            await msg.respond(json.dumps({"status": "error"}).encode())
            return

        event = {
            "agent_id": agent_id,
            "event_type": event_type,
            "ts": time.time(),
            "detail": detail,
        }
        ts_ns = int(event["ts"] * 1_000_000)
        key = f"event_{event_type}-{ts_ns}"

        try:
            await self.kv.put(key, json.dumps(event).encode())
            # 清理旧事件 (保留最近 N 条同类型)
            await self._trim_events(event_type)
            await msg.respond(json.dumps({"status": "ok", "key": key}).encode())
            logger.info(f"事件: {agent_id} {event_type} {json.dumps(detail, ensure_ascii=False)[:120]}")
        except Exception as e:
            logger.error(f"事件保存失败: {e}")
            await msg.respond(json.dumps({"status": "error", "error": str(e)}).encode())

    async def _trim_events(self, event_type: str):
        """清理旧事件，保留最近 EVENT_RETENTION 条"""
        try:
            prefix = f"event_{event_type}-"
            all_keys = await self.kv.keys()
            matching = sorted([k for k in all_keys if k.startswith(prefix)], reverse=True)
            to_delete = matching[self.EVENT_RETENTION:]
            for key in to_delete:
                await self.kv.delete(key)
        except Exception:
            pass  # 非关键路径，静默

    # P2: health query
    async def _handle_health_query(self, msg):
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
        except Exception:
            await msg.respond(json.dumps({"status": "error"}).encode())
            return
        try:
            key = f"health_{agent_id}"
            entry = await self.kv.get(key)
            await msg.respond(json.dumps({"status": "ok", "health": json.loads(entry.value)}).encode())
        except Exception as e:
            await msg.respond(json.dumps({"status": "not_found", "error": str(e)}).encode())

    # P2: event query
    async def _handle_event_query(self, msg):
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
            event_type = req.get("event_type", "")
            limit = req.get("limit", 20)
        except Exception:
            await msg.respond(json.dumps({"status": "error"}).encode())
            return
        try:
            prefix = f"event_{event_type}-" if event_type else "event_"
            all_keys = await self.kv.keys()
            matching = sorted([k for k in all_keys if k.startswith(prefix)], reverse=True)
            if agent_id:
                matching = [k for k in matching if agent_id in k]
            events = []
            for key in matching[:limit]:
                try:
                    entry = await self.kv.get(key)
                    events.append(json.loads(entry.value))
                except Exception:
                    pass
            await msg.respond(json.dumps({"status": "ok", "count": len(events), "events": events}).encode())
        except Exception as e:
            await msg.respond(json.dumps({"status": "error", "error": str(e)}).encode())

    # ── 健康巡检 ──────────────────────────────────────────

    async def _health_monitor(self):
        GRACE_PERIOD = self.HEALTH_INTERVAL * 2
        while self._running:
            await asyncio.sleep(self.HEALTH_INTERVAL)
            if not self._running:
                break
            now = time.time()
            in_grace = (now - self._startup_ts) < GRACE_PERIOD
            for serial, record in list(self._agents.items()):
                gap = now - record.last_seen

                # ── 离线检测 ──
                if record.status in ("online", "stalled") and gap > self.HEARTBEAT_TIMEOUT:
                    if in_grace:
                        continue
                    was_stalled = record.status == "stalled"
                    record.status = "offline"
                    record.offline_since = now
                    record._offline_count += 1
                    record.stalled_since = 0.0
                    await self._save_to_kv(serial, record)
                    logger.warning(f"🔴 Agent 离线: {serial} ({record.name}), "
                                   f"失联 {gap:.0f}s, 累计离线 {record._offline_count} 次"
                                   + (" (was stalled)" if was_stalled else ""))
                    await self._publish_alert("agent_offline", serial,
                        f"{serial} ({record.name}) 失联 {gap:.0f}s > {self.HEARTBEAT_TIMEOUT}s 超时, 累计{record._offline_count}次")

                # ── stalled 检测 (P1-3): heartbeat 正常但 queue 堆积不消费 ──
                elif record.status == "online" and record.last_queue_at > 0:
                    queue_age = now - record.last_queue_at
                    if (record.last_queue_size >= self.STALLED_QUEUE_THRESHOLD
                            and queue_age > self.STALLED_TIME_THRESHOLD):
                        record.status = "stalled"
                        record.stalled_since = now
                        await self._save_to_kv(serial, record)
                        logger.warning(f"🟡 Agent stalled: {serial} ({record.name}), "
                                       f"queue={record.last_queue_size} 积压 {queue_age:.0f}s")
                        await self._publish_alert("agent_stalled", serial,
                            f"{serial} ({record.name}) queue={record.last_queue_size} 积压 {queue_age:.0f}s > {self.STALLED_TIME_THRESHOLD}s")

    async def _publish_alert(self, event_type: str, agent_id: str, detail: str):
        try:
            if self.nc and self.nc.is_connected:
                payload = json.dumps({
                    "event": event_type,
                    "agent_id": agent_id,
                    "detail": detail,
                    "ts": time.time(),
                    "source": "registry",
                }).encode()
                await self.nc.publish(self.SUBJ_OBS, payload)
        except Exception as e:
            logger.error(f"告警发布失败: {e}")

    async def _save_to_kv(self, serial: str, record: AgentRecord):
        try:
            data = json.dumps(asdict(record), default=str).encode()
            await self.kv.put(serial, data)
        except Exception as e:
            logger.error(f"KV 保存失败 {serial}: {e}")


# ── CLI ───────────────────────────────────────────────────────
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="AIM Registry Service v1.1")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    parser.add_argument("--credentials", default="", help="NATS credentials file")
    args = parser.parse_args()

    registry = Registry(nats_url=args.nats_url, credentials=args.credentials)

    async def run():
        await registry.start()
        while registry._running:
            await asyncio.sleep(5)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[registry] 中断")
        asyncio.run(registry.stop())


if __name__ == "__main__":
    main()
