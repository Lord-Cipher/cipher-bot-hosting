"""Authenticated SSH worker operations for verified VPS nodes."""
from __future__ import annotations
import os, posixpath, shlex, time
from pathlib import Path
from typing import Any, Dict
from sandbox_runtime import limits_for_plan


def _client(node: Dict[str, Any], secret: str, timeout: int = 10):
    import paramiko
    if not secret:
        raise ValueError("remote credential required")
    c = paramiko.SSHClient(); c.load_system_host_keys(); c.set_missing_host_key_policy(paramiko.RejectPolicy())
    kwargs = {"hostname": node.get("hostname") or node.get("ipv4") or node.get("ipv6"), "port": int(node.get("ssh_port", 22)), "username": node.get("username"), "timeout": timeout, "banner_timeout": timeout, "auth_timeout": timeout}
    if node.get("auth_method", "key") == "password":
        kwargs["password"] = secret
    else:
        kwargs["pkey"] = paramiko.RSAKey.from_private_key(__import__("io").StringIO(secret))
    c.connect(**kwargs); return c


def _run(c, command: str, timeout: int = 60):
    _, stdout, stderr = c.exec_command(command, timeout=timeout)
    out, err = stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


class RemoteHandle:
    def __init__(self, node: Dict[str, Any], secret: str, bot_id: str, container_id: str):
        self.node, self.secret, self.bot_id, self.container_id = node, secret, bot_id, container_id
        self.pid = 0
        self.returncode = None
        self.last_state = "unknown"

    def _inspect(self) -> Dict[str, Any]:
        c = _client(self.node, self.secret)
        try:
            code, out, err = _run(c, f"docker inspect --format '{{{{.State.Status}}}}|{{{{.State.ExitCode}}}}|{{{{.Id}}}}' {shlex.quote(self.container_id)}", timeout=15)
            if code:
                return {"state": "offline" if "connect" in err.lower() else "missing", "exit": None, "error": err[-300:]}
            state, exit_code, actual_id = (out.strip().split("|", 2) + [""] * 3)[:3]
            if actual_id:
                self.container_id = actual_id
            return {"state": state, "exit": int(exit_code) if exit_code.lstrip("-").isdigit() else None}
        finally:
            c.close()

    def poll(self):
        try:
            status = self._inspect()
            self.last_state = status.get("state", "unknown")
            if self.last_state == "running":
                self.returncode = None
            else:
                self.returncode = status.get("exit") if status.get("exit") is not None else 1
        except Exception:
            self.last_state, self.returncode = "offline", 1
        return self.returncode

    def wait(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            result = self.poll()
            if result is not None:
                return result
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("remote container wait timed out")
            time.sleep(1)


def _cleanup(c, remote: str, container: str) -> None:
    _run(c, f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote)}", timeout=30)


def deploy(node: Dict[str, Any], secret: str, bot_id: str, local_dir: str | Path, runtime: str, entry: str, plan: str, env: Dict[str, str]) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") not in {"ONLINE", "AUTHENTICATED"}:
        return {"ok": False, "error": "Node is disabled or unauthenticated."}
    if not secret:
        return {"ok": False, "error": "Remote credential required."}
    if not bot_id or "/" in bot_id or ".." in bot_id or Path(entry).is_absolute() or ".." in Path(entry).parts:
        return {"ok": False, "error": "Unsafe bot or entrypoint path."}
    c = _client(node, secret); sftp = c.open_sftp(); remote = f"/tmp/cipher-bots/{bot_id}"; container = f"cipher-bot-{bot_id}"
    success = False
    try:
        _run(c, f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote)}; mkdir -p {shlex.quote(remote)}/.deps {shlex.quote(remote)}/.tmp_run && chmod 700 {shlex.quote(remote)}")
        root = Path(local_dir).resolve()
        for src in root.rglob("*"):
            if not src.is_file() or any(x in src.parts for x in {".git", "__pycache__", ".deps", ".tmp_run", ".cipher-runtime.env"}): continue
            rel = src.relative_to(root).as_posix(); dst = posixpath.join(remote, rel)
            _run(c, f"mkdir -p {shlex.quote(posixpath.dirname(dst))}")
            sftp.put(str(src), dst)
        env_path = posixpath.join(remote, ".cipher-runtime.env")
        with sftp.open(env_path, "w") as f:
            for k, v in env.items():
                if k.isidentifier(): f.write(f"{k}={str(v).replace(chr(10), '')}\n")
        sftp.chmod(env_path, 0o600)
        lim = limits_for_plan(plan)
        image_cmd = "node:22-slim node" if runtime == "node" else "python:3.11-slim python"
        cmd = f"docker run -d --rm --name {shlex.quote(container)} --cpus {shlex.quote(lim['cpus'])} --memory {shlex.quote(lim['memory'])} --pids-limit {int(lim['pids'])} --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 65532:65532 --network none --env-file {shlex.quote(env_path)} -v {shlex.quote(remote)}:/app:ro -v {shlex.quote(remote+'/.deps')}:/app/.deps:rw -v {shlex.quote(remote+'/.tmp_run')}:/app/.tmp_run:rw -w /app {image_cmd} {shlex.quote(entry)}"
        code, out, err = _run(c, cmd)
        if code or not out.strip():
            return {"ok": False, "error": err[-300:] or "Docker startup failed."}
        container_id = out.strip()[-128:]
        _run(c, f"rm -f -- {shlex.quote(env_path)}")
        success = True
        return {"ok": True, "container_id": container_id, "error": "", "node_id": node.get("id")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        try: sftp.close()
        except Exception: pass
        if not success:
            try: _cleanup(c, remote, container)
            except Exception: pass
        c.close()


def control(node: Dict[str, Any], secret: str, bot_id: str, action: str) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") not in {"ONLINE", "AUTHENTICATED"}: return {"ok": False, "error": "Node is disabled or unauthenticated."}
    if action not in {"stop", "restart", "delete", "logs", "stats"}: return {"ok": False, "error": "Unsupported action"}
    c = _client(node, secret)
    try:
        name = f"cipher-bot-{bot_id}"
        command = {"stop": f"docker stop {shlex.quote(name)}", "restart": f"docker restart {shlex.quote(name)}", "delete": f"docker rm -f {shlex.quote(name)}", "logs": f"docker logs --tail 200 {shlex.quote(name)}", "stats": f"docker stats --no-stream --format '{{{{json .}}}}' {shlex.quote(name)}"}[action]
        code, out, err = _run(c, command)
        return {"ok": code == 0, "output": out[-6000:], "error": err[-500:]}
    finally: c.close()

clear = control
def deploy_remote(*args, **kwargs): return deploy(*args, **kwargs)
