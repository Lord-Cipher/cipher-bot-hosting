import os

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_token_for_backup_defaults")
os.environ.setdefault("OWNER_ID", "9001")

import bot

# A configured owner ID must never become an implicit Telegram backup target.
bot.set_setting("tg_backup_channel", "")
assert bot._tg_backup_channel() == ""
assert bot._tg_channel_backup_enabled() is False

bot.set_setting("tg_backup_channel", "-1001234567890")
assert bot._tg_backup_channel() == "-1001234567890"
assert bot._tg_channel_backup_enabled() is True

bot.set_setting("tg_backup_channel", "")
print("Telegram backup default regression tests passed")
