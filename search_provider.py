#!/usr/bin/env python3
"""
SearchProvider — Mycelium Network Abstraction Layer
====================================================
HybridFeeder is responsible for:
  - Proxy selection / scoring
  - Transport tiers (curl / browser)
  - Raw HTML acquisition

SearchProvider is responsible for:
  - Page classification
  - Domain extraction from raw HTML
  - Source-specific parsing strategies (Google today, others later)

This keeps acquisition and intelligence extraction decoupled.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------
@dataclass
class SearchExtractResult:
    """Standard output of any SearchProvider implementation."""
    domains: List[str] = field(default_factory=list)
    page_type: str = "UNKNOWN"
    css_candidates: int = 0
    css_valid: int = 0
    regex_candidates: int = 0
    regex_valid: int = 0
    fallback_valid: int = 0
    total_anchors: int = 0
    input_bytes: int = 0


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------
class SearchProvider(ABC):
    """
    Abstract interface for HTML → domain extraction.

    Implementations may target Google SERP, alternative search engines,
    or proprietary intel feeds. HybridFeeder must not know the internals.
    """

    name: str = "base"

    @abstractmethod
    def extract_domains(self, html: str, source_url: str = "") -> SearchExtractResult:
        """Parse raw HTML and return structured domain candidates."""
        ...

    def classify(self, html: Optional[str]) -> str:
        """Optional page-type classification. Override per provider."""
        if html is None or len(html) == 0:
            return "NAVIGATION_FAILED"
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _clean_domain(url: str) -> Optional[str]:
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


# ---------------------------------------------------------------------------
# Google Search Provider (current production implementation)
# ---------------------------------------------------------------------------
class GoogleSearchProvider(SearchProvider):
    """
    Google SERP extractor.

    Strategies (in order):
      1. Classic CSS selectors
      2. /url?q= regex
      3. Broad external <a href> fallback
      4. Bare https URL regex last-resort
    """

    name = "google"

    BANNED = [
        "google", "youtube", "facebook", "github.com", "gitlab.com",
        "stackoverflow", "microsoft", "bing", "yahoo", "duckduckgo",
        "support.google", "accounts.google", "policies.google", "gstatic",
    ]

    def _is_banned(self, domain: str) -> bool:
        return any(b in domain for b in self.BANNED)

    def classify(self, html: Optional[str]) -> str:
        if html is None or len(html) == 0:
            return "NAVIGATION_FAILED"

        lower = html.lower()

        captcha_signals = [
            "unusual traffic", "our systems have detected", "prove you're not a robot",
            "captcha", "recaptcha", "/sorry/", "sorry/index", "g-recaptcha",
        ]
        if any(s in lower for s in captcha_signals):
            return "CAPTCHA"

        js_signals = [
            "enablejs", "httpservice/retry", "emsg=sg_rel", "sg_rel",
            "please enable javascript", "enable javascript", "jsdisabled",
        ]
        if any(s in lower for s in js_signals):
            return "JS_REQUIRED"

        consent_signals = [
            "before you continue", "consent.google", "we use cookies",
            "accept all", "reject all", "cookie consent",
        ]
        if any(s in lower for s in consent_signals):
            return "CONSENT_PAGE"

        result_markers = [
            "yurubf", "/url?q=", "data-ved",
            'class="g "', 'class="g"', "result-stats", "kcr9t",
        ]
        if any(m in lower for m in result_markers):
            return "SEARCH_RESULTS"

        if "support.google.com/websearch" in lower and html.count("<a ") < 10:
            return "SEARCH_BLOCKED"

        if len(html) < 200:
            return "ERROR_PAGE"

        if html.count("<a ") < 8 and html.count("href=") < 10:
            return "EMPTY_RESULTS"

        return "UNKNOWN"

    def extract_domains(self, html: str, source_url: str = "") -> SearchExtractResult:
        result = SearchExtractResult(
            page_type=self.classify(html),
            input_bytes=len(html) if html else 0,
        )

        if not html:
            return result

        domains: List[str] = []
        soup = None

        try:
            soup = BeautifulSoup(html, "lxml")
            anchors = soup.find_all("a", href=True)
            result.total_anchors = len(anchors)

            print(f"[DIAG][HTML] input_bytes={result.input_bytes} total <a> tags={result.total_anchors}")
            print(
                f"[DIAG][HTML] yuRUbf={'yes' if 'yuRUbf' in html else 'no'}  "
                f"/url?q={'yes' if '/url?q=' in html else 'no'}  "
                f"data-ved={'yes' if 'data-ved' in html else 'no'}"
            )

            samples = []
            for a in anchors[:40]:
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

        print(f"[DIAG][PAGE_TYPE] {result.page_type}")

        # --- 1) Classic CSS ---
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
                    result.css_candidates += 1
                    href = a.get("href", "") or ""
                    if href.startswith("/url?q="):
                        href = unquote(href.split("/url?q=")[1].split("&")[0])
                    if href.startswith("http") and "google." not in href:
                        domain = _clean_domain(href)
                        if domain and not self._is_banned(domain):
                            result.css_valid += 1
                            if domain not in domains:
                                domains.append(domain)

            if domains:
                result.domains = domains
                print(
                    f"[DIAG][PARSER] CSS candidates={result.css_candidates} "
                    f"CSS valid={result.css_valid} final unique={len(domains)}"
                )
                return result
        except Exception:
            pass

        # --- 2) Regex /url?q= ---
        try:
            pattern = r'/url\?q=(https?://[^&\s"\']+)'
            matches = re.findall(pattern, html)
            for raw in matches:
                result.regex_candidates += 1
                href = unquote(raw)
                if "google." in href:
                    continue
                domain = _clean_domain(href)
                if domain and not self._is_banned(domain):
                    result.regex_valid += 1
                    if domain not in domains:
                        domains.append(domain)
            if domains:
                result.domains = domains
                print(
                    f"[DIAG][PARSER] Regex candidates={result.regex_candidates} "
                    f"Regex valid={result.regex_valid} final unique={len(domains)}"
                )
                return result
        except Exception:
            pass

        # --- 3) Broad external links ---
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
                domain = _clean_domain(href)
                if domain and not self._is_banned(domain):
                    result.fallback_valid += 1
                    if domain not in domains:
                        domains.append(domain)
        except Exception:
            pass

        # --- 4) Bare URL regex ---
        if not domains:
            try:
                bare = re.findall(
                    r'https?://[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}(?:/[^\s"\'<>]*)?',
                    html,
                )
                for href in bare:
                    if "google." in href or "gstatic." in href:
                        continue
                    domain = _clean_domain(href)
                    if domain and not self._is_banned(domain) and domain not in domains:
                        domains.append(domain)
                        result.fallback_valid += 1
            except Exception:
                pass

        result.domains = domains
        print(
            f"[DIAG][PARSER] CSS={result.css_candidates}/{result.css_valid} "
            f"Regex={result.regex_candidates}/{result.regex_valid} "
            f"Fallback={result.fallback_valid} final unique={len(domains)}"
        )
        return result


# ---------------------------------------------------------------------------
# Factory / registry (Mycelium entry point)
# ---------------------------------------------------------------------------
_PROVIDERS = {
    "google": GoogleSearchProvider,
}


def get_search_provider(name: str = "google") -> SearchProvider:
    """
    Resolve a SearchProvider by name.

    Future providers (e.g. 'bing', 'intel_feed') register here without
    touching HybridFeeder.
    """
    cls = _PROVIDERS.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown SearchProvider: {name}. Available: {list(_PROVIDERS)}")
    return cls()
