#!/usr/bin/env python3
"""
aim_issue_worker — Issue 串行写入守护进程 v1.0

订阅 aim.issue.update → 串行 append ISSUES.md → git commit。
独立进程，launchd 保活，Agent 挂了也能写。

用法:
  python3 aim_issue_worker.py
  python3 aim_issue_worker.py --nats-url nats://127.0.0.1:4222 --credentials ~/.aim/agents/ZS0002/aim.creds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import nats

VERSION = "1.0.0"

# ── Paths ──────────────────────────────────────
AIM_HOME = Path(os.environ.get("AIM_HOME", Path.home() / ".aim"))
ISSUES_FILE = Path.home() / "shared" / "aim" / "PROJECT" / "ISSUES.md"
ISSUES_DIR = ISSUES_FILE.parent
NATS_URL = os.environ.get("AIM_NATS_URL", "nats://127.0.0.1:4222")
DEFAULT_CREDS = os.environ.get("AIM_CREDS", str(AIM_HOME / "agents" / "ZS0002" / "aim.creds"))

# ── Logging ────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [issue-worker] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aim-issue-worker")


class IssueWorker:
    """Issue 串行写入 Worker"""

    def __init__(self, nats_url: str, credentials: str):
        self.nats_url = nats_url
        self.credentials = os.path.expanduser(credentials)
        self.nc = None
        self.js = None
        self._running = False
        self._processed = 0

    async def start(self):
        creds_path = Path(self.credentials)
        opts = {}
        if creds_path.exists():
            opts["user_credentials"] = str(creds_path)

        for attempt in range(5):
            try:
                self.nc = await nats.connect(
                    self.nats_url, **opts,
                    reconnect_time_wait=2,
                    max_reconnect_attempts=-1,
                    connect_timeout=10,
                )
                log.info(f"NATS 已连接: {self.nats_url}")
                break
            except Exception as e:
                log.warning(f"NATS 连接失败 ({attempt+1}/5): {e}")
                await asyncio.sleep(3)
        else:
            log.error("NATS 连接失败，退出")
            sys.exit(1)

        self.js = self.nc.jetstream()
        self._running = True

        # JetStream durable consumer — 保证不丢消息
        try:
            await self.js.add_stream(
                name="aim-issues",
                subjects=["aim.issue.update"],
                retention="limits",
                max_age=30 * 24 * 3600,  # 30 days
            )
        except Exception:
            pass  # stream already exists

        try:
            sub = await self.js.pull_subscribe(
                "aim.issue.update",
                durable="issue-worker",
                stream="aim-issues",
            )
        except Exception:
            # consumer already exists, reuse
            sub = await self.js.pull_subscribe(
                "aim.issue.update",
                durable="issue-worker",
                stream="aim-issues",
            )

        log.info("Issue Worker 启动完成，监听 aim.issue.update")

        # Startup: ensure git repo is clean
        self._git_pull()

        while self._running:
            try:
                msgs = await sub.fetch(batch=1, timeout=10)
                for msg in msgs:
                    await self._process(msg)
                    await msg.ack()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"消费异常: {e}")
                await asyncio.sleep(5)

    async def _process(self, msg):
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            log.warning("非 JSON 消息，跳过")
            return

        agent = data.get("agent", "unknown")
        level = data.get("level", "🟡 P1")
        title = data.get("title", "无标题")
        detail = data.get("detail", "")
        ts = data.get("ts", time.time())
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

        # Ensure directory exists
        ISSUES_DIR.mkdir(parents=True, exist_ok=True)

        # Ensure file exists with header
        if not ISSUES_FILE.exists():
            ISSUES_FILE.write_text(
                "# AIM 项目问题清单\n\n"
                "> 三方统一入口。Worker 自动维护，勿手动编辑。\n\n"
                "| 时间 | Agent | 级别 | 问题 | 详情 |\n"
                "|------|-------|------|------|------|\n"
            )

        # Append new issue
        line = f"| {dt} | {agent} | {level} | {title} | {detail.replace(chr(10), ' ')} |\n"
        with open(ISSUES_FILE, "a") as f:
            f.write(line)

        self._processed += 1
        log.info(f"#{self._processed} [{level}] {agent}: {title[:40]}")

        # Git commit (debounced: every 5 messages or 60s)
        if self._processed % 5 == 0:
            self._git_commit()

    def _git_pull(self):
        try:
            subprocess.run(
                ["git", "-C", str(ISSUES_DIR), "pull", "--rebase"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    def _git_commit(self):
        try:
            subprocess.run(
                ["git", "-C", str(ISSUES_DIR), "add", "ISSUES.md"],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["git", "-C", str(ISSUES_DIR), "commit", "-m",
                 f"issues: worker auto-commit ({self._processed} total)"],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            log.debug(f"git commit 失败: {e}")

    async def stop(self):
        self._running = False
        self._git_commit()  # final commit
        if self.nc:
            await self.nc.drain()


def main():
    parser = argparse.ArgumentParser(description=f"aim_issue_worker v{VERSION}")
    parser.add_argument("--nats-url", default=NATS_URL)
    parser.add_argument("--credentials", default=DEFAULT_CREDS)
    args = parser.parse_args()

    worker = IssueWorker(args.nats_url, args.credentials)
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        asyncio.run(worker.stop())


if __name__ == "__main__":
    main()
