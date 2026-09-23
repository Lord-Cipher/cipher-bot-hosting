import os
import copy

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_language_currency")
os.environ.setdefault("OWNER_ID", "1")

import bot


users = {"42": {"_id": 42, "name": "Test User", "username": "testuser", "lang": "en"}}
settings = {"default_language": "en"}


def fake_db_load():
    return {"users": copy.deepcopy(users), "payments": []}


def fake_db_load_ro():
    return {"users": users, "payments": []}


def fake_db_save(value):
    users.clear()
    users.update(value["users"])


def fake_get_setting(key, default=None):
    return settings.get(key, default)


bot.db_load = fake_db_load
bot.db_load_ro = fake_db_load_ro
bot.db_save = fake_db_save
bot.get_setting = fake_get_setting

assert bot._lang_set_user(42, "bn") is True
assert bot._lang_get_user(42) == "bn"
assert bot._tr(42, "welcome", brand="Cipher") == "Cipher-এ আপনাকে স্বাগতম!"
assert bot._normalize_currency_code("৳") == "BDT"
assert bot._normalize_currency_code("₹") == "INR"

class Response:
    ok = True
    status_code = 200

    def json(self):
        return {"rates": {"BDT": 110.0}}


bot._FX_RATE_CACHE.clear()
bot.requests.get = lambda *args, **kwargs: Response()
converted, error = bot._local_amount_to_usd(110, "৳")
assert error == ""
assert converted == 1.0, converted
print("language and currency regression tests passed")
