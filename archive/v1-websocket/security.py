from typing import Optional
#!/usr/bin/env python3
"""
AIM 安全Phase2 — 密钥管理 + HMAC签名

用法:
    from security import SecurityManager
    
    sec = SecurityManager()
    sig = sec.generate_signature("ZS0001")
    ok = sec.verify_signature("ZS0001", timestamp, signature)
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path


class SecurityManager:
    """AIM 安全管理器 — HMAC-SHA256签名 + 时间戳防重放"""

    SECRETS_DIR = Path.home() / ".hermes" / "aim" / "secrets"
    TIMESTAMP_WINDOW = 60  # 认证时间戳窗口（秒）
    MESSAGE_TIMESTAMP_WINDOW = 120  # 消息时间戳窗口（秒）

    def __init__(self):
        self.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}  # agent_id -> secret（内存缓存）

    def _secret_path(self, agent_id: str) -> Path:
        return self.SECRETS_DIR / f"{agent_id}.secret"

    def load_secret(self, agent_id: str) -> Optional[str]:
        """加载密钥（优先缓存，其次文件）"""
        if agent_id in self._cache:
            return self._cache[agent_id]

        path = self._secret_path(agent_id)
        if not path.exists():
            return None

        secret = path.read_text().strip()
        self._cache[agent_id] = secret
        return secret

    def generate_secret(self, agent_id: str) -> str:
        """生成并保存密钥"""
        secret = secrets.token_hex(32)
        path = self._secret_path(agent_id)
        path.write_text(secret)
        os.chmod(path, 0o600)
        self._cache[agent_id] = secret
        return secret

    def ensure_secret(self, agent_id: str) -> str:
        """确保密钥存在，不存在则生成"""
        secret = self.load_secret(agent_id)
        if not secret:
            secret = self.generate_secret(agent_id)
        return secret

    def generate_signature(self, agent_id: str, timestamp: Optional[int] = None) -> tuple:
        """生成签名，返回 (timestamp, signature)"""
        secret = self.load_secret(agent_id)
        if not secret:
            raise ValueError(f"密钥不存在: {agent_id}")

        if timestamp is None:
            timestamp = int(time.time())

        message = f"{agent_id}:{timestamp}"
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return timestamp, signature

    def verify_signature(self, agent_id: str, timestamp: int, signature: str) -> bool:
        """验证签名"""
        secret = self.load_secret(agent_id)
        if not secret:
            return False

        # 1. 验证时间戳窗口
        if not self.verify_timestamp(timestamp):
            return False

        # 2. 验证签名
        message = f"{agent_id}:{timestamp}"
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def verify_timestamp(self, timestamp: int, window: Optional[int] = None) -> bool:
        """验证时间戳在允许窗口内"""
        if window is None:
            window = self.TIMESTAMP_WINDOW

        now = int(time.time())
        return abs(now - timestamp) <= window

    def build_auth_payload(self, agent_id: str) -> dict:
        """构建认证请求载荷"""
        timestamp, signature = self.generate_signature(agent_id)
        return {
            "cmd": "auth",
            "agent_id": agent_id,
            "timestamp": timestamp,
            "signature": signature,
        }

    def generate_message_signature(self, agent_id: str, msg_id: str, content: str, timestamp: Optional[int] = None) -> tuple:
        """生成消息签名，返回 (timestamp, signature)"""
        secret = self.load_secret(agent_id)
        if not secret:
            raise ValueError(f"密钥不存在: {agent_id}")

        if timestamp is None:
            timestamp = int(time.time())

        # 消息签名：agent_id:msg_id:timestamp:content_hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        message = f"{agent_id}:{msg_id}:{timestamp}:{content_hash}"
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return timestamp, signature

    def verify_message_signature(self, agent_id: str, msg_id: str, content: str, timestamp: int, signature: str) -> bool:
        """验证消息签名"""
        secret = self.load_secret(agent_id)
        if not secret:
            return False

        # 1. 验证时间戳窗口（消息用更宽松的窗口）
        if not self.verify_timestamp(timestamp, self.MESSAGE_TIMESTAMP_WINDOW):
            return False

        # 2. 验证签名
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        message = f"{agent_id}:{msg_id}:{timestamp}:{content_hash}"
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def rotate_secret(self, agent_id: str) -> str:
        """轮换密钥：备份旧密钥，生成新密钥"""
        import shutil
        
        old_path = self._secret_path(agent_id)
        if old_path.exists():
            # 备份旧密钥（带时间戳）
            backup_name = f"{agent_id}.secret.bak.{int(time.time())}"
            backup_path = self.SECRETS_DIR / backup_name
            shutil.copy2(old_path, backup_path)
            backup_path.chmod(0o600)
        
        # 生成新密钥
        new_secret = self.generate_secret(agent_id)
        
        # 清除缓存
        self._cache.pop(agent_id, None)
        
        return new_secret

    def get_secret_info(self, agent_id: str) -> dict:
        """获取密钥信息（不含明文）"""
        path = self._secret_path(agent_id)
        if not path.exists():
            return {"exists": False}
        
        stat = path.stat()
        return {
            "exists": True,
            "path": str(path),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "permissions": oct(stat.st_mode)[-3:],
        }


# ── 审计日志 ─────────────────────────────────────────

import logging

AUDIT_LOG_FILE = Path.home() / ".hermes" / "aim" / "logs" / "audit.log"

def _get_audit_logger() -> logging.Logger:
    """获取审计日志logger"""
    logger = logging.getLogger("aim.audit")
    if not logger.handlers:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [AUDIT] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def audit_auth(agent_id: str, success: bool, method: str, reason: str = ""):
    """记录认证事件"""
    logger = _get_audit_logger()
    status = "SUCCESS" if success else "FAIL"
    msg = f"AUTH {status} agent={agent_id} method={method}"
    if reason:
        msg += f" reason={reason}"
    logger.info(msg)


def audit_message(from_id: str, to_id: str, verified: bool, group: bool = False):
    """记录消息事件"""
    logger = _get_audit_logger()
    status = "VERIFIED" if verified else "UNVERIFIED"
    target_type = "GROUP" if group else "DM"
    logger.info(f"MSG {status} from={from_id} to={to_id} type={target_type}")


def audit_key_rotation(agent_id: str):
    """记录密钥轮换事件"""
    logger = _get_audit_logger()
    logger.info(f"KEY_ROTATE agent={agent_id}")


# 全局单例
_security_manager = None


def get_security_manager() -> SecurityManager:
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager
