#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v2.0
================================
- Parallel scheme race (HTTP/SOCKS4/SOCKS5) FIRST_COMPLETED
- Rotate up to PROXY_RETRIES_PER_TIER proxies inside each tier
  before escalating (dead/CAPTCHA proxies no longer kill the whole tier)
- Acquisition only; extraction via SearchProvider
"""

import os
import time
import asyncio
import random
import logging
from urllib.parse import quote_plus
from typing import Optional, Tuple, List, Any, Set

from curl_cffi.requests import AsyncSession

from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers
from browser_engine import BrowserEngine
from search_provider import get_search_provider, SearchProvider

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_FILE = "targets.txt"
DORKS_FILE = "dorks.txt"

MAX_BROWSER_SESSIONS = int(os.getenv("MAX_BROWSER_SESSIONS", "1"))
BROWSER_SEMAPHORE = asyncio.Semaphore(MAX_BROWSER_SESSIONS)
SEARCH_PROVIDER_NAME = os.getenv("SEARCH_PROVIDER", "google")

SCHEME_PROBE_TIMEOUT = float(os.getenv("SCHEME_PROBE_TIMEOUT", "10"))
FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "20"))
# How many different proxies to try inside ONE tier before giving up that tier
PROXY_RETRIES_PER_TIER = int(os.getenv("PROXY_RETRIES_PER_TIER", "5"))

logger = logging.getLogger("HYBRID_FEEDER")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

scorer: Optional[ProxyScorer] = None
provider: Optional[SearchProvider] = None

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

def is_usable_search_html(html: Optional[str], final_url: str = "") -> bool:
    if not html or provider is None:
        return False
    page_type = provider.classify(html)
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

async def humanized_delay(min_s: float = 2.8, max_s: float = 7.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

def _google_url(dork: str, start: int = 0, basic: bool = True) -> str:
    query = quote_plus(dork)
    extra = "&gbv=1" if basic else ""
    return f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}{extra}"

# ---------------------------------------------------------------------------
# Parallel scheme race
# ---------------------------------------------------------------------------
async def _race_schemes(
    session: AsyncSession,
    url: str,
    headers: dict,
    candidates: List[str],
    impersonate: str,
    tier_label: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], float]:
    async def _probe(proxy_url: str) -> Tuple[bool, str, str, int, str, str, float]:
        scheme = proxy_url.split("://")[0] if "://" in proxy_url else "unknown"
        t0 = time.time()
        try:
            resp = await session.get(
                url,
                headers=headers,
                proxy=proxy_url,
                timeout=SCHEME_PROBE_TIMEOUT,
                allow_redirects=True,
                impersonate=impersonate,
            )
            elapsed = time.time() - t0
            html = resp.text or ""
            final_url = str(resp.url) if hasattr(resp, "url") else url
            ok = resp.status_code == 200 and len(html) > 0
            print(
                f"[DIAG][RACE] tier={tier_label} scheme={scheme} "
                f"status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s "
                f"result={'WIN' if ok else 'fail'}"
            )
            return ok, proxy_url, scheme, resp.status_code, html, final_url, elapsed
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.time() - t0
            print(
                f"[DIAG][RACE] tier={tier_label} scheme={scheme} "
                f"exception={type(e).__name__}: {e} elapsed={elapsed:.2f}s result=fail"
            )
            return False, proxy_url, scheme, 0, "", url, elapsed

    tasks = {asyncio.create_task(_probe(c)): c for c in candidates}
    pending = set(tasks.keys())
    race_start = time.time()

    print(f"[DIAG][RACE] launching {len(pending)} schemes in parallel (timeout={SCHEME_PROBE_TIMEOUT}s)")

    winner: Optional[Tuple[Any, ...]] = None

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            try:
                ok, proxy_url, scheme, status, html, final_url, elapsed = task.result()
            except asyncio.CancelledError:
                continue
            except Exception as e:
                print(f"[DIAG][RACE] task error: {type(e).__name__}: {e}")
                continue

            if ok and winner is None:
                winner = (proxy_url, scheme, html, final_url, elapsed)
                for p in pending:
                    p.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                pending.clear()
                break

    total_elapsed = time.time() - race_start

    if winner:
        proxy_url, scheme, html, final_url, elapsed = winner
        print(f"[DIAG][RACE] WINNER scheme={scheme} elapsed={elapsed:.2f}s total_race={total_elapsed:.2f}s")
        return proxy_url, scheme, html, final_url, elapsed

    print(f"[DIAG][RACE] all schemes failed total_race={total_elapsed:.2f}s")
    return None, None, None, None, total_elapsed


async def _try_one_proxy_curl(
    session: AsyncSession,
    url: str,
    headers: dict,
    raw_proxy: str,
    impersonate: str,
    tier_label: str,
) -> Tuple[Optional[str], str, bool]:
    """Attempt a single proxy (with scheme race). Returns (html, final_url, poisoned)."""
    candidates = scorer.get_scheme_candidates(raw_proxy)
    schemes = [c.split("://")[0] for c in candidates]
    print(f"[DIAG][PROXY] selected={_mask_proxy(raw_proxy)} candidates={schemes}")
    start_time = time.time()

    if len(candidates) == 1:
        proxy_url = candidates[0]
        scheme = schemes[0]
        try:
            t0 = time.time()
            resp = await session.get(
                url,
                headers=headers,
                proxy=proxy_url,
                timeout=FETCH_TIMEOUT,
                allow_redirects=True,
                impersonate=impersonate,
            )
            html = resp.text or ""
            final_url = str(resp.url) if hasattr(resp, "url") else url
            elapsed = time.time() - t0
            print(
                f"[DIAG][HTTP] tier={tier_label} scheme={scheme} "
                f"status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s"
            )

            if resp.status_code == 200 and html:
                usable = is_usable_search_html(html, final_url)
                page_type = provider.classify(html) if provider else "UNKNOWN"
                print(f"[DIAG][PAGE_TYPE] {page_type} → {'usable' if usable else 'unusable'}")
                if usable:
                    await scorer.record_result(raw_proxy, True, time.time() - start_time, scheme=scheme)
                    return html, final_url, False
                await scorer.record_result(raw_proxy, False, time.time() - start_time)
                return None, final_url, True

            await scorer.record_result(raw_proxy, False, time.time() - start_time)
            return None, final_url, True
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} exception={type(e).__name__}: {e}")
            await scorer.record_result(raw_proxy, False, time.time() - start_time)
            return None, url, True

    proxy_url, scheme, html, final_url, elapsed = await _race_schemes(
        session, url, headers, candidates, impersonate, tier_label
    )

    if proxy_url and html and scheme:
        usable = is_usable_search_html(html, final_url or url)
        page_type = provider.classify(html) if provider else "UNKNOWN"
        print(f"[DIAG][PAGE_TYPE] {page_type} → {'usable' if usable else 'unusable'}")
        if usable:
            await scorer.record_result(raw_proxy, True, time.time() - start_time, scheme=scheme)
            return html, final_url or url, False
        await scorer.record_result(raw_proxy, False, time.time() - start_time)
        return None, final_url or url, True

    await scorer.record_result(raw_proxy, False, time.time() - start_time)
    print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
    return None, url, True


# ---------------------------------------------------------------------------
# Tier 1 & Tier 2: curl + multi-proxy rotation
# ---------------------------------------------------------------------------
async def fetch_with_curl(
    session: AsyncSession,
    dork: str,
    start: int = 0,
    impersonate: str = "chrome120",
    tier_label: str = "TIER1",
) -> Tuple[Optional[str], str, bool]:
    global scorer

    url = _google_url(dork, start, basic=True)
    headers = get_stealth_headers(mobile_bias=0.20)

    tried: Set[str] = set()

    for attempt in range(1, PROXY_RETRIES_PER_TIER + 1):
        raw_proxy = await scorer.get_best_proxy()

        if not raw_proxy:
            print("[DIAG][PROXY] no proxy available → direct")
            try:
                t0 = time.time()
                resp = await session.get(
                    url,
                    headers=headers,
                    timeout=FETCH_TIMEOUT,
                    allow_redirects=True,
                    impersonate=impersonate,
                )
                html = resp.text or ""
                final_url = str(resp.url) if hasattr(resp, "url") else url
                elapsed = time.time() - t0
                print(
                    f"[DIAG][HTTP] tier={tier_label} scheme=direct "
                    f"status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s"
                )
                if not html:
                    return None, final_url, True
                page_type = provider.classify(html) if provider else "UNKNOWN"
                if resp.status_code == 200 and is_usable_search_html(html, final_url):
                    print(f"[DIAG][PAGE_TYPE] {page_type} → usable")
                    return html, final_url, False
                print(f"[DIAG][PAGE_TYPE] {page_type} → unusable")
                return None, final_url, True
            except Exception as e:
                print(f"[DIAG][HTTP] tier={tier_label} scheme=direct exception={type(e).__name__}: {e}")
                return None, url, True

        # Skip already-tried proxy in this tier loop
        if raw_proxy in tried:
            print(f"[DIAG][PROXY] skip already-tried {_mask_proxy(raw_proxy)} ({attempt}/{PROXY_RETRIES_PER_TIER})")
            # Force scorer to deprioritize by recording soft fail if stuck
            await scorer.record_result(raw_proxy, False, 0.01)
            continue

        tried.add(raw_proxy)
        print(f"[DIAG][PROXY] attempt={attempt}/{PROXY_RETRIES_PER_TIER}")

        html, final_url, poisoned = await _try_one_proxy_curl(
            session, url, headers, raw_proxy, impersonate, tier_label
        )

        if html and not poisoned:
            return html, final_url, False

        # Dead / CAPTCHA / unusable → rotate to next proxy inside same tier
        print(f"[DIAG][PROXY] rotate after failure attempt={attempt}/{PROXY_RETRIES_PER_TIER}")

    print(f"[DIAG][PROXY] exhausted {PROXY_RETRIES_PER_TIER} proxies in {tier_label}")
    return None, url, True


# ---------------------------------------------------------------------------
# Tier 3: BrowserEngine + multi-proxy rotation
# ---------------------------------------------------------------------------
async def fetch_with_playwright(dork: str, start: int = 0) -> Tuple[Optional[str], str, bool]:
    global scorer

    url = _google_url(dork, start, basic=False)
    tried: Set[str] = set()

    for attempt in range(1, PROXY_RETRIES_PER_TIER + 1):
        raw_proxy = await scorer.get_best_proxy()
        if not raw_proxy:
            print("[DIAG][BROWSER] no proxy available")
            return None, url, True

        if raw_proxy in tried:
            await scorer.record_result(raw_proxy, False, 0.01)
            continue

        tried.add(raw_proxy)
        candidates = scorer.get_scheme_candidates(raw_proxy)
        preferred = candidates[0] if candidates else None
        scheme = preferred.split("://")[0] if preferred and "://" in preferred else None

        print(
            f"[DIAG][PROXY] attempt={attempt}/{PROXY_RETRIES_PER_TIER} "
            f"selected={_mask_proxy(raw_proxy)} preferred_scheme={scheme}"
        )
        start_time = time.time()

        async with BROWSER_SEMAPHORE:
            try:
                async with BrowserEngine(proxy=preferred, block_resources=True) as engine:
                    if not engine.enabled:
                        print("[DIAG][BROWSER] navigation=failed reason=engine_not_enabled")
                        await scorer.record_result(raw_proxy, False, time.time() - start_time)
                        continue

                    html, final_url = await engine.request(url)
                    elapsed = time.time() - start_time
                    bytes_len = len(html) if html else 0

                    if not html or bytes_len == 0:
                        print("[DIAG][BROWSER] navigation=failed bytes=0")
                        print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
                        await scorer.record_result(raw_proxy, False, elapsed)
                        continue

                    page_type = provider.classify(html) if provider else "UNKNOWN"
                    usable = is_usable_search_html(html, final_url)

                    print(
                        f"[DIAG][BROWSER] navigation=success bytes={bytes_len} "
                        f"page_type={page_type} usable={usable} elapsed={elapsed:.2f}s"
                    )

                    if usable:
                        await scorer.record_result(raw_proxy, True, elapsed, scheme=scheme)
                        return html, final_url, False

                    # CAPTCHA / blocked → burn this proxy, try next
                    await scorer.record_result(raw_proxy, False, elapsed)
                    print(f"[DIAG][PROXY] rotate after {page_type} attempt={attempt}/{PROXY_RETRIES_PER_TIER}")
                    continue

            except Exception as e:
                print(f"[DIAG][BROWSER] navigation=failed exception={type(e).__name__}: {e}")
                print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
                await scorer.record_result(raw_proxy, False, time.time() - start_time)
                continue

    print(f"[DIAG][PROXY] exhausted {PROXY_RETRIES_PER_TIER} proxies in TIER3")
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
    html, final_url, poisoned = await fetch_with_curl(
        session, dork, start, impersonate="chrome120", tier_label="TIER1"
    )
    if html and not poisoned:
        return html

    await humanized_delay(2.5, 5.0)

    logger.info("[TIER2] Re-attack with new identity")
    html, final_url, poisoned = await fetch_with_curl(
        session, dork, start, impersonate="edge99", tier_label="TIER2"
    )
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

        extract = provider.extract_domains(html)
        domains = extract.domains

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

        print(
            f"[DIAG][OUTPUT] domain_candidates={len(domains)} "
            f"new_targets={new_count} duplicates={dup_count}"
        )

        start += 30
        await humanized_delay(3.0, 6.5)

    print(f"[DIAG][DORK] COMPLETE | captured={captured}")
    return captured


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def start_feeding():
    global scorer, provider

    print("\n🕷️  DΞMON HYBRID FEEDER v2.0 (MULTI-PROXY ROTATION)  🕷️")
    print("---------------------------------------------------------------")
    print(f"[CONFIG] MAX_BROWSER_SESSIONS = {MAX_BROWSER_SESSIONS}")
    print(f"[CONFIG] SEARCH_PROVIDER = {SEARCH_PROVIDER_NAME}")
    print(f"[CONFIG] SCHEME_PROBE_TIMEOUT = {SCHEME_PROBE_TIMEOUT}s")
    print(f"[CONFIG] PROXY_RETRIES_PER_TIER = {PROXY_RETRIES_PER_TIER}")

    provider = get_search_provider(SEARCH_PROVIDER_NAME)
    print(f"[PROVIDER] Active: {provider.name}")

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
