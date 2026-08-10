#!/usr/bin/env python3
"""
DΞMON CORE - Runtime Guard
==========================
Pre-flight checks for the correct Python environment and critical dependencies.
Does NOT auto-install anything. Reports clearly what is missing.
"""

import sys
import os
from pathlib import Path

REQUIRED = [
    ("curl_cffi", "curl_cffi"),
    ("playwright", "playwright"),
    ("psutil", "psutil"),
    ("bs4", "beautifulsoup4"),
    ("lxml", "lxml"),
]

OPTIONAL = [
    ("pyvirtualdisplay", "pyvirtualdisplay"),
]

def check_runtime(strict: bool = True) -> bool:
    print("[BOOT] DEMON CORE runtime check")
    print(f"[BOOT] Python : {sys.executable}")
    print(f"[BOOT] Version: {sys.version.split()[0]}")

    project_root = Path(__file__).resolve().parent
    expected_venv = project_root / "venv" / "bin" / "python"

    if expected_venv.exists() and Path(sys.executable).resolve() != expected_venv.resolve():
        print(f"[BOOT] WARNING: Not running inside project venv")
        print(f"[BOOT] Current : {sys.executable}")
        print(f"[BOOT] Expected: {expected_venv}")
        print(f"[BOOT] Tip     : source venv/bin/activate")

    missing = []
    for import_name, package_name in REQUIRED:
        try:
            __import__(import_name)
            print(f"[BOOT] {package_name}: OK")
        except ImportError:
            print(f"[BOOT] {package_name}: MISSING")
            missing.append(package_name)

    for import_name, package_name in OPTIONAL:
        try:
            __import__(import_name)
            print(f"[BOOT] {package_name}: OK")
        except ImportError:
            print(f"[BOOT] {package_name}: optional / not found")

    # Basic Chromium check via playwright
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Just verify the module loads; full browser check is heavier
            print("[BOOT] Playwright: OK")
    except Exception as e:
        print(f"[BOOT] Playwright: PROBLEM ({e})")
        missing.append("playwright (or browsers)")

    if missing:
        print("\n[BOOT] ERROR - Missing required packages:")
        for m in missing:
            print(f"       - {m}")
        print(f"\n[BOOT] Install with:")
        print(f"       source venv/bin/activate")
        print(f"       pip install -r requirements.txt")
        print(f"       playwright install chromium")
        if strict:
            return False

    print("[BOOT] Runtime check passed\n")
    return True


if __name__ == "__main__":
    ok = check_runtime(strict=True)
    sys.exit(0 if ok else 1)
