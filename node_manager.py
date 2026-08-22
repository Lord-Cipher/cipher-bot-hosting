"""Safe, provider-neutral infrastructure node primitives.

This module deliberately performs discovery and connectivity tests only. It never
installs packages or runs destructive commands without an explicit caller action.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet

STATES = {"ONLINE", "OFFLINE", "AUTHENTICATION FAILED", "UNSUPPORTED", "NEEDS SETUP"}


def new_node(name: str, connection_type: str = "local", **fields: Any) -> Dict[str, Any]:
    if connection_type not in {"local", "ssh", "agent"}:
        raise ValueError("connection_type must be local, ssh, or agent")
    return {
        "id": uuid.uuid4().hex,
        "name": name.strip(),
        "provider": fields.get("provider", ""),
        "connection_type": connection_type,
        "ipv4": fields.get("ipv4", ""),
        "ipv6": fields.get("ipv6", ""),
        "hostname": fields.get("hostname", ""),
        "url": fields.get("url", ""),
        "ssh_port": int(fields.get("ssh_port", 22)),
        "username": fields.get("username", ""),
        "auth_method": fields.get("auth_method", "key"),
        "enabled": bool(fields.get("enabled", True)),
        "status": "NEEDS SETUP",
        "capabilities": {},
        "last_test": None,
        "secret_ref": fields.get("secret_ref", ""),
    }


def encrypt_secret(value: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt_secret(value: str, key: str) -> str:
    return Fernet(key.encode()).decrypt(value.encode()).decode()


def local_capabilities() -> Dict[str, Any]:
    usage = shutil.disk_usage(Path.cwd())
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpuCores": os.cpu_count() or 1,
        "ramBytes": _ram_bytes(),
        "diskBytes": {"total": usage.total, "free": usage.free},
        "docker": shutil.which("docker") is not None,
        "python": platform.python_version(),
    }


def _ram_bytes() -> Optional[int]:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except Exception:
        return None
    return None


def test_node(node: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    """Test connectivity without changing the node or running remote commands."""
    if not node.get("enabled"):
        return {"state": "OFFLINE", "reason": "Node disabled"}
    kind = node.get("connection_type")
    if kind == "local":
        caps = local_capabilities()
        return {"state": "ONLINE", "capabilities": caps}
    if kind == "agent":
        url = node.get("url") or node.get("hostname")
        if not url or not str(url).startswith(("https://", "http://")):
            return {"state": "UNSUPPORTED", "reason": "Documented HTTP(S) agent URL required"}
        return {"state": "NEEDS SETUP", "reason": "Authenticated agent adapter not configured"}
    if kind == "ssh":
        host = node.get("hostname") or node.get("ipv4") or node.get("ipv6")
        if not host or not node.get("username"):
            return {"state": "NEEDS SETUP", "reason": "SSH host and username required"}
        try:
            with socket.create_connection((host, int(node.get("ssh_port", 22))), timeout=timeout):
                return {"state": "ONLINE", "capabilities": {"transport": "tcp-reachable"}}
        except socket.timeout:
            return {"state": "OFFLINE", "reason": "Connection timed out"}
        except PermissionError:
            return {"state": "AUTHENTICATION FAILED", "reason": "Permission denied"}
        except OSError as exc:
            return {"state": "OFFLINE", "reason": str(exc)[:160]}
    return {"state": "UNSUPPORTED", "reason": "Unknown connection type"}
