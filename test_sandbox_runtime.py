"""Offline tests for local Docker Sandbox dependency/runtime commands."""
from __future__ import annotations

import tempfile
from pathlib import Path

from sandbox_runtime import build_run_command, install_dependencies_command


with tempfile.TemporaryDirectory(prefix="sandbox-runtime-test-") as tmp:
    root = Path(tmp)
    (root / "bot.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    install = install_dependencies_command(root, "free", runtime="python")
    assert "--network" in install and install[install.index("--network") + 1] == "bridge"
    assert "--target /app/.deps -r /app/requirements.txt" in install[-1]
    assert install[install.index("--user") + 1] == f"{(root / '.deps').stat().st_uid}:{(root / '.deps').stat().st_gid}"
    assert f"{root}/.deps:/app/.deps:rw" in install

    run = build_run_command("bot1", root, "bot.py", "free", network=False, runtime="python")
    assert run[run.index("--network") + 1] == "none"
    assert "PYTHONPATH=/app/.deps" in run
    assert f"{root}/.deps:/app/.deps:ro" in run
    assert any(arg.startswith("/app/.tmp_run:rw,noexec,nosuid") for arg in run)
    assert not any("/.tmp_run:/app/.tmp_run" in arg for arg in run)

with tempfile.TemporaryDirectory(prefix="sandbox-runtime-node-test-") as tmp:
    root = Path(tmp)
    (root / "index.js").write_text("console.log('ok')\n", encoding="utf-8")
    (root / "package.json").write_text('{"dependencies":{"example":"1.0.0"}}', encoding="utf-8")
    install = install_dependencies_command(root, "basic", runtime="node")
    assert "npm install --ignore-scripts --no-audit --no-fund" in install[-1]
    assert "cd /app/.deps" in install[-1]
    run = build_run_command("bot2", root, "index.js", "basic", network=True, runtime="node")
    assert f"{root}/.deps/node_modules:/app/node_modules:ro" in run
    assert "PYTHONPATH=/app/.deps" not in run
    assert not any(arg == "none" for arg in run)

for unsafe in ("../bot.py", "/etc/passwd"):
    try:
        build_run_command("bot3", "/tmp", unsafe)
    except ValueError:
        pass
    else:
        raise AssertionError(f"unsafe entrypoint unexpectedly accepted: {unsafe}")

print("Local Sandbox runtime regression tests passed")
