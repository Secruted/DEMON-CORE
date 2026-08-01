import os
import sys
import json
import random

# DΞMON CORE v2.0 - ROUTER MODULE
# PROTOCOL: TARGET_GENERATOR & INTELLIGENCE FUSION
# ARCHITECTURE: GENERATOR (Yields paths to Harvester)

class Router:
    """
    Strategic Generator.
    - Same inputs/outputs as before.
    - Adds ONLY safety & stability guards:
        * Cardinality caps
        * Deterministic ordering
        * Optional sampling (disabled by default)
    """

    def __init__(self, targets_file="targets.txt", recon_file="recon_intel.json"):
        self.targets_file = targets_file
        self.recon_file = recon_file
        self.targets = []

        # -------- SAFETY / STABILITY GUARDS (DEFAULTS PRESERVE BEHAVIOR) --------
        # Hard cap to prevent combinatorial explosion
        # Can be set via env vars: export ROUTER_MAX_TARGETS=50
        self.MAX_TARGETS = int(os.getenv("ROUTER_MAX_TARGETS", "0"))  # 0 = unlimited (default)
        self.MAX_ROUTES  = int(os.getenv("ROUTER_MAX_ROUTES", "0"))   # 0 = unlimited (default)

        # Optional sampling (OFF by default)
        self.SAMPLE_TARGETS = int(os.getenv("ROUTER_SAMPLE_TARGETS", "0"))  # 0 = disabled

        # Deterministic order for reproducibility
        self.DETERMINISTIC = True

        # ----------------------------------------------------------------------

        # [🔥 THE MASTER GOLD LIST]
        self.GOLD_PATHS = [
            "/api/v1/users",
            "/api/v1/user/profile",
            "/api/v1/wallet/balance",
            "/api/v1/admin/users",
            "/api/v2/users",
            "/api/config",
            "/api/settings",
            "/mobile/config.json",
            "/app-data.json",
            "/v1/config",

            "/.env",
            "/.env.save",
            "/.env.bak",
            "/.env.production",
            "/.env.local",
            "/docker-compose.yml",
            "/config/database.yml",
            "/sftp-config.json",
            "/.git/config",
            "/.vscode/sftp.json",
            "/web.config",

            "/backup.sql",
            "/database.sql",
            "/dump.sql",
            "/users.sql",
            "/data/backup.tar.gz",
            "/db_backup.sql",
            "/backup.zip",
            "/site_backup.zip",

            "/wallet.json",
            "/keystore.json",
            "/presale-config.json",
            "/id_rsa",
            "/id_rsa.pub",
            "/secrets.json",
            "/claim.json",
        ]

        # Initial load
        self.refresh_targets()

    # ------------------------------------------------------------------
    # TARGET INGESTION (UNCHANGED LOGIC + CAPS ONLY)
    # ------------------------------------------------------------------
    def refresh_targets(self):
        raw_targets = set()

        # SOURCE 1: targets.txt
        if not os.path.exists(self.targets_file):
            try:
                with open(self.targets_file, 'w') as f:
                    f.write("# Put target domains here (e.g., target.com)\n")
            except Exception:
                pass
        else:
            try:
                with open(self.targets_file, 'r') as f:
                    for line in f:
                        clean = line.strip()
                        if clean and not clean.startswith("#"):
                            clean = clean.replace("https://", "").replace("http://", "").strip("/")
                            if clean:
                                raw_targets.add(clean)
            except Exception:
                pass

        # SOURCE 2: recon_intel.json
        if os.path.exists(self.recon_file):
            try:
                with open(self.recon_file, 'r') as f:
                    intel_data = json.load(f)
                    if isinstance(intel_data, list):
                        for entry in intel_data:
                            root = entry.get("root_domain")
                            if root:
                                raw_targets.add(root)
                            subs = entry.get("found_subdomains", [])
                            for sub in subs:
                                if sub:
                                    raw_targets.add(sub)
            except Exception:
                pass

        # Deterministic ordering for reproducibility
        targets = list(raw_targets)
        if self.DETERMINISTIC:
            targets.sort()

        # Optional sampling (OFF by default)
        if self.SAMPLE_TARGETS and self.SAMPLE_TARGETS < len(targets):
            targets = random.sample(targets, self.SAMPLE_TARGETS)

        # Hard cap on targets (OFF by default)
        if self.MAX_TARGETS and self.MAX_TARGETS > 0:
            if len(targets) > self.MAX_TARGETS:
                targets = targets[:self.MAX_TARGETS]

        self.targets = targets

    # ------------------------------------------------------------------
    # GENERATOR (UNCHANGED OUTPUT, ADDS ROUTE CAP ONLY)
    # ------------------------------------------------------------------
    def generator(self):
        # Refresh to catch updates
        self.refresh_targets()

        if not self.targets:
            return

        emitted = 0
        for target in self.targets:
            base_url = f"https://{target}"
            for path in self.GOLD_PATHS:
                yield f"{base_url}{path}"
                emitted += 1

                # Hard cap on total routes (OFF by default)
                if self.MAX_ROUTES and self.MAX_ROUTES > 0:
                    if emitted >= self.MAX_ROUTES:
                        return

    def get_route_count(self):
        # Conservative estimate respecting caps
        base = len(self.targets) * len(self.GOLD_PATHS)
        
        # Apply Route Cap logic for estimation
        if self.MAX_ROUTES and self.MAX_ROUTES > 0:
            if base > self.MAX_ROUTES:
                return self.MAX_ROUTES
        
        return base

# Self-test
if __name__ == "__main__":
    print("DΞMON ROUTER v2.0 (Architectural Safe Mode) ONLINE")
    r = Router()
    print(f"Targets Loaded: {len(r.targets)}")
    print(f"Estimated Routes: {r.get_route_count()}")
    
    # Test Generator
    gen = r.generator()
    try:
        print("Sample Route:", next(gen))
    except StopIteration:
        print("No routes generated.")
