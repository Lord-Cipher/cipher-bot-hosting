"""Offline regression checks for AI provider response parsing and failover."""
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


assert bot._extract_ai_reply({"result": "Claude answer"}) == "Claude answer"
assert bot._extract_ai_reply({"data": {"reply": "Nested hotbot answer"}}) == "Nested hotbot answer"
assert bot._extract_ai_reply({"response": "Kaalix answer"}) == "Kaalix answer"
assert bot._extract_ai_reply({"content": [{"text": "Nested content answer"}]}) == "Nested content answer"
assert bot._extract_ai_reply({"success": True, "result": "  "}) is None

original_get = bot.requests.get
original_get_setting = bot.get_setting
calls = []


def fake_get(url, params=None, timeout=None):
    calls.append((url, params, timeout))
    if len(calls) == 1:
        return Response(503, {"error": "temporary mirror failure"})
    return Response(200, {"statusCode": 200, "success": True, "result": "fallback mirror answer"})


bot.requests.get = fake_get
bot.get_setting = lambda key, default=None: default
bot.AI_FAILURE_COUNT = 0
bot.AI_LAST_FAILURE = 0
bot.AI_CIRCUIT_OPEN = False

try:
    assert bot._call_ai_model("claude", "Reply with a short answer") == "fallback mirror answer"
    assert len(calls) == 2, calls
finally:
    bot.requests.get = original_get
    bot.get_setting = original_get_setting

print("AI endpoint parsing and failover tests passed")