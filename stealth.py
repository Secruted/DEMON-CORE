#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stealth.py
DEMON-CORE – Production-Grade Coherent Fingerprint Engine
محرك تمويه متعدد الطبقات مع ضمان تطابق HTTP ↔ TLS وحقن Client Hints ديناميكي
"""

from __future__ import annotations

import os
import time
import json
import hashlib
import hmac
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# مصدر الإنتروبيا غير الحتمي (HMAC-SHA256)
# ---------------------------------------------------------------------------
_PROCESS_SEED = os.urandom(32)
_ENTROPY_COUNTER = 0


def _next_entropy(nbytes: int = 32) -> bytes:
    global _ENTROPY_COUNTER
    _ENTROPY_COUNTER += 1
    msg = f"{time.time_ns()}:{_ENTROPY_COUNTER}:{os.getpid()}".encode()
    return hmac.new(_PROCESS_SEED, msg, hashlib.sha256).digest()[:nbytes]


def _secure_shuffle(seq: list) -> list:
    """خلط آمن يعتمد على إنتروبيا HMAC بدلاً من random القياسي."""
    rng = random.Random(int.from_bytes(_next_entropy(8), "big"))
    seq = list(seq)
    rng.shuffle(seq)
    return seq


# ---------------------------------------------------------------------------
# نموذج الملف الشخصي المتماسك
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BrowserProfile:
    name: str
    ua: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    accept_language: str
    tls_impersonate: str
    platform: str
    is_mobile: bool
    browser_family: str


# ---------------------------------------------------------------------------
# المحرك الرئيسي
# ---------------------------------------------------------------------------
class StealthEngine:
    def __init__(self, profiles_path: str | Path = "profiles.json"):
        self.profiles_path = Path(profiles_path)
        self.profiles: List[BrowserProfile] = []
        self._last_profile: Optional[BrowserProfile] = None
        self._session_token = _next_entropy(8).hex()
        self._load_profiles()

    def _load_profiles(self) -> None:
        if not self.profiles_path.exists():
            raise FileNotFoundError(
                f"[STEALTH] ملف الملفات الشخصية غير موجود: {self.profiles_path}"
            )

        with open(self.profiles_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        if not isinstance(raw, list) or len(raw) < 3:
            raise ValueError("[STEALTH] يجب أن يحتوي profiles.json على ≥ 3 ملفات شخصية")

        loaded: List[BrowserProfile] = []
        for item in raw:
            try:
                profile = BrowserProfile(
                    name=item["name"],
                    ua=item["ua"],
                    sec_ch_ua=item["sec_ch_ua"],
                    sec_ch_ua_mobile=item["sec_ch_ua_mobile"],
                    sec_ch_ua_platform=item["sec_ch_ua_platform"],
                    accept_language=item.get("accept_language", "en-US,en;q=0.9"),
                    tls_impersonate=item["tls_impersonate"],
                    platform=item["platform"],
                    is_mobile=bool(item["is_mobile"]),
                    browser_family=item["browser_family"].lower(),
                )
                if profile.is_mobile and "Mobile" not in profile.ua and "Android" not in profile.ua and "iPhone" not in profile.ua:
                    raise ValueError(f"عدم تماسك mobile flag مع UA: {profile.name}")
                if profile.browser_family == "chrome" and "Chrome/" not in profile.ua:
                    raise ValueError(f"عدم تماسك chrome family مع UA: {profile.name}")
                loaded.append(profile)
            except KeyError as e:
                raise ValueError(f"[STEALTH] مفتاح ناقص في الملف الشخصي: {e}") from e

        self.profiles = loaded

    def select_profile(self, mobile_bias: float = 0.30) -> BrowserProfile:
        if not self.profiles:
            raise RuntimeError("[STEALTH] لا توجد ملفات شخصية محمّلة")

        want_mobile = random.Random(int.from_bytes(_next_entropy(4), "big")).random() < mobile_bias
        pool = [p for p in self.profiles if p.is_mobile == want_mobile]
        if not pool:
            pool = self.profiles[:]

        if self._last_profile is not None and len(pool) > 1:
            pool = [p for p in pool if p.name != self._last_profile.name]

        chosen = random.Random(int.from_bytes(_next_entropy(8), "big")).choice(pool)
        self._last_profile = chosen
        return chosen

    def build_headers(
        self,
        profile: BrowserProfile,
        is_api: bool = False,
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "User-Agent": profile.ua,
            "Accept-Language": profile.accept_language,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
        }

        if profile.browser_family in ("chrome", "edge"):
            headers["Sec-CH-UA"] = profile.sec_ch_ua
            headers["Sec-CH-UA-Mobile"] = profile.sec_ch_ua_mobile
            headers["Sec-CH-UA-Platform"] = profile.sec_ch_ua_platform

        if is_api:
            headers["Accept"] = "application/json, text/plain, */*"
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-origin"
        else:
            headers["Accept"] = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            )
            headers["Sec-Fetch-Dest"] = "document"
            headers["Sec-Fetch-Mode"] = "navigate"
            headers["Sec-Fetch-Site"] = "none"
            headers["Sec-Fetch-User"] = "?1"
            headers["Upgrade-Insecure-Requests"] = "1"
            headers["Cache-Control"] = "max-age=0"

        if extra:
            headers.update(extra)

        ordered_keys = _secure_shuffle(list(headers.keys()))
        return {k: headers[k] for k in ordered_keys}

    def get_transport_bundle(
        self,
        mobile_bias: float = 0.30,
        is_api: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, str], str, BrowserProfile]:
        profile = self.select_profile(mobile_bias=mobile_bias)
        headers = self.build_headers(profile, is_api=is_api, extra=extra_headers)
        return headers, profile.tls_impersonate, profile


# ---------------------------------------------------------------------------
# واجهة التوافق العكسي
# ---------------------------------------------------------------------------
_engine: Optional[StealthEngine] = None


def get_stealth_headers(mobile_bias: float = 0.35) -> Dict[str, str]:
    global _engine
    if _engine is None:
        _engine = StealthEngine()
    headers, _, _ = _engine.get_transport_bundle(mobile_bias=mobile_bias)
    return headers


def get_transport_bundle(
    mobile_bias: float = 0.30,
    is_api: bool = False,
) -> Tuple[Dict[str, str], str]:
    global _engine
    if _engine is None:
        _engine = StealthEngine()
    headers, impersonate, _ = _engine.get_transport_bundle(
        mobile_bias=mobile_bias, is_api=is_api
    )
    return headers, impersonate
