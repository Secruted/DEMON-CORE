import getpass
import json
import sys
import logging
import time
import asyncio
from db_manager import DBManager

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - [NEXUS] %(message)s',
    handlers=[
        logging.FileHandler("nexus.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("NEXUS")

def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("config.json not found. Using defaults.")
        return {
            "db_path": "watcher.db",
            "messenger_interval": 120
        }
    except json.JSONDecodeError as e:
        logger.critical(f"Failed to parse config.json: {e}")
        sys.exit(1)

async def main():
    logger.info("--- Nexus Intelligence Analyzer Initializing ---")
    config = load_config()
    db_path = config.get("db_path", "watcher.db")

    try:
        master_password = getpass.getpass(prompt='Enter Master Password to decrypt Armory: ')
        if not master_password:
            logger.critical("Master password cannot be empty. Exiting.")
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        logger.info("Password entry cancelled. Exiting.")
        sys.exit(0)

    db = None
    try:
        db = DBManager(db_path, master_password)
        await db.init_db()
        logger.info("Encrypted Armory connection established.")

        logger.info("Scanning vault for intelligence...")

        # Since we converted to aiosqlite, we use async methods
        # For now we focus on reading leaks (the main harvest result)
        try:
            async with db.conn.execute("SELECT leak_type, encrypted_value, source, timestamp FROM leaks ORDER BY timestamp DESC LIMIT 100") as cursor:
                rows = await cursor.fetchall()

            if not rows:
                logger.info("The Armory is empty. No intelligence to report.")
            else:
                print("\n" + "="*50)
                print("           NEXUS INTELLIGENCE REPORT")
                print("="*50)

                for row in rows:
                    leak_type, encrypted_value, source, ts = row
                    try:
                        decrypted = db._decrypt(encrypted_value)
                        if decrypted == "[DECRYPTION_ERROR]":
                            continue
                        print(f"\n[+] Type    : {leak_type}")
                        print(f"    Value   : {decrypted[:80]}{'...' if len(decrypted) > 80 else ''}")
                        print(f"    Source  : {source}")
                    except Exception:
                        continue

                print("\n" + "="*50)
                logger.info(f"Report finished. Total records shown: {len(rows)}")

        except Exception as e:
            logger.error(f"Failed to read from vault: {e}")

    except Exception as e:
        logger.critical(f"Failed to initialize Nexus: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if db:
            await db.close()
            logger.info("Armory connection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Nexus halted by user.")
