#!/usr/bin/env python3
"""
DΞMON CORE - Proxy Merger
=========================
Collects proxies from all .txt files in the current directory,
cleans them, deduplicates, and writes the clean list to proxy.txt.

IMPORTANT:
- Does NOT force any scheme (http/socks).
- Preserves the original format exactly.
- Scheme resolution is the responsibility of ProxyScorer / runtime.
"""

from pathlib import Path
from typing import Set, List

# Files that must never be treated as proxy sources
EXCLUDE_FILES = {
    "proxy.txt",
    "targets.txt",
    "dorks.txt",
    "requirements.txt",
    "README.md",
    "runtime.json",
    "strategy.json",
    "intelligence.json",
    "tracker.json",
    "transport.json",
    "config.json.example",
    "proxy_scores.json",
    "bip39_english.txt",
}

PROXY_FILE = "proxy.txt"


def is_proxy_like(line: str) -> bool:
    """Basic sanity check: looks like ip:port or scheme://ip:port"""
    line = line.strip()
    if not line or line.startswith("#"):
        return False
    return ":" in line and len(line) >= 7


def normalize_proxy(line: str) -> str:
    """
    Only clean whitespace. Do NOT add or change any scheme.
    Preserve original format exactly.
    """
    return line.strip()


def collect_proxies(directory: Path = None) -> tuple[Set[str], List[str]]:
    """
    Scan directory for .txt files, collect and clean proxies.
    Returns (unique_proxies, processed_files)
    """
    if directory is None:
        directory = Path(".")

    unique: Set[str] = set()
    processed_files: List[str] = []

    for file_path in directory.glob("*.txt"):
        name = file_path.name

        if name in EXCLUDE_FILES or name.startswith("."):
            continue

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            count_before = len(unique)
            for raw in lines:
                if is_proxy_like(raw):
                    cleaned = normalize_proxy(raw)
                    unique.add(cleaned)

            added = len(unique) - count_before
            processed_files.append(f"{name} (+{added})")
            print(f"   ✓ Read {name} → added {added} new proxies")

        except Exception as e:
            print(f"   ✗ Failed to read {name}: {e}")

    return unique, processed_files


def write_proxy_file(proxies: Set[str], output: str = PROXY_FILE) -> int:
    """Write sorted unique proxies to proxy.txt. Returns count."""
    sorted_proxies = sorted(proxies)
    with open(output, "w", encoding="utf-8") as f:
        for p in sorted_proxies:
            f.write(p + "\n")
    return len(sorted_proxies)


def main():
    print("\n🕷️  DΞMON CORE – Proxy Merger")
    print("--------------------------------")
    print("Scanning current directory for proxy lists...\n")
    print("NOTE: Original schemes are preserved. No http:// is forced.\n")

    unique_proxies, processed = collect_proxies()

    if not unique_proxies:
        print("\n⚠️  No proxies found. Place your .txt proxy lists in this folder and run again.")
        return

    total = write_proxy_file(unique_proxies)

    print("\n📊 REPORT")
    print("---------")
    print(f"Files processed : {len(processed)}")
    for f in processed:
        print(f"   • {f}")
    print(f"Unique proxies  : {total}")
    print(f"Output written  : {PROXY_FILE}")
    print("\n✅ Done. proxy.txt is ready for the system.")
    print("   Scheme resolution is handled at runtime by ProxyScorer.")


if __name__ == "__main__":
    main()
