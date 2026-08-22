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


def test_ssh_node(node: Dict[str, Any], secret: str, timeout: int = 8) -> Dict[str, Any]:
    """Authenticate with SSH and run read-only capability probes."""
    try:
        import paramiko
    except ImportError:
        return {"state": "NEEDS SETUP", "reason": "paramiko is not installed"}
    host = node.get("hostname") or node.get("ipv4") or node.get("ipv6")
    if not host or not node.get("username"):
        return {"state": "NEEDS SETUP", "reason": "SSH host and username required"}
    client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        kwargs = {"hostname": host, "port": int(node.get("ssh_port", 22)), "username": node["username"], "timeout": timeout, "banner_timeout": timeout, "auth_timeout": timeout}
        if node.get("auth_method", "key") == "password":
            kwargs["password"] = secret
        else:
            key = paramiko.RSAKey.from_private_key(__import__("io").StringIO(secret))
            kwargs["pkey"] = key
        client.connect(**kwargs)
        command = "uname -s; uname -m; getconf _NPROCESSORS_ONLN; awk '/MemTotal/ {print $2}' /proc/meminfo; df -Pk / | tail -1; command -v docker || true; python3 --version 2>/dev/null || true; node --version 2>/dev/null || true"
        _, stdout, _ = client.exec_command(command, timeout=timeout)
        lines = [line.strip() for line in stdout.read().decode("utf-8", "replace").splitlines()]
        return {"state": "ONLINE", "capabilities": {"os": lines[0] if len(lines)>0 else "", "architecture": lines[1] if len(lines)>1 else "", "cpuCores": lines[2] if len(lines)>2 else "", "ramKb": lines[3] if len(lines)>3 else "", "disk": lines[4] if len(lines)>4 else "", "docker": bool(lines[5]) if len(lines)>5 else False, "python": lines[6] if len(lines)>6 else "", "node": lines[7] if len(lines)>7 else ""}}
    except (paramiko.AuthenticationException, paramiko.BadAuthenticationType):
        return {"state": "AUTHENTICATION FAILED", "reason": "SSH authentication failed"}
    except (paramiko.SSHException, OSError, socket.timeout) as exc:
        return {"state": "OFFLINE", "reason": str(exc)[:160]}
    finally:
        client.close()


def test_node(node: Dict[str, Any], timeout: int = 5, secret: str = "") -> Dict[str, Any]:
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
        if secret:
            return test_ssh_node(node, secret, timeout=max(timeout, 8))
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
