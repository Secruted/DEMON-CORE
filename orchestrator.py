import getpass
import json
import logging
import sys
import time
import signal
import asyncio
from pathlib import Path

from db_manager import DBManager
from transport_manager import TransportManager
from content_parser import ContentParser
from harvester import Harvester
from negan_config import *

C_GREEN = "\033[92m"
C_CYAN = "\033[96m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_RESET = "\033[0m"

START_TIME = time.time()

def force_exit(signum, frame):
    print(f"\n\n{C_RED}[!] FORCE STOP TRIGGERED (SIGINT). SYSTEM HALTED.{C_RESET}")
    sys.exit(0)

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%m-%d %H:%M:%S"
    )

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

async def main():
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

    print(f"\n💦 🪼 🦠 {C_GREEN}SYSTEM CORE ONLINE (ULTRA 911 + SMART SCORING){C_RESET} 🦠 🪼 💦\n")

    try:
        db = DBManager("watcher.db", master_pwd)
        await db.init_db()

        transport_mgr = TransportManager(transport_cfg)

        # Show Scorer status
        summary = await transport_mgr.scorer.get_stats_summary()
        logger.info(f"| [INIT] ProxyScorer Online → Total: {summary['total']} | Alive: {summary['alive']} | Avg Score: {summary['avg_score']}")

        parser = ContentParser(intel, transport_mgr)
        harvester = Harvester(strategy, runtime, db, parser, None)

        logger.info("| [INIT] Intelligence Core Online.")
        logger.info("| [INIT] Async Encrypted Vault Online.")
        logger.info("| [INIT] Harvester Connected (Async Swarm + Smart Proxy Scoring).")
        logger.info("=== MISSION STARTED ===")

        await harvester.start_mission()

        logger.info("=== MISSION COMPLETE ===")
        await db.close()

    except Exception as e:
        logger.critical(f"[FATAL] System failure: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{C_RED}[!] System halted by user.{C_RESET}")
