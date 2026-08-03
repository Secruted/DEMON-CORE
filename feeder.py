import os
import time
import asyncio
import random
import signal
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from proxy_scoring import ProxyScorer
from stealth import get_stealth_headers

# DΞMON CORE - FEEDER MODULE v3.0 (Async + ProxyScorer + Stealth)
# FUNCTION: MASS TARGET ACQUISITION (File & Manual Support)
# Fully integrated with system architecture (asyncio, ProxyScorer, stealth, curl_cffi)

TARGET_FILE = "targets.txt"
DORKS_FILE = "dorks.txt"
BANNED_DOMAINS = ["google", "youtube", "facebook", "github.com", "gitlab.com", "stackoverflow", "microsoft", "bing", "yahoo"]

# Global scorer instance (shared across the feeder session)
scorer: ProxyScorer = None

def clean_domain(url: str):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if ":" in domain:
            domain = domain.split(":")[0]
        return domain.replace("www.", "").lower()
    except Exception:
        return None

def save_target(domain: str) -> bool:
    if not os.path.exists(TARGET_FILE):
        open(TARGET_FILE, "w").close()
    with open(TARGET_FILE, "r", encoding="utf-8", errors="ignore") as f:
        existing = f.read()
    if domain not in existing:
        with open(TARGET_FILE, "a", encoding="utf-8") as f:
            f.write(f"{domain}\n")
        return True
    return False

async def humanized_delay(min_s: float = 2.5, max_s: float = 6.0):
    """Random human-like pause to reduce detection risk."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)

async def fetch_google_page(session: AsyncSession, dork: str, start: int = 0) -> str | None:
    """
    Perform a single Google search request using curl_cffi + stealth headers + proxy.
    Returns HTML text or None on failure.
    """
    global scorer

    query = quote_plus(dork)
    url = f"https://www.google.com/search?q={query}&num=30&hl=en&start={start}"

    headers = get_stealth_headers(mobile_bias=0.25)
    proxy = await scorer.get_best_proxy()
    formatted_proxy = None
    if proxy:
        formatted_proxy = proxy if "://" in proxy else f"http://{proxy}"

    start_time = time.time()
    success = False
    html = None

    try:
        resp = await session.get(
            url,
            headers=headers,
            proxy=formatted_proxy,
            timeout=25,
            allow_redirects=True,
            impersonate="chrome120"
        )
        if resp.status_code == 200:
            html = resp.text
            success = True
        else:
            # Detect soft blocks
            if resp.status_code in (403, 429, 503) or "captcha" in (resp.text or "").lower():
                success = False
    except Exception:
        success = False

    elapsed = time.time() - start_time
    if proxy:
        await scorer.record_result(proxy=proxy, success=success, response_time=elapsed)

    return html if success else None

def parse_google_results(html: str) -> list[str]:
    """Extract clean domains from Google search HTML using BeautifulSoup."""
    domains = []
    try:
        soup = BeautifulSoup(html, "lxml")
        # Common result containers (Google changes these, so we keep it flexible)
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            # Google often wraps real links as /url?q=...
            if href.startswith("/url?q="):
                href = href.split("/url?q=")[1].split("&")[0]
            if href.startswith("http") and "google." not in href:
                domain = clean_domain(href)
                if domain and not any(banned in domain for banned in BANNED_DOMAINS):
                    if domain not in domains:
                        domains.append(domain)
    except Exception:
        pass
    return domains

async def execute_search_cycle(session: AsyncSession, dork: str, limit: int) -> int:
    """Execute one dork and capture up to `limit` new targets."""
    print(f"\n🚀 Executing Dork: {dork}")
    print("   Scanning sector with curl_cffi + stealth + ProxyScorer...")

    captured = 0
    start = 0
    max_pages = 3  # Safety limit to avoid excessive requests

    for page in range(max_pages):
        if captured >= limit:
            break

        html = await fetch_google_page(session, dork, start=start)
        if not html:
            print("   ❌ Failed to fetch page (possible block or bad proxy). Trying next...")
            await humanized_delay(4.0, 8.0)
            continue

        domains = parse_google_results(html)
        if not domains:
            print("   ⚠️ No usable results extracted from this page.")
            break

        for domain in domains:
            if captured >= limit:
                break
            if save_target(domain):
                print(f"   ✅ [CAPTURED] {domain}")
                captured += 1

        start += 30
        await humanized_delay(3.0, 7.0)  # Pause between pages

    return captured

async def start_feeding():
    global scorer

    print("\n🕷️  DΞMON FEEDER v3.0 (ASYNC + PROXYSCORER + STEALTH)  🕷️")
    print("---------------------------------------------------------------")

    # Initialize ProxyScorer (shared with the rest of the system)
    scorer = ProxyScorer(proxy_file="proxy.txt", state_file="proxy_scores.json")
    summary = await scorer.get_stats_summary()
    print(f"[SCORER] Loaded | Total: {summary['total']} | Alive: {summary['alive']} | Avg Score: {summary['avg_score']}")

    mode = input("Select Mode: [1] Manual Input  [2] Read dorks.txt : ").strip()

    try:
        limit = int(input("🔢 Targets per Dork (Recommended 20-30): ").strip())
    except Exception:
        limit = 25

    total_captured = 0

    async with AsyncSession() as session:
        try:
            if mode == "2":
                if not os.path.exists(DORKS_FILE):
                    print(f"❌ Error: {DORKS_FILE} not found!")
                    return

                with open(DORKS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    dorks = [line.strip() for line in f if line.strip()]

                print(f"🔥 Loaded {len(dorks)} dorks. Starting bulk acquisition...")

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
            print("\n⚠️  Interrupted by user (CTRL+C). Saving proxy state...")
        finally:
            # Always save proxy scores so the main system benefits
            await scorer.save_state()
            print("[SCORER] State saved to proxy_scores.json")

    print(f"\n🩸 MISSION COMPLETE. Total New Targets: {total_captured}")
    print(f"👉 Now run: python harvester.py")

def main():
    try:
        asyncio.run(start_feeding())
    except KeyboardInterrupt:
        print("\n🛑 Feeder terminated.")

if __name__ == "__main__":
    main()
