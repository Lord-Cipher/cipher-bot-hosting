
import os
import json
from pathlib import Path

SETTINGS_FILE = Path("storage/data/panel_settings.json")
PUBLIC_URL = "https://10460-ifdszhph8bcyi096anya7-eb75fb27.us2.manus.computer"

def update():
    if not SETTINGS_FILE.parent.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    data = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            
    data["webhook_enabled"] = True
    data["public_url"] = PUBLIC_URL
    
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Updated settings: webhook_enabled=True, public_url={PUBLIC_URL}")

if __name__ == "__main__":
    update()
