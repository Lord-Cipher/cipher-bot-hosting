"""Universal environment loader for Cipher Bot Hosting.

Priority order (highest first):
    1. Variables already supplied by the hosting platform/process.
    2. .env beside the application.
    3. cipher_vault.env beside the application.
    4. Safe derived values and defaults.

The loader never prints or returns secret values in its status output. It is
safe to import from bot.py, a worker, a CLI, systemd, Railway, Render, or
Replit. Existing process variables are never overwritten.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
from pathlib import Path
from typing import Dict, Mapping, MutableMapping, Optional, Tuple

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_WORDS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "DATABASE_URL", "PRIVATE")

# The first name in each group is the canonical name used by the application.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "BOT_TOKEN": ("BOT_TOKEN", "MAIN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TG_BOT_TOKEN", "TOKEN"),
    "CHAT_ID": ("CHAT_ID", "TELEGRAM_CHAT_ID", "TG_CHAT_ID", "TARGET_CHAT_ID"),
    "OWNER_ID": ("OWNER_ID", "ADMIN_ID", "TELEGRAM_OWNER_ID"),
    "TELEGRAM_WEBHOOK_SECRET": ("TELEGRAM_WEBHOOK_SECRET", "WEBHOOK_SECRET"),
    "OXAPAY_API_KEY": ("OXAPAY_API_KEY", "OXAPAY_KEY", "OXAPAY_MERCHANT_KEY"),
    "OMEGA_API_KEY": ("OMEGA_API_KEY", "OMEGATECH_API_KEY"),
    "OPENAI_API_KEY": ("OPENAI_API_KEY",),
    "PUBLIC_URL": ("PUBLIC_URL", "APP_URL", "BASE_URL"),
}


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse common dotenv syntax without requiring python-dotenv."""
    result: Dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.fullmatch(key):
            continue
        value = value.strip()
        if value and value[0] in {"'", '"'}:
            try:
                value = shlex.split(value, comments=False, posix=True)[0]
            except (ValueError, IndexError):
                value = value[1:-1] if len(value) > 1 and value[-1] == value[0] else value
        else:
            value = value.split(" #", 1)[0].rstrip()
        result[key] = value
    return result


def _platform_public_url(env: Mapping[str, str]) -> str:
    direct = str(env.get("PUBLIC_URL") or env.get("APP_URL") or env.get("BASE_URL") or "").strip()
    if direct:
        return direct.rstrip("/")
    railway = str(env.get("RAILWAY_PUBLIC_DOMAIN") or env.get("RAILWAY_STATIC_URL") or "").strip()
    if railway:
        return (railway if railway.startswith("http") else f"https://{railway}").rstrip("/")
    render = str(env.get("RENDER_EXTERNAL_URL") or "").strip()
    if render:
        return render.rstrip("/")
    return ""


def detect_platform(env: Optional[Mapping[str, str]] = None) -> str:
    env = env or os.environ
    if (env.get("RAILWAY_ENVIRONMENT_ID") or env.get("RAILWAY_PROJECT_ID")
            or env.get("RAILWAY_PUBLIC_DOMAIN") or env.get("RAILWAY_STATIC_URL")):
        return "railway"
    if env.get("RENDER_SERVICE_ID") or env.get("RENDER_EXTERNAL_URL"):
        return "render"
    if env.get("REPL_ID") or env.get("REPLIT_DB_URL"):
        return "replit"
    if env.get("DYNO") or env.get("HEROKU_APP_NAME"):
        return "heroku"
    if env.get("K_SERVICE"):
        return "cloud-run"
    return "vps-or-local"


def load_environment(base_dir: Optional[Path] = None,
                     environ: Optional[MutableMapping[str, str]] = None,
                     *, verbose: bool = False) -> Dict[str, str]:
    """Load and normalize configuration into *environ* and return a copy.

    Process/platform variables always win. Files are only used to fill missing
    values. This makes the same code work on VPS, Railway, Render, and Replit.
    """
    target = environ if environ is not None else os.environ
    root = Path(base_dir or Path(__file__).resolve().parent).resolve()
    merged: Dict[str, str] = {}
    for filename in (".env", "cipher_vault.env"):
        # .env has priority over the optional vault file among local files.
        values = _parse_dotenv(root / filename)
        for key, value in values.items():
            merged.setdefault(key, value)
    for key, value in target.items():
        merged[key] = value

    # Canonicalize aliases without erasing aliases supplied by a project.
    for canonical, names in ALIASES.items():
        value = next((str(merged.get(name, "")).strip() for name in names if str(merged.get(name, "")).strip()), "")
        if value:
            merged[canonical] = value
            target.setdefault(canonical, value)
            # Keep common aliases synchronized for third-party project code.
            for name in names:
                target.setdefault(name, value)

    public_url = _platform_public_url(merged)
    if public_url:
        merged["PUBLIC_URL"] = public_url
        target.setdefault("PUBLIC_URL", public_url)

    if not str(merged.get("PLATFORM", "")).strip():
        merged["PLATFORM"] = detect_platform(merged)
        target.setdefault("PLATFORM", merged["PLATFORM"])
    if not str(merged.get("APP_DIR", "")).strip():
        merged["APP_DIR"] = str(root)
        target.setdefault("APP_DIR", str(root))

    if verbose:
        print_status(merged, root)
    return dict(merged)


def required_missing(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    env = env or os.environ
    return tuple(name for name in ("BOT_TOKEN",) if not str(env.get(name, "")).strip())


def _is_secret(name: str) -> bool:
    upper = name.upper()
    return any(word in upper for word in _SECRET_WORDS)


def _masked(name: str, value: str) -> str:
    if not value:
        return "missing"
    if not _is_secret(name):
        return "set"
    return "set (masked)"


def print_status(env: Optional[Mapping[str, str]] = None, root: Optional[Path] = None) -> None:
    env = env or os.environ
    print(f"[config] platform: {env.get('PLATFORM', detect_platform(env))}")
    if root:
        print(f"[config] app dir: {root}")
    for name in ("BOT_TOKEN", "OWNER_ID", "CHAT_ID", "OXAPAY_API_KEY", "OMEGA_API_KEY",
                 "OPENAI_API_KEY", "PUBLIC_URL", "WEBHOOK_ENABLED", "DATABASE_URL"):
        print(f"[config] {name}: {_masked(name, str(env.get(name, '')))}")
    missing = required_missing(env)
    print(f"[config] required: {'ok' if not missing else 'missing ' + ', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and validate Cipher Bot environment variables")
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent, help="Application directory")
    args = parser.parse_args()
    env = load_environment(args.dir, verbose=True)
    return 0 if not required_missing(env) else 2


if __name__ == "__main__":
    raise SystemExit(main())
