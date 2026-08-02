import getpass
import json
import sys
import time
import logging
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
            pending_alerts = db.get_pending_alerts()
            
            if not pending_alerts:
                logger.info("No pending alerts found. Waiting for next cycle.")
            else:
                logger.info(f"Found {len(pending_alerts)} pending alerts to dispatch.")
                for alert_id, decrypted_message in pending_alerts:
                    if "[DECRYPTION_ERROR]" in decrypted_message:
                        logger.error(f"Failed to decrypt alert ID {alert_id}. Deleting malformed alert.")
                        db.delete_pending_alert(alert_id)
                        continue
                    
                    if notifier.send_message(decrypted_message):
                        logger.info(f"  [SUCCESS] Alert ID {alert_id} dispatched.")
                        db.delete_pending_alert(alert_id)
                    else:
                        logger.warning(f"  [WARNING] Failed to dispatch alert ID {alert_id}. Will retry next cycle.")
            
            # Sleep for the messenger's specific interval
            time.sleep(config.get('messenger_interval', 120)) 

        except KeyboardInterrupt:
            logger.info("\nShutdown signal received. Exiting.")
            db.close()
            sys.exit(0)
        except Exception as e:
            logger.critical(f"An unexpected error occurred in the main loop: {e}", exp_info=True)
            db.close()
            time.sleep(60)

if __name__ == "__main__":
    main()
