import getpass
import json
import logging
import sys
import time
import signal
import asyncio
from pathlib import Path

# [Imports for Core Components]
from db_manager import DBManager
from proxy_manager import ProxyManager
from transport_manager import TransportManager
from content_parser import ContentParser
from harvester import Harvester

# [Proxy System Components]
from negan_config import *

# [ANSI Colors]
C_GREEN = "\033[92m"
C_CYAN = "\033[96m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_RESET = "\033[0m"

START_TIME = time.time()

# ===================== [KILL SWITCH] =====================

def force_exit(signum, frame):
    print(f"\n\n{C_RED}[!] FORCE STOP TRIGGERED (SIGINT). SYSTEM HALTED.{C_RESET}")
    sys.exit(0)

# ===================== [LOGGING] =====================

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%m-%d %H:%M:%S"
    )

# ===================== [LAYERS] =====================

def load_layer(filename):
    try:
        if not Path(filename).exists():
            with open(filename, "w") as f:
                json.dump({}, f)
        with open(filename, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"[FATAL] Layer Integrity Error ({filename}): {e}")
        sys.exit(1)

# ===================== [PROXY TELEMETRY] =====================

def count_real_tank():
    count = 0
    tank_dir = Path("tank")
    if tank_dir.exists():
        for f in tank_dir.glob("*.txt"):
            try:
                count += len(open(f).read().splitlines())
            except:
                pass
    return count

def load_tracker():
    if TRACKER_FILE.exists():
        try:
            return json.load(open(TRACKER_FILE))
        except:
            return {}
    return {}

def count_hospital():
    total = 0
    if COOLING_DIR.exists():
        for f in COOLING_DIR.glob("room_*.txt"):
            try:
                total += len(open(f).read().splitlines())
            except:
                pass
    return total

# ===================== [LIVE STATUS PANEL] =====================

def live_panel(logger):
    uptime = int(time.time() - START_TIME)
    tank = count_real_tank()
    hospital = count_hospital()
    tracker = load_tracker()

    shane = tracker.get("shane", {})
    negan = tracker.get("negan", {})

    logger.info(
        f"{C_CYAN}[LIVE]{C_RESET} "
        f"UP={uptime}s | "
        f"TANK={tank}/{MAX_TANK} | "
        f"HOSPITAL={hospital} | "
        f"SHANE(room={shane.get('room','-')},line={shane.get('line','-')}) | "
        f"NEGAN(file={Path(negan.get('file','-')).name if negan.get('file') else '-'})"
    )

# ===================== [MAIN CORE] =====================

async def main():
    # Register Kill Switch
    signal.signal(signal.SIGINT, force_exit)

    setup_logging()
    logger = logging.getLogger("ORCHESTRATOR")

    strategy = load_layer("strategy.json")
    intel = load_layer("intelligence.json")
    transport_cfg = load_layer("transport.json")
    runtime = load_layer("runtime.json")

    logger.info("=== SYSTEM CORE: LAYERED BOOTSTRAP INITIATED ===")

    try:
        master_pwd = getpass.getpass(prompt="Enter Master Password: ")
    except:
        master_pwd = "123"

    print(f"\n💦 🪼 🦠 {C_GREEN}SYSTEM CORE ONLINE (ULTRA 911 ASYNC){C_RESET} 🦠 🪼 💦\n")

    try:
        db = DBManager("watcher.db", master_pwd)
        await db.init_db()   # Initialize async aiosqlite connection + encryption

        proxy_mgr = ProxyManager(transport_cfg.get("proxy_file", "proxy.txt"))
        transport_mgr = TransportManager(transport_cfg)

        real_fuel = count_real_tank()

        parser = ContentParser(intel, transport_mgr)
        harvester = Harvester(strategy, runtime, db, parser, proxy_mgr)

        logger.info(f"| [INIT] Proxy Tank Level: {real_fuel} live proxies.")
        logger.info("| [INIT] Intelligence Core Online.")
        logger.info("| [INIT] Async Encrypted Vault Online (aiosqlite).")
        logger.info("| [INIT] Harvester Connected (Async Swarm Mode).")
        logger.info("=== MISSION STARTED ===")

        # Run the async swarm mission
        await harvester.start_mission()

        logger.info("=== MISSION COMPLETE ===")

        # Graceful shutdown
        await db.close()

    except Exception as e:
        logger.critical(f"[FATAL] System failure: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{C_RED}[!] System halted by user.{C_RESET}")
