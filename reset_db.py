
import json
from pathlib import Path

DB_FILE = Path("storage/data/panel_db.json")

def reset():
    if not DB_FILE.exists():
        print("DB file not found.")
        return
        
    with open(DB_FILE, "r") as f:
        data = json.load(f)
        
    # Reset core collections
    data["users"] = {}
    data["bots"] = {}
    data["payments"] = []
    data["audit"] = []
    data["notifications"] = []
    data["scan_log"] = []
    data["tickets"] = {}
    data["rate_violations"] = {}
    
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Global database reset complete. All users, bots, and logs cleared.")

if __name__ == "__main__":
    reset()
