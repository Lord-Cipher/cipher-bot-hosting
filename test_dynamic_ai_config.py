from pathlib import Path
import json

from ai_engine import AIEngine, ModelRoute


class Response:
    status_code = 200
    def json(self):
        return {"text": "API connection successful"}


class Session:
    def __init__(self):
        self.url = None
        self.params = None
    def get(self, url, params=None, timeout=None):
        self.url = url
        self.params = params
        return Response()


session = Session()
engine = AIEngine(
    session=session,
    base_urls=("https://api.omegatech.app",),
    routes={
        "shared-gemini": ModelRoute(
            "https://api.omegatech.app/v1/chat/completions",
            lambda prompt: {"model": "gemini-2.5-pro", "messages": [{"role": "user", "content": prompt}]},
        )
    },
    context_builder=lambda prompt: "",
    enabled=lambda key: True,
)
result = engine.complete("shared-gemini", "hello")
assert result.ok
assert session.url == "https://api.omegatech.app/v1/chat/completions"
assert session.params["model"] == "gemini-2.5-pro"

source = Path("bot.py").read_text(encoding="utf-8")
assert "adm_ai_omega_browser" in source
assert "await_adm_ai_model_endpoint" in source
assert "adm_ai_omega_format_openai" in source
assert "Choose an Omega endpoint" in source
assert "ai_custom_models" in source
assert "reload_ai_runtime" in source
assert "key|display name|endpoint|format|model_id" not in source
print("dynamic AI configuration tests passed")
