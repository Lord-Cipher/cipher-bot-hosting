from pathlib import Path
import tempfile
from ai_preflight import run_preflight
from sandbox_runtime import build_run_command
from vault_sync import sync_vault

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
