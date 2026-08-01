import re
import json
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from bip39_validator import verify_bip39_checksum
from statuses import Status  # [SEPARATION OF CONCERNS]

class ContentParser:
    def __init__(self, intel_cfg, transport_mgr):
        self.logger = logging.getLogger("PARSER")
        self.intel = intel_cfg
        self.transport = transport_mgr

        # [MEMORY GUARD]
        self.seen_assets = set()
        self.max_seen_limit = self.intel.get("max_seen_limit", 10000)

        # [INTELLIGENCE PATTERNS]
        # Regex remains useful for finding patterns inside JSON values (like AWS keys)
        self.patterns = {
            p["type"]: re.compile(p["pattern"]) for p in self.intel.get("regex_patterns", [])
        }

        # [DIRECT JSON KEYS]
        # If we are in JSON mode, we grab these keys immediately regardless of regex
        self.gold_keys = {
            'password', 'passwd', 'secret', 'token', 'access_token', 'api_key', 
            'mnemonic', 'seed', 'private_key', 'balance', 'wallet', 'credential'
        }

        # [BIP-39 VALIDATION]
        try:
            with open("bip39_english.txt", "r") as f:
                self.bip39_wordlist = [w.strip().lower() for w in f]
            self.bip39_set = set(self.bip39_wordlist)
            self.logger.info(f"[INIT] Intelligence Core Online. Patterns: {len(self.patterns)}.")
        except FileNotFoundError:
            self.logger.error("[!] bip39_english.txt missing.")
            self.bip39_wordlist, self.bip39_set = [], set()

    def process_url(self, url):
        """
        🔧 [HYBRID ENGINE]: Automatically switches between JSON-Hunter and HTML-Scraper.
        """
        html, final_url = self.transport.request(url)

        if not html:
            return Status.FAILED_FETCH, []

        # [🔥 DΞMON FAST-TRACK: JSON MODE]
        # Check if content looks like JSON (starts with { or [)
        stripped = html.strip()
        if stripped and stripped[0] in ('{', '['):
            return self._engage_json_mode(html)

        # [LEGACY MODE: HTML ROUTER & SCRAPER]
        if "google.com" in url or "github.com/search" in url:
            leads = self._extract_strategic_leads(html)
            return Status.ROUTER_SIGNAL, leads

        # [CONTENT STRIPPING (Only for HTML)]
        soup = BeautifulSoup(html, "html.parser")
        for junk in soup(["script", "style", "nav", "footer"]): junk.decompose()
        text = soup.get_text(separator=" ", strip=True)

        # [ENTROPY SCORING]
        if self._get_hungry_score(text) < 20:
            return Status.SKIPPED_LOW_ENTROPY, []

        # [HUNTING]
        findings = self.extract_valuable_info(text)
        return (Status.HARVEST_SUCCESS, findings) if findings else (Status.SKIPPED_NO_TECH_SIGNALS, [])

    def _engage_json_mode(self, raw_json):
        """
        🚀 [PURE STRUCTURED EXTRACTION]
        Bypasses BeautifulSoup completely. Parses JSON and hunts recursively.
        """
        try:
            data = json.loads(raw_json)
            findings = []
            self._recursive_json_hunt(data, findings)
            
            if findings:
                return Status.HARVEST_SUCCESS, findings
            else:
                return Status.SKIPPED_NO_TECH_SIGNALS, []
        except json.JSONDecodeError:
            # False alarm, treat as text
            return Status.FAILED_FETCH, []

    def _recursive_json_hunt(self, data, findings):
        """
        🕷️ [DEEP RECURSION]
        Drills down into nested JSON objects/lists to find Gold Keys.
        """
        if isinstance(data, dict):
            for k, v in data.items():
                # 1. Check if Key is Gold
                if k.lower() in self.gold_keys:
                    # Capture the value immediately if it's a primitive
                    if isinstance(v, (str, int, float, bool)):
                        findings.append((f"JSON_KEY:{k.upper()}", str(v)))
                
                # 2. Recurse or Scan Value
                self._recursive_json_hunt(v, findings)

        elif isinstance(data, list):
            for item in data:
                self._recursive_json_hunt(item, findings)

        elif isinstance(data, str):
            # 3. If it's a string, run the standard Regex Patterns on it
            # (In case an AWS key is buried inside a JSON value)
            self._scan_text_segment(data, findings)

    def _scan_text_segment(self, text, findings):
        """Helper to run regex on specific text blocks (used by both modes)."""
        if len(text) > 5000: return # Skip massive blobs to save CPU
        
        for p_type, compiled_p in self.patterns.items():
            match = compiled_p.search(text)
            if match:
                val = match.group(0).strip()
                if val in self.seen_assets: continue
                
                if p_type == "CRYPTO_SEED" and not self._validate_seed(val):
                    continue
                
                findings.append((p_type, val))
                self.seen_assets.add(val)

    def _extract_strategic_leads(self, html):
        """[NORMALIZATION]: Prevents duplicate pointer leads."""
        soup = BeautifulSoup(html, "html.parser")
        whitelist = ['pastebin.com', 'gist.github.com', 'gitlab.com', 'bitbucket.org']
        leads = []
        for a in soup.find_all('a', href=True):
            link = a['href']
            if any(domain in link for domain in whitelist) and "google.com" not in link:
                parsed = urlparse(link)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                leads.append(clean_url)
        return list(set(leads))

    def extract_valuable_info(self, text):
        """[LEGACY COMPATIBILITY]: Flushes assets when limit is reached."""
        if len(self.seen_assets) > self.max_seen_limit:
            self.seen_assets.clear()
            self.logger.warning("[MEMORY] Clearing seen_assets to prevent bloat.")

        found = []
        self._scan_text_segment(text, found) # Reusing the centralized scanner
        return found

    def _get_hungry_score(self, text):
        """[HUNGRY SCORING]: Technical signals density."""
        if not text or len(text) < 50: return 0
        symbols = len(re.findall(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', text)) / len(text)
        keywords = ['env', 'key', 'token', 'mnemonic', 'secret', 'sk-', 'ghp_']
        score = (40 if symbols > 0.05 else 0) + (40 if any(k in text.lower() for k in keywords) else 0)
        return score

    def _validate_seed(self, phrase):
        """[BIP-39]: Cryptographic verification."""
        words = phrase.lower().split()
        if len(words) not in {12, 15, 18, 21, 24}: return False
        if not all(w in self.bip39_set for w in words): return False
        return verify_bip39_checksum(phrase, self.bip39_wordlist)
