import time
import logging
import asyncio
from datetime import datetime
from pathlib import Path

from router import Router
from statuses import Status
from negan_config import *

C_RED, C_GREEN, C_YELLOW, C_BLUE, C_CYAN, C_RESET = "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[96m", "\033[0m"

class Harvester:
    def __init__(self, strategy_cfg, runtime_cfg, db_mgr, content_parser):
        self.logger = logging.getLogger("HARVESTER")
        self.strategy = strategy_cfg
        self.runtime = runtime_cfg
        self.db = db_mgr
        self.parser = content_parser
        self.transport = self.parser.transport

        self.router = Router("targets.txt")
        self.target_warnings = {}
        self.CONCURRENCY_LIMIT = 300   # Slightly reduced for hybrid stability

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    async def _worker(self, queue):
        while True:
            url = await queue.get()
            try:
                domain = self._extract_domain(url)
                if self.target_warnings.get(domain, 0) < 3:
                    await self._execute_strike(url, domain)
            except Exception:
                pass
            finally:
                queue.task_done()

    async def start_mission(self):
        total_targets = self.router.get_route_count()
        if total_targets == 0:
            print(f"{self._timestamp()} | {C_YELLOW}WARNING{C_RESET} | No targets found.")
            return

        print(f"{self._timestamp()} | {C_RED}HYBRID ENGINE{C_RESET} | Starting swarm ({self.CONCURRENCY_LIMIT} workers)...")

        # Initialize hybrid transport
        if hasattr(self.transport, "init"):
            await self.transport.init()
        elif hasattr(self.transport, "init_session"):
            await self.transport.init_session()

        queue = asyncio.Queue()
        workers = [asyncio.create_task(self._worker(queue)) for _ in range(self.CONCURRENCY_LIMIT)]

        for url in self.router.generator():
            await queue.put(url)

        await queue.join()

        for w in workers:
            w.cancel()

        if hasattr(self.transport, "close"):
            await self.transport.close()
        elif hasattr(self.transport, "close_session"):
            await self.transport.close_session()

        print(f"{self._timestamp()} | {C_BLUE}INFO{C_RESET} | Mission complete.")

    async def _execute_strike(self, url, domain):
        status, findings = await self.parser.process_url(url)

        if status == Status.HARVEST_SUCCESS:
            print(f"{self._timestamp()} | {C_GREEN}HIT{C_RESET} | {url}")
            for item_type, value in findings:
                val_short = value[:50] + "..." if len(value) > 50 else value
                print(f"             └── {C_YELLOW}{item_type}{C_RESET}: {val_short}")
                try:
                    await self.db.add_leak(item_type, value, url)
                except Exception:
                    await asyncio.to_thread(self._save_to_file, url, item_type, value)
            self.target_warnings[domain] = 0

        elif status in [Status.BLOCKED, Status.WAF_DETECTED]:
            self.target_warnings[domain] = self.target_warnings.get(domain, 0) + 1

    def _save_to_file(self, url, item_type, value):
        with open("loots.txt", "a", encoding="utf-8") as f:
            f.write(f"[{self._timestamp()}] {item_type} | {url} | {value}\n")

    def _extract_domain(self, url):
        try:
            return url.split("/")[2]
        except:
            return "unknown"
