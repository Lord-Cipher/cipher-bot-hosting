from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("BOT_TOKEN", "123456789:AA_settings_compatibility")
os.environ.setdefault("OWNER_ID", "9001")

import bot

with tempfile.TemporaryDirectory() as td:
    path = bot.Path(td) / "panel_settings.json"
    path.write_text(json.dumps({"settings": {
        "github_token": "backup-token",
        "github_repo": "Lord-Cipher/cipher-vault",
    }}))
    original = bot.SETTINGS_FILE
    bot.SETTINGS_FILE = path
    bot._DB_CACHE.clear()
    try:
        assert bot.get_setting("github_token") == "backup-token"
        bot.set_setting("github_branch", "main")
        saved = json.loads(path.read_text())
        assert saved["settings"]["github_branch"] == "main"
        bot.gh_load_config()
        assert bot.GH["token"] == "backup-token"
        assert bot.GH["repo"] == "Lord-Cipher/cipher-vault"
    finally:
        bot.SETTINGS_FILE = original
        bot._DB_CACHE.clear()

print("nested panel settings compatibility tests passed")
