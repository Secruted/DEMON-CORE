import random
import requests
import logging
import urllib3
from requests.exceptions import SSLError, ConnectionError, ReadTimeout

# Disable insecure request warnings for SSL verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class TransportManager:
    def __init__(self, transport_cfg):
        self.logger = logging.getLogger("TRANSPORT")
        self.cfg = transport_cfg

        # [IDENTITY PARAMETERS]
        self.user_agents = self.cfg.get("user_agents", [])
        self.proxy_file = self.cfg.get("proxy_file", "proxy.txt")
        self.timeout = self.cfg.get("connection_timeout", 15)
        
        # 👇 [NEW] متغير لتتبع البروكسي المستخدم حالياً (لإبلاغ ماغي عند الفشل)
        self.current_proxy = None

        # [🔥 DΞMON MIMICRY ARSENAL]
        self.mobile_agents = [
            "Dalvik/2.1.0 (Linux; U; Android 10; SM-G960F Build/QP1A.190711.020)",
            "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6 Build/SD1A.210817.036)",
            "shop-app/45.0 (Android 12; Scale/2.0)",
            "CFNetwork/1240.0.4 Darwin/20.6.0", # iOS Backend calls
            "okhttp/4.9.1", # Standard Android Library
            "Alamofire/5.4.3" # Standard iOS Library
        ]

        # [LOAD PROXIES]
        try:
            with open(self.proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
            self.logger.info(f"[INIT] Transport Layer armed with {len(self.proxies)} mixed proxies.")
        except FileNotFoundError:
            self.proxies = []
            self.logger.warning("[!] proxy.txt not found. Running without masking.")

    def _get_mimicry_headers(self):
        """
        Internal: Generates high-fidelity mobile application headers.
        """
        # 90% chance to use Mobile Agents (The Strategy), 10% fallback to config agents
        if self.mobile_agents and random.random() > 0.1:
            agent = random.choice(self.mobile_agents)
        elif self.user_agents:
            agent = random.choice(self.user_agents)
        else:
            agent = "DΞMON-CORE/2.0 (Mobile)"

        headers = {
            'User-Agent': agent,
            'Accept': 'application/json, text/plain, */*', # We want JSON
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'X-Requested-With': 'XMLHttpRequest', # The Magic Key for WAF Bypass
            'Cache-Control': 'no-cache'
        }

        # Clean up headers if pretending to be a raw library (OkHttp/Alamofire)
        if "okhttp" in agent.lower() or "alamofire" in agent.lower():
            return {
                'User-Agent': agent,
                'Accept': 'application/json'
            }

        return headers

    def get_next_identity(self):
        """Prepares a Mimicry Header set and a correctly formatted proxy."""
        # Use the new header generator
        headers = self._get_mimicry_headers()

        # 👇 [NEW] تحديث البروكسي الحالي في الذاكرة
        if self.proxies:
            self.current_proxy = random.choice(self.proxies)
        else:
            self.current_proxy = None
            
        proxy_dict = None

        if self.current_proxy:
            # [SMART PROTOCOL DETECTION]
            formatted_proxy = self.current_proxy if "://" in self.current_proxy else f"http://{self.current_proxy}"
            proxy_dict = {
                "http": formatted_proxy,
                "https": formatted_proxy
            }

        return {
            "headers": headers,
            "proxies": proxy_dict,
            "timeout": self.timeout
        }

    def request(self, url):
        """
        The Unified Gate: Executes the network request with SMART PROTOCOL SWITCHING.
        Now handles SSL failures by automatically downgrading to HTTP.
        """
        identity = self.get_next_identity()

        try:
            # Attempt 1: Standard Execution (Usually HTTPS)
            response = requests.get(
                url,
                headers=identity["headers"],
                proxies=identity["proxies"],
                timeout=identity["timeout"],
                verify=False,
                allow_redirects=True
            )

            if response.status_code == 200:
                return response.text, response.url
            else:
                self.logger.debug(f"[TRANSPORT] Failed {url} with Status: {response.status_code}")
                return None, url

        except (SSLError, ConnectionError) as e:
            # [🔥 FALLBACK PROTOCOL]
            # If SSL fails (common with old configs/IPs), try HTTP immediately
            if url.startswith("https://"):
                http_url = url.replace("https://", "http://")
                self.logger.debug(f"[TRANSPORT] SSL/Connection Failed. Downgrading to HTTP -> {http_url}")

                try:
                    response = requests.get(
                        http_url,
                        headers=identity["headers"], # Keep the same identity (same proxy)
                        proxies=identity["proxies"],
                        timeout=identity["timeout"],
                        verify=False,
                        allow_redirects=True
                    )
                    if response.status_code == 200:
                        return response.text, response.url
                except Exception as e2:
                    # If HTTP also fails, give up
                    pass

            # If we reached here, both attempts failed
            self.logger.debug(f"[TRANSPORT] Connection Error on {url}: {e}")
            return None, url

        except Exception as e:
            self.logger.debug(f"[TRANSPORT] Critical Error on {url}: {e}")
            return None, url
