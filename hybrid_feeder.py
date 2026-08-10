#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v1.4
================================
Escalation Matrix Feeder + Diagnostic Telemetry

Logic unchanged. Only diagnostic hooks added for:
PROXY → HTTP → POISON → BROWSER → RESPONSE → PARSER → OUTPUT
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

scorer: Optional[ProxyScorer] = None

# ---------------------------------------------------------------------------
# Helpers for safe logging
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

def _poison_reason(html: str, final_url: str = "") -> str:
    if not html:
        return "empty_html"
    lower = html.lower()
    url_lower = (final_url or "").lower()
    for sig in POISON_TEXT_SIGNATURES:
        if sig in lower or sig in url_lower:
            return sig
    for sig in POISON_DOM_SIGNATURES:
        if sig in lower:
            return sig
    if "/sorry/" in url_lower or "sorry/index" in url_lower:
        return "sorry_path"
    return "unknown"

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
# Parsing with CSS + Regex fallback + DIAG counters
# ---------------------------------------------------------------------------
def parse_google_results(html: str) -> List[str]:
    domains: List[str] = []
    css_candidates = 0
    css_valid = 0
    regex_candidates = 0
    regex_valid = 0

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
                css_candidates += 1
                href = a.get("href", "")
                if href.startswith("/url?q="):
                    href = href.split("/url?q=")[1].split("&")[0]
                    href = unquote(href)
                if href.startswith("http") and "google." not in href:
                    domain = clean_domain(href)
                    if domain and not any(b in domain for b in BANNED_DOMAINS):
                        css_valid += 1
                        if domain not in domains:
                            domains.append(domain)

        if domains:
            print(f"[DIAG][PARSER] CSS candidates={css_candidates} CSS valid={css_valid} Regex candidates=0 Regex valid=0 final unique={len(domains)}")
            return domains
    except Exception:
        pass

    try:
        pattern = r'/url\?q=(https?://[^&\s"\']+)'
        matches = re.findall(pattern, html)
        for raw in matches:
            regex_candidates += 1
            href = unquote(raw)
            if "google." in href:
                continue
            domain = clean_domain(href)
            if domain and not any(b in domain for b in BANNED_DOMAINS):
                regex_valid += 1
                if domain not in domains:
                    domains.append(domain)
    except Exception:
        pass

    print(f"[DIAG][PARSER] CSS candidates={css_candidates} CSS valid={css_valid} Regex candidates={regex_candidates} Regex valid={regex_valid} final unique={len(domains)}")
    return domains

# ---------------------------------------------------------------------------
# Humanized delay
# ---------------------------------------------------------------------------
async def humanized_delay(min_s: float = 2.8, max_s: float = 7.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

# ---------------------------------------------------------------------------
# Tier 1 & Tier 2: curl_cffi with scheme candidates + DIAG
# ---------------------------------------------------------------------------
async def fetch_with_curl(
    session: AsyncSession,
    dork: str,
    start: int = 0,
    impersonate: str = "chrome120",
    tier_label: str = "TIER1",
) -> Tuple[Optional[str], str, bool]:
    """Returns (html, final_url, is_poisoned)"""
    global scorer

    query = quote_plus(dork)
    url = f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}"

    headers = get_stealth_headers(mobile_bias=0.20)
    raw_proxy = await scorer.get_best_proxy()

    print(f"[DIAG][PROXY] selected={_mask_proxy(raw_proxy)}")

    if not raw_proxy:
        print("[DIAG][PROXY] no proxy available → direct")
        try:
            t0 = time.time()
            resp = await session.get(url, headers=headers, timeout=28, allow_redirects=True, impersonate=impersonate)
            html = resp.text or ""
            final_url = str(resp.url) if hasattr(resp, "url") else url
            elapsed = time.time() - t0
            poisoned = is_poisoned(html, final_url) if resp.status_code == 200 else False
            print(f"[DIAG][HTTP] tier={tier_label} scheme=direct status={resp.status_code} bytes={len(html)} final_url={final_url[:80]} elapsed={elapsed:.2f}s")
            if resp.status_code == 200 and not poisoned:
                return html, final_url, False
            if resp.status_code == 200 and poisoned:
                print(f"[DIAG][POISON] detected=true reason={_poison_reason(html, final_url)}")
            return None, final_url, True
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme=direct exception={type(e).__name__}: {e}")
            return None, url, True

    candidates = scorer.get_scheme_candidates(raw_proxy)
    print(f"[DIAG][PROXY] scheme candidates={[c.split('://')[0] for c in candidates]}")
    start_time = time.time()

    for proxy_url in candidates:
        scheme = proxy_url.split("://")[0] if "://" in proxy_url else "unknown"

        try:
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

            print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} status={resp.status_code} bytes={len(html)} final_url={final_url[:80]} elapsed={elapsed:.2f}s")

            if resp.status_code == 200:
                if is_poisoned(html, final_url):
                    reason = _poison_reason(html, final_url)
                    print(f"[DIAG][POISON] detected=true reason={reason}")
                    await scorer.record_result(raw_proxy, False, time.time() - start_time)
                    continue
                else:
                    print(f"[DIAG][POISON] detected=false")
                    await scorer.record_result(raw_proxy, True, time.time() - start_time, scheme=scheme)
                    return html, final_url, False
            else:
                await scorer.record_result(raw_proxy, False, time.time() - start_time)
                if resp.status_code in (403, 429, 503):
                    continue
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} exception={type(e).__name__}: {e}")
            await scorer.record_result(raw_proxy, False, time.time() - start_time)

    return None, url, True

# ---------------------------------------------------------------------------
# Tier 3: Unified BrowserEngine + DIAG
# ---------------------------------------------------------------------------
async def fetch_with_playwright(dork: str, start: int = 0) -> Tuple[Optional[str], str, bool]:
    global scorer

    query = quote_plus(dork)
    url = f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}"

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
                    if raw_proxy:
                        await scorer.record_result(raw_proxy, False, time.time() - start_time)
                    return None, url, True

                html, final_url = await engine.request(url)
                elapsed = time.time() - start_time
                bytes_len = len(html) if html else 0
                poisoned = is_poisoned(html or "", final_url) if html else True

                print(f"[DIAG][BROWSER] navigation={'success' if html else 'failed'} bytes={bytes_len} final_url={(final_url or '')[:80]} poisoned={poisoned} elapsed={elapsed:.2f}s")

                if html and not poisoned:
                    print(f"[DIAG][POISON] detected=false")
                    await scorer.record_result(raw_proxy, True, elapsed, scheme=scheme)
                    return html, final_url, False
                else:
                    if html and poisoned:
                        print(f"[DIAG][POISON] detected=true reason={_poison_reason(html, final_url)}")
                    await scorer.record_result(raw_proxy, False, elapsed)
                    return None, final_url, True

        except Exception as e:
            print(f"[DIAG][BROWSER] navigation=failed exception={type(e).__name__}: {e}")
            if raw_proxy:
                await scorer.record_result(raw_proxy, False, time.time() - start_time)

    return None, url, True

# ---------------------------------------------------------------------------
# Escalation Matrix for a single page
# ---------------------------------------------------------------------------
async def fetch_page_with_escalation(
    session: AsyncSession,
    dork: str,
    start: int = 0,
) -> Optional[str]:
    logger.info(f"[TIER1] Ghost attack for dork start={start}")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="chrome120", tier_label="TIER1")
    if html and not poisoned:
        return html

    await humanized_delay(3.5, 6.5)

    logger.info("[TIER2] Tactical re-attack with new identity")
    html, final_url, poisoned = await fetch_with_curl(session, dork, start, impersonate="edge99", tier_label="TIER2")
    if html and not poisoned:
        return html

    await humanized_delay(4.0, 8.0)

    logger.info("[TIER3] Escalating to unified Chromium BrowserEngine (preferred scheme only)")
    html, final_url, poisoned = await fetch_with_playwright(dork, start)
    if html and not poisoned:
        return html

    logger.warning("[ESCALATION] All tiers failed for this page")
    return None

# ---------------------------------------------------------------------------
# Main search cycle + DIAG RESPONSE / OUTPUT
# ---------------------------------------------------------------------------
async def execute_search_cycle(session: AsyncSession, dork: str, limit: int) -> int:
    print(f"\n[DIAG][DORK] start | {dork}")
    print(f"🚀 Executing Dork: {dork}")
    print("   Hybrid Escalation Matrix + Scheme candidates active...")

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
            await humanized_delay(5.0, 9.0)
            continue

        print(f"[DIAG][RESPONSE] received=true bytes={len(html)}")

        domains = parse_google_results(html)
        if not domains:
            print("[DIAG][OUTPUT] domain_candidates=0 new_targets=0 duplicates=0")
            print("   ⚠️ No usable domains extracted (CSS + Regex both empty).")
            break

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
        await humanized_delay(3.5, 7.5)

    print(f"[DIAG][DORK] COMPLETE | captured={captured}")
    return captured

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def start_feeding():
    global scorer

    print("\n🕷️  DΞMON HYBRID FEEDER v1.4 (DIAGNOSTIC TELEMETRY)  🕷️")
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
