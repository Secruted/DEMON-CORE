import time
import random
import asyncio
import aiohttp
import logging
from aiohttp import TCPConnector, ClientTimeout
from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers

class TransportManager:
    def __init__(self, transport_cfg):
        self.logger = logging.getLogger("TRANSPORT")
        self.cfg = transport_cfg

        self.proxy_file = self.cfg.get("proxy_file", "proxy.txt")
        self.timeout = ClientTimeout(total=self.cfg.get("connection_timeout", 15))

        self.current_proxy = None
        self.session = None
        self.scorer = ProxyScorer(proxy_file=self.proxy_file)

    async def init_session(self):
        if not self.session:
            # limit high, ssl=False for speed (Phase 3 foundation)
            connector = TCPConnector(
                limit=3000,
                ssl=False,
                enable_cleanup_closed=True,
                force_close=False
            )
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            )
            self.logger.info("[TRANSPORT] Async session + ProxyScorer + Stealth headers online.")

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await self.scorer.save_state()

    async def request(self, url):
        if not self.session:
            await self.init_session()

        headers = get_stealth_headers(mobile_bias=0.3)

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
        except Exception:
            success = False

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
