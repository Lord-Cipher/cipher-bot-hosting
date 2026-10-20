"""Regression coverage for cloned bot credentials and environment injection."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_clone_env")
os.environ.setdefault("OWNER_ID", "9001")

import bot

with tempfile.TemporaryDirectory(prefix="clone-env-test-") as tmp:
    old_host_secret = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "host-secret-must-not-leak"
    try:
        child_env = bot.safe_env(Path(tmp), {
            "BOT_TOKEN": "per-bot-telegram-token",
            "TOKEN": "project-telegram-token",
            "TELEGRAM_CHAT_ID": "-100123",
            "OPENAI_API_KEY": "per-bot-openai-key",
            "DATABASE_URL": "postgres://per-bot",
            "BAD KEY": "invalid-name",
        })
        assert child_env["BOT_TOKEN"] == "per-bot-telegram-token"
        assert child_env["OPENAI_API_KEY"] == "per-bot-openai-key"
        assert child_env["DATABASE_URL"] == "postgres://per-bot"
        assert "BAD KEY" not in child_env
    finally:
        if old_host_secret is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_host_secret

cloned = bot._clone_env_with_credentials({
    "TOKEN": "old-telegram-token",
    "TELEGRAM_CHAT_ID": "-100-old",
    "OPENAI_API_KEY": "keep-this-project-secret",
    "CUSTOM_SETTING": "keep-this",
}, "new-telegram-token", "-100-new")
assert cloned["TOKEN"] == "new-telegram-token"
assert cloned["BOT_TOKEN"] == "new-telegram-token"
assert cloned["TELEGRAM_CHAT_ID"] == "-100-new"
assert cloned["CHAT_ID"] == "-100-new"
assert cloned["OPENAI_API_KEY"] == "keep-this-project-secret"
assert cloned["CUSTOM_SETTING"] == "keep-this"

try:
    bot._clone_env_with_credentials({"TOKEN": "same-token"}, "same-token", "-100-new")
except ValueError as exc:
    assert "different Telegram bot token" in str(exc)
else:
    raise AssertionError("cloning must reject reuse of the source Telegram bot token")

print("Clone environment regression tests passed")
