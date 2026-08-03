import time
import random
import asyncio
import aiohttp
import logging
from aiohttp import TCPConnector, ClientTimeout
from proxy_scoring import ProxyScorer

# ULTRA 911 ARCHITECTURE: ASYNC TRANSPORT LAYER + SMART SCORING

class TransportManager:
    def __init__(self, transport_cfg):
        self.logger = logging.getLogger("TRANSPORT")
        self.cfg = transport_cfg

        self.user_agents = self.cfg.get("user_agents", [])
        self.proxy_file = self.cfg.get("proxy_file", "proxy.txt")
        self.timeout = ClientTimeout(total=self.cfg.get("connection_timeout", 15))

        self.current_proxy = None
        self.session = None

        # Smart Scoring System
        self.scorer = ProxyScorer(proxy_file=self.proxy_file)

        self.mobile_agents = [
            "Dalvik/2.1.0 (Linux; U; Android 10; SM-G960F)",
            "Dalvik/2.1.0 (Linux; U; Android 12; Pixel 6)",
            "shop-app/45.0 (Android 12; Scale/2.0)",
            "CFNetwork/1240.0.4 Darwin/20.6.0",
            "okhttp/4.9.1"
        ]

    async def init_session(self):
        if not self.session:
            connector = TCPConnector(limit=5000, ssl=False, enable_cleanup_closed=True)
            self.session = aiohttp.ClientSession(connector=connector, timeout=self.timeout)
            self.logger.info("[TRANSPORT] Async TCP Pool + ProxyScorer online.")

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
        # Persist scores on shutdown
        await self.scorer.save_state()

    def _get_mimicry_headers(self):
        if self.mobile_agents and random.random() > 0.1:
            agent = random.choice(self.mobile_agents)
        elif self.user_agents:
            agent = random.choice(self.user_agents)
        else:
            agent = "D-CORE-911/3.0"

        return {
            'User-Agent': agent,
            'Accept': 'application/json, text/plain, */*',
            'Connection': 'keep-alive',
            'X-Requested-With': 'XMLHttpRequest'
        }

    async def request(self, url):
        """
        Smart request with scoring integration.
        Returns (html_text, final_url) or (None, url)
        """
        if not self.session:
            await self.init_session()

        headers = self._get_mimicry_headers()

        # Get best proxy from Scorer
        self.current_proxy = await self.scorer.get_best_proxy()

        formatted_proxy = None
        if self.current_proxy:
            formatted_proxy = self.current_proxy if "://" in self.current_proxy else f"http://{self.current_proxy}"

        start_time = time.time()
        success = False
        text = None
        final_url = url

        try:
            async with self.session.get(
                url,
                headers=headers,
                proxy=formatted_proxy,
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    final_url = str(response.url)
                    success = True
                else:
                    success = False
        except Exception:
            success = False

        # Record result into Scoring system
        elapsed = time.time() - start_time
        if self.current_proxy:
            await self.scorer.record_result(
                proxy=self.current_proxy,
                success=success,
                response_time=elapsed
            )

        if success:
            return text, final_url
        return None, url
