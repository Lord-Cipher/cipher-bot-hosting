"""Encrypted, versioned Cipher Vault synchronization for the hosting panel.

The engine creates a complete snapshot from the local storage and sandbox,
then publishes it to GitHub using the Git Data API: blobs -> tree -> commit ->
ref update. The ref is updated only after every blob and the manifest are
ready, so readers see either the previous complete snapshot or the new one.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet

# "backups" is excluded so vault snapshots never nest the platform's own
# published backup artifacts (backup-of-backup growth on every sync).
EXCLUDES = {"node_modules", ".deps", ".tmp_run", "__pycache__", ".git", "logs", "backups", ".cache"}
VAULT_SYNC_LOCK = threading.Lock()
REQUIRED_STATE_DIRS = ("storage", "sandbox")


def _safe_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & EXCLUDES:
            continue
        if path.suffix in {".pyc", ".log", ".tmp"}:
            continue
        yield path


def _archive(base_dir: Path) -> tuple[bytes, Dict[str, Any]]:
    """Build a deterministic compressed snapshot of restorable state."""
    buf = io.BytesIO()
    entries = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for dirname in ("storage", "sandbox"):
            root = base_dir / dirname
            for path in _iter_files(root):
                rel = f"{dirname}/{_safe_rel(path, root)}"
                info = tf.gettarinfo(str(path), arcname=rel)
                with path.open("rb") as fh:
                    tf.addfile(info, fh)
                raw = path.read_bytes()
                entries.append({"path": rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    payload = buf.getvalue()
    return payload, {"files": entries, "fileCount": len(entries), "sizeBytes": len(payload)}


def _github(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = session.request(method, url, timeout=120, **kwargs)
            response.raise_for_status()
            return response
        except (requests.RequestException, KeyError, ValueError) as exc:
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                try:
                    detail = exc.response.json().get("message")
                except Exception:
                    detail = None
                if detail:
                    exc = requests.HTTPError(f"{exc} ({detail})", response=exc.response)
            last_exc = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_exc or RuntimeError("GitHub request failed")


def _api_base(repo: str) -> str:
      raw = str(repo or "").strip()
      if raw.startswith("http://") or raw.startswith("https://"):
          raw = raw.split("github.com/", 1)[-1]
      raw = raw.strip("/")
      if raw.endswith(".git"):
          raw = raw[:-4]
      parts = raw.split("/")
      if len(parts) != 2 or not all(parts):
          raise ValueError("Vault repository must be an owner/name pair.")
      return f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
    

def _sync_vault_unlocked(base_dir: str | Path, token: str, repo: str, branch: str = "main", key: str = "") -> Dict[str, Any]:
    """Create and atomically publish one encrypted snapshot to GitHub."""
    if not token:
        return {"ok": False, "error": "Vault token and owner/repository are required."}
    try:
        _api_base(repo)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not key:
        return {"ok": False, "error": "CIPHER_VAULT_KEY is required; refusing plaintext backup."}
    try:
        Fernet(key.encode()).encrypt(b"vault-key-check")
    except Exception as exc:
        return {"ok": False, "error": f"Invalid CIPHER_VAULT_KEY: {exc}"}

    base = Path(base_dir)
    missing = [name for name in REQUIRED_STATE_DIRS if not (base / name).is_dir()]
    if missing:
        return {"ok": False, "error": f"Required state directories missing: {', '.join(missing)}"}
    raw_archive, stats = _archive(base)
    if not stats.get("files"):
        return {"ok": False, "error": "Required state is empty; refusing to publish snapshot."}
    encrypted = Fernet(key.encode()).encrypt(raw_archive)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{now}-{hashlib.sha256(encrypted).hexdigest()[:12]}"
    manifest = {
        "format": "cipher-vault-v1",
        "snapshotId": snapshot_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "encrypted": True,
        "cipher": "Fernet",
        "archive": f"snapshots/{snapshot_id}/platform.tar.gz.enc",
        "archiveSha256": hashlib.sha256(encrypted).hexdigest(),
        **stats,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    session = requests.Session()
    session.headers.update({
        "Authorization": f"token {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cipher-bot-hosting",
    })
    api = _api_base(repo)
    branch_ref = quote(str(branch or "main").strip() or "main", safe="")
    # Blob creation is idempotent (same content -> same SHA), so the whole
    # publish sequence can safely be retried if the branch moves between the
    # ref read and the ref update (409/422 non-fast-forward).
    last_error: Optional[str] = None
    for attempt in range(3):
        try:
            ref = _github(session, "GET", f"{api}/git/ref/heads/{branch_ref}").json()
            parent_sha = ref["object"]["sha"]
            parent_commit = _github(session, "GET", f"{api}/git/commits/{parent_sha}").json()
            base_tree = parent_commit["tree"]["sha"]

            def blob(data: bytes) -> str:
                return _github(session, "POST", f"{api}/git/blobs", json={
                    "content": base64.b64encode(data).decode(), "encoding": "base64"
                }).json()["sha"]

            archive_sha = blob(encrypted)
            manifest["archiveBlobSha"] = archive_sha
            manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            manifest_sha = blob(manifest_bytes)
            latest_bytes = (json.dumps({"snapshotId": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"}, indent=2) + "\n").encode()
            latest_sha = blob(latest_bytes)
            tree = _github(session, "POST", f"{api}/git/trees", json={
                "base_tree": base_tree,
                "tree": [
                    {"path": f"snapshots/{snapshot_id}/platform.tar.gz.enc", "mode": "100644", "type": "blob", "sha": archive_sha},
                    {"path": f"snapshots/{snapshot_id}/manifest.json", "mode": "100644", "type": "blob", "sha": manifest_sha},
                    {"path": "LATEST.json", "mode": "100644", "type": "blob", "sha": latest_sha},
                ],
            }).json()["sha"]
            commit = _github(session, "POST", f"{api}/git/commits", json={
                "message": f"vault: snapshot {snapshot_id}", "tree": tree, "parents": [parent_sha]
            }).json()["sha"]
            # The sole ref update is the atomic publication point.
            _github(session, "PATCH", f"{api}/git/refs/heads/{branch_ref}", json={"sha": commit, "force": False})
            return {"ok": True, "snapshotId": snapshot_id, "commit": commit, "manifest": manifest}
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (409, 422) and attempt < 2:
                # Branch moved (e.g. manual commit or concurrent publisher):
                # rebase on the new head and publish again.
                time.sleep(1 + attempt)
                continue
            return {"ok": False, "error": f"GitHub API error: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": last_error or "Vault publish failed after retries."}


def sync_vault(base_dir: str | Path, token: str, repo: str, branch: str = "main", key: str = "") -> Dict[str, Any]:
    """Serialize concurrent syncs so a later job cannot race ref publication."""
    if not VAULT_SYNC_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "Another Cipher Vault sync is already in progress."}
    try:
        return _sync_vault_unlocked(base_dir, token, repo, branch, key)
    finally:
        VAULT_SYNC_LOCK.release()


def download_latest_snapshot(token: str, repo: str, branch: str = "main") -> tuple[bytes, Dict[str, Any]]:
    """Download and verify the latest encrypted snapshot and its manifest."""
    if not token:
        raise ValueError("Vault token is required.")
    api = _api_base(repo)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"token {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cipher-bot-hosting",
    })

    def content(path: str) -> bytes:
        branch_ref = quote(str(branch or "main").strip() or "main", safe="")
        response = _github(session, "GET", f"{api}/contents/{quote(path, safe='/')}?ref={branch_ref}")
        payload = response.json()
        if payload.get("encoding") != "base64" or not payload.get("content"):
            raise ValueError(f"Vault file is not a base64 GitHub content response: {path}")
        return base64.b64decode("".join(str(payload["content"]).split()))

    latest = json.loads(content("LATEST.json").decode("utf-8"))
    manifest_path = str(latest.get("manifest") or "")
    if not manifest_path.startswith("snapshots/") or not manifest_path.endswith("/manifest.json"):
        raise ValueError("Vault latest pointer is invalid.")
    manifest = json.loads(content(manifest_path).decode("utf-8"))
    archive_path = str(manifest.get("archive") or "")
    if not archive_path.startswith("snapshots/") or not archive_path.endswith(".enc"):
        raise ValueError("Vault manifest archive path is invalid.")
    encrypted = content(archive_path)
    expected = str(manifest.get("archiveSha256") or "")
    if not expected or hashlib.sha256(encrypted).hexdigest() != expected:
        raise ValueError("Vault archive integrity check failed.")
    return encrypted, manifest


def _restore_safe_support_available() -> bool:
    """Return whether the platform can safely create archive paths below a dir fd."""
    return bool(
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in getattr(os, "supports_dir_fd", ())
        and os.mkdir in getattr(os, "supports_dir_fd", ())
    )


def _restore_member_parts(member: tarfile.TarInfo) -> tuple[str, ...]:
    """Validate a tar member name as a strict, relative POSIX path."""
    name = member.name
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise ValueError("Unsafe archive path.")
    windows_name = PureWindowsPath(name)
    if PurePosixPath(name).is_absolute() or windows_name.drive or windows_name.root:
        raise ValueError("Unsafe archive path.")

    trailing_separator = name.endswith("/")
    if trailing_separator:
        name = name[:-1]
        if not name or name.endswith("/") or member.type != tarfile.DIRTYPE:
            raise ValueError("Unsafe archive path.")
    parts = tuple(name.split("/"))
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Unsafe archive path.")
    return parts


def _restore_destination(stage_root: Path, parts: tuple[str, ...]) -> None:
    """Confirm the resolved destination remains below the staging directory."""
    destination = (stage_root.joinpath(*parts)).resolve(strict=False)
    try:
        destination.relative_to(stage_root)
    except ValueError as exc:
        raise ValueError("Unsafe archive path.") from exc


def _restore_open_dir(parent_fd: int, name: str) -> int:
    """Open one child directory without following a link, creating it if absent."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)


def _restore_open_parent(root_fd: int, parts: tuple[str, ...]) -> int:
    """Walk parent components with O_NOFOLLOW and return the final directory fd."""
    current_fd = root_fd
    try:
        for part in parts:
            next_fd = _restore_open_dir(current_fd, part)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        if current_fd != root_fd:
            os.close(current_fd)
        raise


def _restore_directory(root_fd: int, parts: tuple[str, ...]) -> None:
    parent_fd = _restore_open_parent(root_fd, parts[:-1])
    try:
        try:
            os.mkdir(parts[-1], 0o700, dir_fd=parent_fd)
        except FileExistsError:
            existing_fd = _restore_open_dir(parent_fd, parts[-1])
            os.close(existing_fd)
    finally:
        if parent_fd != root_fd:
            os.close(parent_fd)


def _restore_regular_file(root_fd: int, member: tarfile.TarInfo, archive: tarfile.TarFile, parts: tuple[str, ...]) -> None:
    if member.size < 0:
        raise ValueError("Invalid archive member size.")
    parent_fd = _restore_open_parent(root_fd, parts[:-1])
    fd: Optional[int] = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(parts[-1], flags, 0o600, dir_fd=parent_fd)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("Unable to read regular archive member.")
        with source:
            with os.fdopen(fd, "wb") as destination:
                fd = None
                shutil.copyfileobj(source, destination, length=1024 * 1024)
    finally:
        if fd is not None:
            os.close(fd)
        if parent_fd != root_fd:
            os.close(parent_fd)


def materialize_snapshot(base_dir: str | Path, encrypted_archive: bytes, key: str, overwrite: bool = False) -> Dict[str, Any]:
    """Validate/decrypt an archive into a staging directory, then replace state."""
    if not key:
        return {"ok": False, "error": "CIPHER_VAULT_KEY is required."}
    try:
        plain = Fernet(key.encode()).decrypt(encrypted_archive)
    except Exception as exc:
        return {"ok": False, "error": f"Backup authentication failed: {exc}"}
    base = Path(base_dir)
    if not _restore_safe_support_available():
        return {"ok": False, "error": "Safe snapshot staging is unsupported on this platform."}
    stage: Optional[Path] = None
    try:
        stage = Path(tempfile.mkdtemp(prefix="cipher-vault-restore-", dir=str(base.parent)))
        stage_root = stage.resolve()
        root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            root_flags |= os.O_CLOEXEC
        root_fd = os.open(stage, root_flags)
        try:
            with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tf:
                for member in tf.getmembers():
                    parts = _restore_member_parts(member)
                    _restore_destination(stage_root, parts)
                    if member.type == tarfile.DIRTYPE:
                        _restore_directory(root_fd, parts)
                    elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                        _restore_regular_file(root_fd, member, tf, parts)
                    else:
                        raise ValueError("Archive contains unsupported link or special member.")
        finally:
            os.close(root_fd)
        if overwrite:
            backup = base.with_name(base.name + f".pre-restore-{int(time.time())}")
            moved_base = False
            if base.exists():
                base.rename(backup)
                moved_base = True
            try:
                stage.rename(base)
            except Exception:
                if moved_base and not base.exists() and backup.exists():
                    backup.rename(base)
                raise
            stage = None
            return {"ok": True, "restoredFrom": str(backup)}
        staged_at = str(stage)
        stage = None
        return {"ok": True, "stagedAt": staged_at}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
