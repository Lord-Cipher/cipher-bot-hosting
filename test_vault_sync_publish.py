"""Focused tests for Cipher Vault GitHub publication bootstrap."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from cryptography.fernet import Fernet

import vault_sync


class _Response:
    def __init__(self, payload=None, status=200):
        self.payload = payload or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.payload


class EmptyRepositoryBootstrapTests(unittest.TestCase):
    def test_first_sync_bootstraps_empty_repository(self):
        calls = []
        blob_number = 0

        class Session:
            def __init__(self):
                self.headers = {}

            def request(self, method, url, **kwargs):
                nonlocal blob_number
                calls.append((method, url, kwargs.get("json")))
                if method == "GET" and "/git/ref/heads/main" in url:
                    return _Response(status=404)
                if method == "POST" and url.endswith("/git/blobs"):
                    blob_number += 1
                    return _Response({"sha": f"blob-{blob_number}"})
                if method == "POST" and url.endswith("/git/trees"):
                    payload = kwargs["json"]
                    self.tree_payload = payload
                    return _Response({"sha": "tree-1"})
                if method == "POST" and url.endswith("/git/commits"):
                    payload = kwargs["json"]
                    self.commit_payload = payload
                    return _Response({"sha": "commit-1"})
                if method == "POST" and url.endswith("/git/refs"):
                    self.ref_payload = kwargs["json"]
                    return _Response({"ref": self.ref_payload["ref"]})
                raise AssertionError(f"Unexpected GitHub request: {method} {url}")

        with tempfile.TemporaryDirectory(prefix="vault-publish-test-") as raw_root:
            root = Path(raw_root)
            (root / "storage" / "data").mkdir(parents=True)
            (root / "sandbox").mkdir()
            (root / "storage" / "data" / "settings.json").write_text("{}\n")
            with patch.object(vault_sync.requests, "Session", Session), patch.object(vault_sync.time, "sleep"):
                result = vault_sync.sync_vault(
                    root,
                    "token",
                    "Lord-Cipher/cipher-vault",
                    "main",
                    Fernet.generate_key().decode(),
                )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["commit"], "commit-1")
        self.assertEqual(blob_number, 3)
        ref_calls = [item for item in calls if item[0] == "POST" and item[1].endswith("/git/refs")]
        self.assertEqual(len(ref_calls), 1)
        self.assertEqual(ref_calls[0][2]["ref"], "refs/heads/main")
        tree_calls = [item for item in calls if item[0] == "POST" and item[1].endswith("/git/trees")]
        self.assertNotIn("base_tree", tree_calls[0][2])
        commit_calls = [item for item in calls if item[0] == "POST" and item[1].endswith("/git/commits")]
        self.assertNotIn("parents", commit_calls[0][2])


if __name__ == "__main__":
    unittest.main()
