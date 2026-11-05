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
assert bot._public_ai_model_name("gpt-4o-mini") == "GPT-4o Mini"
assert bot._public_ai_model_name("gemini-pro") == "Gemini Pro"
assert len(set(bot._AI_OPERATIVE_LABELS.values())) == len(bot._AI_OPERATIVE_LABELS)
assert bot._OMEGATECH_HOSTS == ("https://api.omegatech.app",)
assert set(bot._AI_OPERATIVE_KEYS) == {"gpt-4o-mini", "deepseek-cli", "chatgpt-v2", "gemini-pro"}

original_get = bot.AI_HTTP.get
original_get_setting = bot.get_setting
original_log_notification = bot.log_notification
calls = []


def gemini_model_reply(url, params=None, timeout=None):
    calls.append((url, params, timeout))
    return Response(200, {
        "statusCode": 200,
        "success": True,
        "answer": "usable Gemini Pro model answer",
    })


bot.get_setting = lambda key, default=None: default
bot.AI_MODEL_FAILURE_COUNT.clear()
bot.AI_MODEL_CIRCUIT_OPEN_UNTIL.clear()
try:
    bot.AI_HTTP.get = gemini_model_reply
    assert bot._call_ai_model("gemini-pro", "Reply with a short answer") == "usable Gemini Pro model answer"
    assert len(calls) == 1, calls
    url, params, timeout = calls[0]
    assert url == "https://api.omegatech.app/api/ai/gemini-pro", url
    assert params["text"].endswith("USER REQUEST:\nReply with a short answer"), params
finally:
    bot.AI_HTTP.get = original_get
    bot.get_setting = original_get_setting

# Models that failed the latest live smoke test have been removed instead of
# being left as admin-enableable entries.
removed_models = {
    "claude", "chatbot", "qwen-80b", "deepai-deepseek-v3-2",
    "deepai-gemini-flash-lite", "deepai-gpt-oss-120b", "deepai-gpt-5-nano",
    "claude-sonnet-4-6", "claude-opus-4-7", "gpt-5-4",
    "gemini-3-5-flash", "deepseek-v4-pro", "deepai-llama-3-3-70b",
    "claude-haiku", "agents-mistral", "agents-qwen-vl", "agents-qwen25-vl",
    "agents-moondream2",
}
assert not (removed_models & set(bot._AI_OPERATIVE_KEYS))
assert not (removed_models & set(bot._create_ai_engine().routes))
assert not any(key.startswith("agents-") for key in bot._AI_OPERATIVE_KEYS)

# Retired provider keys must not silently route to the old Kaalix API.
calls.clear()
bot.AI_HTTP.get = lambda *args, **kwargs: calls.append((args, kwargs))
try:
    for retired in removed_models | {"deepseek-v3"}:
        assert bot._call_ai_model(retired, "short prompt") is None
    assert not calls, calls
finally:
    bot.AI_HTTP.get = original_get

# Gemini Pro was documented and returned a usable answer in the live check.
new_models = {
    "gemini-pro": ("gemini-pro", "basic"),
}
assert len(set(bot._AI_OPERATIVE_LABELS.values())) == len(bot._AI_OPERATIVE_LABELS)
for model, (provider_model, plan) in new_models.items():
    assert model in bot._AI_OPERATIVE_KEYS, model
    assert model in bot._AI_PLAN_DEFAULT_MODELS[plan], (model, plan)
    route_path, build_params = bot._OMEGATECH_MODELS[model]
    assert route_path == "gemini-pro", route_path
    assert build_params("safe test prompt") == {"text": "safe test prompt"}

model = "chatgpt-v2"
assert model in bot._AI_PLAN_DEFAULT_MODELS["free"]
assert bot._OMEGATECH_MODELS[model][1]("safe test prompt") == {
    "action": "chat", "message": "safe test prompt"
}

assert bot._AGENTS_MODEL_IDS == ()

route_calls = []
def new_route_reply(url, params=None, timeout=None):
    route_calls.append((url, params or {}))
    return Response(200, {"statusCode": 200, "success": True,
                          "data": {"reply": "Helpful test response"}})

bot.AI_HTTP.get = new_route_reply
bot.get_setting = lambda key, default=None: default
bot.AI_MODEL_FAILURE_COUNT.clear()
bot.AI_MODEL_CIRCUIT_OPEN_UNTIL.clear()
try:
    for model in [*new_models, "chatgpt-v2"]:
        route_calls.clear()
        assert bot._call_ai_model(model, "safe test prompt") == "Helpful test response", model
        assert route_calls and route_calls[-1][0].endswith(
            "/api/ai/" + bot._OMEGATECH_MODELS[model][0]
        ), (model, route_calls)
        sent = route_calls[-1][1]
        assert "safe test prompt" in str(sent), (model, sent)
finally:
    bot.AI_HTTP.get = original_get
    bot.get_setting = original_get_setting

# Repeated failures quarantine only one operative. A different healthy model
# remains callable as the ordered fallback for the same user request.
bot.get_setting = lambda key, default=None: default
bot.log_notification = lambda *args, **kwargs: None
bot.AI_HTTP.get = lambda url, params=None, timeout=None: Response(503, {"error": "offline"})
try:
    for _ in range(5):
        assert bot._call_ai_model("gemini-pro", "short prompt") is None
    assert "gemini-pro" in bot.AI_MODEL_CIRCUIT_OPEN_UNTIL
    bot.AI_HTTP.get = lambda url, params=None, timeout=None: Response(
        200, {"success": True, "result": "healthy independent operative answer"})
    assert bot._call_ai_model("gpt-4o-mini", "short prompt") == "healthy independent operative answer"
finally:
    bot.AI_HTTP.get = original_get
    bot.get_setting = original_get_setting
    bot.log_notification = original_log_notification

print("AI endpoint parsing and routing tests passed")
