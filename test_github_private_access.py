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
    def json(self):
        return {"ok": True}
    def raise_for_status(self):
        return None

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

# Public repository browsing must also work without any user token.
old_user_token = bot._user_gh_token
old_get = bot.requests.get
try:
    calls = {}
    bot._user_gh_token = lambda uid: ""
    def fake_public_get(url, **kwargs):
        calls["url"] = url
        calls["headers"] = kwargs["headers"]
        return FakeResponse(200)
    bot.requests.get = fake_public_get
    assert bot._gh_user_api(9001, "/repos/acme/public-repo/branches") == {"ok": True}
    assert "Authorization" not in calls["headers"]
finally:
    bot._user_gh_token = old_user_token
    bot.requests.get = old_get

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

progress_view = bot._gh_clone_progress("https://github.com/acme/private-repo", 56, "Downloading files")
assert "[🟩🟩🟩🟩🟩⬜⬜⬜⬜⬜] 56%" in progress_view
assert "Downloading files" in progress_view
escaped_progress = bot._gh_clone_progress("<bad>", 150, "Done")
assert "100%" in escaped_progress and "&lt;bad&gt;" in escaped_progress
shared_bar = bot._colored_progress_bar(70, width=10, decimals=0)
assert shared_bar == "🟩🟩🟩🟩🟩🟨🟨⬜⬜⬜ 70%"
assert bot._progress_bar(25, total=100, width=4) == "🟩⬜⬜⬜ 25.0%"

# Resource bars are relative to plan limits: green through the first half,
# yellow through 79%, then red from 80% to the limit.
assert bot._resource_usage_bar(40).startswith("🟩🟩🟩🟩⬜")
assert bot._resource_usage_bar(70).startswith("🟩🟩🟩🟩🟩🟨🟨⬜")
assert bot._resource_usage_bar(90).startswith("🟩🟩🟩🟩🟩🟨🟨🟨🟥")
assert bot._resource_usage_bar(125).endswith("100%")

# Commit picker requests the most recent commits for the selected branch.
uid = 9102
old_state = bot.USER_STATES.get(uid)
old_user_api = bot._gh_user_api
old_browser_show = bot._gh_browser_show
old_ack = bot.ack
try:
    bot.USER_STATES[uid] = {"ghb_repo": "acme/demo", "ghb_branch": "release/v2", "ghb_path": ""}
    api_seen = {}
    def fake_commits_api(user_id, endpoint):
        api_seen["endpoint"] = endpoint
        return [{"sha": "abcdef1234567890", "commit": {"message": "Release v2"}}]
    bot._gh_user_api = fake_commits_api
    rendered = {}
    bot._gh_browser_show = lambda call, text, kb: rendered.update(text=text, keyboard=kb)
    bot.ack = lambda *args, **kwargs: None
    fake_call = types.SimpleNamespace(from_user=types.SimpleNamespace(id=uid))
    bot.action_gh_browser_commits(fake_call)
    assert api_seen["endpoint"] == "/repos/acme/demo/commits?sha=release%2Fv2&per_page=10"
    assert bot.USER_STATES[uid]["ghb_commits"][0]["sha"] == "abcdef1234567890"
    assert "Choose a commit" in rendered["text"]
finally:
    bot._gh_user_api = old_user_api
    bot._gh_browser_show = old_browser_show
    bot.ack = old_ack
    if old_state is None:
        bot.USER_STATES.pop(uid, None)
    else:
        bot.USER_STATES[uid] = old_state

# Update checks compare the pinned commit to the selected GitHub branch head.
old_user_api = bot._gh_user_api
try:
    update_calls = []
    def fake_update_api(user_id, endpoint):
        update_calls.append(endpoint)
        if "commits?sha=release%2Fv2" in endpoint:
            return [{"sha": "new-head-123456"}]
        return {"files": [{"filename": "bot.py", "status": "modified", "additions": 2, "deletions": 1}], "commits": [{}]}
    bot._gh_user_api = fake_update_api
    sync_bot = {"gh_repo": "https://github.com/acme/demo", "gh_branch": "release/v2", "gh_commit_sha": "old-base-123456"}
    update = bot._gh_find_update(uid, sync_bot)
    assert update["status"] == "behind" and update["head"] == "new-head-123456"
    assert update["files"][0]["filename"] == "bot.py"
finally:
    bot._gh_user_api = old_user_api
