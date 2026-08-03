#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v1.0
================================
Escalation Matrix Feeder with advanced poison detection,
ProxyScorer integration, stealth headers, and Tier-3 Chromium force-kill.

Designed for constrained Ubuntu VPS environments.
"""

import os
import re
import time
import asyncio
import random
import signal
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse, quote_plus, unquote
from typing import Optional, List, Tuple

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers

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

    # Text signatures
    for sig in POISON_TEXT_SIGNATURES:
        if sig in lower or sig in url_lower:
            return True

    # DOM / structural signatures
    for sig in POISON_DOM_SIGNATURES:
        if sig in lower:
            return True

    # Explicit /sorry/ path
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

    # --- Primary: BeautifulSoup CSS selectors ---
    try:
        soup = BeautifulSoup(html, "lxml")

        # Common modern Google result containers
        selectors = [
            "div.yuRUbf > a",
            "div.g a",
            "a[href^=\"/url?q=\"]",
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

    # --- Fallback: Regex for raw /url?q= links ---
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
    """
    Returns (html, final_url, is_poisoned)
    """
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
        # Heavy penalty for poison / burned proxy
        await scorer.record_result(
            proxy=proxy,
            success=success and not poisoned,
            response_time=elapsed,
        )

    return (html if success else None), final_url, poisoned

# ---------------------------------------------------------------------------
# Tier 3: Direct Playwright Chromium with force-kill + virtual display
# ---------------------------------------------------------------------------
@asynccontextmanager
async def chromium_session(proxy: Optional[str] = None):
    """
    Strict lifecycle manager for Chromium.
    - Automatic Xvfb via pyvirtualdisplay on headless Linux
    - Force-kill PIDs on failure
    - Resource hardening (no images, limited JS, etc.)
    """
    from playwright.async_api import async_playwright
    import psutil  # for force-kill if needed (optional but recommended)

    display = None
    playwright = None
    browser = None
    context = None
    pid_list = []

    # Auto virtual display on Linux without GUI
    try:
        if os.name == "posix" and not os.environ.get("DISPLAY"):
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1920, 1080))
            display.start()
            logger.info("[TIER3] Virtual display (Xvfb) started")
    except Exception as e:
        logger.warning(f"[TIER3] Could not start virtual display: {e}")

    try:
        playwright = await async_playwright().start()

        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-images",
                "--blink-settings=imagesEnabled=false",
            ],
        }

        if proxy:
            launch_args["proxy"] = {"server": proxy if "://" in proxy else f"http://{proxy}"}

        browser = await playwright.chromium.launch(**launch_args)
        pid_list.append(browser.process.pid if hasattr(browser, "process") else None)

        context = await browser.new_context(
            user_agent=random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ]),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            java_script_enabled=True,
        )

        # Stealth injection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)

        yield context

    finally:
        # Ruthless cleanup
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if playwright:
                await playwright.stop()
        except Exception:
            pass

        # Force-kill any remaining Chromium processes related to this session
        try:
            import psutil
            current = psutil.Process()
            for child in current.children(recursive=True):
                if "chrom" in child.name().lower():
                    try:
                        child.kill()
                    except Exception:
                        pass
        except Exception:
            pass

        if display:
            try:
                display.stop()
            except Exception:
                pass

async def fetch_with_playwright(dork: str, start: int = 0) -> Tuple[Optional[str], str, bool]:
    """Tier 3 heavy engine."""
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
        async with chromium_session(proxy=formatted_proxy) as context:
            page = None
            try:
                page = await context.new_page()
                # Block heavy resources
                await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda route: route.abort())

                response = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                final_url = page.url
                html = await page.content()

                if response and response.status == 200:
                    if is_poisoned(html, final_url):
                        poisoned = True
                        success = False
                    else:
                        success = True
                else:
                    success = False
                    poisoned = is_poisoned(html or "", final_url)

            except Exception as e:
                logger.debug(f"[TIER3] Exception: {e}")
                success = False
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

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
    """
    Tier 1 → Tier 2 → Tier 3
    Returns clean HTML or None.
    """
    # ----- Tier 1: Ghost (chrome120) -----
    logger.info(f"[TIER1] Ghost attack for dork start={start}")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="chrome120")
    if html and not poisoned:
        return html

    await humanized_delay(3.5, 6.5)

    # ----- Tier 2: Tactical (different impersonate) -----
    logger.info("[TIER2] Tactical re-attack with new identity")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="edge99")
    if html and not poisoned:
        return html

    await humanized_delay(4.0, 8.0)

    # ----- Tier 3: Heavy artillery -----
    logger.info("[TIER3] Escalating to Chromium heavy engine")
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

    print("\n🕷️  DΞMON HYBRID FEEDER v1.0 (ESCALATION MATRIX)  🕷️")
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
