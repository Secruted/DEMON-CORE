#!/usr/bin/env python3
"""
DΞMON CORE - HYBRID FEEDER v1.9
================================
- Parallel scheme race (HTTP/SOCKS4/SOCKS5) with FIRST_COMPLETED
- Fast-fail probe timeout 5s
- Winner takes all; losers cancelled immediately
- Acquisition only; extraction via SearchProvider
"""

import os
import time
import asyncio
import random
import logging
from urllib.parse import quote_plus
from typing import Optional, Tuple, List, Any

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

# Fast-fail: handshake / scheme probe only (not full page budget)
SCHEME_PROBE_TIMEOUT = float(os.getenv("SCHEME_PROBE_TIMEOUT", "5"))
# Full fetch timeout after scheme is known
FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "20"))

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
# Parallel scheme race — FIRST_COMPLETED, cancel losers
# ---------------------------------------------------------------------------
async def _race_schemes(
    session: AsyncSession,
    url: str,
    headers: dict,
    candidates: List[str],
    impersonate: str,
    tier_label: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], float]:
    """
    Fire all scheme candidates concurrently.
    First successful response (status 200 + body) wins.
    Remaining tasks are cancelled immediately.

    Returns: (proxy_url, scheme, html, final_url, elapsed) or (None, None, None, None, elapsed)
    """

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
                # Mercy kill: cancel all remaining probes immediately
                for p in pending:
                    p.cancel()
                # Drain cancellations
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

# ---------------------------------------------------------------------------
# Tier 1 & Tier 2: curl_cffi + parallel scheme race
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
    raw_proxy = await scorer.get_best_proxy()

    print(f"[DIAG][PROXY] selected={_mask_proxy(raw_proxy)}")

    # --- Direct (no proxy) ---
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
            print(f"[DIAG][HTTP] tier={tier_label} scheme=direct status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s")
            if not html:
                print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
                return None, final_url, True
            page_type = provider.classify(html) if provider else "UNKNOWN"
            if resp.status_code == 200 and is_usable_search_html(html, final_url):
                print(f"[DIAG][PAGE_TYPE] {page_type} → usable")
                return html, final_url, False
            print(f"[DIAG][PAGE_TYPE] {page_type} → unusable")
            return None, final_url, True
        except Exception as e:
            print(f"[DIAG][HTTP] tier={tier_label} scheme=direct exception={type(e).__name__}: {e}")
            print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
            return None, url, True

    candidates = scorer.get_scheme_candidates(raw_proxy)
    schemes = [c.split("://")[0] for c in candidates]
    print(f"[DIAG][PROXY] scheme candidates={schemes}")
    start_time = time.time()

    # Single known scheme → no race needed
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
            print(f"[DIAG][HTTP] tier={tier_label} scheme={scheme} status={resp.status_code} bytes={len(html)} elapsed={elapsed:.2f}s")

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
            print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
            await scorer.record_result(raw_proxy, False, time.time() - start_time)
            return None, url, True

    # --- Parallel race for unknown multi-scheme proxies ---
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

        # Scheme connected but content not usable — soft fail
        await scorer.record_result(raw_proxy, False, time.time() - start_time)
        return None, final_url or url, True

    await scorer.record_result(raw_proxy, False, time.time() - start_time)
    print("[DIAG][PAGE_TYPE] FETCH_FAILED → unusable")
    return None, url, True

# ---------------------------------------------------------------------------
# Tier 3: BrowserEngine
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

                if not html or bytes_len == 0:
                    print("[DIAG][BROWSER] navigation=failed bytes=0")
                    print("[DIAG][PAGE_TYPE] NAVIGATION_FAILED → unusable")
                    await scorer.record_result(raw_proxy, False, elapsed)
                    return None, final_url or url, True

                page_type = provider.classify(html) if provider else "UNKNOWN"
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

        print(f"[DIAG][OUTPUT] domain_candidates={len(domains)} new_targets={new_count} duplicates={dup_count}")

        start += 30
        await humanized_delay(3.0, 6.5)

    print(f"[DIAG][DORK] COMPLETE | captured={captured}")
    return captured

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def start_feeding():
    global scorer, provider

    print("\n🕷️  DΞMON HYBRID FEEDER v1.9 (PARALLEL SCHEME RACE)  🕷️")
    print("---------------------------------------------------------------")
    print(f"[CONFIG] MAX_BROWSER_SESSIONS = {MAX_BROWSER_SESSIONS}")
    print(f"[CONFIG] SEARCH_PROVIDER = {SEARCH_PROVIDER_NAME}")
    print(f"[CONFIG] SCHEME_PROBE_TIMEOUT = {SCHEME_PROBE_TIMEOUT}s")

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
