"""Focused security tests for symlink-safe remote project uploads."""
from __future__ import annotations

import io
import tempfile
import types
import unittest
from pathlib import Path

import remote_worker


class _Stream(io.BytesIO):
    def __init__(self, data: bytes = b""):
        super().__init__(data)
        self.channel = types.SimpleNamespace(recv_exit_status=lambda: 0)


class _SFTPFile:
    def write(self, value: bytes) -> int:
        return len(value)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _SFTP:
    def __init__(self):
        self.uploads: dict[str, bytes] = {}

    def putfo(self, stream, remote_path: str) -> None:
        self.uploads[remote_path] = stream.read()

    def open(self, remote_path: str, mode: str):
        return _SFTPFile()

    def chmod(self, remote_path: str, mode: int) -> None:
        pass

    def close(self) -> None:
        pass


class _SSHClient:
    def __init__(self):
        self.sftp = _SFTP()

    def open_sftp(self):
        return self.sftp

    def exec_command(self, command: str, timeout: int = 60):
        if command == "id -u && id -g":
            output = b"1001\n1001\n"
        elif command.startswith("docker run -d "):
            output = b"container-id-test\n"
        else:
            output = b""
        return None, _Stream(output), _Stream()

    def close(self) -> None:
        pass


def _deploy(root: Path) -> tuple[dict, dict[str, bytes]]:
    client = _SSHClient()
    original_client = remote_worker._client
    remote_worker._client = lambda *args, **kwargs: client
    try:
        result = remote_worker.deploy(
            {"id": "node1", "enabled": True, "status": "AUTHENTICATED"},
            "credential", "bot123", root, "python", "main.py", "free", {},
        )
    finally:
        remote_worker._client = original_client
    return result, client.sftp.uploads


class RemoteWorkerSymlinkSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="remote-worker-security-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()

    def _make_symlink(self, target: Path, link: Path, *, is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=is_directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

    @staticmethod
    def _project_paths(uploads: dict[str, bytes]) -> set[str]:
        prefix = "/tmp/cipher-bots/bot123/"
        return {path.removeprefix(prefix) for path in uploads if path.startswith(prefix)}

    def test_uploads_normal_regular_file(self):
        payload = b"print('ordinary project file')\n"
        (self.root / "main.py").write_bytes(payload)

        result, uploads = _deploy(self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(uploads["/tmp/cipher-bots/bot123/main.py"], payload)

    def test_preserves_existing_project_exclusions(self):
        (self.root / "main.py").write_text("print('ok')\n", encoding="utf-8")
        excluded_dirs = (".git", "__pycache__", ".deps", ".tmp_run")
        for dirname in excluded_dirs:
            directory = self.root / dirname
            directory.mkdir()
            (directory / "must-not-upload.txt").write_text(dirname, encoding="utf-8")
        (self.root / ".cipher-runtime.env").write_text("SECRET=not-uploaded\n", encoding="utf-8")

        result, uploads = _deploy(self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(self._project_paths(uploads), {"main.py"})

    def test_skips_symlink_to_external_file(self):
        external = Path(self.tmp.name) / "external-secret.txt"
        secret = b"external symlink target must never be uploaded"
        external.write_bytes(secret)
        self._make_symlink(external, self.root / "external-link.txt")
        (self.root / "main.py").write_text("print('ok')\n", encoding="utf-8")

        result, uploads = _deploy(self.root)

        self.assertTrue(result["ok"], result)
        self.assertNotIn("external-link.txt", self._project_paths(uploads))
        self.assertNotIn(secret, uploads.values())

    def test_skips_symlink_to_in_tree_file(self):
        deps = self.root / ".deps"
        deps.mkdir()
        secret = b"excluded in-tree symlink target must never be uploaded"
        (deps / "internal-secret.txt").write_bytes(secret)
        self._make_symlink(deps / "internal-secret.txt", self.root / "internal-link.txt")
        (self.root / "main.py").write_text("print('ok')\n", encoding="utf-8")

        result, uploads = _deploy(self.root)

        self.assertTrue(result["ok"], result)
        self.assertNotIn("internal-link.txt", self._project_paths(uploads))
        self.assertNotIn(secret, uploads.values())

    def test_does_not_traverse_symlinked_directory(self):
        external_dir = Path(self.tmp.name) / "external-dir"
        external_dir.mkdir()
        secret = b"external directory target must never be uploaded"
        (external_dir / "secret.txt").write_bytes(secret)
        self._make_symlink(external_dir, self.root / "linked-dir", is_directory=True)
        (self.root / "main.py").write_text("print('ok')\n", encoding="utf-8")

        result, uploads = _deploy(self.root)

        self.assertTrue(result["ok"], result)
        self.assertFalse(any(path.startswith("linked-dir/") for path in self._project_paths(uploads)))
        self.assertNotIn(secret, uploads.values())


if __name__ == "__main__":
    unittest.main()
