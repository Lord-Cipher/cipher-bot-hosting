from __future__ import annotations

import base64
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_github_private_access")
os.environ.setdefault("OWNER_ID", "9001")

import bot

# Git clone must receive Basic x-access-token auth, not a Bearer header that
# makes git prompt for a password in a non-interactive server process.
seen = {}
class FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""

def fake_run(args, **kwargs):
    seen["args"] = args
    seen["env"] = kwargs["env"]
    return FakeResult()

old_subprocess = sys.modules.get("subprocess")
sys.modules["subprocess"] = types.SimpleNamespace(run=fake_run)
try:
    result = bot._clone_gh_repo("https://github.com/acme/private-repo", "pat_test", Path("/tmp/private-clone-test"), branch="feature/demo")
finally:
    if old_subprocess is None:
        sys.modules.pop("subprocess", None)
    else:
        sys.modules["subprocess"] = old_subprocess

assert result["ok"] is True
assert "--branch" in seen["args"] and "feature/demo" in seen["args"]
assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
expected = base64.b64encode(b"x-access-token:pat_test").decode()
assert seen["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"

# The bot must verify a user's credential against the exact repository before
# trying to browse/clone it, and must not expose the token in returned errors.
class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

old_get = bot.requests.get
try:
    calls = {}
    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["headers"] = kwargs["headers"]
        return FakeResponse(200)
    bot.requests.get = fake_get
    access = bot._check_gh_repo_access("https://github.com/acme/private-repo", "pat_test")
    assert access["ok"] is True
    assert calls["url"] == "https://api.github.com/repos/acme/private-repo"
    assert calls["headers"]["Authorization"] == "Bearer pat_test"

    bot.requests.get = lambda *args, **kwargs: FakeResponse(404)
    access = bot._check_gh_repo_access("https://github.com/acme/private-repo", "pat_test")
    assert access["ok"] is False
    assert "Contents: Read" in access["error"]
finally:
    bot.requests.get = old_get

old_repo = os.environ.get("CIPHER_VAULT_REPO")
os.environ["CIPHER_VAULT_REPO"] = "https://github.com/acme/private-vault.git/"
try:
    assert bot._vault_config()["repo"] == "acme/private-vault"
finally:
    if old_repo is None:
        os.environ.pop("CIPHER_VAULT_REPO", None)
    else:
        os.environ["CIPHER_VAULT_REPO"] = old_repo

print("private GitHub access regression tests passed")

# The interactive browser must authenticate with the requesting user's token.
old_user_token = bot._user_gh_token
old_get = bot.requests.get
try:
    api_calls = {}
    bot._user_gh_token = lambda uid: f"user-token-{uid}"
    def fake_api_get(url, **kwargs):
        api_calls["url"] = url
        api_calls["headers"] = kwargs["headers"]
        return FakeResponse(200)
    FakeResponse.json = lambda self: {"ok": True}
    FakeResponse.raise_for_status = lambda self: None
    bot.requests.get = fake_api_get
    assert bot._gh_user_api(9001, "/repos/acme/private-repo/branches") == {"ok": True}
    assert api_calls["url"] == "https://api.github.com/repos/acme/private-repo/branches"
    assert api_calls["headers"]["Authorization"] == "Bearer user-token-9001"
finally:
    bot._user_gh_token = old_user_token
    bot.requests.get = old_get
