"""Focused regression tests for configurable AI malware scanning."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_token_for_scanner_tests")
os.environ.setdefault("OWNER_ID", "9001")

import bot  # noqa: E402

settings = {"ai_scanner_model": "gpt-4o-mini"}
bot.get_setting = lambda key, default=None: settings.get(key, default)

calls = []
responses = {
    "gpt-4o-mini": None,
    "deepseek-cli": '{"verdict":"SUSPICIOUS","risk_score":80,"reason":"dynamic execution","threats":["dynamic execution"]}',
}


def fake_call(model, prompt):
    calls.append(model)
    return responses.get(model)


bot._call_kaalix_model = fake_call
result = bot._ai_scan_code("eval(input())", "sample.py")
assert calls[:2] == ["gpt-4o-mini", "deepseek-cli"]
assert result["ai_model"] == "deepseek-cli"
assert result["ai_risk_score"] == 80

# The configured scanner model is used first, independently of user plan
# routing, and the combined scanner exposes progress stages.
progress = []
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "sample.py"
    path.write_text("print('hello')\n", encoding="utf-8")
    bot._scan_file = lambda filename: {
        "verdict": "SAFE", "risk_score": 0, "all_threats": [], "filename": "sample.py"
    }
    responses["gpt-4o-mini"] = '{"verdict":"SAFE","risk_score":2,"reason":"clean","threats":[]}'
    calls.clear()
    combined = bot._combined_scan(str(path), progress_cb=lambda pct, status: progress.append((pct, status)))

assert calls == ["gpt-4o-mini"]
assert combined["ai_model"] == "gpt-4o-mini"
assert progress
assert progress[0][0] <= progress[-1][0]
assert any("pattern scan" in status for _, status in progress)
assert any("AI analyzing" in status for _, status in progress)
assert any("Merging" in status for _, status in progress)

print("Scanner regression tests passed")
