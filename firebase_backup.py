"""Firebase Cloud Storage disaster-recovery provider.

This module is deliberately independent of Telegram and GitHub. It stores the
same encrypted portable recovery bundle used by the manual export flow. The
Firebase service account is supplied through deployment secrets or the encrypted
Admin-panel runtime configuration, never through the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from vault_sync import export_portable_backup, import_portable_backup

_POINTER_NAME = "cipher-backups/LATEST.json"
_OBJECT_PREFIX = "cipher-backups/snapshots/"
_DEFAULT_RETENTION = 30


def _configured() -> bool:
    return bool((os.getenv("FIREBASE_STORAGE_BUCKET") or "").strip()) and bool(
        (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    )


def _bucket():
    """Return an authenticated Firebase Storage bucket, or raise a useful error."""
    try:
        import firebase_admin
        from firebase_admin import credentials, storage
    except ImportError as exc:
        raise RuntimeError("firebase-admin is not installed") from exc

    bucket_name = str(os.getenv("FIREBASE_STORAGE_BUCKET") or "").strip()
    if not bucket_name:
        raise RuntimeError("FIREBASE_STORAGE_BUCKET is not configured")
    if bucket_name.startswith("gs://"):
        bucket_name = bucket_name[5:]

    try:
        app = firebase_admin.get_app()
    except ValueError:
        raw_json = str(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
        if raw_json:
            try:
                service_account = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
            app = firebase_admin.initialize_app(
                credentials.Certificate(service_account),
                {"storageBucket": bucket_name},
            )
        else:
            credential_path = str(os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
            if not credential_path:
                raise RuntimeError("Firebase service-account credentials are not configured")
            app = firebase_admin.initialize_app(
                credentials.Certificate(credential_path),
                {"storageBucket": bucket_name},
            )
    try:
        return storage.bucket(name=bucket_name, app=app)
    except TypeError:
        # Older firebase-admin releases accept only the app positional form.
        return storage.bucket(bucket_name, app=app)


def _retention() -> int:
    try:
        return max(2, min(365, int(os.getenv("FIREBASE_BACKUP_RETENTION", str(_DEFAULT_RETENTION)))))
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION


def _local_state_present(base_dir: str | Path) -> bool:
    """Never overwrite a non-empty installation during automatic boot restore."""
    base = Path(base_dir)
    db_path = base / "storage" / "data" / "panel_db.json"
    if db_path.exists():
        try:
            data = json.loads(db_path.read_text(encoding="utf-8") or "{}")
            if data.get("users") or data.get("bots") or data.get("payments"):
                return True
        except Exception:
            return True
    # Static panel photos, an empty settings file, host-key metadata, and the
    # empty database file are normal in a fresh checkout. A non-empty sandbox,
    # however, represents hosted state and must prevent an automatic overwrite.
    sandbox = base / "sandbox"
    if sandbox.exists() and any(path.is_file() and path.name != ".gitkeep" for path in sandbox.rglob("*")):
        return True
    return False


def firebase_status() -> Dict[str, Any]:
    return {
        "configured": _configured(),
        "bucket": str(os.getenv("FIREBASE_STORAGE_BUCKET") or "").strip() or None,
        "retention": _retention(),
    }


def firebase_check_connection() -> Dict[str, Any]:
    """Authenticate and verify that the configured Storage bucket is reachable."""
    if not _configured():
        return {"ok": False, "configured": False, "error": "Firebase is not configured."}
    try:
        bucket = _bucket()
        # exists() performs an authenticated bucket metadata request without
        # creating or changing any user data.
        exists = bool(bucket.exists())
        if not exists:
            return {"ok": False, "configured": True, "error": "Firebase Storage bucket was not found."}
        latest = bucket.blob(_POINTER_NAME)
        has_latest = bool(latest.exists())
        return {"ok": True, "configured": True, "bucket": bucket.name, "hasLatest": has_latest}
    except Exception as exc:
        return {"ok": False, "configured": True, "error": str(exc)}


def firebase_backup_now(base_dir: str | Path, passphrase: str) -> Dict[str, Any]:
    """Upload one encrypted recovery bundle and atomically publish LATEST.json."""
    if not _configured():
        return {"ok": False, "skipped": True, "error": "Firebase backup is not configured."}
    try:
        data = export_portable_backup(base_dir, passphrase)
        digest = hashlib.sha256(data).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_name = f"{_OBJECT_PREFIX}{stamp}-{digest[:12]}.cipher-backup"
        bucket = _bucket()
        blob = bucket.blob(object_name)
        blob.upload_from_string(data, content_type="application/octet-stream")
        pointer = {
            "format": "cipher-firebase-pointer-v1",
            "object": object_name,
            "sha256": digest,
            "sizeBytes": len(data),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        bucket.blob(_POINTER_NAME).upload_from_string(
            (json.dumps(pointer, sort_keys=True) + "\n").encode("utf-8"),
            content_type="application/json",
        )
        _trim_old_snapshots(bucket)
        return {"ok": True, "object": object_name, "sizeBytes": len(data), "sha256": digest}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _trim_old_snapshots(bucket) -> None:
    try:
        blobs = list(bucket.list_blobs(prefix=_OBJECT_PREFIX))
        blobs.sort(key=lambda item: getattr(item, "name", ""), reverse=True)
        for blob in blobs[_retention():]:
            blob.delete()
    except Exception:
        # Retention cleanup must never turn a successful backup into a failure.
        pass


def firebase_restore_on_boot(base_dir: str | Path, passphrase: str) -> Dict[str, Any]:
    """Restore Firebase's latest bundle only on an empty local installation."""
    if not _configured():
        return {"ok": False, "skipped": True, "error": "Firebase backup is not configured."}
    if _local_state_present(base_dir):
        return {"ok": False, "skipped": True, "reason": "local data present; not overwriting it"}
    try:
        bucket = _bucket()
        pointer_blob = bucket.blob(_POINTER_NAME)
        pointer = json.loads(pointer_blob.download_as_bytes().decode("utf-8"))
        object_name = str(pointer.get("object") or "")
        if not re.fullmatch(r"cipher-backups/snapshots/[A-Za-z0-9TZ-]+-[0-9a-f]{12}\.cipher-backup", object_name):
            raise ValueError("Firebase backup pointer is invalid")
        data = bucket.blob(object_name).download_as_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != str(pointer.get("sha256") or ""):
            raise ValueError("Firebase backup integrity check failed")
        result = import_portable_backup(base_dir, data, passphrase, overwrite=True)
        if result.get("ok"):
            result.update({"source": "firebase", "object": object_name, "sizeBytes": len(data)})
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def firebase_auto_loop(base_dir: str | Path, passphrase_getter, interval_seconds: int = 1800) -> None:
    """Periodic provider loop; failures are logged and leave GitHub available."""
    while True:
        try:
            time.sleep(max(300, int(interval_seconds)))
            if not _configured():
                continue
            result = firebase_backup_now(base_dir, passphrase_getter())
            print(
                f"[firebase-backup] ok={result.get('ok')} skipped={result.get('skipped', False)} "
                f"error={result.get('error', '')}",
                flush=True,
            )
        except Exception as exc:
            print(f"[firebase-backup] loop error: {exc}", flush=True)
