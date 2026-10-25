"""Focused security and regression tests for vault snapshot materialization."""
from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

import vault_sync


class VaultSnapshotMaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="vault-sync-test-")
        self.root = Path(self._tmp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        self.key = Fernet.generate_key().decode()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _archive(self, entries: list[tuple]) -> bytes:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            for entry in entries:
                kind = entry[0]
                name = entry[1]
                member = tarfile.TarInfo(name)
                if kind == "file":
                    data = entry[2]
                    member.size = len(data)
                    member.mode = 0o600
                    archive.addfile(member, io.BytesIO(data))
                elif kind == "dir":
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o700
                    archive.addfile(member)
                elif kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = entry[2]
                    archive.addfile(member)
                elif kind == "hardlink":
                    member.type = tarfile.LNKTYPE
                    member.linkname = entry[2]
                    archive.addfile(member)
                elif kind == "fifo":
                    member.type = tarfile.FIFOTYPE
                    archive.addfile(member)
                elif kind == "contiguous":
                    data = entry[2]
                    member.type = tarfile.CONTTYPE
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    raise AssertionError(f"unknown test archive entry kind: {kind}")
        return Fernet(self.key.encode()).encrypt(raw.getvalue())

    def _stage_names(self) -> set[str]:
        return {path.name for path in self.root.glob("cipher-vault-restore-*")}

    def _base_tree(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.base).as_posix(): path.read_bytes()
            for path in self.base.rglob("*")
            if path.is_file()
        }

    def _assert_rejected(self, encrypted: bytes, outside: Path, *, overwrite: bool = True) -> None:
        (self.base / "existing.txt").write_bytes(b"existing data")
        before_tree = self._base_tree()
        before_stage_names = self._stage_names()
        result = vault_sync.materialize_snapshot(self.base, encrypted, self.key, overwrite=overwrite)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self._base_tree(), before_tree)
        self.assertEqual(outside.read_bytes(), b"outside sentinel")
        self.assertEqual(self._stage_names(), before_stage_names)
        self.assertFalse(any(path.name.startswith("base.pre-restore-") for path in self.root.iterdir()))

    def test_normal_nested_files_are_staged_without_touching_base(self) -> None:
        (self.base / "existing.txt").write_bytes(b"keep this")
        encrypted = self._archive(
            [
                ("dir", "storage/"),
                ("dir", "storage/config/"),
                ("file", "storage/config/settings.json", b'{"enabled":true}\n'),
                ("file", "sandbox/main.py", b"print('restored')\n"),
            ]
        )

        result = vault_sync.materialize_snapshot(self.base, encrypted, self.key)

        self.assertTrue(result["ok"], result)
        stage = Path(result["stagedAt"])
        try:
            self.assertEqual((stage / "storage/config/settings.json").read_text(), '{"enabled":true}\n')
            self.assertEqual((stage / "sandbox/main.py").read_text(), "print('restored')\n")
            self.assertEqual((self.base / "existing.txt").read_bytes(), b"keep this")
        finally:
            shutil.rmtree(stage)

    def test_successful_overwrite_replaces_base_and_keeps_backup(self) -> None:
        (self.base / "old.txt").write_bytes(b"old data")
        encrypted = self._archive(
            [
                ("file", "storage/new.txt", b"new data"),
                ("file", "sandbox/run.py", b"run()\n"),
            ]
        )

        result = vault_sync.materialize_snapshot(self.base, encrypted, self.key, overwrite=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual((self.base / "storage/new.txt").read_bytes(), b"new data")
        self.assertEqual((self.base / "sandbox/run.py").read_bytes(), b"run()\n")
        self.assertFalse((self.base / "old.txt").exists())
        backup = Path(result["restoredFrom"])
        self.assertEqual((backup / "old.txt").read_bytes(), b"old data")
        self.assertEqual(self._stage_names(), set())

    def test_parent_traversal_is_rejected_without_changes(self) -> None:
        outside = self.root / "escaped.txt"
        outside.write_bytes(b"outside sentinel")
        encrypted = self._archive([("file", "../escaped.txt", b"attacker data")])

        self._assert_rejected(encrypted, outside)

    def test_absolute_member_path_is_rejected_without_changes(self) -> None:
        outside = self.root / "absolute-escaped.txt"
        outside.write_bytes(b"outside sentinel")
        encrypted = self._archive([("file", str(outside), b"attacker data")])

        self._assert_rejected(encrypted, outside)

    def test_malformed_member_paths_are_rejected_without_changes(self) -> None:
        outside = self.root / "malformed-target.txt"
        outside.write_bytes(b"outside sentinel")
        for name in ("a//b", "./file.txt", "a/./b", "C:/absolute.txt", ""):
            with self.subTest(name=name):
                encrypted = self._archive([("file", name, b"attacker data")])
                self._assert_rejected(encrypted, outside)

    def test_symlink_member_is_rejected_without_following_it(self) -> None:
        outside = self.root / "symlink-target"
        outside.mkdir()
        outside_file = outside / "escaped.txt"
        outside_file.write_bytes(b"outside sentinel")
        encrypted = self._archive(
            [
                ("file", "storage/partial.txt", b"must be cleaned up"),
                ("symlink", "pivot", str(outside)),
                ("file", "pivot/escaped.txt", b"attacker"),
            ]
        )

        self._assert_rejected(encrypted, outside_file)

    def test_hard_link_member_is_rejected_without_changes(self) -> None:
        outside = self.root / "hard-link-target.txt"
        outside.write_bytes(b"outside sentinel")
        encrypted = self._archive([("hardlink", "linked.txt", "../hard-link-target.txt")])

        self._assert_rejected(encrypted, outside)

    def test_special_member_is_rejected_without_changes(self) -> None:
        outside = self.root / "special-target.txt"
        outside.write_bytes(b"outside sentinel")
        for kind, payload in (("fifo", b""), ("contiguous", b"not an ordinary file")):
            with self.subTest(kind=kind):
                encrypted = self._archive([(kind, "special-entry", payload)])
                self._assert_rejected(encrypted, outside)

    def test_authentication_failure_does_not_create_staging(self) -> None:
        encrypted = self._archive([("file", "storage/file.txt", b"data")])
        wrong_key = Fernet.generate_key().decode()

        result = vault_sync.materialize_snapshot(self.base, encrypted, wrong_key)

        self.assertFalse(result["ok"])
        self.assertIn("Backup authentication failed", result["error"])
        self.assertEqual(self._stage_names(), set())


if __name__ == "__main__":
    unittest.main()
