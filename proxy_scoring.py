import time
import json
import asyncio
import logging
import random
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger("PROXY_SCORER")

class ProxyType(Enum):
    UNKNOWN = "unknown"
    DATACENTER = "datacenter"
    RESIDENTIAL = "residential"
    MOBILE = "mobile"

@dataclass
class ProxyStats:
    proxy: str
    success: int = 0
    fail: int = 0
    total_response_time: float = 0.0
    last_used: float = 0.0
    last_checked: float = 0.0
    score: float = 50.0          # Start with neutral score
    proxy_type: ProxyType = ProxyType.UNKNOWN
    consecutive_fails: int = 0
    is_alive: bool = True
    banned_until: float = 0.0    # Timestamp until which this proxy is banned

    @property
    def success_rate(self) -> float:
        total = self.success + self.fail
        if total == 0:
            return 0.5
        return self.success / total

    @property
    def avg_response_time(self) -> float:
        if self.success == 0:
            return 999.0
        return self.total_response_time / self.success

    def calculate_score(self) -> float:
        """
        Advanced scoring formula:
        - Success rate has high weight
        - Response time penalty
        - Consecutive fails heavy penalty
        - Type bonus (Residential/Mobile better)
        """
        if not self.is_alive or time.time() < self.banned_until:
            return 0.0

        # Base score from success rate (0-70 points)
        rate_score = self.success_rate * 70

        # Response time score (0-20 points)
        # Faster than 1.5s = full points, slower = less
        if self.avg_response_time <= 1.5:
            speed_score = 20
        elif self.avg_response_time <= 4.0:
            speed_score = 20 * (1 - (self.avg_response_time - 1.5) / 2.5)
        else:
            speed_score = 0

        # Type bonus
        type_bonus = {
            ProxyType.RESIDENTIAL: 15,
            ProxyType.MOBILE: 12,
            ProxyType.DATACENTER: 0,
            ProxyType.UNKNOWN: 5
        }.get(self.proxy_type, 0)

        # Consecutive fails penalty
        fail_penalty = min(self.consecutive_fails * 8, 40)

        raw_score = rate_score + speed_score + type_bonus - fail_penalty
        self.score = max(0.0, min(100.0, raw_score))
        return self.score


class ProxyScorer:
    """
    Advanced Proxy Scoring & Management System (Phase 1)
    - Tracks success/fail/response time
    - Calculates dynamic score
    - Supports smart selection of best proxies
    - Auto-ban on repeated failures
    """

    def __init__(self, proxy_file: str = "proxy.txt", state_file: str = "proxy_scores.json"):
        self.proxy_file = Path(proxy_file)
        self.state_file = Path(state_file)
        self.proxies: Dict[str, ProxyStats] = {}
        self.lock = asyncio.Lock()

        self.MIN_SCORE_TO_USE = 25.0
        self.BAN_THRESHOLD = 5          # consecutive fails → temporary ban
        self.BAN_DURATION = 60 * 15     # 15 minutes ban

        self._load_proxies()
        self._load_state()

    def _load_proxies(self):
        if not self.proxy_file.exists():
            logger.warning(f"Proxy file not found: {self.proxy_file}")
            return

        with open(self.proxy_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                proxy = line.strip()
                if proxy and not proxy.startswith("#"):
                    if proxy not in self.proxies:
                        self.proxies[proxy] = ProxyStats(proxy=proxy)

        logger.info(f"[SCORER] Loaded {len(self.proxies)} proxies")

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for proxy, stats in data.items():
                if proxy in self.proxies:
                    p = self.proxies[proxy]
                    p.success = stats.get("success", 0)
                    p.fail = stats.get("fail", 0)
                    p.total_response_time = stats.get("total_response_time", 0.0)
                    p.score = stats.get("score", 50.0)
                    p.consecutive_fails = stats.get("consecutive_fails", 0)
                    p.is_alive = stats.get("is_alive", True)
                    p.banned_until = stats.get("banned_until", 0.0)
                    p.proxy_type = ProxyType(stats.get("proxy_type", "unknown"))
            logger.info("[SCORER] Previous scores restored")
        except Exception as e:
            logger.warning(f"[SCORER] Could not load state: {e}")

    async def save_state(self):
        async with self.lock:
            data = {}
            for proxy, stats in self.proxies.items():
                data[proxy] = {
                    "success": stats.success,
                    "fail": stats.fail,
                    "total_response_time": stats.total_response_time,
                    "score": stats.score,
                    "consecutive_fails": stats.consecutive_fails,
                    "is_alive": stats.is_alive,
                    "banned_until": stats.banned_until,
                    "proxy_type": stats.proxy_type.value
                }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    async def record_result(self, proxy: str, success: bool, response_time: float = 0.0):
        """Called by the transport/harvester after every request."""
        async with self.lock:
            if proxy not in self.proxies:
                self.proxies[proxy] = ProxyStats(proxy=proxy)

            stats = self.proxies[proxy]
            stats.last_used = time.time()

            if success:
                stats.success += 1
                stats.total_response_time += response_time
                stats.consecutive_fails = 0
                stats.is_alive = True
            else:
                stats.fail += 1
                stats.consecutive_fails += 1

                if stats.consecutive_fails >= self.BAN_THRESHOLD:
                    stats.banned_until = time.time() + self.BAN_DURATION
                    stats.is_alive = False
                    logger.warning(f"[SCORER] Banned {proxy} for {self.BAN_DURATION//60} min (too many fails)")

            stats.calculate_score()

    async def get_best_proxy(self) -> Optional[str]:
        """Returns the highest scoring available proxy."""
        async with self.lock:
            now = time.time()
            candidates = []

            for stats in self.proxies.values():
                if stats.score >= self.MIN_SCORE_TO_USE and stats.banned_until <= now:
                    candidates.append(stats)

            if not candidates:
                # Fallback: try any non-banned proxy
                candidates = [s for s in self.proxies.values() if s.banned_until <= now]

            if not candidates:
                return None

            # Weighted random choice favoring higher scores
            candidates.sort(key=lambda x: x.score, reverse=True)
            top = candidates[:max(5, len(candidates)//4)]  # Top 25% or at least 5
            chosen = random.choice(top)
            return chosen.proxy

    async def get_stats_summary(self) -> dict:
        async with self.lock:
            total = len(self.proxies)
            alive = sum(1 for p in self.proxies.values() if p.is_alive and p.banned_until <= time.time())
            avg_score = sum(p.score for p in self.proxies.values()) / total if total else 0
            return {
                "total": total,
                "alive": alive,
                "avg_score": round(avg_score, 1),
                "banned": total - alive
            }
