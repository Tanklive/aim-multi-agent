#!/usr/bin/env python3
"""
AIM 密钥管理工具
================
用于生成、存储和加载HMAC-SHA256密钥

用法：
  python3 secrets.py generate ZS0001  # 为ZS0001生成密钥
  python3 secrets.py load ZS0001      # 加载ZS0001的密钥
  python3 secrets.py list             # 列出所有密钥
"""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
SECRETS_DIR = BASE_DIR / "aim_secrets_store"
SECRETS_DIR.mkdir(exist_ok=True)


def generate_secret(agent_id: str) -> str:
    """为指定Agent生成32字节随机密钥"""
    return secrets.token_hex(32)


def save_secret(agent_id: str, secret: str):
    """保存密钥到文件"""
    secret_file = SECRETS_DIR / f"{agent_id}.secret"
    with open(secret_file, 'w') as f:
        f.write(secret)
    # 设置权限为600（仅所有者可读写）
    os.chmod(secret_file, 0o600)
    print(f"✅ 密钥已保存: {secret_file}")


def load_secret(agent_id: str) -> str:
    """加载指定Agent的密钥"""
    secret_file = SECRETS_DIR / f"{agent_id}.secret"
    if not secret_file.exists():
        raise FileNotFoundError(f"密钥文件不存在: {secret_file}")
    with open(secret_file, 'r') as f:
        return f.read().strip()


def list_secrets():
    """列出所有密钥文件"""
    print("📋 已存储的密钥:")
    for secret_file in SECRETS_DIR.glob("*.secret"):
        agent_id = secret_file.stem
        print(f"  - {agent_id}: {secret_file}")


def main():
    parser = argparse.ArgumentParser(description="AIM 密钥管理工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # generate 命令
    gen_parser = subparsers.add_parser("generate", help="为指定Agent生成密钥")
    gen_parser.add_argument("agent_id", help="Agent ID (如 ZS0001)")
    
    # load 命令
    load_parser = subparsers.add_parser("load", help="加载指定Agent的密钥")
    load_parser.add_argument("agent_id", help="Agent ID (如 ZS0001)")
    
    # list 命令
    subparsers.add_parser("list", help="列出所有密钥")
    
    args = parser.parse_args()
    
    if args.command == "generate":
        secret = generate_secret(args.agent_id)
        save_secret(args.agent_id, secret)
        print(f"🔐 密钥: {secret}")
    elif args.command == "load":
        try:
            secret = load_secret(args.agent_id)
            print(f"🔐 密钥: {secret}")
        except FileNotFoundError as e:
            print(f"❌ {e}")
    elif args.command == "list":
        list_secrets()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()