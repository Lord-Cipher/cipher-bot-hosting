import tempfile
from pathlib import Path

from vault_sync import export_portable_backup, import_portable_backup

with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as target_tmp:
    source = Path(source_tmp)
    target = Path(target_tmp)
    for root in (source, target):
        (root / "storage").mkdir()
        (root / "sandbox").mkdir()
    (source / "storage" / "data.json").write_text('{"users": {"1": {"plan": "pro"}}}', encoding="utf-8")
    (source / "storage" / "keycache").mkdir()
    (source / "storage" / "keycache" / "vault.bin").write_bytes(b"encrypted-key-cache")
    (source / "sandbox" / "bot_1" ).mkdir()
    (source / "sandbox" / "bot_1" / "index.js").write_text("console.log('restored')", encoding="utf-8")
    (target / "storage" / "old.json").write_text("old", encoding="utf-8")

    bundle = export_portable_backup(source, "correct horse battery staple")
    assert b"cipher-portable-backup-v1" in bundle
    bad = import_portable_backup(target, bundle, "wrong passphrase")
    assert not bad["ok"]
    assert (target / "storage" / "old.json").read_text(encoding="utf-8") == "old"

    result = import_portable_backup(target, bundle, "correct horse battery staple", overwrite=True)
    assert result["ok"], result
    assert (target / "storage" / "data.json").read_text(encoding="utf-8").startswith('{"users"')
    assert (target / "storage" / "keycache" / "vault.bin").read_bytes() == b"encrypted-key-cache"
    assert (target / "sandbox" / "bot_1" / "index.js").read_text(encoding="utf-8") == "console.log('restored')"
    assert not (target / "storage" / "old.json").exists()

print("portable recovery export/import tests passed")
