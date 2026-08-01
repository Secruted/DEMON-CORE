import time
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

# [CORE IMPORTS]
from maggie import Maggie
from shane import Shane
from router import Router
from statuses import Status
from negan_config import *

# [ANSI COLORS]
C_RED     = "\033[91m"
C_GREEN   = "\033[92m"
C_YELLOW  = "\033[93m"
C_BLUE    = "\033[94m"
C_CYAN    = "\033[96m"
C_RESET   = "\033[0m"

class Harvester:
    def __init__(self, strategy_cfg, runtime_cfg, db_mgr, content_parser, proxy_mgr):
        self.logger = logging.getLogger("HARVESTER")
        self.strategy = strategy_cfg
        self.runtime = runtime_cfg
        self.db = db_mgr
        self.parser = content_parser
        self.transport = self.parser.transport
        self.proxy_mgr = proxy_mgr

        # Routing
        self.router = Router("targets.txt")

        # Proxy Systems
        self.maggie = Maggie()
        self.shane = Shane()

        # Memory for Retreat Logic
        self.target_warnings = {}

        # Initialize Fuel
        self.current_fuel = self._get_real_fuel()

    def _get_real_fuel(self):
        count = 0
        if TANK_DIR.exists():
            for f in TANK_DIR.glob("*.txt"):
                try:
                    count += len(open(f).read().splitlines())
                except:
                    pass
        return count

    def _timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    # --- [KILL SWITCH] ---
    def _register_signals(self):
        def signal_handler(sig, frame):
            print(f"\n{C_RED}[!] CTRL+C DETECTED. SYSTEM HALTED.{C_RESET}")
            sys.exit(0)
        signal.signal(signal.SIGINT, signal_handler)

    def start_mission(self):
        self._register_signals()

        total_targets = self.router.get_route_count()
        self.current_fuel = self._get_real_fuel()
        sleep_time = self.runtime.get("sleep_interval", 0.1)

        if total_targets == 0:
            print(f"{self._timestamp()} | {C_YELLOW}WARNING{C_RESET}  | No targets found.")
            return

        print(f"{self._timestamp()} | {C_BLUE}INFO{C_RESET}     | [INIT] System X Engine Started. Targets: {total_targets}")

        try:
            for url in self.router.generator():
                self.current_fuel = self._get_real_fuel()

                # 1. Lifecycle Check (Shane & Negan)
                self._manage_lifecycle()

                # 2. Retreat Logic
                domain = self._extract_domain(url)
                if self.target_warnings.get(domain, 0) >= 2:
                    print(f"{self._timestamp()} | {C_YELLOW}SKIP{C_RESET}     | [RETREAT] Skipping {domain} (Hostile)")
                    continue

                # 3. Visual Log (SCAN)
                print(f"{self._timestamp()} | {C_CYAN}SCAN{C_RESET}     | Evaluating: {url}")

                # 4. Execution
                self._execute_strike(url, domain)

                time.sleep(sleep_time)

        except KeyboardInterrupt:
            print(f"\n{self._timestamp()} | {C_YELLOW}STOP{C_RESET}     | Mission Paused.")

    def _manage_lifecycle(self):
        # Call Shane if fuel is low
        if self.current_fuel <= TRIGGER_REFILL:
            needed = MAX_TANK - self.current_fuel
            self.shane.perform_rounds(needed)

        # Emergency (tank empty)
        if self.current_fuel == 0:
            print(f"{self._timestamp()} | {C_RED}CRITICAL{C_RESET} | Tank Empty. Emergency Call to Shane...")
            self.shane.perform_rounds(MAX_TANK)
            self.current_fuel = self._get_real_fuel()

    def _execute_strike(self, url, domain):
        status, findings = self.parser.process_url(url)

        # [FAIL] -> Immediate medical evacuation
        if status == Status.FAILED_FETCH:
            try:
                dead_proxy = self.transport.current_proxy
                if dead_proxy:
                    # Evacuation process (from tank -> hospital)
                    self._evacuate_casualty(dead_proxy)

                    p_display = dead_proxy.split('|')[0]
                    # Show (Evacuated) to confirm it was not permanently deleted but moved
                    print(f"{self._timestamp()} | {C_RED}FAIL{C_RESET}     | [DIED] {p_display} -> Evacuated to Hospital")
            except:
                pass

        # [SUCCESS]
        elif status == Status.HARVEST_SUCCESS:
            print(f"{self._timestamp()} | {C_GREEN}SUCCESS{C_RESET}  | [HIT] Assets Secured!")
            for item_type, value in findings:
                val_short = value[:50] + "..." if len(value) > 50 else value
                print(f"             └── {C_YELLOW}{item_type}{C_RESET}: {val_short}")
                try:
                    self.db.save_leaked_asset(url, item_type, value)
                except:
                    self._save_to_file(url, item_type, value)
            self.target_warnings[domain] = 0

        # [WAF/BLOCK]
        elif status in [Status.BLOCKED, Status.WAF_DETECTED]:
            self.target_warnings[domain] = self.target_warnings.get(domain, 0) + 1
            strikes = self.target_warnings[domain]
            print(f"{self._timestamp()} | {C_YELLOW}WARN{C_RESET}     | [WAF] Defense Detected ({strikes}/2).")
            if strikes >= 2:
                print(f"{self._timestamp()} | {C_YELLOW}RETREAT{C_RESET}  | [TACTICAL] Disengaging from {domain}.")

    def _evacuate_casualty(self, proxy_str):
        """
        Evacuation process:
        1. Remove from tank (to clean the field).
        2. Insert into hospital (for later recovery).
        """
        proxy_clean = proxy_str.split('|')[0].strip()
        timestamp = int(time.time())

        # 1. Remove from tank
        removed = False
        for unit_file in TANK_DIR.glob("*.txt"):
            try:
                lines = open(unit_file).read().splitlines()
                original_len = len(lines)
                # Keep only proxies that do not match the dead one
                new_lines = [line for line in lines if proxy_clean not in line]

                if len(new_lines) < original_len:
                    with open(unit_file, 'w') as f:
                        f.write('\n'.join(new_lines) + '\n')
                    removed = True
                    break  # Enough to delete from one file
            except:
                pass

        # 2. Insert into hospital (room 12 - emergency)
        # Format: IP|Timestamp|Failures
        current_fails = 1
        try:
            parts = proxy_str.split('|')
            if len(parts) > 2:
                current_fails = int(parts[2]) + 1
        except:
            pass

        hospital_entry = f"{proxy_clean}|{timestamp}|{current_fails}"

        try:
            with open(COOLING_DIR / "room_12.txt", "a") as f:
                f.write(hospital_entry + "\n")
        except:
            pass

    def _extract_domain(self, url):
        try:
            return url.split('/')[2]
        except:
            return "unknown"

    def _save_to_file(self, url, item_type, value):
        with open("loots.txt", "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {item_type} | {url} | {value}\n")
