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
    result = bot._clone_gh_repo("https://github.com/acme/private-repo", "pat_test", Path("/tmp/private-clone-test"))
finally:
    if old_subprocess is None:
        sys.modules.pop("subprocess", None)
    else:
        sys.modules["subprocess"] = old_subprocess

assert result["ok"] is True
assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
expected = base64.b64encode(b"x-access-token:pat_test").decode()
assert seen["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"

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
