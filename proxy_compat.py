"""
DΞMON CORE - Proxy Compatibility Layer
======================================
Thin adapter that maps the old ProxyManager API to the current ProxyScorer.

Used only by legacy modules (domain_parser.py, telegram_notifier.py).
Does NOT re-implement ProxyManager logic.
"""

import asyncio
import logging
import random
from typing import Optional, Dict, Any

from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers, REALISTIC_UA

logger = logging.getLogger("PROXY_COMPAT")


class ProxyCompat:
    """
    Minimal compatibility wrapper.

    Provides the small set of methods that legacy modules still call:
    - get_random_user_agent()
    - get_healthy_proxy()  → returns dict {'http': url, 'https': url}
    - report_failure(proxy_info, duration=...)
    - user_agents (list fallback)
    """

    def __init__(self, proxy_file: str = "proxy.txt", state_file: str = "proxy_scores.json"):
        self.scorer = ProxyScorer(proxy_file=proxy_file, state_file=state_file)
        self.user_agents = REALISTIC_UA
        self.cooldown_duration = 900  # default fallback used by some legacy code

    def get_random_user_agent(self) -> str:
        """Return a realistic User-Agent string."""
        headers = get_stealth_headers(mobile_bias=0.2)
        return headers.get("User-Agent", random.choice(self.user_agents))

    def _run_async(self, coro):
        """Helper to run async ProxyScorer methods from sync context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Fallback for already-running loop (rare in these legacy modules)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, coro).result()
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    def get_healthy_proxy(self) -> Optional[Dict[str, str]]:
        """
        Return a proxy in the old dict format expected by legacy code:
        {'http': 'scheme://ip:port', 'https': 'scheme://ip:port'}
        """
        raw = self._run_async(self.scorer.get_best_proxy())
        if not raw:
            return None

        candidates = self.scorer.get_scheme_candidates(raw)
        if not candidates:
            return None

        # Prefer the first candidate (or last known successful scheme)
        chosen = candidates[0]
        return {"http": chosen, "https": chosen}

    def report_failure(self, proxy_info: Dict[str, Any], duration: float = 600):
        """
        Map old report_failure call to ProxyScorer.record_result(False).
        duration is accepted for API compatibility but scoring uses its own ban logic.
        """
        if not proxy_info:
            return

        # Extract raw proxy key (strip scheme if present for scoring key consistency)
        proxy_url = proxy_info.get("https") or proxy_info.get("http") or ""
        if not proxy_url:
            return

        # Use the original raw form if possible; otherwise the full URL
        raw_key = proxy_url
        if "://" in proxy_url:
            # Try to find matching key in scorer
            for key in self.scorer.proxies:
                if key in proxy_url or proxy_url.endswith(key):
                    raw_key = key
                    break

        self._run_async(self.scorer.record_result(raw_key, success=False, response_time=0.0))
