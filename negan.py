import glob
import json
import time
from pathlib import Path
from negan_config import *
import requests
import logging

logger = logging.getLogger("Negan")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Negan:
    """
    Negan: Smart Proxy Supplier
    Supplies Lucille tank from Warehouse when Shane cannot find ready proxies.
    """
    def __init__(self):
        if TRACKER_FILE.exists():
            try:
                self.tracker = json.load(open(TRACKER_FILE))
            except:
                self.tracker = {}
        else:
            self.tracker = {}

        self.UNIT_LIMIT = UNIT_CAPACITY
        self.TOTAL_LIMIT = MAX_TANK

    def check_proxy(self, proxy_ip):
        """Check if proxy is alive"""
        try:
            proxies = {"http": f"http://{proxy_ip}", "https": f"http://{proxy_ip}"}
            r = requests.get(HEALTH_CHECK_URL, proxies=proxies, timeout=HEALTH_CHECK_TIMEOUT)
            return r.status_code == 200
        except:
            return False

    def supply(self, amount_needed):
        """
        Fill Lucille tank from Warehouse smartly
        """
        current_total = self._get_tank_count()
        if current_total >= self.TOTAL_LIMIT:
            logger.info("Lucille tank full. No supply needed.")
            return

        real_needed = self.TOTAL_LIMIT - current_total
        logger.info(f"Negan Engine Started. Need {real_needed} proxies.")

        wh_data = self.tracker.setdefault("negan", {"file": None, "idx": 0})
        files = sorted(glob.glob(str(WAREHOUSE_DIR / WAREHOUSE_PATTERN)))

        if not files:
            logger.warning("Warehouse empty. Cannot supply proxies.")
            return

        # Use first file if none selected
        if not wh_data["file"] or wh_data["file"] not in files:
            wh_data["file"] = files[0]
            wh_data["idx"] = 0

        current_file = wh_data["file"]

        try:
            all_lines = open(current_file, errors="ignore").read().splitlines()
            start = wh_data["idx"]

            if start >= len(all_lines):
                # Move to next file if available
                idx_current_file = files.index(current_file)
                if idx_current_file + 1 < len(files):
                    wh_data["file"] = files[idx_current_file + 1]
                    wh_data["idx"] = 0
                    self.tracker["negan"] = wh_data
                    with open(TRACKER_FILE, "w") as f:
                        json.dump(self.tracker, f, indent=2)
                    return self.supply(real_needed)
                else:
                    logger.info("Reached end of warehouse files.")
                    return

            chunk = all_lines[start: start + WAREHOUSE_CHUNK]
            supplied = 0

            for raw_proxy in chunk:
                if supplied >= real_needed:
                    break

                proxy_ip = raw_proxy.strip()
                if ":" not in proxy_ip:
                    continue

                # Check proxy
                if self.check_proxy(proxy_ip):
                    identity = f"{proxy_ip}|0|{int(time.time())}"
                    if self._inject_to_lucille(identity):
                        supplied += 1
                else:
                    identity = f"{proxy_ip}|1|{int(time.time())}"
                    self._send_to_hospital(identity)

            wh_data["idx"] += len(chunk)
            self.tracker["negan"] = wh_data
            with open(TRACKER_FILE, "w") as f:
                json.dump(self.tracker, f, indent=2)

            logger.info(f"Negan batch finished. Supplied {supplied} proxies.")

        except Exception as e:
            logger.error(f"Negan error: {e}")

    def _get_tank_count(self):
        total = 0
        if TANK_DIR.exists():
            for f in TANK_DIR.glob("*.txt"):
                try: total += len(open(f).read().splitlines())
                except: pass
        return total

    def _inject_to_lucille(self, identity_string):
        """
        Inject proxy into Lucille sequentially
        """
        for i in range(1, 4):
            unit_file = TANK_DIR / f"unit_{i}.txt"
            try:
                count = len(open(unit_file).read().splitlines()) if unit_file.exists() else 0
            except:
                count = 0

            if count < self.UNIT_LIMIT:
                with open(unit_file, "a") as f:
                    f.write(identity_string + "\n")
                return True
        return False

    def _send_to_hospital(self, identity_string):
        """
        Send dead proxies directly to Hospital Room 16
        """
        room_path = COOLING_DIR / "room_16.txt"
        with open(room_path, "a") as f:
            f.write(identity_string + "\n")
