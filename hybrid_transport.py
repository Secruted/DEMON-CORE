"""
Hybrid Transport Engine
-----------------------
Smart request layer that chooses the best engine automatically:

1. curl_cffi   → Fast + better TLS fingerprint (default)
2. Playwright (unified BrowserEngine / Chromium) → Escalation when protection is detected

Now uses centralized scheme candidates from ProxyScorer:
HTTP → SOCKS5 → SOCKS4 when no scheme is present.
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
        self.current_proxy = None  # raw proxy string from file

    async def init(self):
        summary = await self.scorer.get_stats_summary()
        logger.info(f"[HYBRID] Ready | Proxies: {summary['total']} | Alive: {summary['alive']}")

    async def _get_raw_proxy(self) -> Optional[str]:
        proxy = await self.scorer.get_best_proxy()
        self.current_proxy = proxy
        return proxy

    async def _request_curl_cffi(self, url: str, proxy_url: str) -> Tuple[Optional[str], str, bool]:
        """Try a single proxy URL with curl_cffi. Returns (html, final_url, should_escalate)"""
        try:
            from curl_cffi.requests import AsyncSession

            headers = get_stealth_headers(mobile_bias=0.25)
            async with AsyncSession() as session:
                resp = await session.get(
                    url,
                    headers=headers,
                    proxy=proxy_url,
                    timeout=20,
                    allow_redirects=True,
                    impersonate="chrome120"
                )
                if resp.status_code == 200:
                    return resp.text, str(resp.url), False
                if resp.status_code in (403, 429, 503) or "cloudflare" in (resp.text or "").lower() or "cf-ray" in str(resp.headers):
                    return None, url, True
                return None, url, False
        except Exception:
            return None, url, True

    async def _request_playwright(self, url: str, proxy_url: Optional[str]) -> Tuple[Optional[str], str]:
        try:
            async with BrowserEngine(headless=True, proxy=proxy_url, block_resources=True) as engine:
                if not engine.enabled:
                    return None, url
                html, final = await engine.request(url)
                return html, final
        except Exception as e:
            logger.debug(f"[HYBRID] BrowserEngine failed: {e}")
            return None, url

    async def request(self, url: str) -> Tuple[Optional[str], str]:
        """
        Smart request with automatic engine selection + scheme fallback.
        Returns (html, final_url)
        """
        raw_proxy = await self._get_raw_proxy()
        start = time.time()

        if not raw_proxy:
            # No proxy available – try direct
            html, final_url, _ = await self._request_curl_cffi(url, None)
            return html, final_url

        candidates = self.scorer.get_scheme_candidates(raw_proxy)
        last_should_escalate = True

        for proxy_url in candidates:
            scheme = proxy_url.split("://")[0] if "://" in proxy_url else "unknown"
            logger.debug(f"[HYBRID] Trying {scheme}:// for {raw_proxy}")

            html, final_url, should_escalate = await self._request_curl_cffi(url, proxy_url)
            last_should_escalate = should_escalate

            success = html is not None
            elapsed = time.time() - start

            if success:
                await self.scorer.record_result(raw_proxy, True, elapsed, scheme=scheme)
                return html, final_url
            else:
                # Record partial fail for this attempt (still counts against the raw proxy)
                await self.scorer.record_result(raw_proxy, False, elapsed)

        # All schemes failed with curl_cffi → escalate to BrowserEngine if needed
        if last_should_escalate:
            logger.info(f"[HYBRID] All schemes failed → Escalating to Chromium for {url}")
            # Prefer the first candidate (or last known) for Playwright
            proxy_for_browser = candidates[0] if candidates else None
            html, final_url = await self._request_playwright(url, proxy_for_browser)
            if html:
                elapsed = time.time() - start
                scheme = proxy_for_browser.split("://")[0] if proxy_for_browser and "://" in proxy_for_browser else None
                await self.scorer.record_result(raw_proxy, True, elapsed, scheme=scheme)
                return html, final_url

        return None, url

    async def close(self):
        await self.scorer.save_state()
