import requests
import logging
import random
import time
from proxy_compat import ProxyCompat

class TelegramNotifier:
    def __init__(self, token, chat_id, proxy_manager: ProxyCompat = None):
        """
        Initializes the notifier with the bot token, chat ID, and an optional proxy layer.
        Uses ProxyCompat (thin layer over ProxyScorer) instead of the old ProxyManager.
        :param token: Telegram bot token.
        :param chat_id: Telegram chat ID.
        :param proxy_manager: An instance of ProxyCompat (or compatible object).
        """
        if not token or not chat_id:
            raise ValueError("Telegram token and chat_id are required for TelegramNotifier.")
        
        self.logger = logging.getLogger(__name__)
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.proxy_manager = proxy_manager  # can be None (direct connection)

    def send_message(self, message, retries=3, backoff_factor=2):
        """
        Sends a message to the specified Telegram chat, utilizing proxies and enhanced stealth.
        :param message: The message text to send.
        :param retries: Number of retry attempts.
        :param backoff_factor: Multiplier for exponential backoff on failures.
        :return: True if message sent successfully, False otherwise.
        """
        if len(message) > 4096:
            self.logger.warning("Message too long for Telegram. Truncating.")
            message = message[:4090] + "\n[...TRUNCATED...]" 

        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'Markdown'
        }

        headers = {
            "User-Agent": self.proxy_manager.get_random_user_agent() if self.proxy_manager else "Mozilla/5.0",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

        for attempt in range(retries):
            proxies_dict = None
            proxy_info = None

            if self.proxy_manager:
                proxy_info = self.proxy_manager.get_healthy_proxy()
                if proxy_info:
                    proxies_dict = {'http': proxy_info['http'], 'https': proxy_info['https']}
                    self.logger.debug(f"Using proxy {proxy_info.get('https')} for Telegram message (Attempt {attempt + 1}/{retries})")
                else:
                    self.logger.warning("No healthy proxies available. Attempting direct connection (HIGH RISK).")
            
            try:
                humanized_delay = random.uniform(0.5, 3.0)
                time.sleep(humanized_delay)
                self.logger.debug(f"Applying humanized delay of {humanized_delay:.2f}s before sending Telegram message.")

                response = requests.post(self.base_url, data=payload, headers=headers, proxies=proxies_dict, timeout=20)
                
                if response.status_code == 429:
                    retry_after = response.json().get('parameters', {}).get('retry_after', 30)
                    self.logger.warning(f"Telegram API rate limited. Retrying after {retry_after}s.")
                    if proxy_info: self.proxy_manager.report_failure(proxy_info, duration=retry_after * 2)
                    time.sleep(retry_after + random.uniform(1, 5))
                    continue
                elif 400 <= response.status_code < 500:
                    self.logger.error(f"Telegram API Client Error: {response.status_code} - {response.json().get('description', 'No description')}.")
                    if proxy_info: self.proxy_manager.report_failure(proxy_info, duration=getattr(self.proxy_manager, 'cooldown_duration', 900) / 2)
                    return False
                elif response.status_code >= 500:
                    self.logger.error(f"Telegram API Server Error: {response.status_code}")
                    if proxy_info: self.proxy_manager.report_failure(proxy_info, duration=600)
                    if attempt < retries - 1:
                        time.sleep(backoff_factor * (2 ** attempt) + random.uniform(1, 5))
                    continue
                
                response.raise_for_status()
                self.logger.info("Alert sent successfully to Telegram (via proxy/direct).")
                return True

            except requests.exceptions.RequestException as e:
                self.logger.error(f"Attempt {attempt + 1}/{retries} failed to send Telegram message. Error: {e}")
                if proxy_info:
                    if isinstance(e, requests.exceptions.Timeout):
                        self.proxy_manager.report_failure(proxy_info, duration=600)
                    elif isinstance(e, requests.exceptions.ConnectionError):
                        self.proxy_manager.report_failure(proxy_info, duration=900)
                    else:
                        self.proxy_manager.report_failure(proxy_info, duration=300)
                
                if attempt < retries - 1:
                    time.sleep(backoff_factor * (2 ** attempt) + random.uniform(1, 5))
            except Exception as e:
                self.logger.critical(f"An unexpected critical error occurred in TelegramNotifier: {e}", exc_info=True)
                return False

        return False
