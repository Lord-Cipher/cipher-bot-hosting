"""Offline regression checks for AI provider response parsing and routing."""
from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_ai_endpoints")
os.environ.setdefault("OWNER_ID", "1")

import bot  # noqa: E402


class Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


assert bot._extract_ai_reply({"result": "Legacy answer"}) == "Legacy answer"
assert bot._extract_ai_reply({"data": {"reply": "Secret route answer"}}) == "Secret route answer"
assert bot._extract_ai_reply({"response": "Nested response"}) == "Nested response"
assert bot._extract_ai_reply({"content": [{"text": "Nested content answer"}]}) == "Nested content answer"
assert bot._extract_ai_reply({"success": True, "result": "  "}) is None
assert bot._public_ai_model_name("claude-fable-5") == "Claude Fable 5"
assert bot._public_ai_model_name("gpt-5-5") == "GPT-5.5"
assert len(set(bot._AI_OPERATIVE_LABELS.values())) == len(bot._AI_OPERATIVE_LABELS)
assert bot._OMEGATECH_HOSTS == ("https://api.omegatech.app",)
assert bot._OMEGATECH_SECRET_MODELS["claude-fable-5"] == "claude-fable-5"
assert bot._OMEGATECH_SECRET_MODELS["gemini-3-5-flash"] == "gemini-3.5-flash"

original_get = bot.AI_HTTP.get
original_get_setting = bot.get_setting
original_log_notification = bot.log_notification
calls = []


def secret_model_reply(url, params=None, timeout=None):
    calls.append((url, params, timeout))
    return Response(200, {
        "statusCode": 200,
        "success": True,
        "data": {"model": "claude-fable-5", "reply": "usable Secret model answer"},
    })


bot.AI_HTTP.get = secret_model_reply
bot.get_setting = lambda key, default=None: default
bot.AI_MODEL_FAILURE_COUNT.clear()
bot.AI_MODEL_CIRCUIT_OPEN_UNTIL.clear()
try:
    assert bot._call_ai_model("claude-fable-5", "Reply with a short answer") == "usable Secret model answer"
    assert len(calls) == 1, calls
    url, params, timeout = calls[0]
    assert url == "https://api.omegatech.app/api/ai/Secret", url
    assert params["action"] == "chat", params
    assert params["model"] == "claude-fable-5", params
    assert params["message"].endswith("USER REQUEST:\nReply with a short answer"), params
finally:
    bot.AI_HTTP.get = original_get
    bot.get_setting = original_get_setting

# Catalog models currently rejected by the provider are registered for the
# admin catalog but hidden from user routing until an admin enables them.
assert bot._ai_operative_enabled("claude-fable-5")
for model in bot._AI_MODELS_DISABLED_BY_DEFAULT:
    assert not bot._ai_operative_enabled(model), model
    assert model in bot._AI_OPERATIVE_KEYS

# Retired provider keys must not silently route to the old Kaalix API.
calls.clear()
bot.AI_HTTP.get = lambda *args, **kwargs: calls.append((args, kwargs))
try:
    assert bot._call_ai_model("deepseek-v3", "short prompt") is None
    assert not calls, calls
finally:
    bot.AI_HTTP.get = original_get

# Repeated failures quarantine only one operative. A different healthy model
# remains callable as the ordered fallback for the same user request.
bot.get_setting = lambda key, default=None: default
bot.log_notification = lambda *args, **kwargs: None
bot.AI_HTTP.get = lambda url, params=None, timeout=None: Response(503, {"error": "offline"})
try:
    for _ in range(5):
        assert bot._call_ai_model("claude", "short prompt") is None
    assert "claude" in bot.AI_MODEL_CIRCUIT_OPEN_UNTIL
    bot.AI_HTTP.get = lambda url, params=None, timeout=None: Response(
        200, {"success": True, "result": "healthy independent operative answer"})
    assert bot._call_ai_model("gpt-4o-mini", "short prompt") == "healthy independent operative answer"
finally:
    bot.AI_HTTP.get = original_get
    bot.get_setting = original_get_setting
    bot.log_notification = original_log_notification

print("AI endpoint parsing and routing tests passed")
