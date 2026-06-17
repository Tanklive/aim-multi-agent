#!/usr/bin/env python3
"""
直接运行 AIM NATS 端到端测试（绕过 pytest-asyncio 兼容性问题）
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aim_nats.test_e2e import main

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
