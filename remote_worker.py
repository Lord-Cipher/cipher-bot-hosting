"""Authenticated SSH worker operations for verified VPS nodes."""
from __future__ import annotations
import os, posixpath, shlex
from pathlib import Path
from typing import Any, Dict, Iterable


def _client(node: Dict[str, Any], secret: str, timeout: int = 10):
    import paramiko
    c = paramiko.SSHClient(); c.load_system_host_keys(); c.set_missing_host_key_policy(paramiko.RejectPolicy())
    kwargs = {"hostname": node.get("hostname") or node.get("ipv4") or node.get("ipv6"), "port": int(node.get("ssh_port", 22)), "username": node.get("username"), "timeout": timeout, "banner_timeout": timeout, "auth_timeout": timeout}
    if node.get("auth_method", "key") == "password": kwargs["password"] = secret
    else: kwargs["pkey"] = paramiko.RSAKey.from_private_key(__import__("io").StringIO(secret))
    c.connect(**kwargs); return c


def _run(c, command: str, timeout: int = 60):
    stdin, stdout, stderr = c.exec_command(command, timeout=timeout)
    out, err = stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


def deploy(node: Dict[str, Any], secret: str, bot_id: str, local_dir: str | Path, runtime: str, entry: str, plan: str, env: Dict[str, str]) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") != "ONLINE": return {"ok": False, "error": "Node is disabled or unverified."}
    c = _client(node, secret); sftp = c.open_sftp(); remote = f"/tmp/cipher-bots/{bot_id}"
    try:
        code, _, err = _run(c, f"mkdir -p {shlex.quote(remote)}/.deps {shlex.quote(remote)}/.tmp_run && chmod 700 {shlex.quote(remote)}")
        if code: return {"ok": False, "error": err[-300:]}
        root = Path(local_dir).resolve()
        for src in root.rglob("*"):
            if not src.is_file() or any(x in src.parts for x in {".git", "__pycache__", ".deps", ".tmp_run", ".cipher-runtime.env"}): continue
            rel = src.relative_to(root).as_posix(); dst = posixpath.join(remote, rel)
            parent = posixpath.dirname(dst); _run(c, f"mkdir -p {shlex.quote(parent)}")
            sftp.put(str(src), dst)
        env_path = posixpath.join(remote, ".cipher-runtime.env")
        with sftp.open(env_path, "w") as f:
            for k, v in env.items():
                if k.isidentifier(): f.write(f"{k}={str(v).replace(chr(10), '')}\n")
        sftp.chmod(env_path, 0o600)
        cmd = f"docker run -d --rm --name cipher-bot-{shlex.quote(bot_id)} --cpus 1 --memory 512m --pids-limit 256 --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 65532:65532 --network none --env-file {shlex.quote(env_path)} -v {shlex.quote(remote)}:/app:ro -v {shlex.quote(remote+'/.deps')}:/app/.deps:rw -v {shlex.quote(remote+'/.tmp_run')}:/app/.tmp_run:rw -w /app {'node:22-slim node' if runtime == 'node' else 'python:3.11-slim python'} {shlex.quote(entry)}"
        code, out, err = _run(c, cmd)
        return {"ok": code == 0, "container_id": out.strip()[-128:] if code == 0 else "", "error": err[-300:] if code else "", "node_id": node.get("id")}
    finally:
        sftp.close(); c.close()


def control(node: Dict[str, Any], secret: str, bot_id: str, action: str) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") != "ONLINE": return {"ok": False, "error": "Node is disabled or unverified."}
    if action not in {"stop", "restart", "delete", "logs"}: return {"ok": False, "error": "Unsupported action"}
    c = _client(node, secret)
    try:
        command = {"stop": f"docker stop cipher-bot-{shlex.quote(bot_id)}", "restart": f"docker restart cipher-bot-{shlex.quote(bot_id)}", "delete": f"docker rm -f cipher-bot-{shlex.quote(bot_id)}", "logs": f"docker logs --tail 200 cipher-bot-{shlex.quote(bot_id)}"}[action]
        code, out, err = _run(c, command)
        return {"ok": code == 0, "output": out[-6000:], "error": err[-500:]}
    finally: c.close()
clear = control
def deploy_remote(*args, **kwargs): return deploy(*args, **kwargs)
