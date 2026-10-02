"""Offline tests for the isolated AI transport and answer-quality engine."""
from __future__ import annotations

from urllib.parse import urlencode

from ai_engine import AIEngine, ModelRoute


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        return self.handler(url, params or {})


def route(name):
    return ModelRoute(name, lambda text: {"message": text})


def engine(session, routes, **kwargs):
    return AIEngine(
        session=session,
        base_urls=("https://provider.invalid",),
        routes=routes,
        context_builder=lambda model: f"PLATFORM CONTEXT for {model}\n",
        enabled=lambda _model: True,
        **kwargs,
    )


# Normalize common OmegaTech and OpenAI-compatible response envelopes.
assert AIEngine.extract_reply({"data": {"reply": "OmegaTech answer"}}) == "OmegaTech answer"
assert AIEngine.extract_reply({"choices": [{"message": {"content": "OpenAI answer"}}]}) == "OpenAI answer"
assert AIEngine.extract_reply({"candidates": [{"content": {"parts": [{"text": "Gemini answer"}]}}]}) == "Gemini answer"
assert AIEngine.extract_reply({"success": False, "error": "offline"}) is None

# Generic welcomes and service failures are not answers to substantive requests.
assert not AIEngine.is_usable("What can I assist you with today?", "Explain how to restart my bot")
assert not AIEngine.is_usable(
    "I'm ready to assist you with your question about Cipher Tech Hosting. How can I help you today?",
    "Reply with exactly: synthetic health check passed.",
)
assert AIEngine.is_usable("What can I assist you with today?", "hello")
assert not AIEngine.is_usable("I received your message, but the AI provider returned no usable text.")
assert not AIEngine.is_usable("Your free usage limit was reached. Upgrade to VIP.")
assert not AIEngine.is_usable("I'm Claude, developed by Anthropic. How can I help?")
assert AIEngine.is_usable("Restart the process with the service manager, then inspect its logs.", "How do I restart it?")

# Long requests are trimmed while preserving the context prefix and newest tail.
fit_engine = engine(FakeSession(lambda *_: FakeResponse(200, {"result": "ok"})), {"primary": route("primary")}, max_encoded_query=190)
fitted = fit_engine.fit_prompt("SYS CONTEXT\n", "A" * 600 + "LATEST-TAIL", route("primary"))
assert fitted is not None and fitted.startswith("SYS CONTEXT\n")
assert "LATEST-TAIL" in fitted
assert len(urlencode({"message": fitted})) <= 190

# A welcome from the selected operative must continue to the next configured model.
def fallback_response(url, _params):
    if url.endswith("/primary"):
        return FakeResponse(200, {"success": True, "result": "What can I assist you with today?"})
    return FakeResponse(200, {"choices": [{"message": {"content": "Here is the requested explanation with useful steps."}}]})

fallback_session = FakeSession(fallback_response)
fallback_engine = engine(fallback_session, {"primary": route("primary"), "backup": route("backup")})
attempts = fallback_engine.complete_chain(["primary", "backup"], "Explain how to restart my bot")
assert [attempt.model for attempt in attempts] == ["primary", "backup"]
assert attempts[0].text is None and attempts[0].error == "provider returned a non-answer"
assert attempts[1].text == "Here is the requested explanation with useful steps."
assert [call[0].rsplit("/", 1)[-1] for call in fallback_session.calls] == ["primary", "backup"]

# Circuit breakers are model-scoped; a broken route does not block a healthy one.
opened = []
quarantine_session = FakeSession(
    lambda url, _params: FakeResponse(
        503 if url.endswith("/broken") else 200,
        {"error": "offline"} if url.endswith("/broken") else {"result": "healthy model answer"},
    )
)
quarantine_engine = engine(
    quarantine_session,
    {"broken": route("broken"), "healthy": route("healthy")},
    failure_threshold=2,
    on_circuit_open=lambda model, seconds: opened.append((model, seconds)),
)
assert not quarantine_engine.complete("broken", "question").ok
assert not quarantine_engine.complete("broken", "question").ok
assert opened == [("broken", 300)]
called_before_block = len(quarantine_session.calls)
assert quarantine_engine.complete("broken", "question").error == "model circuit open"
assert len(quarantine_session.calls) == called_before_block
assert quarantine_engine.complete("healthy", "question").text == "healthy model answer"

# Transport diagnostics never include the prompt or a query-bearing request URL.
logged = []
def raising_get(url, params=None, timeout=None):
    raise RuntimeError(f"GET {url}?message=TOP_SECRET_PROMPT")
private_engine = engine(FakeSession(raising_get), {"primary": route("primary")}, logger=logged.append)
result = private_engine.complete("primary", "TOP_SECRET_PROMPT")
assert result.text is None
assert "TOP_SECRET_PROMPT" not in (result.error or "")
assert "TOP_SECRET_PROMPT" not in " ".join(logged)

print("standalone AI engine regression tests passed")
