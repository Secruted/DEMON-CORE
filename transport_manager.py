import random
import asyncio
import aiohttp
import logging
from aiohttp import TCPConnector, ClientTimeout

# ULTRA 911 ARCHITECTURE: ASYNC TRANSPORT LAYER

class TransportManager:
    def __init__(self, transport_cfg):
        self.logger = logging.getLogger("TRANSPORT")
        self.cfg = transport_cfg

        self.user_agents = self.cfg.get("user_agents", [])
        self.proxy_file = self.cfg.get("proxy_file", "proxy.txt")
        self.timeout = ClientTimeout(total=self.cfg.get("connection_timeout", 15))
        
        self.current_proxy = None
        self.session = None

        self.mobile_agents = [
            "Dalvik/2.1.0 (Linux; U; Android 10; SM-G960F)",
            "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6)",
            "shop-app/45.0 (Android 12; Scale/2.0)",
            "CFNetwork/1240.0.4 Darwin/20.6.0",
            "okhttp/4.9.1"
        ]

        self._load_proxies()

    def _load_proxies(self):
        try:
            with open(self.proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
            self.logger.info(f"[ULTRA 911] Transport Layer armed with {len(self.proxies)} proxies.")
        except FileNotFoundError:
            self.proxies = []
            self.logger.warning("[!] proxy.txt missing. Stealth disabled.")

    async def init_session(self):
        """
        The Nuclear Reactor: Opens 5000 concurrent sockets.
        Bypasses SSL verification for extreme speed.
        """
        if not self.session:
            connector = TCPConnector(limit=5000, ssl=False, enable_cleanup_closed=True)
            self.session = aiohttp.ClientSession(connector=connector, timeout=self.timeout)
            self.logger.info("[ULTRA 911] Asynchronous TCP Pool Initialized (5000 connections limit).")

    async def close_session(self):
        """Graceful shutdown of the async engine."""
        if self.session and not self.session.closed:
            await self.session.close()

    def _get_mimicry_headers(self):
        if self.mobile_agents and random.random() > 0.1:
            agent = random.choice(self.mobile_agents)
        elif self.user_agents:
            agent = random.choice(self.user_agents)
        else:
            agent = "D-CORE-911/3.0"

        headers = {
            'User-Agent': agent,
            'Accept': 'application/json, text/plain, */*',
            'Connection': 'keep-alive',
            'X-Requested-With': 'XMLHttpRequest'
        }
        return headers

    async def request(self, url):
        """
        The Async Kill Shot.
        Returns (html_text, final_url) or (None, url) instantly.
        """
        if not self.session:
            await self.init_session()

        headers = self._get_mimicry_headers()
        
        # Fast Proxy Rotation
        if self.proxies:
            self.current_proxy = random.choice(self.proxies)
        else:
            self.current_proxy = None

        formatted_proxy = None
        if self.current_proxy:
            formatted_proxy = self.current_proxy if "://" in self.current_proxy else f"http://{self.current_proxy}"

        try:
            async with self.session.get(url, headers=headers, proxy=formatted_proxy, allow_redirects=True) as response:
                if response.status == 200:
                    text = await response.text()
                    return text, str(response.url)
                else:
                    return None, url
        except Exception as e:
            # Absolute silence on failures to prevent Terminal bottleneck
            return None, url
