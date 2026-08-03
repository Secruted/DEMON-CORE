import getpass
import json
import logging
import sys
import signal
import asyncio
from pathlib import Path

from db_manager import DBManager
from hybrid_transport import HybridTransport
from content_parser import ContentParser
from harvester import Harvester

C_GREEN = "\033[92m"
C_CYAN = "\033[96m"
C_RED = "\033[91m"
C_RESET = "\033[0m"

def force_exit(signum, frame):
    print(f"\n\n{C_RED}[!] FORCE STOP.{C_RESET}")
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
        logging.critical(f"Failed to load {filename}: {e}")
        sys.exit(1)

async def main():
    signal.signal(signal.SIGINT, force_exit)
    setup_logging()
    logger = logging.getLogger("ORCHESTRATOR")

    strategy = load_layer("strategy.json")
    intel = load_layer("intelligence.json")
    runtime = load_layer("runtime.json")

    logger.info("=== DEMON CORE | HYBRID ENGINE ===")

    try:
        master_pwd = getpass.getpass(prompt="Enter Master Password: ")
    except:
        master_pwd = "123"

    print(f"\n{C_GREEN}SYSTEM ONLINE - HYBRID MODE (curl_cffi → Playwright){C_RESET}\n")

    try:
        db = DBManager("watcher.db", master_pwd)
        await db.init_db()

        transport = HybridTransport(proxy_file="proxy.txt")
        await transport.init()

        parser = ContentParser(intel, transport)
        harvester = Harvester(strategy, runtime, db, parser)

        logger.info("| Hybrid Transport ready (curl_cffi + Playwright escalation)")
        logger.info("| Encrypted Vault online")
        logger.info("=== MISSION STARTED ===")

        await harvester.start_mission()

        await db.close()
        await transport.close()
        logger.info("=== MISSION COMPLETE ===")

    except Exception as e:
        logger.critical(f"Fatal: {e}", exp_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{C_RED}Halted by user.{C_RESET}")
