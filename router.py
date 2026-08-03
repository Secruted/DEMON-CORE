import os
import json
import random
from pathlib import Path

class Router:
    """
    Smart Path Generator (Phase 2)
    - Dynamic path generation based on target type
    - Expanded intelligent wordlists
    - Still supports classic high-value paths
    """

    def __init__(self, targets_file="targets.txt", recon_file="recon_intel.json"):
        self.targets_file = targets_file
        self.recon_file = recon_file
        self.targets = []

        self.MAX_TARGETS = int(os.getenv("ROUTER_MAX_TARGETS", "0"))
        self.MAX_ROUTES  = int(os.getenv("ROUTER_MAX_ROUTES", "0"))
        self.SAMPLE_TARGETS = int(os.getenv("ROUTER_SAMPLE_TARGETS", "0"))
        self.DETERMINISTIC = True

        # High-value classic paths
        self.CORE_PATHS = [
            "/.env", "/.env.local", "/.env.production", "/.env.bak", "/.env.save",
            "/api/config", "/api/v1/config", "/api/settings", "/api/v1/users",
            "/api/v1/user/profile", "/api/v1/wallet/balance", "/api/v1/admin/users",
            "/config.json", "/config.js", "/app-config.json", "/settings.json",
            "/backup.sql", "/database.sql", "/dump.sql", "/db.sql",
            "/wallet.json", "/keystore.json", "/secrets.json",
            "/.git/config", "/docker-compose.yml", "/web.config",
            "/id_rsa", "/id_rsa.pub", "/.ssh/id_rsa",
            "/admin", "/administrator", "/api", "/graphql", "/swagger.json",
            "/v1/api", "/v2/api", "/internal/config", "/debug/vars"
        ]

        # Dynamic prefixes & suffixes for smarter generation
        self.PREFIXES = ["", "api/", "api/v1/", "api/v2/", "v1/", "v2/", "admin/", "internal/", "backend/", "app/", "mobile/"]
        self.SUFFIXES = ["config", "settings", "env", "secret", "secrets", "key", "keys", "token", "wallet", "backup", "dump", "users", "profile", "balance", "admin"]
        self.EXTENSIONS = ["", ".json", ".js", ".yml", ".yaml", ".env", ".bak", ".old", ".txt", ".sql"]

        self.refresh_targets()

    def _generate_smart_paths(self):
        """Generate additional paths dynamically."""
        paths = set(self.CORE_PATHS)

        for prefix in self.PREFIXES:
            for suffix in self.SUFFIXES:
                for ext in self.EXTENSIONS:
                    path = f"/{prefix}{suffix}{ext}".replace("//", "/")
                    paths.add(path)

        return list(paths)

    def refresh_targets(self):
        raw_targets = set()

        if Path(self.targets_file).exists():
            with open(self.targets_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    clean = line.strip()
                    if clean and not clean.startswith("#"):
                        clean = clean.replace("https://", "").replace("http://", "").strip("/")
                        if clean:
                            raw_targets.add(clean)

        if Path(self.recon_file).exists():
            try:
                with open(self.recon_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for entry in data:
                            if entry.get("root_domain"):
                                raw_targets.add(entry["root_domain"])
                            for sub in entry.get("found_subdomains", []):
                                if sub:
                                    raw_targets.add(sub)
            except:
                pass

        targets = sorted(list(raw_targets)) if self.DETERMINISTIC else list(raw_targets)

        if self.SAMPLE_TARGETS and self.SAMPLE_TARGETS < len(targets):
            targets = random.sample(targets, self.SAMPLE_TARGETS)

        if self.MAX_TARGETS and len(targets) > self.MAX_TARGETS:
            targets = targets[:self.MAX_TARGETS]

        self.targets = targets
        self.smart_paths = self._generate_smart_paths()

    def generator(self):
        self.refresh_targets()
        if not self.targets:
            return

        emitted = 0
        for target in self.targets:
            base_url = f"https://{target}"
            for path in self.smart_paths:
                yield f"{base_url}{path}"
                emitted += 1
                if self.MAX_ROUTES and emitted >= self.MAX_ROUTES:
                    return

    def get_route_count(self):
        base = len(self.targets) * len(getattr(self, "smart_paths", self.CORE_PATHS))
        if self.MAX_ROUTES and base > self.MAX_ROUTES:
            return self.MAX_ROUTES
        return base
