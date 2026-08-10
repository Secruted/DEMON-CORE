import re
import requests
import json
import random
import logging
import time
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from proxy_compat import ProxyCompat

class DomainParser:
    def __init__(self, proxy_manager: ProxyCompat = None):
        """
        Initializes the domain parser with advanced OSINT capabilities.
        Uses ProxyCompat (thin layer over ProxyScorer) instead of the old ProxyManager.
        :param proxy_manager: An instance of ProxyCompat (or compatible object).
        """
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()
        self.proxy_manager = proxy_manager or ProxyCompat()
        self.dir_listing_pattern = re.compile(r"Index of|Listing of|Directory listing for", re.IGNORECASE)
        
        self.default_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "TE": "trailers"
        }

    def _fetch_url(self, url, retries=3, backoff_factor=5, custom_headers=None, timeout=30):
        """
        Helper to fetch content with full retry, proxy rotation, humanized latency, and custom headers.
        """
        user_agent = self.proxy_manager.get_random_user_agent()
        if not user_agent:
            self.logger.warning("No User-Agent available. Using fallback.")
            user_agent = random.choice(self.proxy_manager.user_agents)

        headers = self.default_headers.copy()
        if custom_headers:
            headers.update(custom_headers)
        headers['User-Agent'] = user_agent
        
        self.session.headers.update(headers)

        for attempt in range(retries):
            proxy_info = self.proxy_manager.get_healthy_proxy()
            if not proxy_info:
                self.logger.error(f"No healthy proxies available for {url}. Cannot fetch content.")
                return None
            
            proxies_dict = {'http': proxy_info['http'], 'https': proxy_info['https']}

            try:
                self.logger.debug(f"Fetching {url} with proxy {proxy_info.get('https')} (Attempt {attempt + 1}/{retries})")
                response = self.session.get(url, timeout=timeout, proxies=proxies_dict, allow_redirects=True)
                
                if response.status_code == 429:
                    wait_time = backoff_factor * (2 ** attempt) + random.uniform(1, 5)
                    self.logger.warning(f"Rate limited by {url}. Backing off for {wait_time:.2f}s.")
                    self.proxy_manager.report_failure(proxy_info, duration=wait_time * 2) 
                    time.sleep(wait_time)
                    continue 
                
                if any(phrase in response.text for phrase in ["captcha", "robot check", "Cloudflare I am human"]):
                    self.logger.warning(f"Possible anti-bot/CAPTCHA page detected on {url}.")
                    self.proxy_manager.report_failure(proxy_info, duration=3600) 
                    time.sleep(random.uniform(5, 15)) 
                    continue 
                
                response.raise_for_status() 
                return response.text
            
            except requests.exceptions.HTTPError as e:
                self.logger.error(f"HTTP Error {e.response.status_code} for {url}")
                cooldown_duration_map = {403: 7200, 404: 300, 500: 1800, 429: backoff_factor * (2 ** attempt) * 2}
                self.proxy_manager.report_failure(proxy_info, duration=cooldown_duration_map.get(e.response.status_code, 600))
                if attempt < retries - 1:
                    time.sleep(backoff_factor)
            except requests.exceptions.ConnectionError as e:
                self.logger.error(f"Connection Error for {url}")
                self.proxy_manager.report_failure(proxy_info, duration=900)
                if attempt < retries - 1:
                    time.sleep(backoff_factor)
            except requests.exceptions.Timeout as e:
                self.logger.error(f"Timeout Error for {url}")
                self.proxy_manager.report_failure(proxy_info, duration=600) 
                if attempt < retries - 1:
                    time.sleep(backoff_factor)
            except Exception as e:
                self.logger.critical(f"An unexpected error occurred during fetch for {url}: {e}", exc_info=True)
                self.proxy_manager.report_failure(proxy_info, duration=1800)
                if attempt < retries - 1:
                    time.sleep(backoff_factor)

        self.logger.error(f"All {retries} attempts failed for {url}.")
        return None

    def find_open_directories(self, domain_url):
        """
        Improved strategy to find open directories through common paths and robots.txt analysis.
        """
        found_open_dirs = set()
        self.logger.info(f"  [RECON] Checking for open directories on {domain_url}")
        
        common_paths = ["/backup/", "/temp/", "/.git/", "/admin/", "/config/", "/logs/", "/db/", "/uploads/", "/data/"]
        for path in common_paths:
            test_url = domain_url.rstrip('/') + path
            content = self._fetch_url(test_url, timeout=15)
            if content:
                soup = BeautifulSoup(content, 'html.parser')
                page_title = soup.title.string if soup.title else ""
                if self.dir_listing_pattern.search(page_title) or "parent directory" in content.lower():
                    found_open_dirs.add(test_url)
                    self.logger.info(f"    [SUCCESS] Found open directory: {test_url}")
        
        robots_url = domain_url.rstrip('/') + "/robots.txt"
        robots_content = self._fetch_url(robots_url, timeout=10)
        if robots_content:
            self.logger.info(f"  [RECON] Analyzing robots.txt for {domain_url}")
            for line in robots_content.splitlines():
                if line.lower().startswith("disallow:"):
                    disallowed_path = line[len("disallow:"):].strip()
                    if disallowed_path and disallowed_path != "/":
                        test_url = domain_url.rstrip('/') + disallowed_path
                        content = self._fetch_url(test_url, timeout=10)
                        if content:
                            soup = BeautifulSoup(content, 'html.parser')
                            page_title = soup.title.string if soup.title else ""
                            if self.dir_listing_pattern.search(page_title) or "parent directory" in content.lower():
                                found_open_dirs.add(test_url)
                                self.logger.info(f"    [SUCCESS] Found potentially open directory: {test_url}")

        return list(found_open_dirs)

    def enumerate_subdomains_from_osint(self, root_domain, sources=None):
        """
        Finds subdomains using multiple OSINT sources (e.g., crt.sh).
        """
        found_subdomains = set()
        self.logger.info(f"  [RECON] Enumerating subdomains for {root_domain} via OSINT sources.")

        crt_sh_url = f"https://crt.sh/?q=%25.{root_domain}&output=json"
        self.logger.info(f"    Querying crt.sh for {root_domain}...")
        try:
            content = self._fetch_url(crt_sh_url, custom_headers={"Accept": "application/json"})
            if content:
                certs = json.loads(content)
                for cert in certs:
                    name_values = cert.get('name_value', '').split('\n')
                    common_names = cert.get('common_name', '').split('\n')
                    
                    for name in name_values + common_names:
                        name = name.strip().replace('*.', '')
                        if name.endswith(root_domain) and name != root_domain:
                            found_subdomains.add(name)
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to query crt.sh for {root_domain}: {e}")

        self.logger.info(f"    [SUCCESS] Found a total of {len(found_subdomains)} unique subdomains.")
        return list(found_subdomains)

    def analyze_domain(self, root_domain):
        """Main analysis function for a given root domain."""
        self.logger.info(f"  [RECON] Starting full analysis for root domain: {root_domain}")
        https_url = f"https://{root_domain}"
        
        open_dirs = self.find_open_directories(https_url)
        subdomains = self.enumerate_subdomains_from_osint(root_domain)

        return {"root_domain": root_domain, "open_directories": open_dirs, "found_subdomains": subdomains}
