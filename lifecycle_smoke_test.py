import os
from datetime import timedelta

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")

import bot


def parse_iso(value):
    return bot.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main() -> None:
    now = bot.now_utc()
    state = {
        "users": {
            "100": {
                "_id": 100,
                "name": "Trial User",
                "plan": "pro",
                "plan_expires": (now - timedelta(minutes=1)).isoformat(),
                "bot_slots_bonus": 0,
            }
        },
        "bots": {
            "bot-a": {"_id": "bot-a", "owner": 100, "name": "First", "created": "2026-01-01T00:00:00+00:00", "status": "running"},
            "bot-b": {"_id": "bot-b", "owner": 100, "name": "Second", "created": "2026-01-02T00:00:00+00:00", "status": "running"},
        },
    }
    sent = []
    stopped = []

    bot.db_load = lambda: state
    bot.db_load_ro = lambda: state
    bot.db_save = lambda _data: None
    bot.save_bot = lambda document: state["bots"].__setitem__(document["_id"], document) or document
    bot.find_bot = lambda bot_id: state["bots"].get(bot_id)
    bot.stop_child = lambda bot_id, manual=True: stopped.append((bot_id, manual)) or {"ok": True}
    bot.bot.send_message = lambda *args, **kwargs: sent.append((args, kwargs))
    bot.RUNNING = {
        "bot-a": {"proc": type("P", (), {"poll": lambda self: None})()},
        "bot-b": {"proc": type("P", (), {"poll": lambda self: None})()},
    }

    original_free_limit = bot.PLAN_LIMITS["free"]["max_bots"]
    bot.PLAN_LIMITS["free"]["max_bots"] = 1
    try:
        bot.downgrade_expired_users()
        user = state["users"]["100"]
        assert user["plan"] == "free"
        assert state["bots"]["bot-a"].get("slot_suspended") is not True
        assert state["bots"]["bot-b"]["status"] == "suspended_quota"
        assert state["bots"]["bot-b"]["slot_suspended"] is True
        assert stopped == [("bot-b", True)]
        assert bot.bot_is_within_user_slot(state["bots"]["bot-a"], user)
        assert not bot.bot_is_within_user_slot(state["bots"]["bot-b"], user)

        bot.grant_plan(100, "pro", hours=24)
        expires = parse_iso(state["users"]["100"]["plan_expires"])
        remaining_hours = (expires - bot.now_utc()).total_seconds() / 3600
        assert 23.9 < remaining_hours <= 24.01
        assert state["bots"]["bot-b"]["slot_suspended"] is False

        bot.get_setting = lambda key, default=None: {"trial_hours": 24}.get(key, default)
        assert bot.trial_duration_hours() == 24
        assert bot._progress_bar(50, 100).endswith("50.0%")
    finally:
        bot.PLAN_LIMITS["free"]["max_bots"] = original_free_limit

    print("lifecycle smoke test: PASS")


if __name__ == "__main__":
    main()
