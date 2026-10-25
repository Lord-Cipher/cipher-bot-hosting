"""Authenticated SSH worker operations for verified VPS nodes."""
from __future__ import annotations
import base64, errno, os, posixpath, shlex, stat, time
from pathlib import Path
from typing import Any, Dict, Iterator
from sandbox_runtime import limits_for_plan
from node_manager import load_ssh_private_key


def _client(node: Dict[str, Any], secret: str, timeout: int = 10):
    import paramiko
    if not secret:
        raise ValueError("remote credential required")
    c = paramiko.SSHClient(); c.load_system_host_keys(); c.set_missing_host_key_policy(paramiko.RejectPolicy())
    kwargs = {"hostname": node.get("hostname") or node.get("ipv4") or node.get("ipv6"), "port": int(node.get("ssh_port", 22)), "username": node.get("username"), "timeout": timeout, "banner_timeout": timeout, "auth_timeout": timeout}
    if node.get("auth_method", "key") == "password":
        kwargs["password"] = secret
    else:
        kwargs["pkey"] = load_ssh_private_key(secret)
    c.connect(**kwargs); return c


def _run(c, command: str, timeout: int = 60):
    _, stdout, stderr = c.exec_command(command, timeout=timeout)
    out, err = stdout.read().decode("utf-8", "replace"), stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


def _dependency_install_command(remote: str, runtime: str, plan: str, uid: int, gid: int) -> str:
    """Build a network-enabled but otherwise constrained dependency-install job."""
    lim = limits_for_plan(plan)
    if runtime == "node":
        image = "node:22-slim"
        script = (
            "mkdir -p /app/.deps/node_modules && "
            "if [ -f /app/package.json ]; then "
            "cp /app/package.json /app/.deps/package.json && "
            "if [ -f /app/package-lock.json ]; then cp /app/package-lock.json /app/.deps/package-lock.json; fi && "
            "cd /app/.deps && npm install --ignore-scripts --no-audit --no-fund; "
            "fi"
        )
    else:
        image = "python:3.11-slim"
        script = (
            "if [ -f /app/requirements.txt ]; then "
            "python -m pip install --disable-pip-version-check --no-cache-dir --no-input "
            "--target /app/.deps -r /app/requirements.txt; fi"
        )
    args = [
        "docker", "run", "--rm", "--network", "bridge",
        "--cpus", lim["cpus"], "--memory", lim["memory"],
        "--pids-limit", str(lim["pids"]), "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", f"{uid}:{gid}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "-e", "PIP_NO_CACHE_DIR=1", "-e", "NPM_CONFIG_CACHE=/tmp/npm-cache",
        "-v", f"{remote}:/app:ro", "-v", f"{remote}/.deps:/app/.deps:rw",
        image, "sh", "-lc", script,
    ]
    return " ".join(shlex.quote(arg) for arg in args)


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


def _run_fresh(node: Dict[str, Any], secret: str, command: str, timeout: int = 60):
    c = _client(node, secret)
    try:
        return _run(c, command, timeout=timeout)
    finally:
        c.close()


def _put_via_exec(node: Dict[str, Any], secret: str, remote_path: str, payload: bytes) -> None:
    c = _client(node, secret)
    try:
        _put_via_exec_on_client(c, remote_path, payload)
    finally:
        c.close()


def _put_via_exec_on_client(c, remote_path: str, payload: bytes) -> None:
    """Transfer bounded bytes through SSH when SFTP is unavailable."""
    encoded = base64.b64encode(payload).decode("ascii")
    stdin, stdout, stderr = c.exec_command(f"base64 -d > {shlex.quote(remote_path)}", timeout=30)
    stdin.write(encoded)
    stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    if code:
        detail = stderr.read().decode("utf-8", "replace")[-240:]
        raise RuntimeError(f"remote file transfer failed: {detail}")


def _cleanup(c, remote: str, container: str) -> None:
    _run(c, f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote)}", timeout=30)


_PROJECT_UPLOAD_EXCLUDES = {".git", "__pycache__", ".deps", ".tmp_run", ".cipher-runtime.env"}


def _require_safe_project_walk() -> int:
    """Return flags for descriptor-relative, no-follow filesystem access."""
    supported = (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.listdir in os.supports_fd
    )
    if not supported:
        raise OSError(errno.ENOTSUP, "safe no-follow project traversal is unavailable on this platform")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_absolute_directory(path: Path, directory_flags: int) -> int:
    """Open an absolute directory one component at a time without following links."""
    fd = os.open(os.sep, directory_flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _iter_project_payloads(local_dir: str | Path) -> Iterator[tuple[str, bytes]]:
    """Yield safe regular files below the resolved root; skip links and exclusions.

    Every directory and file is opened relative to an already-open directory
    descriptor with O_NOFOLLOW. A race that changes an entry between lstat and
    open therefore fails closed instead of reading a link target.
    """
    root = Path(local_dir).resolve(strict=True)
    root_stat = root.stat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError(str(root))
    directory_flags = _require_safe_project_walk()
    root_fd = _open_absolute_directory(root, directory_flags)
    try:
        opened_root = os.fstat(root_fd)
        if (not stat.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino) != (root_stat.st_dev, root_stat.st_ino)):
            raise OSError(errno.EIO, "project root changed while opening")

        def walk(directory_fd: int, prefix: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
            for name in sorted(os.listdir(directory_fd)):
                parts = prefix + (name,)
                if any(part in _PROJECT_UPLOAD_EXCLUDES for part in parts):
                    continue
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(entry_stat.st_mode):
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if (not stat.S_ISDIR(opened.st_mode)
                                or (opened.st_dev, opened.st_ino) != (entry_stat.st_dev, entry_stat.st_ino)):
                            raise OSError(errno.EIO, "project directory changed while opening")
                        resolved = root.joinpath(*parts).resolve(strict=True)
                        if not resolved.is_relative_to(root):
                            raise ValueError(f"Project directory escapes root: {'/'.join(parts)}")
                        yield from walk(child_fd, parts)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    continue

                file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if (not stat.S_ISREG(opened.st_mode)
                            or (opened.st_dev, opened.st_ino) != (entry_stat.st_dev, entry_stat.st_ino)):
                        raise OSError(errno.EIO, "project file changed while opening")
                    resolved = root.joinpath(*parts).resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        raise ValueError(f"Project file escapes root: {'/'.join(parts)}")
                    resolved_stat = resolved.stat()
                    if (not stat.S_ISREG(resolved_stat.st_mode)
                            or (resolved_stat.st_dev, resolved_stat.st_ino) != (opened.st_dev, opened.st_ino)):
                        raise OSError(errno.EIO, "project file path changed while opening")
                    with os.fdopen(os.dup(file_fd), "rb") as source:
                        payload = source.read()
                    after_read = os.fstat(file_fd)
                    if (not stat.S_ISREG(after_read.st_mode)
                            or (after_read.st_dev, after_read.st_ino) != (opened.st_dev, opened.st_ino)):
                        raise OSError(errno.EIO, "project file changed while reading")
                    yield Path(*parts).as_posix(), payload
                finally:
                    os.close(file_fd)

        yield from walk(root_fd, ())
    finally:
        os.close(root_fd)


def deploy(node: Dict[str, Any], secret: str, bot_id: str, local_dir: str | Path, runtime: str, entry: str, plan: str, env: Dict[str, str], network: bool = False) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") not in {"ONLINE", "AUTHENTICATED"}:
        return {"ok": False, "error": "Node is disabled or unauthenticated."}
    if not secret:
        return {"ok": False, "error": "Remote credential required."}
    if not bot_id or "/" in bot_id or ".." in bot_id or Path(entry).is_absolute() or ".." in Path(entry).parts:
        return {"ok": False, "error": "Unsafe bot or entrypoint path."}
    c = _client(node, secret); sftp = None; remote = f"/tmp/cipher-bots/{bot_id}"; container = f"cipher-bot-{bot_id}"
    success = False
    try:
        try:
            sftp = c.open_sftp()
        except Exception:
            # Some restricted SSH accounts permit exec but disable SFTP. The
            # failed subsystem request can close the transport, so reconnect
            # before using the exec-channel transfer fallback.
            try: c.close()
            except Exception: pass
            c = None
        run_remote = (lambda command, timeout=60: _run(c, command, timeout=timeout)) if c else (lambda command, timeout=60: _run_fresh(node, secret, command, timeout=timeout))
        identity_code, identity_out, _ = run_remote("id -u && id -g", timeout=10)
        identity = identity_out.strip().splitlines()
        if identity_code or len(identity) < 2 or not all(value.isdigit() for value in identity[:2]):
            return {"ok": False, "error": "Could not determine the remote SSH user's numeric UID/GID."}
        uid, gid = int(identity[0]), int(identity[1])
        if uid == 0:
            return {"ok": False, "error": "Use a non-root SSH account with Docker access for Sandbox isolation."}
        run_remote(f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote)}; mkdir -p {shlex.quote(remote)}/.deps && chmod 700 {shlex.quote(remote)}")
        for rel, payload in _iter_project_payloads(local_dir):
            dst = posixpath.join(remote, rel)
            run_remote(f"mkdir -p {shlex.quote(posixpath.dirname(dst))}")
            if sftp:
                sftp.putfo(__import__("io").BytesIO(payload), dst)
            else:
                _put_via_exec(node, secret, dst, payload)
        env_path = posixpath.join(remote, ".cipher-runtime.env")
        env_payload = "".join(f"{k}={str(v).replace(chr(10), '')}\n" for k, v in env.items() if k.isidentifier()).encode()
        if sftp:
            with sftp.open(env_path, "wb") as f:
                f.write(env_payload)
            sftp.chmod(env_path, 0o600)
        else:
            _put_via_exec(node, secret, env_path, env_payload)
            run_remote(f"chmod 600 {shlex.quote(env_path)}")
        lim = limits_for_plan(plan)
        dep_cmd = _dependency_install_command(remote, runtime, plan, uid, gid)
        dep_code, _, dep_err = run_remote(dep_cmd, timeout=900)
        if dep_code:
            return {"ok": False, "error": dep_err[-300:] or "Remote dependency installation failed."}
        image_cmd = "node:22-slim node" if runtime == "node" else "python:3.11-slim python"
        network_mode = "bridge" if network else "none"
        node_modules_mount = (
            f" -v {shlex.quote(remote+'/.deps/node_modules')}:/app/node_modules:ro"
            if runtime == "node" else ""
        )
        python_path = " -e PYTHONPATH=/app/.deps" if runtime == "python" else ""
        cmd = f"docker run -d --rm --name {shlex.quote(container)} --cpus {shlex.quote(lim['cpus'])} --memory {shlex.quote(lim['memory'])} --pids-limit {int(lim['pids'])} --read-only --cap-drop ALL --security-opt no-new-privileges:true --user {uid}:{gid} --network {network_mode} --env-file {shlex.quote(env_path)} -v {shlex.quote(remote)}:/app:ro -v {shlex.quote(remote+'/.deps')}:/app/.deps:ro{node_modules_mount} --tmpfs /app/.tmp_run:rw,noexec,nosuid,size=64m,uid={uid},gid={gid} --tmpfs /tmp:rw,noexec,nosuid,size=64m{python_path} -w /app {image_cmd} {shlex.quote(entry)}"
        code, out, err = run_remote(cmd)
        if code or not out.strip():
            return {"ok": False, "error": err[-300:] or "Docker startup failed."}
        container_id = out.strip()[-128:]
        run_remote(f"rm -f -- {shlex.quote(env_path)}")
        success = True
        return {"ok": True, "container_id": container_id, "error": "", "node_id": node.get("id")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        try:
            if sftp: sftp.close()
        except Exception: pass
        if not success:
            try:
                if c:
                    _cleanup(c, remote, container)
                else:
                    _run_fresh(node, secret, f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote)}", timeout=30)
            except Exception: pass
        if c:
            c.close()


def control(node: Dict[str, Any], secret: str, bot_id: str, action: str) -> Dict[str, Any]:
    if not node.get("enabled") or node.get("status") not in {"ONLINE", "AUTHENTICATED"}: return {"ok": False, "error": "Node is disabled or unauthenticated."}
    if action not in {"stop", "restart", "delete", "cleanup", "logs", "stats"}: return {"ok": False, "error": "Unsupported action"}
    c = _client(node, secret)
    try:
        name = f"cipher-bot-{bot_id}"
        remote_dir = f"/tmp/cipher-bots/{bot_id}"
        command = {"stop": f"docker stop {shlex.quote(name)}", "restart": f"docker restart {shlex.quote(name)}", "delete": f"docker rm -f {shlex.quote(name)}", "cleanup": f"docker rm -f {shlex.quote(name)} >/dev/null 2>&1 || true; rm -rf -- {shlex.quote(remote_dir)}", "logs": f"docker logs --tail 200 {shlex.quote(name)}", "stats": f"docker stats --no-stream --format '{{{{json .}}}}' {shlex.quote(name)}"}[action]
        code, out, err = _run(c, command)
        return {"ok": code == 0, "output": out[-6000:], "error": err[-500:]}
    finally: c.close()

clear = control
def deploy_remote(*args, **kwargs): return deploy(*args, **kwargs)
