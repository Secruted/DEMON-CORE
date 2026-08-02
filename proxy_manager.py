import urllib3
import requests
import random
import logging
import time
import threading
import json
from urllib.parse import urlparse
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Disable insecure warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
urllib3.disable_warnings(InsecureRequestWarning)


class ProxyManager:
    def __init__(self, proxy_file='proxy.txt'):
        self.logger = logging.getLogger(__name__)
        self.proxy_file = proxy_file

        # Load proxies from file
        self.master_proxies = self._load_proxies(self.proxy_file)
        self.healthy_proxies = []
        self.cooldown_proxies = {}  # {proxy_url: cooldown_expiry_timestamp}
        self.cooldown_duration = 60  # seconds

        # URLs for health checks
        self.health_check_urls = [
            "http://httpbin.org/ip",
            "https://api.ipify.org?format=json"
        ]

        # Load user agents
        self.user_agents = self._load_user_agents() or [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        ]

        # Fallback proxy if no proxies found
        if not self.master_proxies:
            self.master_proxies.append({
                'http': 'socks5h://127.0.0.1:9050',
                'https': 'socks5h://127.0.0.1:9050',
                'type': 'socks5'
            })

        # Start health check thread
        self.health_check_thread = threading.Thread(target=self._health_check_worker, daemon=True)
        self.health_check_thread.start()

        self.logger.info(f"ProxyManager Online. Monitoring {len(self.master_proxies)} proxies.")

    def _load_proxies(self, proxy_file):
        """Load proxies from file"""
        proxies = []
        try:
            with open(proxy_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    p_info = {}
                    parsed = urlparse(line)
                    if not parsed.scheme:
                        url = f"socks5h://{line}"
                        p_info = {'http': url, 'https': url, 'type': 'socks5'}
                    else:
                        p_type = 'socks5' if 'socks' in parsed.scheme else 'http'
                        p_info = {'http': line, 'https': line, 'type': p_type}
                    proxies.append(p_info)
            return proxies
        except Exception:
            return []

    def _load_user_agents(self):
        """Load user agents from config.json"""
        try:
            with open('config.json', 'r') as f:
                return json.load(f).get("user_agents", [])
        except Exception:
            return []

    def _health_check_worker(self):
        """Continuously check proxies and update their status"""
        while True:
            current_time = time.time()
            # Remove expired cooldowns
            self.cooldown_proxies = {
                k: v for k, v in self.cooldown_proxies.items() if v > current_time
            }

            for proxy_info in self.master_proxies:
                self._check_and_update_proxy_status(proxy_info)
                time.sleep(0.05)  # small pause between checks

            time.sleep(30)  # full cycle pause

    def _check_and_update_proxy_status(self, proxy_info):
        proxy_url = proxy_info.get('https') or proxy_info.get('http')
        try:
            r = requests.get(
                random.choice(self.health_check_urls),
                proxies={'http': proxy_url, 'https': proxy_url},
                timeout=10,
                verify=False
            )
            if r.status_code == 200:
                if proxy_info not in self.healthy_proxies:
                    self.healthy_proxies.append(proxy_info)
            else:
                raise Exception()
        except Exception:
            if proxy_info in self.healthy_proxies:
                self.healthy_proxies.remove(proxy_info)

    def get_healthy_proxy(self):
        """Return a random healthy proxy"""
        for _ in range(15):
            available = [
                p for p in self.healthy_proxies
                if (p.get('https') or p.get('http')) not in self.cooldown_proxies
            ]
            if available:
                return random.choice(available)
            time.sleep(1)
        return None

    def report_failure(self, proxy_info, duration=None):
        """Mark a proxy as failed and put it on cooldown"""
        if proxy_info:
            url = proxy_info.get('https') or proxy_info.get('http')
            if proxy_info in self.healthy_proxies:
                self.healthy_proxies.remove(proxy_info)
            self.cooldown_proxies[url] = time.time() + (duration or self.cooldown_duration)

    def get_random_user_agent(self):
        """Return a random user agent"""
        return random.choice(self.user_agents) if self.user_agents else None
