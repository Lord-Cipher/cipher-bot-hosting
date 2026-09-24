import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_referral_coupon_improvements")
os.environ.setdefault("OWNER_ID", "1")

import bot

# Source-level checks for callback structure and user-visible categories.
source = open("bot.py", encoding="utf-8").read()
assert 'callback_data="ref_gift_confirm_yes"' in source
assert 'callback_data="ref_gift_confirm_no"' in source
assert "Bot Slot Redemption" in source
assert "File Coin Redemption" in source

# Exercise the admin coupon parser with the new single-coupon syntax.
store = {"coupons": {}, "users": {"1": {"plan": "free"}}, "audit": []}
bot.db_load = lambda: store
bot.db_save = lambda value: None
bot.admin_can = lambda uid, action: True
replies = []
bot.bot.reply_to = lambda message, text, **kwargs: replies.append(text)
message = SimpleNamespace(
    from_user=SimpleNamespace(id=1),
    text="add PRO30 pro 30 2 30",
)
bot._handle_coupon_admin(message)
coupon = store["coupons"]["PRO30"]
assert coupon["plan"] == "pro"
assert coupon["discount_pct"] == 30
assert coupon["uses_left"] == 2
assert coupon["expiry"]
assert "pro plan" in replies[-1]

# Exact expiration dates are accepted too.
message.text = "add SALE pro 20 5 2099-12-31"
bot._handle_coupon_admin(message)
assert store["coupons"]["SALE"]["expiry"].startswith("2099-12-31")

print("referral/coupon improvement tests passed")
