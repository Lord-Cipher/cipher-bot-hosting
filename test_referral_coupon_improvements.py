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

# Plan promos are redeemed from the user Redeem section and activate the plan
# immediately instead of waiting for a paid purchase.
store = {
    "coupons": {
        "PROFREE": {
            "plan": "pro", "percent": 0, "discount_pct": 0,
            "uses_left": 1, "max_uses": 1, "expiry": "",
        },
        "SAVE25": {
            "plan": "all", "percent": 25, "discount_pct": 25,
            "uses_left": 1, "max_uses": 1, "expiry": "",
        },
    },
    "users": {"1": {"plan": "free", "plan_expires": None}},
    "audit": [],
}
bot.db_load = lambda: store
bot.db_load_ro = lambda: store
bot.db_save = lambda value: None
bot.reconcile_user_slot_quota = lambda uid: None
bot.log_notification = lambda *args, **kwargs: None
bot._wh_fire = lambda *args, **kwargs: None
bot.audit = lambda *args, **kwargs: None
replies.clear()
message.text = "PROFREE"
bot._handle_coupon_user(message)
assert store["users"]["1"]["plan"] == "pro"
assert store["users"]["1"].get("active_coupon") is None
assert store["coupons"]["PROFREE"]["uses_left"] == 0
assert store["coupons"]["PROFREE"]["used_by"] == [1]
assert "PROFREE" in store["users"]["1"]["coupons_used"]
assert "Promo activated" in replies[-1]
assert "Pro" in replies[-1]

# A consumed promo cannot be redeemed a second time.
bot._handle_coupon_user(message)
assert "No uses remaining" in replies[-1]
assert store["users"]["1"]["plan"] == "pro"

# Legacy/all-plan coupons still apply a discount to the next purchase.
message.text = "SAVE25"
bot._handle_coupon_user(message)
assert store["users"]["1"]["plan"] == "pro"
assert store["users"]["1"]["active_coupon"] == "SAVE25"
assert "next plan purchase" in replies[-1]

print("referral/coupon improvement tests passed")
