"""Restricted container command construction for hosted bot workloads.

The builder never executes a command. Callers must explicitly approve and run
the returned command, keeping installation and destructive actions outside the
automatic path.
"""
from __future__ import annotations

import shutil
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


def build_run_command(bot_id: str, workdir: str | Path, entrypoint: str, plan: str = "free", network: bool = False) -> list[str]:
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
    cmd += ["-v", f"{root}:/app:rw", "-w", "/app", "python:3.11-slim", "python", str(entry)]
    return cmd
