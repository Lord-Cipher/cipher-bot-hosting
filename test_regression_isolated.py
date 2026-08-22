from pathlib import Path
import tempfile
from ai_preflight import run_preflight
from sandbox_runtime import build_run_command
from vault_sync import sync_vault
import ai_preflight
import remote_worker
import sandbox_runtime

# Report, but do not require, host Docker availability; live image pulls are avoided.
print("docker available:", sandbox_runtime.docker_available())

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

# Preflight rejects traversal and absolute entrypoints before invoking Docker.
tmp = Path(tempfile.mkdtemp())
(tmp / "main.py").write_text("print('ok')\n", encoding="utf-8")
assert run_preflight(tmp, "python", "../main.py")["verdict"] == "BLOCKED"
assert run_preflight(tmp, "python", "/etc/passwd")["verdict"] == "BLOCKED"

# Sandbox command construction must be containerized, read-only, and non-privileged.
cmd = build_run_command("b1", tmp, "main.py", "free", network=False, runtime="python", env_file=tmp / ".env")
joined = " ".join(map(str, cmd))
for required in ("--read-only", "--cap-drop", "ALL", "--network", "none"):
    assert required in joined, joined
assert "--privileged" not in cmd

# Plan limits are present in every generated sandbox command.
for plan, memory in (("free", "256m"), ("basic", "512m"), ("pro", "1g"), ("ultra", "2g")):
    generated = build_run_command("b1", tmp, "main.py", plan, network=False, runtime="python", env_file=None)
    assert memory in " ".join(generated)

# SSH input validation rejects incomplete nodes before any network attempt.
assert remote_worker.deploy({}, "secret", "b1", tmp, "python", "main.py", "free", {})["ok"] is False
assert remote_worker.deploy({"hostname": "127.0.0.1", "username": "u"}, "secret", "b1", tmp, "python", "main.py", "free", {})["ok"] is False

# Pre-flight timeout is deterministic when its subprocess is replaced by a sleeper.
class TimeoutProcess:
    def run(self, *args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(kwargs.get("args", args[0] if args else "docker"), 1)
old_run = ai_preflight.subprocess.run
try:
    ai_preflight.subprocess.run = TimeoutProcess().run
    assert ai_preflight.run_preflight(tmp, "python", "main.py")["verdict"] == "BLOCKED"
finally:
    ai_preflight.subprocess.run = old_run

# Source invariants protect the approval gate, rollback path, and webhook secret check.
for marker in ("approval_status"):
    assert marker in BOT
assert "def _start_failure" in BOT
assert "deployment_rollback_at" in BOT
assert "compare_digest(supplied_secret, WEBHOOK_SECRET)" in BOT

# Vault refuses plaintext operation when the encryption key is absent/invalid.
vault_result = sync_vault(tmp, token="", repo="owner/private", key="")
assert vault_result["ok"] is False
assert "token" in vault_result["error"].lower()
vault_result = sync_vault(tmp, token="placeholder", repo="owner/private", key="")
assert vault_result["ok"] is False
assert "CIPHER_VAULT_KEY" in vault_result["error"]
print("isolated regression suite passed")
