import time
import json
import logging
from pathlib import Path
from negan_config import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Shane")

class Shane:
    COOLDOWN = 2700  # 45 minutes in seconds
    BATCH_SEND = 10   # Send 10 live proxies at a time to Lucille

    def __init__(self):
        # Load tracker for last positions
        if TRACKER_FILE.exists():
            try:
                self.tracker = json.load(open(TRACKER_FILE))
            except:
                self.tracker = {}
        else:
            self.tracker = {}

        self.TOTAL_LIMIT = MAX_TANK

    def _get_tank_count(self):
        total = 0
        if TANK_DIR.exists():
            for f in TANK_DIR.glob("*.txt"):
                try: total += len(open(f).read().splitlines())
                except: pass
        return total

    def check_proxy(self, proxy_ip):
        """
        Simple health check for a proxy
        """
        import requests
        try:
            proxies = {"http": f"http://{proxy_ip}", "https": f"http://{proxy_ip}"}
            r = requests.get(HEALTH_CHECK_URL, proxies=proxies, timeout=HEALTH_CHECK_TIMEOUT)
            return r.status_code == 200
        except:
            return False

    def perform_rounds(self):
        current_val = self._get_tank_count()
        needed = self.TOTAL_LIMIT - current_val
        if needed <= 0:
            logger.info("Tank full. Shane does not need to refill now.")
            return

        logger.info(f"Starting hospital rounds. Tank Level: {current_val}/{self.TOTAL_LIMIT}")

        # Tracker
        sh_data = self.tracker.setdefault("shane", {"room": 1, "line": 0})

        room_idx = sh_data["room"]
        line_idx = sh_data["line"]

        live_batch = []

        rooms_checked = 0
        while rooms_checked < 16:  # Max 16 rooms
            room_path = COOLING_DIR / f"room_{room_idx}.txt"
            if not room_path.exists():
                room_idx = (room_idx % 16) + 1
                line_idx = 0
                rooms_checked += 1
                continue

            try:
                lines = open(room_path, errors="ignore").read().splitlines()
            except:
                lines = []

            if line_idx >= len(lines):
                room_idx = (room_idx % 16) + 1
                line_idx = 0
                rooms_checked += 1
                continue

            raw_line = lines[line_idx]
            line_idx += 1

            try:
                proxy_ip, reason, ts = raw_line.split("|")
                ts = int(ts)
                now = int(time.time())

                # Skip if cooldown not passed
                if now - ts < self.COOLDOWN:
                    continue

                if self.check_proxy(proxy_ip):
                    live_batch.append(f"{proxy_ip}|0|{now}")
                else:
                    # keep dead proxy in hospital (no action)
                    continue

                # Send batch when BATCH_SEND reached
                if len(live_batch) >= self.BATCH_SEND:
                    self._send_to_lucille(live_batch)
                    live_batch.clear()

            except:
                continue

        # Send remaining batch if any
        if live_batch:
            self._send_to_lucille(live_batch)

        # Update tracker
        sh_data["room"] = room_idx
        sh_data["line"] = line_idx
        self.tracker["shane"] = sh_data
        with open(TRACKER_FILE, "w") as f:
            json.dump(self.tracker, f, indent=2)

        # Call Negan if hospital exhausted and tank not full
        if self._get_tank_count() < self.TOTAL_LIMIT:
            logger.info(f"Hospital exhausted or no ready proxies. Summoning Negan...")
            from negan import Negan
            Negan().supply(self.TOTAL_LIMIT - self._get_tank_count())

    def _send_to_lucille(self, proxies):
        """
        Inject live proxies back into Lucille
        """
        for proxy in proxies:
            for i in range(1, 4):
                unit_file = TANK_DIR / f"unit_{i}.txt"
                try:
                    count = len(open(unit_file).read().splitlines()) if unit_file.exists() else 0
                except:
                    count = 0

                if count < UNIT_CAPACITY:
                    with open(unit_file, "a") as f:
                        f.write(proxy + "\n")
                    break
        logger.info(f"Sent {len(proxies)} live proxies to Lucille")
