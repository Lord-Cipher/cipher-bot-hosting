"""Focused regression tests for plan-aware AI routing.

The bot module is imported with a fake Telegram token, then its persistence
functions are replaced with in-memory stores so the tests never touch live
settings or send network requests.
"""
from __future__ import annotations

import os
import copy
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_token_for_routing_tests")
os.environ.setdefault("OWNER_ID", "9001")

import bot  # noqa: E402


settings = {}
users = {
    "7": {
        "id": 7,
        "plan": "free",
        "ai_models": [],
    },
    "42": {
        "id": 42,
        "name": "Test User",
        "username": "test_user",
        "plan": "lifetime",
        "plan_expires": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "ai_models": [],
    },
    "9001": {
        "id": 9001,
        "plan": "lifetime",
        "plan_expires": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "ai_models": [],
    },
}


def fake_settings_load():
    return dict(settings)


def fake_settings_load_ro():
    return settings


def fake_settings_save(value):
    settings.clear()
    settings.update(value)


def fake_db_load():
    return {"users": copy.deepcopy(users)}


def fake_db_load_ro():
    return {"users": users}


def fake_db_save(value):
    users.clear()
    users.update(value["users"])


bot.settings_load = fake_settings_load
bot.settings_load_ro = fake_settings_load_ro
bot.settings_save = fake_settings_save
bot.db_load = fake_db_load
bot.db_load_ro = fake_db_load_ro
bot.db_save = fake_db_save


all_models = list(bot._AI_OPERATIVE_KEYS)
enabled_models = [model for model in all_models if bot._ai_operative_enabled(model)]
assert len(enabled_models) > 3, "the fixture must exercise more than the old limit"
bot.set_plan_ai_models("lifetime", all_models)
assert settings["ai_plan_lifetime_models"] == all_models
assert bot.get_plan_ai_models("lifetime", include_disabled=True) == all_models
assert bot.get_plan_ai_models("lifetime") == enabled_models
assert set(bot._AI_MODELS_DISABLED_BY_DEFAULT).isdisjoint(enabled_models)

# An explicitly assigned paid plan is respected for an admin/owner too, so
# the admin can verify the same plan pool that ordinary users see.
assert bot.get_ai_model(42) == "lifetime"
assert bot.get_ai_model(9001) == "lifetime"

# Owner/admin accounts are Lifetime even if an old database record still says
# Free. Ordinary Free users remain restricted to the Free pool.
users["9001"]["plan"] = "free"
assert bot.get_ai_model(9001) == "lifetime"
assert bot.get_ai_model(7) == "free"
users["9001"]["plan"] = "lifetime"

# No user-side three-model truncation remains, either for defaults or saved
# choices. A saved choice is preserved without deleting later fallbacks.
assert bot.get_user_ai_models(42, "lifetime") == enabled_models
bot.set_user_ai_models(42, all_models)
assert users["42"]["ai_models"] == all_models
assert bot.get_user_ai_models(42, "lifetime") == enabled_models

# The selected model is tried first, but the full plan pool remains in the
# chain rather than being sliced to three entries.
bot.USER_STATES[42] = {"ai_model": enabled_models[-1]}
called = []
bot._call_kaalix_model = lambda model, prompt: called.append(model) or None
reply, used = bot._call_ai_chain("test", "lifetime", 42)
assert reply is None and used is None
assert called == [enabled_models[-1], *enabled_models[:-1]]

# A selected model that fails must fall through in configured order, not race
# a later provider that happens to answer faster.
fallback_calls = []
def primary_then_fallback(model, prompt):
    fallback_calls.append(model)
    if model == enabled_models[-1]:
        return None
    return "answer from the first configured fallback"
bot._call_kaalix_model = primary_then_fallback
reply, used = bot._call_ai_chain("test", "lifetime", 42)
assert reply == "answer from the first configured fallback"
assert used == enabled_models[0]
assert fallback_calls == [enabled_models[-1], enabled_models[0]]

# A generic welcome is not a successful answer to a substantive prompt; the
# selected model must be skipped in favor of the next plan-eligible operative.
generic_fallback_calls = []
def generic_then_useful(model, prompt):
    generic_fallback_calls.append(model)
    if model == enabled_models[-1]:
        return "I'm ready to assist you with your question about Cipher Tech Hosting. How can I help you today?"
    return "Here is the requested explanation with practical steps."
bot._call_kaalix_model = generic_then_useful
reply, used = bot._call_ai_chain("Explain how to restart my bot", "lifetime", 42)
assert reply == "Here is the requested explanation with practical steps."
assert used == enabled_models[0]
assert generic_fallback_calls == [enabled_models[-1], enabled_models[0]]

# A plan with one operative must not silently invoke hard-coded premium models.
bot.set_plan_ai_models("free", ["gpt-4o-mini"])
users["7"]["ai_models"] = []
bot.USER_STATES[7] = {"ai_model": "gpt-4o-mini"}
free_calls = []
bot._call_kaalix_model = lambda model, prompt: free_calls.append(model) or None
reply, used = bot._call_ai_chain("test", "free", 7)
assert reply is None and used is None
assert free_calls == ["gpt-4o-mini"], free_calls

# Existing deployments can have saved pools containing only retired IDs;
# those users should receive the current working defaults rather than no AI.
settings["ai_plan_starter_models"] = ["deepseek-v3", "mistral"]
assert bot.get_plan_ai_models("starter") == bot._AI_PLAN_DEFAULT_MODELS["starter"]

# The shared response boundary enforces the same relationship for every AI
# feature, not only the Telegram chat handler. A real first question must reach
# the model chain rather than being replaced with a generic welcome.
context = bot._build_cipher_ai_context("Qwen 80B")
assert "Cipher Tech Hosting" in context
assert "Telegram platform" in context
assert "Lord Cipher" in context and "master" in context
assert "tiered hosting" in context.lower() or "plans and prices" in context.lower()
assert "general-purpose AI" in context
assert "coding, debugging" in context
assert "do not redirect unrelated questions to hosting" in context
assert "ask one concise clarifying question" in context
assert "do not mention them when unrelated" in context.lower()
assert "ABSOLUTE STEALTH" not in context
assert "Military-Grade End-to-End Encryption" not in context
fable_context = bot._build_cipher_ai_context("claude-fable-5")
assert "CURRENT OPERATIVE: Claude Fable 5" in fable_context
assert "do not guess who trained the underlying model" in fable_context.lower()
assert bot._is_lord_cipher_identity_request("Who is Lord Cipher to you?")
assert bot._is_lord_cipher_identity_request("Who is your master and creator?")
corrected_identity = bot._enforce_lord_cipher_identity(
    "Who is Lord Cipher to you?",
    "Lord Cipher is not my creator, mentor, or master. I am Claude.",
)
assert "Lord Cipher is my creator" in corrected_identity
assert "master" in corrected_identity.lower()
assert "not my creator" not in corrected_identity.lower()

chain_calls = []
def fake_ai_chain(prompt, plan, uid=None, preferred_model=None):
    chain_calls.append((prompt, plan, uid))
    return "I am a general assistant.", "claude"
bot._call_ai_chain = fake_ai_chain
identity_reply = bot._call_ai_api("Who is your master and creator?", "lifetime", 42)
identity_lower = identity_reply.lower()
for term in ("lord cipher", "creator", "mentor", "master"):
    assert term in identity_lower, identity_reply
assert len(chain_calls) == 1, chain_calls
ordinary_reply = bot._call_ai_api("How do I restart my bot?", "lifetime", 42)
assert "lord cipher is my creator" not in ordinary_reply.lower(), ordinary_reply
assert "welcome to cipher tech hosting" not in ordinary_reply.lower(), ordinary_reply
assert len(chain_calls) == 2, chain_calls

# Greeting variants get a warm general welcome; a short substantive question
# like "Why?" must still go through the AI chain instead of being mistaken for
# a greeting.
before_greeting = len(chain_calls)
greeting_reply = bot._call_ai_api("Hi there!", "lifetime", 42)
assert "hello" in greeting_reply.lower() and "coding" in greeting_reply.lower()
assert "what would you like to work on" in greeting_reply.lower()
assert len(chain_calls) == before_greeting
short_question = bot._call_ai_api("Why?", "free", None)
assert short_question == "I am a general assistant."
assert len(chain_calls) == before_greeting + 1

bot._remember_ai_turn(42, "My project is called Atlas", "I will remember Atlas for this account.")
profile_prompt = bot._build_ai_request("Can you help me debug this?", 42)
assert "Can you help me debug this?" in profile_prompt
assert "name='Test User'" in profile_prompt
assert "@test_user" in profile_prompt
assert "My project is called Atlas" in profile_prompt
identity_prompt = bot._build_ai_request("Who is your mentor?", 42)
assert "telegram_id=42" in identity_prompt
banner_only = "🤖 AI OPERATIVE (CLAUDE)\n━━━━━━━━━━━━━━━━\n\nᶜᴵᴾᴴᴱᴿ Tᴇᴄʜ Hᴏsᴛ v2.1"
assert bot._sanitize_ai_reply(banner_only) == ""
assert bot._ai_unavailable_reply().strip()
assert bot._extract_ai_reply({"result": "I am Standard AI Chat by DeepAI, serving as the official AI assistant."}) is None
assert bot._extract_ai_reply({"result": "Hello! I'm HotBot Chat. How can I help you today?"}) is None
assert bot._extract_ai_reply({"result": "What can I assist you with today?"}) is None
assert bot._extract_ai_reply({"result": "A useful answer about restarting a bot."}) == "A useful answer about restarting a bot."
assert "standard ai chat by deepai" not in bot._sanitize_ai_reply(
    "I am Standard AI Chat by DeepAI.\n━━━━━━━━\nCipher Tech Hosting v2.1\nUseful answer."
).lower()
provider_attribution = bot._sanitize_ai_reply(
    "Claude Fable 5 is an underlying model from Anthropic; Lord Cipher configured this platform assistant."
)
assert "Anthropic" in provider_attribution
assert "Cipher AI" not in provider_attribution
assert bot._lord_cipher_profile_answer().lower().count("lord cipher") >= 2
bot.AI_LAST_MODEL_USED[42] = "claude"
assert bot.ai_model_tag(42, "lifetime") == bot._public_ai_model_name("claude").upper()

print("AI routing regression tests passed")
