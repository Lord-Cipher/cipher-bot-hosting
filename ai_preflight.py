"""Safe preflight validation for hosted bot source before launch."""
from __future__ import annotations
import json, subprocess
from pathlib import Path
from typing import Any, Dict

def run_preflight(bot_dir: str | Path, runtime: str, entry: str, timeout: int = 90) -> Dict[str, Any]:
    root = Path(bot_dir).resolve()
    if not root.exists() or ".." in Path(entry).parts or Path(entry).is_absolute():
        return {"ok": False, "verdict": "BLOCKED", "reason": "unsafe source path"}
    image, check = ("node:22-slim", ["node", "--check", f"/app/{entry}"]) if runtime == "node" else ("python:3.11-slim", ["python", "-m", "py_compile", f"/app/{entry}"])
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", "65532:65532", "-v", f"{root}:/app:ro", "-w", "/app", image, *check]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "verdict": "NEEDS SETUP", "reason": "Docker unavailable"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "verdict": "BLOCKED", "reason": "preflight timed out"}
    return {"ok": proc.returncode == 0, "verdict": "SAFE" if proc.returncode == 0 else "DANGEROUS", "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
