"""
Browser Engine - Unified Chromium Stealth Engine (v2.1)
-------------------------------------------------------
Centralized heavy escalation engine for the entire DEMON CORE system.

Changes in v2.1:
- close() performs graceful shutdown first
- _force_kill() runs only if graceful cleanup failed
- Force-kill focuses on tracked PIDs only (less aggressive)
"""

import os
import logging
import asyncio
from typing import Optional, Tuple, List

logger = logging.getLogger("BROWSER_ENGINE")

class BrowserEngine:
    """
    Unified Chromium engine with controlled force-kill and virtual display.
    Designed for constrained Ubuntu VPS environments.
    """

    def __init__(
        self,
        headless: bool = True,
        proxy: Optional[str] = None,
        block_resources: bool = True,
    ):
        self.headless = headless
        self.proxy = proxy
        self.block_resources = block_resources

        self.playwright = None
        self.browser = None
        self.context = None
        self.display = None
        self.enabled = False
        self._pids: List[int] = []
        self._cleanup_failed = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Launch Chromium with full hardening and virtual display if needed."""
        try:
            # --- Automatic virtual display on headless Linux ---
            if os.name == "posix" and not os.environ.get("DISPLAY"):
                try:
                    from pyvirtualdisplay import Display
                    self.display = Display(visible=0, size=(1920, 1080))
                    self.display.start()
                    logger.info("[BROWSER] Virtual display (Xvfb) started")
                except Exception as e:
                    logger.warning(f"[BROWSER] Virtual display failed: {e}")

            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

            launch_args = {
                "headless": self.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--no-first-run",
                    "--safebrowsing-disable-auto-update",
                ],
            }

            if self.proxy:
                server = self.proxy if "://" in self.proxy else f"http://{self.proxy}"
                launch_args["proxy"] = {"server": server}

            self.browser = await self.playwright.chromium.launch(**launch_args)

            # Track browser process PID if available
            try:
                if hasattr(self.browser, "process") and self.browser.process:
                    self._pids.append(self.browser.process.pid)
            except Exception:
                pass

            self.context = await self.browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
                ignore_https_errors=True,
            )

            # Deep stealth injection
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter(parameter);
                };
            """)

            self.enabled = True
            logger.info("[BROWSER] Chromium engine started successfully (stealth + hardened)")

        except ImportError as e:
            logger.error(f"[BROWSER] Missing dependency: {e}")
            self.enabled = False
            self._cleanup_failed = True
        except Exception as e:
            logger.error(f"[BROWSER] Failed to start engine: {e}")
            self.enabled = False
            self._cleanup_failed = True
            await self._force_kill()

    async def request(self, url: str, timeout: int = 35000) -> Tuple[Optional[str], str]:
        """Navigate and return (html, final_url)."""
        if not self.enabled or not self.context:
            return None, url

        page = None
        try:
            page = await self.context.new_page()

            if self.block_resources:
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,otf,mp4,webm,mp3}",
                    lambda route: route.abort()
                )

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout
            )

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
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self):
        """Graceful close first. Force-kill only if cleanup failed."""
        graceful_ok = True

        try:
            if self.context:
                await self.context.close()
        except Exception:
            graceful_ok = False

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            graceful_ok = False

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            graceful_ok = False

        # Only force-kill if graceful path failed or start previously failed
        if not graceful_ok or self._cleanup_failed:
            # Small pause to let pipes settle
            await asyncio.sleep(0.3)
            await self._force_kill()

        # Always try to stop virtual display
        if self.display:
            try:
                self.display.stop()
            except Exception:
                pass
            self.display = None

        self.enabled = False
        self._cleanup_failed = False
        logger.info("[BROWSER] Engine closed")

    async def _force_kill(self):
        """Kill only the PIDs we tracked. Avoid aggressive recursive killing."""
        try:
            import psutil

            for pid in list(self._pids):
                try:
                    p = psutil.Process(pid)
                    # Kill children of this specific process first
                    for child in p.children(recursive=True):
                        try:
                            child.kill()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    p.kill()
                    logger.debug(f"[BROWSER] Force-killed tracked PID {pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                except Exception as e:
                    logger.debug(f"[BROWSER] Force-kill error on {pid}: {e}")

            self._pids.clear()

        except ImportError:
            logger.warning("[BROWSER] psutil not available for force-kill")
        except Exception as e:
            logger.debug(f"[BROWSER] Force-kill error: {e}")
