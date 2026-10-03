import getpass
import json
import sys
import time
import logging
from pathlib import Path
from db_manager import DBManager
from telegram_notifier import TelegramNotifier

# --- Logging Configuration for the Messenger ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - [MESSENGER] %(message)s',
    handlers=[
        logging.FileHandler("messenger.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Lightweight alert side-channel used by remote_orchestrator
ALERT_LOG = Path("alerts_dispatch.log")


def dispatch_alert(message_text: str) -> None:
    """
    Synchronous alert broadcaster for remote_orchestrator and other units.
    Writes to a durable log and attempts best-effort Telegram delivery
    when config.json + credentials are available.
    """
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ALERT_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {message_text}\n")
        logger.info("Alert dispatched to log: %s", message_text[:120])
    except Exception as e:
        logger.error("dispatch_alert log write failed: %s", e)

    # Best-effort Telegram push (non-blocking for caller)
    try:
        if Path("config.json").exists():
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token = cfg.get("telegram_bot_token")
            chat_id = cfg.get("telegram_chat_id")
            if token and chat_id:
                notifier = TelegramNotifier(token=token, chat_id=chat_id)
                notifier.send_message(f"[DEMON-CORE ALERT]\n{message_text}")
    except Exception as e:
        logger.debug("Telegram dispatch skipped or failed: %s", e)


def load_config():
    """Loads the configuration file."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.critical(f"Failed to load or parse config.json: {e}. Exiting.")
        sys.exit(1)

def main():
    """Main function to run the Messenger service."""
    logger.info("--- Messenger Service Initializing ---")
    config = load_config()
    
    try:
        master_password = getpass.getpass(prompt='Enter Master Password to access Armory: ')
        if not master_password:
            logger.critical("Master password cannot be empty. Exiting.")
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        logger.info("\nPassword entry cancelled. Exiting.")
        sys.exit(0)

    try:
        db = DBManager(db_path=config.get("db_path", "watcher.db"), password=master_password)
        notifier = TelegramNotifier(token=config.get("telegram_bot_token"), chat_id=config.get("telegram_chat_id"))
    except Exception as e:
        logger.critical(f"Failed to initialize modules. Check password/config. Error: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Messenger is active. Monitoring pending alerts queue...")

    while True:
        try:
            # Note: get_pending_alerts / delete_pending_alert must be implemented on DBManager
            # when full encrypted alert queue is required. Current stub keeps service alive.
            pending_alerts = []
            if hasattr(db, "get_pending_alerts"):
                pending_alerts = db.get_pending_alerts()
            
            if not pending_alerts:
                logger.info("No pending alerts found. Waiting for next cycle.")
            else:
                logger.info(f"Found {len(pending_alerts)} pending alerts to dispatch.")
                for alert_id, decrypted_message in pending_alerts:
                    if "[DECRYPTION_ERROR]" in decrypted_message:
                        logger.error(f"Failed to decrypt alert ID {alert_id}. Deleting malformed alert.")
                        if hasattr(db, "delete_pending_alert"):
                            db.delete_pending_alert(alert_id)
                        continue
                    
                    if notifier.send_message(decrypted_message):
                        logger.info(f"  [SUCCESS] Alert ID {alert_id} dispatched.")
                        if hasattr(db, "delete_pending_alert"):
                            db.delete_pending_alert(alert_id)
                    else:
                        logger.warning(f"  [WARNING] Failed to dispatch alert ID {alert_id}. Will retry next cycle.")
            
            # Sleep for the messenger's specific interval
            time.sleep(config.get('messenger_interval', 120)) 

        except KeyboardInterrupt:
            logger.info("\nShutdown signal received. Exiting.")
            if hasattr(db, "close"):
                db.close()
            sys.exit(0)
        except Exception as e:
            logger.critical(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            if hasattr(db, "close"):
                db.close()
            time.sleep(60)

if __name__ == "__main__":
    main()
