from pathlib import Path

COOLING_DIR = Path("cooling")

for file in COOLING_DIR.glob("room_*.txt"):
    open(file, "w").close()

print("Cooling rooms cleared safely.")
