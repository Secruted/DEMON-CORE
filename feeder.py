import os
import time
from urllib.parse import urlparse

# DΞMON CORE - FEEDER MODULE v2.0
# FUNCTION: MASS TARGET ACQUISITION (File & Manual Support)
# DEPENDENCY: pip install googlesearch-python

try:
    from googlesearch import search
except ImportError:
    print("❌ Critical Missing Dependency!")
    print("Run: pip install googlesearch-python")
    exit()

TARGET_FILE = "targets.txt"
DORKS_FILE = "dorks.txt"  # الملف الجديد الذي ستقرأ منه
BANNED_DOMAINS = ["google", "youtube", "facebook", "github.com", "gitlab.com", "stackoverflow", "microsoft"]

def clean_domain(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if ":" in domain: domain = domain.split(":")[0]
        return domain.replace("www.", "")
    except:
        return None

def save_target(domain):
    if not os.path.exists(TARGET_FILE): open(TARGET_FILE, "w").close()
    with open(TARGET_FILE, "r") as f: existing = f.read()
    if domain not in existing:
        with open(TARGET_FILE, "a") as f: f.write(f"{domain}\n")
        return True
    return False

def execute_search_cycle(dork, limit):
    print(f"\n🚀 Executing Dork: {dork}")
    print("   Scanning sector... (Please wait)")
    count = 0
    try:
        # pause=2.0 is MANDATORY to prevent IP ban
        for url in search(dork, num_results=limit, lang="en", sleep_interval=3.0):
            domain = clean_domain(url)
            if not domain: continue
            if any(banned in domain for banned in BANNED_DOMAINS): continue
            
            if save_target(domain):
                print(f"   ✅ [CAPTURED] {domain}")
                count += 1
            
            if count >= limit: break
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    return count

def start_feeding():
    print("\n🕷️  DΞMON FEEDER v2.0 (BULK EDITION)  🕷️")
    print("-----------------------------------------")
    
    mode = input("Select Mode: [1] Manual Input  [2] Read dorks.txt : ")
    
    try:
        limit = int(input("🔢 Targets per Dork (Recommended 30): "))
    except:
        limit = 30

    total_captured = 0

    if mode == "2":
        if not os.path.exists(DORKS_FILE):
            print(f"❌ Error: {DORKS_FILE} not found!")
            return
        
        with open(DORKS_FILE, "r") as f:
            dorks = [line.strip() for line in f if line.strip()]
        
        print(f"🔥 Loaded {len(dorks)} dorks from file. Starting massive scan...")
        
        for dork in dorks:
            hits = execute_search_cycle(dork, limit)
            total_captured += hits
            # Pause between dorks to let Google breathe
            print("   💤 Cooling down for 5 seconds...")
            time.sleep(5)

    else:
        dork = input("🔥 Enter your Google Dork: ")
        if dork.strip():
            total_captured = execute_search_cycle(dork, limit)

    print(f"\n🩸 MISSION COMPLETE. Total New Targets: {total_captured}")
    print(f"👉 Now run: python harvester.py")

if __name__ == "__main__":
    start_feeding()
