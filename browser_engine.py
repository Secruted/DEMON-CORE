"""
Browser Engine - Unified Chromium Stealth Engine (v2.0)
-------------------------------------------------------
Centralized heavy escalation engine for the entire DEMON CORE system.

Features:
- Chromium only (fingerprint unification with curl_cffi chrome*)
- Automatic Xvfb via pyvirtualdisplay on headless Linux VPS
- Ruthless process-tree annihilation with psutil (SIGKILL)
- Resource hardening (block images, fonts, media)
- Deep stealth injection via CDP + init scripts
- Async context manager for guaranteed cleanup
- Concurrency control via external Semaphore (caller responsibility)

Usage:
    async with BrowserEngine(proxy="http://ip:port") as engine:
        html, final_url = await engine.request(url)
"""

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Tuple, List

logger = logging.getLogger("BROWSER_ENGINE")

class BrowserEngine:
    """
    Unified Chromium engine with force-kill and virtual display.
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
                // Webdriver
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

                // Chrome runtime
                window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };

                // Plugins & languages
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });

                // Permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // WebGL vendor spoof (basic)
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
        except Exception as e:
            logger.error(f"[BROWSER] Failed to start engine: {e}")
            self.enabled = False
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
        """Graceful close + ruthless force-kill of any remaining processes."""
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass

        # Ruthless process tree annihilation
        await self._force_kill()

        # Stop virtual display
        if self.display:
            try:
                self.display.stop()
            except Exception:
                pass
            self.display = None

        self.enabled = False
        logger.info("[BROWSER] Engine closed and process tree cleaned")

    async def _force_kill(self):
        """Kill entire Chromium process tree using psutil."""
        try:
            import psutil
            current = psutil.Process()
            children = current.children(recursive=True)

            for child in children:
                try:
                    name = child.name().lower()
                    if "chrom" in name or "playwright" in name:
                        logger.debug(f"[BROWSER] Force-killing PID {child.pid} ({name})")
                        child.kill()  # SIGKILL
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Also try any tracked PIDs
            for pid in self._pids:
                try:
                    p = psutil.Process(pid)
                    p.kill()
                except Exception:
                    pass

            self._pids.clear()

        except ImportError:
            logger.warning("[BROWSER] psutil not available for force-kill")
        except Exception as e:
            logger.debug(f"[BROWSER] Force-kill error: {e}")
