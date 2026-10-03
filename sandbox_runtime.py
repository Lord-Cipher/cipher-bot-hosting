"""Restricted container command construction for hosted bot workloads.

The builder never executes a command. Callers must explicitly approve and run
the returned command, keeping installation and destructive actions outside the
automatic path.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

PLAN_LIMITS = {
    "free": {"cpus": "0.25", "memory": "256m", "pids": 64, "timeout": 300},
    "basic": {"cpus": "0.50", "memory": "512m", "pids": 128, "timeout": 600},
    "pro": {"cpus": "1.00", "memory": "1g", "pids": 256, "timeout": 900},
    "ultra": {"cpus": "2.00", "memory": "2g", "pids": 512, "timeout": 1800},
}


def docker_available() -> bool:
    return shutil.which("docker") is not None


def limits_for_plan(plan: str) -> Dict[str, Any]:
    return dict(PLAN_LIMITS.get(plan, PLAN_LIMITS["free"]))


def install_dependencies_command(workdir: str | Path, plan: str = "free", runtime: str = "python") -> list[str]:
    root = Path(workdir).resolve()
    lim = limits_for_plan(plan)
    if runtime == "node":
        image = "node:22-slim"
        script = (
            "mkdir -p /app/.deps/node_modules && "
            "if [ -f /app/package.json ]; then "
            "cp /app/package.json /app/.deps/package.json && "
            "if [ -f /app/package-lock.json ]; then cp /app/package-lock.json /app/.deps/package-lock.json; fi && "
            "cd /app/.deps && npm install --ignore-scripts --no-audit --no-fund; fi"
        )
    elif runtime == "python":
        image = "python:3.11-slim"
        script = (
            "if [ -f /app/requirements.txt ]; then "
            "python -m pip install --disable-pip-version-check --no-cache-dir --no-input "
            "--target /app/.deps -r /app/requirements.txt; fi"
        )
    else:
        raise ValueError("unsupported runtime")
    deps = root / ".deps"; deps.mkdir(parents=True, exist_ok=True)
    deps_stat = deps.stat()
    install_user = f"{deps_stat.st_uid}:{deps_stat.st_gid}"
    return ["docker", "run", "--rm", "--network", "bridge", "--cpus", lim["cpus"], "--memory", lim["memory"], "--pids-limit", str(lim["pids"]), "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", install_user, "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-e", "PIP_NO_CACHE_DIR=1", "-e", "NPM_CONFIG_CACHE=/tmp/npm-cache", "-v", f"{root}:/app:ro", "-v", f"{deps}:/app/.deps:rw", image, "sh", "-lc", script]


def build_run_command(bot_id: str, workdir: str | Path, entrypoint: str, plan: str = "free", network: bool = False, runtime: str = "python", env_file: str | Path | None = None) -> list[str]:
    if not bot_id or "/" in bot_id or ".." in bot_id:
        raise ValueError("invalid bot id")
    root = Path(workdir).resolve()
    entry = Path(entrypoint)
    if entry.is_absolute() or ".." in entry.parts:
        raise ValueError("entrypoint must stay inside workdir")
    lim = limits_for_plan(plan)
    cmd = ["docker", "run", "--rm", "--name", f"cipher-bot-{bot_id}",
           "--cpus", lim["cpus"], "--memory", lim["memory"], "--pids-limit", str(lim["pids"]),
           "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
           "--user", "65532:65532", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
    if not network:
        cmd += ["--network", "none"]
    if env_file:
        cmd += ["--env-file", str(Path(env_file).resolve())]
    if runtime not in {"python", "node"}:
        raise ValueError("unsupported runtime")
    image, executable = ("node:22-slim", "node") if runtime == "node" else ("python:3.11-slim", "python")
    deps = root / ".deps"; deps.mkdir(parents=True, exist_ok=True)
    cmd += ["-v", f"{root}:/app:ro", "-v", f"{deps}:/app/.deps:ro"]
    if runtime == "python":
        cmd += ["-e", "PYTHONPATH=/app/.deps"]
    else:
        node_modules = deps / "node_modules"
        node_modules.mkdir(parents=True, exist_ok=True)
        cmd += ["-v", f"{node_modules}:/app/node_modules:ro"]
    cmd += ["--tmpfs", "/app/.tmp_run:rw,noexec,nosuid,size=64m,uid=65532,gid=65532"]
    cmd += ["-w", "/app", image, executable, str(entry)]
    return cmd
