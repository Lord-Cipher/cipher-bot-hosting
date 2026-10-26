"""Focused regressions for force-join membership and catalogue descriptions."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456789:AA_force_join_catalogue_tests")
os.environ.setdefault("OWNER_ID", "9001")

import bot  # noqa: E402


# A successful membership check is reused briefly so cb_group_verify and the
# following menu callback cannot race each other into a false join prompt.
original_groups = bot.REQUIRED_GROUPS
original_get_chat_member = bot.bot.get_chat_member
try:
    calls = []
    bot.REQUIRED_GROUPS = [{"id": -100123, "name": "Test", "link": "https://t.me/test"}]
    bot._GROUP_MEMBERSHIP_CACHE.clear()
    bot.bot.get_chat_member = lambda group_id, uid: calls.append((group_id, uid)) or SimpleNamespace(status="member")
    assert bot._check_group_membership(7) == []
    assert bot._check_group_membership(7) == []
    assert len(calls) == 1

    bot._GROUP_MEMBERSHIP_CACHE.clear()
    bot.bot.get_chat_member = lambda _group_id, _uid: SimpleNamespace(status="restricted", is_member=False)
    assert bot._check_group_membership(7)

    bot._GROUP_MEMBERSHIP_CACHE.clear()
    bot.bot.get_chat_member = lambda _group_id, _uid: SimpleNamespace(status="administrator")
    assert bot._check_group_membership(7) == []
finally:
    bot.REQUIRED_GROUPS = original_groups
    bot.bot.get_chat_member = original_get_chat_member
    bot._GROUP_MEMBERSHIP_CACHE.clear()

plain = bot._product_description_markup(SimpleNamespace(html_text=""), "> First quoted line\n> Second quoted line\nNormal text")
assert "<blockquote>First quoted line\nSecond quoted line</blockquote>" in plain
assert "Normal text" in plain

telegram_quote = bot._product_description_markup(
    SimpleNamespace(html_text="<blockquote expandable>Telegram quote</blockquote>"),
    "Telegram quote",
)
assert telegram_quote == "<blockquote expandable>Telegram quote</blockquote>"

print("force-join and catalogue quote regressions passed")
