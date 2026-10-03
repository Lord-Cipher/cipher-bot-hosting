"""Offline regression tests for remote IPv6 Sandbox VPS nodes."""
from __future__ import annotations

import io
import os
import shlex
import sys
import subprocess
import tempfile
import types
from pathlib import Path

import node_manager
import remote_worker


class _FakeKey:
    algorithm = ""

    @classmethod
    def from_private_key(cls, stream):
        value = stream.read().strip()
        if value != cls.algorithm:
            raise ValueError("wrong fake key type")
        return (cls.__name__, value)


class _Ed25519Key(_FakeKey):
    algorithm = "ED25519"


class _ECDSAKey(_FakeKey):
    algorithm = "ECDSA"


class _RSAKey(_FakeKey):
    algorithm = "RSA"


class _Stream(io.BytesIO):
    def __init__(self, data=b"", code=0):
        super().__init__(data)
        self.channel = types.SimpleNamespace(recv_exit_status=lambda: code)


class _AuthSSHClient:
    last = None
    docker_line = b"26.0.0"

    def __init__(self):
        self.system_keys_loaded = False
        self.kwargs = None
        _AuthSSHClient.last = self

    def load_system_host_keys(self):
        self.system_keys_loaded = True

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, **kwargs):
        self.kwargs = kwargs

    def exec_command(self, command, timeout=0):
        info = b"Linux\nx86_64\n2\n4096000\n/dev/vda1 100000 1000 99000 1% /\n" + self.docker_line + b"\nPython 3.11.0\nv22.0.0\n"
        return None, _Stream(info), _Stream()

    def close(self):
        pass


_fake_paramiko = types.ModuleType("paramiko")
_fake_paramiko.Ed25519Key = _Ed25519Key
_fake_paramiko.ECDSAKey = _ECDSAKey
_fake_paramiko.RSAKey = _RSAKey
_fake_paramiko.SSHClient = _AuthSSHClient
_fake_paramiko.RejectPolicy = type("RejectPolicy", (), {})
_original_paramiko = sys.modules.get("paramiko")
sys.modules["paramiko"] = _fake_paramiko

assert node_manager.load_ssh_private_key("ED25519") == ("_Ed25519Key", "ED25519")
assert node_manager.load_ssh_private_key("ECDSA") == ("_ECDSAKey", "ECDSA")
assert node_manager.load_ssh_private_key("RSA") == ("_RSAKey", "RSA")

probe = node_manager.test_ssh_node(
    {
        "connection_type": "ssh",
        "ipv6": "2001:db8::25",
        "ssh_port": 22,
        "username": "cipherbot",
        "auth_method": "key",
    },
    "ED25519",
)
assert probe["state"] == "AUTHENTICATED", probe
assert probe["capabilities"]["docker"] is True
assert _AuthSSHClient.last.system_keys_loaded is True
assert _AuthSSHClient.last.kwargs["hostname"] == "2001:db8::25"
assert _AuthSSHClient.last.kwargs["pkey"] == ("_Ed25519Key", "ED25519")
_AuthSSHClient.docker_line = b""
no_docker_probe = node_manager.test_ssh_node(
    {"connection_type": "ssh", "ipv6": "2001:db8::25", "username": "cipherbot", "auth_method": "key"},
    "ED25519",
)
assert no_docker_probe["capabilities"]["docker"] is False
assert no_docker_probe["capabilities"]["python"] == "Python 3.11.0"
_AuthSSHClient.docker_line = b"26.0.0"

if _original_paramiko is None:
    sys.modules.pop("paramiko", None)
else:
    sys.modules["paramiko"] = _original_paramiko


class _FakeSFTPFile:
    def __init__(self):
        self.data = b""

    def write(self, value):
        self.data += value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSFTP:
    def putfo(self, stream, remote_path):
        stream.read()

    def open(self, remote_path, mode):
        return _FakeSFTPFile()

    def chmod(self, remote_path, mode):
        pass

    def close(self):
        pass


class _RemoteClient:
    def __init__(self, uid=1001, gid=1001):
        self.commands = []
        self.uid, self.gid = uid, gid

    def open_sftp(self):
        return _FakeSFTP()

    def exec_command(self, command, timeout=60):
        self.commands.append(command)
        if command == "id -u && id -g":
            output = f"{self.uid}\n{self.gid}\n".encode()
        elif command.startswith("docker run -d "):
            output = b"container-id-123\n"
        else:
            output = b""
        return None, _Stream(output), _Stream()

    def close(self):
        pass


def _deploy(runtime: str, network: bool, uid: int = 1001):
    client = _RemoteClient(uid=uid)
    old_client = remote_worker._client
    remote_worker._client = lambda *args, **kwargs: client
    try:
        with tempfile.TemporaryDirectory(prefix="remote-sandbox-test-") as tmp:
            root = Path(tmp)
            if runtime == "node":
                (root / "main.js").write_text("console.log('ok')\n", encoding="utf-8")
                (root / "package.json").write_text('{"dependencies":{"example":"1.0.0"}}', encoding="utf-8")
            else:
                (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
                (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
            result = remote_worker.deploy(
                {"id": "node1", "enabled": True, "status": "AUTHENTICATED"},
                "private-key", "bot123", root, runtime,
                "main.js" if runtime == "node" else "main.py",
                "free", {}, network=network,
            )
    finally:
        remote_worker._client = old_client
    return result, client.commands


result, commands = _deploy("python", network=True)
assert result["ok"] is True, result
install = next(cmd for cmd in commands if "pip install" in cmd)
run = next(cmd for cmd in commands if cmd.startswith("docker run -d "))
assert "--network bridge" in install
assert "--network bridge" in run
assert "--user 1001:1001" in install and "--user 1001:1001" in run
assert "-e PYTHONPATH=/app/.deps" in run
assert "/app/.deps:ro" in run
assert "--tmpfs /app/.tmp_run:rw,noexec,nosuid,size=64m,uid=1001,gid=1001" in run

result, commands = _deploy("python", network=False)
assert result["ok"] is True, result
run = next(cmd for cmd in commands if cmd.startswith("docker run -d "))
assert "--network none" in run

result, commands = _deploy("node", network=False)
assert result["ok"] is True, result
install = next(cmd for cmd in commands if "npm install" in cmd)
run = next(cmd for cmd in commands if cmd.startswith("docker run -d "))
assert "npm install --ignore-scripts" in install
assert "/app/node_modules:ro" in run
assert "/app/.deps:ro" in run
assert "--network none" in run

for runtime in ("python", "node"):
    install_args = shlex.split(
        remote_worker._dependency_install_command(
            "/tmp/cipher-bots/test", runtime, "free", 1001, 1001
        )
    )
    subprocess.run(["/bin/sh", "-n", "-c", install_args[-1]], check=True)

result, _ = _deploy("python", network=False, uid=0)
assert result["ok"] is False and "non-root" in result["error"].lower(), result

os.environ.setdefault("BOT_TOKEN", "123456789:AA_remote_sandbox_regression")
os.environ.setdefault("OWNER_ID", "9001")
import bot

record = {"id": "bot1", "owner": 77, "allow_network": False}
bot.find_bot = lambda bid: record if bid == "bot1" else None
bot.is_admin = lambda uid: False
bot.save_bot = lambda value: None
bot.audit = lambda *args, **kwargs: None
bot.render_bot_view = lambda *args, **kwargs: None
messages = []
bot.ack = lambda call, message="", **kwargs: messages.append((message, kwargs))
bot._bc_get = lambda key: False
assert bot._sandbox_network_allowed({"allow_network": True}) is False
bot._bc_get = lambda key: True
assert bot._sandbox_network_allowed({"allow_network": True}) is True
assert bot._sandbox_network_allowed({"allow_network": False}) is False
bot._bc_get = lambda key: False
owner_call = types.SimpleNamespace(from_user=types.SimpleNamespace(id=77))
other_call = types.SimpleNamespace(from_user=types.SimpleNamespace(id=78))
bot.action_bot_sandbox_network_toggle(owner_call, "bot1")
assert record["allow_network"] is True
assert "globally" in messages[-1][0].lower()
bot.action_bot_sandbox_network_toggle(other_call, "bot1")
assert record["allow_network"] is True
assert messages[-1][1].get("show_alert") is True

keyboard = bot.bot_actions_kb("bot1", running=False)
network_button = next(
    button for row in keyboard.keyboard for button in row
    if button.callback_data == "bot_network_bot1"
)
assert network_button.style == "success"

print("Remote Sandbox VPS regression tests passed")
