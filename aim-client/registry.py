"""
AIM Registry 服务端 — Phase 1 最小实现

功能:
  1. Agent 注册: 分配全局唯一 serial (ZS0001, ZS0002...)
  2. 身份管理: 存储 Agent Card + 公钥映射
  3. 在线列表: 查询当前活跃 Agent 列表
  4. NATS KV 持久化: 所有数据存储于 NATS JetStream KV

Phase 1 范围:
  - Registry 作为 NATS 微服务运行，不独立端口
  - 通过 NATS subject `aim.registry.*` 提供注册/查询
  - 基于 NATS KV `aim-kv-registry` 持久化

协议 (NATS request-reply):
  注册: → aim.registry.register  {"action":"register","agent_id":"ZS0001","card":{...}}
        ← {"status":"ok","serial":"ZS0001"}
  
  查询: → aim.registry.lookup  {"action":"lookup","agent_id":"ZS0001"}
        ← {"status":"ok","agent":{...}}
  
  列表: → aim.registry.list    {"action":"list"}
        ← {"status":"ok","agents":[...]}

启动:
  python3 registry.py --nats-url nats://127.0.0.1:4222
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict
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
    status: str               # online | offline | retired
    card_version: str         # Agent Card version
    nkey_pub: str = ""        # NKEY 公钥 (P2+)
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# ── Registry Service ─────────────────────────────────────────

class Registry:
    """Registry 服务端 — NATS 微服务"""

    # NATS subjects
    SUBJ_REGISTER = "aim.registry.register"
    SUBJ_LOOKUP   = "aim.registry.lookup"
    SUBJ_LIST     = "aim.registry.list"
    SUBJ_HEARTBEAT = "aim.registry.heartbeat"

    def __init__(self, nats_url: str = "nats://127.0.0.1:4222"):
        self.nats_url = nats_url
        self.nc = None
        self.js = None
        self.kv = None
        self._agents: dict[str, AgentRecord] = {}
        self._next_serial = 0
        self._running = False

    async def start(self):
        """启动 Registry — 连接 NATS + 加载 KV"""
        from nats import connect as nats_connect

        self.nc = await nats_connect(self.nats_url)
        self.js = self.nc.jetstream()

        # 初始化 KV 存储
        try:
            self.kv = await self.js.create_key_value(
                bucket="aim-kv-registry",
                description="AIM Agent Registry",
            )
        except Exception:
            self.kv = await self.js.key_value("aim-kv-registry")

        # 从 KV 恢复已注册 Agent
        await self._load_from_kv()

        # 订阅
        await self.nc.subscribe(self.SUBJ_REGISTER, cb=self._handle_register)
        await self.nc.subscribe(self.SUBJ_LOOKUP, cb=self._handle_lookup)
        await self.nc.subscribe(self.SUBJ_LIST, cb=self._handle_list)
        await self.nc.subscribe(self.SUBJ_HEARTBEAT, cb=self._handle_heartbeat)

        self._running = True
        logger.info(f"Registry 启动完成 — {len(self._agents)} agent(s)")

    async def stop(self):
        self._running = False
        if self.nc:
            await self.nc.drain()

    async def _get_client_nc(self):
        """获取 NATS 连接：优先复用服务端连接，否则新建（2026-06-17 修复）"""
        from nats import connect as _connect
        if self.nc and self.nc.is_connected:
            return self.nc
        return await _connect(self.nats_url)

    # ── 客户端方法（Agent 侧调用） ────────────────────────

    async def register(self, agent_id: str, card: dict) -> dict:
        """客户端注册 — Agent 启动时调用"""
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

    async def _load_from_kv(self):
        """从 KV 恢复注册数据"""
        try:
            keys = await self.kv.keys()
            for key in keys:
                try:
                    entry = await self.kv.get(key)
                    data = json.loads(entry.value.decode())
                    record = AgentRecord(**data)
                    self._agents[record.serial] = record
                    # 恢复 serial 计数器
                    num = int(record.serial[2:])
                    if num > self._next_serial:
                        self._next_serial = num
                except Exception as e:
                    logger.warning(f"KV 记录解析失败 {key}: {e}")
        except Exception as e:
            logger.warning(f"KV 加载失败: {e}")

    def _alloc_serial(self) -> str:
        """分配下一个全局唯一 serial"""
        self._next_serial += 1
        return f"ZS{self._next_serial:04d}"

    # ── 消息处理 ──────────────────────────────────────────

    async def _handle_register(self, msg):
        """处理注册请求"""
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
            card = req.get("card", {})
        except Exception:
            await msg.respond(json.dumps({"status": "error", "error": "invalid json"}).encode())
            return

        now = time.time()

        # 检查是否已注册
        if agent_id and agent_id in self._agents:
            existing = self._agents[agent_id]
            existing.last_seen = now
            existing.status = "online"
            await self._save_to_kv(agent_id, existing)
            await msg.respond(json.dumps({
                "status": "ok", "action": "reregister", "serial": agent_id
            }).encode())
            return

        # 新注册: 分配 serial
        serial = agent_id if agent_id else self._alloc_serial()

        record = AgentRecord(
            serial=serial,
            global_id=req.get("global_id", str(uuid.uuid4())),
            name=card.get("name", serial),
            execution_model=card.get("execution_model", "deferred"),
            registered_at=now,
            last_seen=now,
            status="online",
            card_version=card.get("protocol_version", "1.0"),
            nkey_pub=card.get("nkey_pub", ""),
            metadata=card.get("metadata", {}),
        )
        self._agents[serial] = record
        await self._save_to_kv(serial, record)

        logger.info(f"注册: {serial} ({record.name})")
        await msg.respond(json.dumps({
            "status": "ok", "action": "registered", "serial": serial,
            "global_id": record.global_id,
        }).encode())

    async def _handle_lookup(self, msg):
        """处理查询请求"""
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
        """返回所有已注册 Agent 列表"""
        agents = {
            serial: {
                "name": r.name, "execution_model": r.execution_model,
                "status": r.status, "last_seen": r.last_seen,
                "registered_at": r.registered_at,
            }
            for serial, r in self._agents.items()
        }
        await msg.respond(json.dumps({
            "status": "ok", "count": len(agents), "agents": agents,
        }).encode())

    async def _handle_heartbeat(self, msg):
        """处理心跳 — 更新 last_seen"""
        try:
            req = json.loads(msg.data.decode())
            agent_id = req.get("agent_id", "")
        except Exception:
            return

        if agent_id in self._agents:
            self._agents[agent_id].last_seen = time.time()
            self._agents[agent_id].status = "online"

    async def _save_to_kv(self, serial: str, record: AgentRecord):
        """持久化到 NATS KV"""
        try:
            data = json.dumps(asdict(record), default=str).encode()
            await self.kv.put(serial, data)
        except Exception as e:
            logger.error(f"KV 保存失败 {serial}: {e}")


# ── CLI ───────────────────────────────────────────────────────
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="AIM Registry Service v1.0")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    args = parser.parse_args()

    registry = Registry(nats_url=args.nats_url)

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
