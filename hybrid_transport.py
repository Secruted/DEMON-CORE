"""
Hybrid Transport Engine
-----------------------
Smart request layer that chooses the best engine automatically:

1. curl_cffi   → Fast + better TLS fingerprint (default)
2. Playwright (unified BrowserEngine / Chromium) → Escalation when protection is detected

Usage:
    transport = HybridTransport(proxy_file="proxy.txt")
    await transport.init()
    html, final_url = await transport.request(url)
"""

import time
import logging
from typing import Optional, Tuple
from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers
from browser_engine import BrowserEngine

logger = logging.getLogger("HYBRID_TRANSPORT")

class HybridTransport:
    def __init__(self, proxy_file: str = "proxy.txt"):
        self.scorer = ProxyScorer(proxy_file=proxy_file)
        self.current_proxy = None

    async def init(self):
        summary = await self.scorer.get_stats_summary()
        logger.info(f"[HYBRID] Ready | Proxies: {summary['total']} | Alive: {summary['alive']}")

    async def _get_proxy(self) -> Optional[str]:
        proxy = await self.scorer.get_best_proxy()
        self.current_proxy = proxy
        if proxy and "://" not in proxy:
            return f"http://{proxy}"
        return proxy

    async def _request_curl_cffi(self, url: str, proxy: Optional[str]) -> Tuple[Optional[str], str, bool]:
        """Fast engine with better TLS fingerprint. Returns (html, final_url, should_escalate)"""
        try:
            from curl_cffi.requests import AsyncSession

            headers = get_stealth_headers(mobile_bias=0.25)
            async with AsyncSession() as session:
                resp = await session.get(
                    url,
                    headers=headers,
                    proxy=proxy,
                    timeout=20,
                    allow_redirects=True,
                    impersonate="chrome120"
                )
                if resp.status_code == 200:
                    return resp.text, str(resp.url), False
                # Detect protection signals → escalate
                if resp.status_code in (403, 429, 503) or "cloudflare" in (resp.text or "").lower() or "cf-ray" in str(resp.headers):
                    return None, url, True
                return None, url, False
        except Exception:
            return None, url, True  # Fail → escalate

    async def _request_playwright(self, url: str, proxy: Optional[str]) -> Tuple[Optional[str], str]:
        """Heavy escalation using unified Chromium BrowserEngine."""
        try:
            async with BrowserEngine(headless=True, proxy=proxy, block_resources=True) as engine:
                if not engine.enabled:
                    return None, url
                html, final = await engine.request(url)
                return html, final
        except Exception as e:
            logger.debug(f"[HYBRID] BrowserEngine failed: {e}")
            return None, url

    async def request(self, url: str) -> Tuple[Optional[str], str]:
        """
        Smart request with automatic engine selection.
        Returns (html, final_url)
        """
        proxy = await self._get_proxy()
        start = time.time()

        # Stage 1: curl_cffi
        html, final_url, should_escalate = await self._request_curl_cffi(url, proxy)

        success = html is not None
        elapsed = time.time() - start

        if self.current_proxy:
            await self.scorer.record_result(self.current_proxy, success, elapsed)

        if success:
            return html, final_url

        # Stage 2: Escalate to unified BrowserEngine if needed
        if should_escalate:
            logger.info(f"[HYBRID] Protection detected → Escalating to Chromium BrowserEngine for {url}")
            html, final_url = await self._request_playwright(url, proxy)
            if html:
                if self.current_proxy:
                    await self.scorer.record_result(self.current_proxy, True, time.time() - start)
                return html, final_url

        return None, url

    async def close(self):
        await self.scorer.save_state()
