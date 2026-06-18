"""
HTTP Transport — AIM NATS 降级传输层

当 NATS 不可用时，自动回退到 HTTP POST/GET 进行消息传输。
零外部依赖（只用标准库 http.server + urllib.request）。

用法:
    from http_transport import HTTPTransport, HAS_HTTP_TRANSPORT
    transport = HTTPTransport(agent_id="ZS0002", port=27391)
    await transport.start_server()       # 启动 HTTP 接收端
    await transport.send_dm("ZS0001", "hello")  # 通过 HTTP 发送
    await transport.stop_server()
"""

import asyncio
import json
import logging
import os
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError
from threading import Thread
from typing import Optional, Callable, Dict, Any

log = logging.getLogger("aim-http")

HAS_HTTP_TRANSPORT = True

# ── 默认配置 ──────────────────────────────────────────────────────────

# 各 Agent 的 HTTP 端点（port 从 aim.json 读取，兜底默认值）
DEFAULT_HTTP_PORTS = {
    "ZS0001": 27390,
    "ZS0002": 27391,
    "ZS0003": 27392,
}

# ── HTTP 请求/响应协议 ───────────────────────────────────────────────

# AIM HTTP Transport 消息信封（与 NATS 信封格式一致）
# POST /aim/message  {"ver":"1.0","id":"...","from":"ZS0002","type":"dm","payload":{"text":"..."}}
# 响应: {"status":"ok","msg_id":"..."}

# GET  /aim/health   健康检查
# 响应: {"status":"ok","agent_id":"ZS0002"}


class AIMHTTPHandler(BaseHTTPRequestHandler):
    """AIM HTTP Transport 请求处理器"""

    # 外部注入
    transport: "HTTPTransport" = None

    def log_message(self, format, *args):
        log.debug(f"[HTTP] {args[0]} {args[1]} {args[2]}")

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/aim/health":
            self._send_json(200, {
                "status": "ok",
                "agent_id": self.transport.agent_id,
                "ts": time.time(),
            })
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/aim/message":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                data = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": f"invalid_request: {e}"})
                return

            msg_id = data.get("id", uuid.uuid4().hex[:12])
            # 投递给消息回调
            handler = self.transport._msg_handler
            if handler:
                try:
                    # 异步回调需要创建任务
                    asyncio.run_coroutine_threadsafe(handler(data), self.transport._loop)
                except Exception as e:
                    log.warning(f"[HTTP] callback error: {e}")

            self._send_json(200, {"status": "ok", "msg_id": msg_id})
        else:
            self._send_json(404, {"error": "not_found"})


class HTTPTransport:
    """AIM HTTP 传输层（NATS 降级方案）

    功能:
      - 启动 HTTP Server 接收消息（替代 NATS subscribe）
      - 通过 HTTP POST 发送消息（替代 NATS publish）
      - 健康检查端点（替代 NATS ping）

    端口取自 ~/.aim/config/aim.json 中 agents.<id>.http_port，
    兜底使用 DEFAULT_HTTP_PORTS 常量。
    """

    def __init__(
        self,
        agent_id: str,
        port: Optional[int] = None,
        host: str = "127.0.0.1",
        peer_ports: Optional[Dict[str, int]] = None,
    ):
        self.agent_id = agent_id
        self.host = host
        self.port = port or DEFAULT_HTTP_PORTS.get(agent_id, 27391)
        self.peer_ports = peer_ports or {}

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None
        self._msg_handler: Optional[Callable] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── 服务端（接收消息） ────────────────────────────────────────────

    def start_server(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        """启动 HTTP 服务端（阻塞线程，不影响主 asyncio 循环）

        Args:
            loop: 主事件循环，用于投递异步回调。不提供则自动获取。

        Returns:
            True=启动成功, False=失败
        """
        if self._running:
            return True

        try:
            self._loop = loop or asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        # 注入 transport 引用到 handler
        AIMHTTPHandler.transport = self

        try:
            self._server = HTTPServer((self.host, self.port), AIMHTTPHandler)
        except OSError as e:
            log.error(f"[HTTP:{self.agent_id}] 端口 {self.port} 被占用: {e}")
            return False

        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._running = True
        log.info(f"[HTTP:{self.agent_id}] Server started on http://{self.host}:{self.port}")
        return True

    def stop_server(self):
        """停止 HTTP 服务端"""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self._running = False
        log.info(f"[HTTP:{self.agent_id}] Server stopped")

    def set_message_handler(self, handler: Callable):
        """设置消息回调（收到 POST /aim/message 时调用）

        Args:
            handler: async Callable(dict) — 接收完整消息信封 dict
        """
        self._msg_handler = handler

    # ── 客户端（发送消息） ────────────────────────────────────────────

    def _get_peer_url(self, peer_id: str) -> Optional[str]:
        """获取对端 HTTP 端点 URL"""
        port = self.peer_ports.get(peer_id) or DEFAULT_HTTP_PORTS.get(peer_id)
        if not port:
            return None
        return f"http://{self.host}:{port}/aim/message"

    def send_message(self, envelope: dict, peer_id: str) -> bool:
        """通过 HTTP POST 发送消息信封

        Args:
            envelope: AIM 消息信封 dict
            peer_id: 目标 Agent ID

        Returns:
            True=发送成功, False=失败
        """
        url = self._get_peer_url(peer_id)
        if not url:
            log.warning(f"[HTTP:{self.agent_id}] 未知对端端口: {peer_id}")
            return False

        try:
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            req = Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-AIM-From": self.agent_id,
                    "X-AIM-Version": "1.0",
                },
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("status") == "ok":
                    return True
                log.warning(f"[HTTP] 对端返回异常: {result}")
                return False
        except URLError as e:
            log.warning(f"[HTTP] 发送到 {peer_id} 失败: {e.reason}")
            return False
        except Exception as e:
            log.warning(f"[HTTP] 发送到 {peer_id} 异常: {e}")
            return False

    def send_dm(self, to_id: str, text: str, reply_to: str = "") -> bool:
        """发送私聊消息（HTTP 降级模式）

        Args:
            to_id: 目标 Agent ID
            text: 消息内容
            reply_to: 回复的消息 ID（可选）

        Returns:
            True=发送成功, False=失败
        """
        envelope = {
            "ver": "1.0",
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "from": self.agent_id,
            "type": "dm",
            "payload": {"text": text},
        }
        if reply_to:
            envelope["meta"] = {"reply_to": reply_to}
        return self.send_message(envelope, to_id)

    def send_grp(self, group_id: str, text: str, to_members: list[str]) -> dict[str, bool]:
        """发送群聊消息（HTTP 降级模式 — 逐个发送）

        Args:
            group_id: 群 ID
            text: 消息内容
            to_members: 群成员 Agent ID 列表

        Returns:
            {peer_id: success} 发送结果字典
        """
        envelope = {
            "ver": "1.0",
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "from": self.agent_id,
            "type": "grp",
            "payload": {"text": text, "group_id": group_id},
        }
        results = {}
        for member in to_members:
            if member == self.agent_id:
                continue
            results[member] = self.send_message(envelope, member)
        return results

    def health_check(self, peer_id: str) -> bool:
        """HTTP 健康检查（GET /aim/health）

        Args:
            peer_id: 目标 Agent ID

        Returns:
            True=健康, False=不可用
        """
        port = self.peer_ports.get(peer_id) or DEFAULT_HTTP_PORTS.get(peer_id)
        if not port:
            return False
        url = f"http://{self.host}:{port}/aim/health"
        try:
            with urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok"
        except Exception:
            return False

    def is_running(self) -> bool:
        return self._running
