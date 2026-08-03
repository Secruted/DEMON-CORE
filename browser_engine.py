"""
Browser Engine - Playwright Integration
---------------------------------------
Optional heavy stealth engine.
Activated only when needed (not default).

Requirements on VPS:
    pip install playwright
    playwright install firefox

For higher stealth later: replace with Camoufox.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger("BROWSER_ENGINE")

class BrowserEngine:
    def __init__(self, headless: bool = True, proxy: str = None):
        self.headless = headless
        self.proxy = proxy
        self.playwright = None
        self.browser = None
        self.context = None
        self.enabled = False

    async def start(self):
        try:
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

            launch_args = {
                "headless": self.headless,
            }

            if self.proxy:
                # proxy format: http://ip:port or socks5://ip:port
                launch_args["proxy"] = {"server": self.proxy}

            self.browser = await self.playwright.firefox.launch(**launch_args)

            self.context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )

            self.enabled = True
            logger.info("[BROWSER] Playwright Firefox engine started successfully.")

        except ImportError:
            logger.error("[BROWSER] Playwright not installed. Run: pip install playwright && playwright install firefox")
            self.enabled = False
        except Exception as e:
            logger.error(f"[BROWSER] Failed to start engine: {e}")
            self.enabled = False

    async def request(self, url: str) -> Tuple[Optional[str], str]:
        if not self.enabled or not self.context:
            return None, url

        page = None
        try:
            page = await self.context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            if response and response.status == 200:
                content = await page.content()
                final_url = page.url
                return content, final_url
            else:
                return None, url

        except Exception as e:
            logger.debug(f"[BROWSER] Request failed for {url}: {e}")
            return None, url
        finally:
            if page:
                await page.close()

    async def close(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("[BROWSER] Engine closed.")
        except Exception:
            pass
