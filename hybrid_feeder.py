#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v1.1
================================
Escalation Matrix Feeder with advanced poison detection,
ProxyScorer integration, stealth headers, and unified BrowserEngine (Chromium).

Designed for constrained Ubuntu VPS environments.
"""

import os
import re
import time
import asyncio
import random
import logging
from urllib.parse import urlparse, quote_plus, unquote
from typing import Optional, List, Tuple

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers
from browser_engine import BrowserEngine

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_FILE = "targets.txt"
DORKS_FILE = "dorks.txt"
BANNED_DOMAINS = [
    "google", "youtube", "facebook", "github.com", "gitlab.com",
    "stackoverflow", "microsoft", "bing", "yahoo", "duckduckgo"
]

MAX_BROWSER_SESSIONS = int(os.getenv("MAX_BROWSER_SESSIONS", "1"))
BROWSER_SEMAPHORE = asyncio.Semaphore(MAX_BROWSER_SESSIONS)

logger = logging.getLogger("HYBRID_FEEDER")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Global scorer (shared with the rest of the system)
scorer: Optional[ProxyScorer] = None

# ---------------------------------------------------------------------------
# Advanced Poison Detection
# ---------------------------------------------------------------------------
POISON_TEXT_SIGNATURES = [
    "unusual traffic",
    "our systems have detected",
    "prove you're not a robot",
    "detected unusual traffic",
    "please complete the security check",
    "captcha",
    "recaptcha",
    "/sorry/",
    "sorry/index",
]

POISON_DOM_SIGNATURES = [
    "captcha-form",
    "g-recaptcha",
    "recaptcha",
    "cf-challenge",
]

def is_poisoned(html: str, final_url: str = "") -> bool:
    """Strict poison detection even on HTTP 200."""
    if not html:
        return True

    lower = html.lower()
    url_lower = (final_url or "").lower()

    for sig in POISON_TEXT_SIGNATURES:
        if sig in lower or sig in url_lower:
            return True

    for sig in POISON_DOM_SIGNATURES:
        if sig in lower:
            return True

    if "/sorry/" in url_lower or "sorry/index" in url_lower:
        return True

    return False

# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------
def clean_domain(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if ":" in domain:
            domain = domain.split(":")[0]
        domain = domain.replace("www.", "").lower()
        if domain and "." in domain:
            return domain
    except Exception:
        pass
    return None

def save_target(domain: str) -> bool:
    if not domain:
        return False
    if not os.path.exists(TARGET_FILE):
        open(TARGET_FILE, "w").close()
    with open(TARGET_FILE, "r", encoding="utf-8", errors="ignore") as f:
        existing = f.read()
    if domain not in existing:
        with open(TARGET_FILE, "a", encoding="utf-8") as f:
            f.write(f"{domain}\n")
        return True
    return False

# ---------------------------------------------------------------------------
# Parsing with CSS + Regex fallback
# ---------------------------------------------------------------------------
def parse_google_results(html: str) -> List[str]:
    """Extract clean domains. CSS first, Regex fallback."""
    domains: List[str] = []

    try:
        soup = BeautifulSoup(html, "lxml")

        selectors = [
            "div.yuRUbf > a",
            "div.g a",
            "a[href^='/url?q=']",
            "a[data-ved]",
            "div#search a[href]",
        ]

        for sel in selectors:
            for a in soup.select(sel):
                href = a.get("href", "")
                if href.startswith("/url?q="):
                    href = href.split("/url?q=")[1].split("&")[0]
                    href = unquote(href)
                if href.startswith("http") and "google." not in href:
                    domain = clean_domain(href)
                    if domain and not any(b in domain for b in BANNED_DOMAINS):
                        if domain not in domains:
                            domains.append(domain)

        if domains:
            return domains
    except Exception:
        pass

    # Regex fallback
    try:
        pattern = r'/url\?q=(https?://[^&\s"\']+)'
        matches = re.findall(pattern, html)
        for raw in matches:
            href = unquote(raw)
            if "google." in href:
                continue
            domain = clean_domain(href)
            if domain and not any(b in domain for b in BANNED_DOMAINS):
                if domain not in domains:
                    domains.append(domain)
    except Exception:
        pass

    return domains

# ---------------------------------------------------------------------------
# Humanized delay
# ---------------------------------------------------------------------------
async def humanized_delay(min_s: float = 2.8, max_s: float = 7.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------------------------------------------------------------------------
# Tier 1 & Tier 2: curl_cffi
# ---------------------------------------------------------------------------
async def fetch_with_curl(
    session: AsyncSession,
    dork: str,
    start: int = 0,
    impersonate: str = "chrome120",
) -> Tuple[Optional[str], str, bool]:
    """Returns (html, final_url, is_poisoned)"""
    global scorer

    query = quote_plus(dork)
    url = f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}"

    headers = get_stealth_headers(mobile_bias=0.20)
    proxy = await scorer.get_best_proxy()
    formatted_proxy = None
    if proxy:
        formatted_proxy = proxy if "://" in proxy else f"http://{proxy}"

    start_time = time.time()
    html = None
    final_url = url
    success = False
    poisoned = False

    try:
        resp = await session.get(
            url,
            headers=headers,
            proxy=formatted_proxy,
            timeout=28,
            allow_redirects=True,
            impersonate=impersonate,
        )
        final_url = str(resp.url) if hasattr(resp, "url") else url
        html = resp.text or ""

        if resp.status_code == 200:
            if is_poisoned(html, final_url):
                poisoned = True
                success = False
            else:
                success = True
        else:
            success = False
            if resp.status_code in (403, 429, 503) or is_poisoned(html, final_url):
                poisoned = True

    except Exception as e:
        logger.debug(f"[curl] Exception: {e}")
        success = False

    elapsed = time.time() - start_time
    if proxy:
        await scorer.record_result(
            proxy=proxy,
            success=success and not poisoned,
            response_time=elapsed,
        )

    return (html if success else None), final_url, poisoned

# ---------------------------------------------------------------------------
# Tier 3: Unified BrowserEngine (Chromium)
# ---------------------------------------------------------------------------
async def fetch_with_playwright(dork: str, start: int = 0) -> Tuple[Optional[str], str, bool]:
    """Tier 3 heavy engine using centralized BrowserEngine."""
    global scorer

    query = quote_plus(dork)
    url = f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}"

    proxy = await scorer.get_best_proxy()
    formatted_proxy = proxy if proxy and "://" in proxy else (f"http://{proxy}" if proxy else None)

    start_time = time.time()
    html = None
    final_url = url
    success = False
    poisoned = False

    async with BROWSER_SEMAPHORE:
        async with BrowserEngine(proxy=formatted_proxy, block_resources=True) as engine:
            if not engine.enabled:
                logger.error("[TIER3] BrowserEngine failed to start")
                return None, url, True

            html, final_url = await engine.request(url)

            if html:
                if is_poisoned(html, final_url):
                    poisoned = True
                    success = False
                else:
                    success = True
            else:
                success = False
                poisoned = True

    elapsed = time.time() - start_time
    if proxy:
        await scorer.record_result(
            proxy=proxy,
            success=success and not poisoned,
            response_time=elapsed,
        )

    return (html if success else None), final_url, poisoned

# ---------------------------------------------------------------------------
# Escalation Matrix for a single page
# ---------------------------------------------------------------------------
async def fetch_page_with_escalation(
    session: AsyncSession,
    dork: str,
    start: int = 0,
) -> Optional[str]:
    """Tier 1 → Tier 2 → Tier 3"""
    # Tier 1
    logger.info(f"[TIER1] Ghost attack for dork start={start}")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="chrome120")
    if html and not poisoned:
        return html

    await humanized_delay(3.5, 6.5)

    # Tier 2
    logger.info("[TIER2] Tactical re-attack with new identity")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="edge99")
    if html and not poisoned:
        return html

    await humanized_delay(4.0, 8.0)

    # Tier 3
    logger.info("[TIER3] Escalating to unified Chromium BrowserEngine")
    html, final_url, poisoned = await fetch_with_playwright(dork, start)
    if html and not poisoned:
        return html

    logger.warning("[ESCALATION] All tiers failed for this page")
    return None

# ---------------------------------------------------------------------------
# Main search cycle
# ---------------------------------------------------------------------------
async def execute_search_cycle(session: AsyncSession, dork: str, limit: int) -> int:
    print(f"\n🚀 Executing Dork: {dork}")
    print("   Hybrid Escalation Matrix active...")

    captured = 0
    start = 0
    max_pages = 3

    for page_idx in range(max_pages):
        if captured >= limit:
            break

        html = await fetch_page_with_escalation(session, dork, start=start)
        if not html:
            print("   ❌ Page acquisition failed after full escalation.")
            await humanized_delay(5.0, 9.0)
            continue

        domains = parse_google_results(html)
        if not domains:
            print("   ⚠️ No usable domains extracted (CSS + Regex both empty).")
            break

        for domain in domains:
            if captured >= limit:
                break
            if save_target(domain):
                print(f"   ✅ [CAPTURED] {domain}")
                captured += 1

        start += 30
        await humanized_delay(3.5, 7.5)

    return captured

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def start_feeding():
    global scorer

    print("\n🕷️  DΞMON HYBRID FEEDER v1.1 (UNIFIED BROWSER ENGINE)  🕷️")
    print("---------------------------------------------------------------")
    print(f"[CONFIG] MAX_BROWSER_SESSIONS = {MAX_BROWSER_SESSIONS}")

    scorer = ProxyScorer(proxy_file="proxy.txt", state_file="proxy_scores.json")
    summary = await scorer.get_stats_summary()
    print(f"[SCORER] Total: {summary['total']} | Alive: {summary['alive']} | Avg: {summary['avg_score']}")

    mode = input("Select Mode: [1] Manual Input  [2] Read dorks.txt : ").strip()

    try:
        limit = int(input("🔢 Targets per Dork (Recommended 15-25): ").strip())
    except Exception:
        limit = 20

    total_captured = 0

    async with AsyncSession() as session:
        try:
            if mode == "2":
                if not os.path.exists(DORKS_FILE):
                    print(f"❌ {DORKS_FILE} not found")
                    return

                with open(DORKS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    dorks = [line.strip() for line in f if line.strip()]

                print(f"🔥 Loaded {len(dorks)} dorks. Starting hybrid acquisition...")

                for dork in dorks:
                    hits = await execute_search_cycle(session, dork, limit)
                    total_captured += hits
                    print("   💤 Cooling down before next dork...")
                    await humanized_delay(6.0, 12.0)

            else:
                dork = input("🔥 Enter your Google Dork: ").strip()
                if dork:
                    total_captured = await execute_search_cycle(session, dork, limit)

        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n⚠️  Interrupted (CTRL+C). Saving proxy state...")
        finally:
            await scorer.save_state()
            print("[SCORER] State saved → proxy_scores.json")

    print(f"\n🩸 MISSION COMPLETE. Total New Targets: {total_captured}")
    print("👉 Next: python harvester.py")

def main():
    try:
        asyncio.run(start_feeding())
    except KeyboardInterrupt:
        print("\n🛑 Hybrid Feeder terminated.")

if __name__ == "__main__":
    main()
