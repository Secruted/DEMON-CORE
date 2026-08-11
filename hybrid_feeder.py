#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v1.7
================================
- NAVIGATION_FAILED / FETCH_FAILED when bytes=0 or exception
- EMPTY_RESULTS only when navigation succeeded with content but no useful results
- Architecture unchanged
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
    "stackoverflow", "microsoft", "bing", "yahoo", "duckduckgo",
    "support.google", "accounts.google", "policies.google"
]

MAX_BROWSER_SESSIONS = int(os.getenv("MAX_BROWSER_SESSIONS", "1"))
BROWSER_SEMAPHORE = asyncio.Semaphore(MAX_BROWSER_SESSIONS)

logger = logging.getLogger("HYBRID_FEEDER")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

scorer: Optional[ProxyScorer] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mask_proxy(proxy: Optional[str]) -> str:
    if not proxy:
        return "None"
    if "://" in proxy:
        scheme, rest = proxy.split("://", 1)
        if ":" in rest:
            host, port = rest.rsplit(":", 1)
            if len(host) > 6:
                host = host[:3] + "***" + host[-2:]
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://***"
    if ":" in proxy:
        host, port = proxy.rsplit(":", 1)
        if len(host) > 6:
            host = host[:3] + "***" + host[-2:]
        return f"{host}:{port}"
    return "***"

def classify_google_page(html: Optional[str]) -> str:
    """
    Classify Google response content.

    NAVIGATION_FAILED / FETCH_FAILED:
        html is None or bytes == 0 (navigation never produced content).

    EMPTY_RESULTS:
        navigation succeeded (html size > 0) but no useful links/results.

    Other states: CAPTCHA, JS_REQUIRED, CONSENT_PAGE, SEARCH_RESULTS,
                  SEARCH_BLOCKED, ERROR_PAGE, UNKNOWN
    """
    # --- Fetch / navigation never produced content ---
    if html is None or len(html) == 0:
        return "NAVIGATION_FAILED"

    lower = html.lower()

    captcha_signals = [
        "unusual traffic", "our systems have detected", "prove you're not a robot",
        "captcha", "recaptcha", "/sorry/", "sorry/index", "g-recaptcha"
    ]
    if any(s in lower for s in captcha_signals):
        return "CAPTCHA"

    js_signals = [
        "enablejs", "httpservice/retry", "emsg=sg_rel", "sg_rel",
        "please enable javascript", "enable javascript", "jsdisabled"
    ]
    if any(s in lower for s in js_signals):
        return "JS_REQUIRED"

    consent_signals = [
        "before you continue", "consent.google", "we use cookies",
        "accept all", "reject all", "cookie consent"
    ]
    if any(s in lower for s in consent_signals):
        return "CONSENT_PAGE"

    result_markers = ["yurubf", "/url?q=", "data-ved", 'class="g "', 'class="g"', "result-stats", "kcr9t"]
    if any(m in lower for m in result_markers):
        return "SEARCH_RESULTS"

    if "support.google.com/websearch" in lower and html.count("<a ") < 10:
        return "SEARCH_BLOCKED"

    # Tiny payload after a successful transfer → likely error fragment
    if len(html) < 200:
        return "ERROR_PAGE"

    # Loaded successfully (bytes > 0) but almost no links → empty results page
    if html.count("<a ") < 8 and html.count("href=") < 10:
        return "EMPTY_RESULTS"

    return "UNKNOWN"

def is_usable_search_html(html: Optional[str], final_url: str = "") -> bool:
    """
    True only when the page looks like actual Google search results.
    NAVIGATION_FAILED / JS_REQUIRED / CAPTCHA / etc. are NOT usable.
    """
    if not html:
        return False
    page_type = classify_google_page(html)
    if page_type in (
        "NAVIGATION_FAILED", "FETCH_FAILED",
        "JS_REQUIRED", "CAPTCHA", "CONSENT_PAGE",
        "SEARCH_BLOCKED", "ERROR_PAGE", "EMPTY_RESULTS",
    ):
        return False
    if page_type == "SEARCH_RESULTS":
        return True
    if page_type == "UNKNOWN" and (html.count("/url?q=") > 0 or html.count("http") > 20):
        return True
    return False

def is_poisoned(html: Optional[str], final_url: str = "") -> bool:
    """Non-usable page types count as poisoned/failed for escalation."""
    if not html:
        return True
    if not is_usable_search_html(html, final_url):
        return True
    lower = html.lower()
    url_lower = (final_url or "").lower()
    for sig in [
        "unusual traffic", "our systems have detected", "prove you're not a robot",
        "captcha", "recaptcha", "/sorry/", "sorry/index"
    ]:
        if sig in lower or sig in url_lower:
            return True
    return False

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

def _is_banned(domain: str) -> bool:
    return any(b in domain for b in BANNED_DOMAINS)

# ---------------------------------------------------------------------------
# Parsing: classic selectors + broad fallback
# ---------------------------------------------------------------------------
def parse_google_results(html: str) -> List[str]:
    domains: List[str] = []
    css_candidates = 0
    css_valid = 0
    regex_candidates = 0
    regex_valid = 0
    fallback_valid = 0

    page_type = classify_google_page(html)
    print(f"[DIAG][PAGE_TYPE] {page_type}")

    try:
        soup = BeautifulSoup(html, "lxml")
        all_anchors = soup.find_all("a", href=True)
        print(f"[DIAG][HTML] input_bytes={len(html)} total <a> tags={len(all_anchors)}")
        print(f"[DIAG][HTML] yuRUbf={'yes' if 'yuRUbf' in html else 'no'}  /url?q={'yes' if '/url?q=' in html else 'no'}  data-ved={'yes' if 'data-ved' in html else 'no'}")

        samples = []
        for a in all_anchors[:40]:
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            samples.append(href)
            if len(samples) >= 8:
                break
        for i, s in enumerate(samples, 1):
            shown = s if len(s) <= 120 else s[:117] + "..."
            print(f"[DIAG][LINK] sample {i} = {shown}")
    except Exception as e:
        print(f"[DIAG][HTML] diagnostic failed: {e}")
        soup = None

    # --- 1) Classic CSS selectors ---
    try:
        if soup is None:
            soup = BeautifulSoup(html, "lxml")

        selectors = [
            "div.yuRUbf > a",
            "div.g a",
            "a[href^='/url?q=']",
            "a[data-ved]",
            "div#search a[href]",
            "div#rso a[href]",
            "a[href^='http']",
        ]

        for sel in selectors:
            for a in soup.select(sel):
                css_candidates += 1
                href = a.get("href", "") or ""
                if href.startswith("/url?q="):
                    href = unquote(href.split("/url?q=")[1].split("&")[0])
                if href.startswith("http") and "google." not in href:
                    domain = clean_domain(href)
                    if domain and not _is_banned(domain):
                        css_valid += 1
                        if domain not in domains:
                            domains.append(domain)

        if domains:
            print(f"[DIAG][PARSER] CSS candidates={css_candidates} CSS valid={css_valid} final unique={len(domains)}")
            return domains
    except Exception:
        pass

    # --- 2) Regex /url?q= ---
    try:
        pattern = r'/url\?q=(https?://[^&\s"\']+)'
        matches = re.findall(pattern, html)
        for raw in matches:
            regex_candidates += 1
            href = unquote(raw)
            if "google." in href:
                continue
            domain = clean_domain(href)
            if domain and not _is_banned(domain):
                regex_valid += 1
                if domain not in domains:
                    domains.append(domain)
        if domains:
            print(f"[DIAG][PARSER] Regex candidates={regex_candidates} Regex valid={regex_valid} final unique={len(domains)}")
            return domains
    except Exception:
        pass

    # --- 3) Broad fallback: any external http(s) link in the page ---
    try:
        if soup is None:
            soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if href.startswith("/url?q="):
                href = unquote(href.split("/url?q=")[1].split("&")[0])
            if not href.startswith("http"):
                continue
            if "google." in href or "gstatic." in href or "youtube." in href:
                continue
            domain = clean_domain(href)
            if domain and not _is_banned(domain):
                fallback_valid += 1
                if domain not in domains:
                    domains.append(domain)
    except Exception:
        pass

    # --- 4) Last-resort regex for bare https URLs in HTML ---
    if not domains:
        try:
            bare = re.findall(r'https?://[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}(?:/[^\s"\'<>]*)?', html)
            for href in bare:
                if "google." in href or "gstatic." in href:
                    continue
                domain = clean_domain(href)
                if domain and not _is_banned(domain) and domain not in domains:
                    domains.append(domain)
                    fallback_valid += 1
        except Exception:
            pass

    print(
        f"[DIAG][PARSER] CSS={css_candidates}/{css_valid} Regex={regex_candidates}/{regex_valid} "
        f"Fallback={fallback_valid} final unique={len(domains)}"
    )
    return domains

# ---------------------------------------------------------------------------
# Humanized delay
# ---------------------------------------------------------------------------
async def humanized_delay(min_s: float = 2.8, max_s: float = 7.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

def _google_url(dork: str, start: int = 0, basic: bool = True) -> str:
    """Build Google search URL. basic=True uses gbv=1 (simpler HTML)."""
    query = quote_plus(dork)
    extra = "&gbv=1" if basic else ""
    return f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}{extra}"

# ---------------------------------------------------------------------------
# Tier 1 & Tier 2: curl_cffi
# ---------------------------------------------------------------------------
async def fetch_with_curl(
    session: AsyncSession,
    dork: str,
    start: int = 0,
    impersonate: str = "chrome120",
    tier_label: str = "TIER1",
) -> Tuple[Optional[str], str, bool]:
    """Returns (html, final_url, is_poisoned/unusable)"""
    global scorer

    url = _google_url(dork, start, basic=True)
    headers = get_stealth_headers(mobile_bias=0.20)
    raw_proxy = await scorer.get_best_proxy()

    print(f"[DIAG][PROXY] selected={_mask_proxy(raw_proxy)}")

    async def _try_once(proxy_url: Optional[str], scheme: str):
        t0 = time.time()
        resp = await session.get(
            url,
            headers=headers,
            proxy=proxy_url,
            timeout=28,
            allow_redirects=True,
            impersonate=impersonate,
        )
        final_url = str(resp.url) if hasattr(resp, "url") else url
        html = resp.text or ""
        elapsed = time.time() - t0
        print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s")
        return resp.status_code, html, final_url, elapsed

    if not raw_proxy:
        print("[DIAG][PROXY] no proxy available → direct")
        try:
            status, html, final_url, _ = await _try_once(None, "direct")
            if not html:
                print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
                return None, final_url, True
            if status == 200 and is_usable_search_html(html, final_url):
                print(f"[DIAG][PAGE_TYPE] {classify_google_page(html)} → usable")
                return html, final_url, False
            print(f"[DIAG][PAGE_TYPE] {classify_google_page(html)} → unusable")
            return None, final_url, True
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme=direct exception={type(e).__name__}: {e}")
            print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
            return None, url, True

    candidates = scorer.get_scheme_candidates(raw_proxy)
    print(f"[DIAG][PROXY] scheme candidates={[c.split('://')[0] for c in candidates]}")
    start_time = time.time()

    for proxy_url in candidates:
        scheme = proxy_url.split("://")[0] if "://" in proxy_url else "unknown"
        try:
            status, html, final_url, elapsed = await _try_once(proxy_url, scheme)

            if not html:
                print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
                await scorer.record_result(raw_proxy, False, time.time() - start_time)
                continue

            if status == 200:
                usable = is_usable_search_html(html, final_url)
                page_type = classify_google_page(html)
                print(f"[DIAG][PAGE_TYPE] {page_type} → {'usable' if usable else 'unusable'}")

                if usable:
                    await scorer.record_result(raw_proxy, True, time.time() - start_time, scheme=scheme)
                    return html, final_url, False
                else:
                    await scorer.record_result(raw_proxy, False, time.time() - start_time)
                    continue
            else:
                await scorer.record_result(raw_proxy, False, time.time() - start_time)
                if status in (403, 429, 503):
                    continue
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} exception={type(e).__name__}: {e}")
            print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
            await scorer.record_result(raw_proxy, False, time.time() - start_time)

    return None, url, True

# ---------------------------------------------------------------------------
# Tier 3: BrowserEngine (full JS rendering)
# ---------------------------------------------------------------------------
async def fetch_with_playwright(dork: str, start: int = 0) -> Tuple[Optional[str], str, bool]:
    global scorer

    url = _google_url(dork, start, basic=False)

    raw_proxy = await scorer.get_best_proxy()
    candidates = scorer.get_scheme_candidates(raw_proxy) if raw_proxy else [None]
    preferred = candidates[0] if candidates else None
    scheme = preferred.split("://")[0] if preferred and "://" in preferred else None

    print(f"[DIAG][PROXY] selected={_mask_proxy(raw_proxy)} preferred_scheme={scheme}")
    start_time = time.time()

    async with BROWSER_SEMAPHORE:
        try:
            async with BrowserEngine(proxy=preferred, block_resources=True) as engine:
                if not engine.enabled:
                    print("[DIAG][BROWSER] navigation=failed reason=engine_not_enabled")
                    print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
                    if raw_proxy:
                        await scorer.record_result(raw_proxy, False, time.time() - start_time)
                    return None, url, True

                html, final_url = await engine.request(url)
                elapsed = time.time() - start_time
                bytes_len = len(html) if html else 0

                # bytes=0 or None → NAVIGATION_FAILED (not EMPTY_RESULTS)
                if not html or bytes_len == 0:
                    print(f"[DIAG][BROWSER] navigation=failed bytes=0")
                    print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
                    await scorer.record_result(raw_proxy, False, elapsed)
                    return None, final_url or url, True

                page_type = classify_google_page(html)
                usable = is_usable_search_html(html, final_url)

                print(
                    f"[DIAG][BROWSER] navigation=success bytes={bytes_len} "
                    f"page_type={page_type} usable={usable} elapsed={elapsed:.2f}s"
                )

                if usable:
                    await scorer.record_result(raw_proxy, True, elapsed, scheme=scheme)
                    return html, final_url, False
                else:
                    await scorer.record_result(raw_proxy, False, elapsed)
                    return None, final_url or url, True

        except Exception as e:
            # Exception during goto / engine lifecycle → NAVIGATION_FAILED
            print(f"[DIAG][BROWSER] navigation=failed exception={type(e).__name__}: {e}")
            print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
            if raw_proxy:
                await scorer.record_result(raw_proxy, False, time.time() - start_time)

    return None, url, True

# ---------------------------------------------------------------------------
# Escalation Matrix
# ---------------------------------------------------------------------------
async def fetch_page_with_escalation(
    session: AsyncSession,
    dork: str,
    start: int = 0,
) -> Optional[str]:
    logger.info(f"[TIER1] Ghost attack start={start}")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="chrome120", tier_label="TIER1")
    if html and not poisoned:
        return html

    await humanized_delay(2.5, 5.0)

    logger.info("[TIER2] Re-attack with new identity")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="edge99", tier_label="TIER2")
    if html and not poisoned:
        return html

    await humanized_delay(3.0, 6.0)

    logger.info("[TIER3] Escalating to Chromium (full render)")
    html, final_url, poisoned = await fetch_with_playwright(dork, start)
    if html and not poisoned:
        return html

    logger.warning("[ESCALATION] All tiers failed for this page")
    return None

# ---------------------------------------------------------------------------
# Main search cycle
# ---------------------------------------------------------------------------
async def execute_search_cycle(session: AsyncSession, dork: str, limit: int) -> int:
    print(f"\n[DIAG][DORK] start | {dork}")
    print(f"🚀 Executing Dork: {dork}")

    captured = 0
    start = 0
    max_pages = 3

    for page_idx in range(max_pages):
        if captured >= limit:
            break

        html = await fetch_page_with_escalation(session, dork, start=start)

        if not html:
            print("[DIAG][RESPONSE] received=false bytes=0")
            print("   ❌ Page acquisition failed after full escalation.")
            await humanized_delay(4.0, 7.0)
            continue

        print(f"[DIAG][RESPONSE] received=true bytes={len(html)}")

        domains = parse_google_results(html)
        if not domains:
            print("[DIAG][OUTPUT] domain_candidates=0 new_targets=0 duplicates=0")
            print("   ⚠️ No usable domains extracted.")
            start += 30
            await humanized_delay(3.0, 6.0)
            continue

        new_count = 0
        dup_count = 0
        for domain in domains:
            if captured >= limit:
                break
            if save_target(domain):
                print(f"   ✅ [CAPTURED] {domain}")
                captured += 1
                new_count += 1
            else:
                dup_count += 1

        print(f"[DIAG][OUTPUT] domain_candidates={len(domains)} new_targets={new_count} duplicates={dup_count}")

        start += 30
        await humanized_delay(3.0, 6.5)

    print(f"[DIAG][DORK] COMPLETE | captured={captured}")
    return captured

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def start_feeding():
    global scorer

    print("\n🕷️  DΞMON HYBRID FEEDER v1.7 (NAVIGATION_FAILED / EMPTY_RESULTS)  🕷️")
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
                    await humanized_delay(5.0, 10.0)

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
    print("👉 Next: python harvester.py / orchestrator.py")

def main():
    try:
        asyncio.run(start_feeding())
    except KeyboardInterrupt:
        print("\n🛑 Hybrid Feeder terminated.")

if __name__ == "__main__":
    main()
