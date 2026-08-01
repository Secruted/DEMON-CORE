from pathlib import Path

# ===================== [SYSTEM LIMITS] =====================

# Lucille Tank Configuration
MAX_TANK = 195            # Total capacity of Lucille (3 units × 65)
UNIT_CAPACITY = 65        # Capacity per unit
TRIGGER_REFILL = 80       # Threshold to trigger Shane refill

# Shane's Hospital (Cooling & Lifecycle)
COOLING_PERIOD = 2700     # 45 minutes in seconds before a proxy can be rechecked
MAX_FAILURES = 7          # Max retries before sending proxy to graveyard

# Negan Warehouse Intake
WAREHOUSE_CHUNK = 1000    # Number of raw proxies processed per Negan call

# ===================== [NETWORK SETTINGS] =====================

# Proxy health check endpoint and timeout
HEALTH_CHECK_URL = "http://www.google.com"  # Can be replaced with internal endpoint
HEALTH_CHECK_TIMEOUT = 5                     # Timeout in seconds for health check

# ===================== [PATHS] =====================

BASE = Path(".")                              # Base directory for the system

# Lucille Tank (Active Proxies) - Divided into units
TANK_DIR = BASE / "tank"

# Shane's Hospital (Dead or Failed Proxies) - Divided into rooms
COOLING_DIR = BASE / "cooling"

# Final Graveyard log for permanently dead proxies
GRAVEYARD_FILE = BASE / "graveyard.txt"

# Negan's Warehouse Source (Raw proxy files)
WAREHOUSE_DIR = BASE
WAREHOUSE_PATTERN = "proxies_*.txt"

# Tracker file to maintain state of Shane & Negan
TRACKER_FILE = BASE / "tracker.json"

# ===================== [BOOTSTRAP] =====================

# Ensure required directories exist
for d in [TANK_DIR, COOLING_DIR]:
    d.mkdir(parents=True, exist_ok=True)
