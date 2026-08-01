import time
from negan_config import *
from pathlib import Path

class Maggie:
    MAGGIE_BUFFER = 10  # Trigger flush at 10 dead proxies

    def __init__(self):
        self.dead_buffer = []

    def report_death(self, proxy, reason="FAILED"):
        """
        Receives death reports from Lucille about failed proxies.
        proxy: Proxy string (IP:Port)
        reason: Reason for failure (FAILED/BLOCKED)
        """
        timestamp = int(time.time())
        entry = f"{proxy}|{reason}|{timestamp}"

        if entry not in self.dead_buffer:
            self.dead_buffer.append(entry)

        # Trigger flush immediately when buffer reaches threshold
        if len(self.dead_buffer) >= self.MAGGIE_BUFFER:
            self._flush_to_hospital()

        # Check Lucille tank level
        current_fuel = self._count_tank()
        if current_fuel <= TRIGGER_REFILL:
            print(f"🔴 [Maggie] Tank Critical ({current_fuel}/{MAX_TANK}). Calling Shane...")
            from shane import Shane  # Lazy import to prevent circular dependencies
            Shane().perform_rounds()  # Shane refills up to MAX_TANK

    def _flush_to_hospital(self):
        """
        Move dead proxies to Hospital Room 1 with timestamp
        Deletes them immediately from Lucille
        """
        target_room = COOLING_DIR / "room_1.txt"
        try:
            with open(target_room, "a") as f:
                for p in self.dead_buffer:
                    f.write(p + "\n")
            print(f"🚩 [Maggie] Flushed {len(self.dead_buffer)} proxies to Hospital Room 1")
            self._remove_from_lucille(self.dead_buffer)
            self.dead_buffer.clear()
        except Exception as e:
            print(f"⚠️ [Maggie] Error flushing to hospital: {e}")

    def _count_tank(self):
        """Count remaining proxies in Lucille's tank"""
        count = 0
        for f in TANK_DIR.glob("*.txt"):
            try:
                count += len(open(f).read().splitlines())
            except:
                pass
        return count

    def _remove_from_lucille(self, entries):
        """
        Remove dead proxies from Lucille tank immediately
        """
        for unit_file in TANK_DIR.glob("*.txt"):
            try:
                lines = open(unit_file).read().splitlines()
                new_lines = [l for l in lines if not any(e.split("|")[0] in l for e in entries)]
                with open(unit_file, "w") as f:
                    f.write("\n".join(new_lines) + ("\n" if new_lines else ""))
            except:
                continue
