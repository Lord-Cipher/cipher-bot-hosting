#!/usr/bin/env python3


import os
import re
import ast
import math
import zipfile
import tarfile
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PATTERNS: Dict[str, List[Tuple[str, str]]] = {

    # ── Real data theft ─────────────────────────────────────────
    # Only flag when a SPECIFIC system path is targeted, not '/' alone.
    "🔴 Data Theft": [
        # os.walk with a real system directory literal
        (r'os\.walk\s*\(\s*["\'][/\\](?:root|home|etc|var|proc)["\']',
            "os.walk server directory — is stealing server files"),
        # send_document / send_file opening a system path
        (r'send_document\s*\([^\n]*open\s*\(\s*["\'][/\\](?:root|etc|proc|sys)',
            "Sending system file over Telegram"),
        # zipfile writing files from a SYSTEM directory (the real threat combo)
        # Note: plain zipfile.ZipFile(f,"w") is legitimate — we only flag when
        # the write comes right after an os.walk of a system path (caught separately).
        # glob on root
        (r'glob\.glob\s*\(\s*["\'][/\\]\*',
            "Root se glob scan — is searching for server files"),
        # shutil.copy from /root
        (r'shutil\.(?:copy|copy2|copyfile)\s*\([^\n]*/root',
            "Copying from /root"),
        # ROOT_DIR = "/" pattern
        (r'ROOT_DIR\s*=\s*["\'][/\\]["\']',
            "Targeting the root directory"),
        # fake backup tables that exfiltrate data
        (r'\bbackup_log\b',
            "backup_log — could be a data-theft disguise"),
        # FileHarvester / scan_for_files are caught via AST class-name check
        # (avoids false positives when these words appear inside string literals
        # such as scanner pattern definitions in legitimate bots).
        # Explicitly walking relative path + arcname + send = archive exfil
        (r'arcname\s*=\s*os\.path\.relpath',
            "Packing files into a ZIP using relative paths"),
        # auto-send on startup (post_init sending files without user trigger)
        (r'post_init\s*=.*send_document|send_document.*post_init',
            "Startup pe file auto-send — suspicious"),
    ],

    # ── Hidden / nested bots ─────────────────────────────────────
    # Normal hosted bots WILL have infinity_polling, CommandHandler, etc.
    # Weight is very low (5) so they cannot alone trigger REJECT.
    # They only matter if combined with data-theft or backdoor findings.
    "🟠 Hidden Bot": [
        (r'\binfinity_polling\s*\(',
            "infinity_polling — another bot is running (normal for hosted bots)"),
        (r'\brun_polling\s*\(',
            "run_polling — dusra bot (normal for hosted bots)"),
        (r'\bbot\.polling\s*\(',
            "bot.polling — a bot is running"),
        (r'\bupdater\.start_polling\s*\(',
            "updater.start_polling — v13 style bot"),
        (r'Application\.builder\s*\(\s*\)',
            "Application.builder() — python-telegram-bot v20"),
        (r'\btelebot\.TeleBot\s*\(',
            "telebot.TeleBot() — bot instance"),
        (r'\bBot\s*\(\s*token\s*=',
            "Bot(token=…) — bot token initialization"),
        (r'\bCommandHandler\s*\(',
            "CommandHandler — bot command wired"),
        (r'\bMessageHandler\s*\(',
            "MessageHandler — bot message handler"),
        # Running another hosting service inside the bot
        (r'\bhosting_bot\b|\bhost_bot\b|\bpanel_bot\b',
            "Nested hosting service — recursive bot hosting"),
    ],

    # ── True backdoors ───────────────────────────────────────────
    # eval/exec/compile are handled in AST scan (avoids false positives on
    # string literals like "eval(compile…" in other scanner code).
    "🔴 Backdoor": [
        # os.system with a non-trivial argument on the same line
        (r'os\.system\s*\(\s*[^\)]{3,}\)',
            "os.system() — system command execution"),
        # subprocess with shell=True AND user-controlled input on SAME line
        (r'subprocess\s*\.\s*(?:Popen|call|run)\s*\([^\n]*shell\s*=\s*True[^\n]*(?:input|stdin)',
            "Shell injection — user input piped to shell"),
        # marshal.loads → arbitrary bytecode
        (r'marshal\.loads\s*\(',
            "marshal.loads() — obfuscated bytecode execution"),
        # Secret admin trigger word hardcoded
        (r'ADMINNAME\s*=\s*["\'][^"\']{1,50}["\']',
            "Hardcoded secret admin trigger word"),
        # Deceptive comments that attackers add to look legitimate
        (r'#.*LEGITIMATE LAGTA HAI|#.*dikhta aisa hai',
            "Deceptive comment — attacker ne add kiya"),
    ],

    # ── Suspicious network ───────────────────────────────────────
    "🟡 Suspicious Network": [
        # Known malicious endpoints
        (r'devil-api\.com|elementfx\.io',
            "Known malicious API endpoint"),
        # Reading a system file + HTTP POST = exfiltration
        (r'open\s*\(\s*["\'][/\\](?:root|etc|proc|sys)[^\)]*\)[^\n]*(?:requests|urllib)',
            "Reading a system file then HTTP POST — data exfiltration"),
        # Pastebin raw fetches (remote code download)
        (r'pastebin\.com/raw',
            "Downloading raw code from Pastebin — remote code execution"),
        # Short URL services (destination hidden)
        (r'bit\.ly/|tinyurl\.com/',
            "Shortened URL — hidden destination"),
        # ngrok / localtunnel / serveo
        (r'\bngrok\b|\blocaltunnel\b|\bserveo\.net\b',
            "Tunnel library — exposing the server"),
        # requests.post sending a bot token to an external URL
        (r'requests\.post\s*\([^\n]*token[^\n]*\)',
            "Sending the token out via POST"),
    ],

    # ── Obfuscation ──────────────────────────────────────────────
    "🟡 Obfuscation": [
        # base64 decode result immediately exec'd
        (r'base64\.b64decode\s*\([^\n]+\)[^\n]*\bexec\b',
            "Base64 decode + exec — hidden code"),
        # zlib decompress + exec
        (r'zlib\.decompress\s*\([^\n]+\)[^\n]*\bexec\b',
            "Compressed code + exec — hidden code"),
        # Long hex string (≥ 6 consecutive \xNN)
        (r'(?:\\x[0-9a-fA-F]{2}){6,}',
            "Long hex string — obfuscated code"),
        # chr() chain
        (r'chr\s*\(\s*\d+\s*\)\s*\+\s*chr\s*\(\s*\d+\s*\)',
            "chr() chain — character encoding obfuscation"),
    ],

    # ── Resource abuse ───────────────────────────────────────────
    "🟠 Resource Abuse": [
        (r'multiprocessing\.Pool\s*\(\s*(?:None|\d{3,})',
            "Massive process pool — resource abuse"),
        (r'fork\s*\(\s*\)\s*[;\n].*fork\s*\(\s*\)',
            "Fork bomb pattern"),
        (r'while\s+True\s*:[^\n]*threading\.Thread',
            "Infinite loop + threading — CPU/memory abuse"),
    ],
}


# ═══════════════════════════════════════════════════════════════
#  CATEGORY WEIGHTS
#  score = weight × min(hit_count, 3)  per category
# ═══════════════════════════════════════════════════════════════
WEIGHTS: Dict[str, int] = {
    "🔴 Data Theft":          40,
    "🔴 Backdoor":            35,
    "🔴 Exposed Credentials": 10,   # token alone → warn, not block
    "🟠 Hidden Bot":           5,   # normal in hosted bots — almost never alone matters
    "🟠 Resource Abuse":      15,
    "🟡 Suspicious Network":  12,
    "🟡 Obfuscation":         12,
}

BOT_TOKEN_RE = re.compile(r'\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b')
IP_RE        = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')


# ═══════════════════════════════════════════════════════════════
#  STATIC PATTERN SCAN
# ═══════════════════════════════════════════════════════════════

def static_scan(code: str) -> Dict[str, List[str]]:
    """Regex-based scan. Uses MULTILINE (NOT DOTALL) to keep .* within lines."""
    results: Dict[str, List[str]] = {}
    for category, pattern_list in PATTERNS.items():
        hits: List[str] = []
        for pattern, description in pattern_list:
            if re.search(pattern, code, re.IGNORECASE | re.MULTILINE):
                hits.append(description)
        if hits:
            results[category] = hits

    # Actual bot token values in the code
    tokens = BOT_TOKEN_RE.findall(code)
    if tokens:
        results.setdefault("🔴 Exposed Credentials", [])
        results["🔴 Exposed Credentials"].append(
            f"Hardcoded Bot Token: {tokens[0][:15]}…  "
            f"({len(tokens)} token{'s' if len(tokens)>1 else ''} found)"
        )
    return results


# ═══════════════════════════════════════════════════════════════
#  AST DEEP SCAN
#  AST correctly ignores string literals, so eval/exec/__import__
#  checks here won't false-positive on scanner pattern lists.
# ═══════════════════════════════════════════════════════════════

def ast_scan(code: str) -> List[str]:
    findings: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        findings.append(
            f"Code failed to parse: {e} — may be encoded/obfuscated"
        )
        return findings

    # Entropy check for hidden payloads in strings
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if len(s) > 60:
                # Calculate Shannon entropy
                prob = [float(s.count(c)) / len(s) for c in dict.fromkeys(list(s))]
                entropy = -sum(p * math.log(p, 2) for p in prob)
                if entropy > 4.8:
                    findings.append(f"High-entropy hidden string payload detected (entropy={entropy:.2f})")

    for node in ast.walk(tree):

        if isinstance(node, ast.Call):
            func = node.func

            # os.walk('<system_path>') — literal only
            if isinstance(func, ast.Attribute):
                if (func.attr == 'walk'
                        and isinstance(func.value, ast.Name)
                        and func.value.id == 'os'
                        and node.args):
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if arg.value in ['/', '/root', '/etc', '/home', '/proc', '/var']:
                            findings.append(
                                f"os.walk('{arg.value}') — sensitive directory scan"
                            )

            if isinstance(func, ast.Name):
                fid = func.id

                # eval / exec only when argument is a function call (dynamic source)
                if fid in ('eval', 'exec') and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, (ast.Call, ast.Attribute)):
                        findings.append(
                            f"Dangerous: {fid}() — dynamic / remote code execution"
                        )

                # __import__('os') — real call only, not string literal
                if fid == '__import__' and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        if node.args[0].value in ('os', 'subprocess', 'ctypes', 'sys'):
                            findings.append(
                                f"Dynamic __import__('{node.args[0].value}') — code injection"
                            )

                # Detect getattr(os, 'system') or similar de-obfuscation tricks
                if fid == 'getattr' and len(node.args) >= 2:
                    arg0 = node.args[0]
                    arg1 = node.args[1]
                    if isinstance(arg0, ast.Name) and arg0.id in ('os', 'sys', 'subprocess'):
                        if isinstance(arg1, ast.Constant) and arg1.value in ('system', 'popen', 'exec', 'spawn'):
                            findings.append(f"Dynamic attribute resolution getattr({arg0.id}, '{arg1.value}') — obfuscated execution")

        # Suspicious class names (harvest / steal / exfil / grabber)
        if isinstance(node, ast.ClassDef):
            bad = {'harvest', 'steal', 'exfil', 'harvester', 'collector', 'grabber'}
            if any(b in node.name.lower() for b in bad):
                findings.append(f"Suspicious class name: {node.name}")

        # Hardcoded sensitive path constants
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val in ['/root', '/etc', '/proc', '/sys', '/home']:
                findings.append(f"Hardcoded sensitive path: '{val}'")

    return findings


# ═══════════════════════════════════════════════════════════════
#  RISK CALCULATOR
# ═══════════════════════════════════════════════════════════════

def calculate_risk(static_findings: Dict[str, List[str]],
                   ast_findings: List[str]) -> int:
    score = 0
    for category, hits in static_findings.items():
        weight = WEIGHTS.get(category, 5)
        score += weight * min(len(hits), 3)

    # Deduplicate AST findings; cap contribution so repeated path strings
    # (common in any bot that defines allowed directories) don't inflate score.
    unique_ast = list(dict.fromkeys(ast_findings))
    score += min(len(unique_ast) * 5, 20)

    return min(score, 100)


# ═══════════════════════════════════════════════════════════════
#  VERDICT
# ═══════════════════════════════════════════════════════════════

def get_verdict(risk_score: int,
                static_findings: Dict[str, List[str]]) -> Tuple[str, str]:
    """
    Verdict logic (tuned for hosting bots):

    REJECT      → only when truly malicious patterns are found at high risk
    MANUAL_REVIEW → uncertain, admin should check
    APPROVE     → safe to run

    Hardcoded token alone (score ≤ 15) → APPROVE with warning
    Hidden Bot patterns alone → APPROVE (normal for hosted bots)
    """
    # Only Data Theft and Backdoor are hard-blocking
    has_blocking = any(
        static_findings.get(c)
        for c in ("🔴 Data Theft", "🔴 Backdoor")
    )
    # Credentials alone (no other threat) → warn but don't block
    cred_only = (
        "🔴 Exposed Credentials" in static_findings
        and not has_blocking
    )

    if risk_score >= 70 and has_blocking:
        return "DANGEROUS", "REJECT"
    if risk_score >= 40 and has_blocking:
        return "DANGEROUS", "REJECT"
    if cred_only and risk_score < 15:
        return "SUSPICIOUS", "APPROVE"   # warn in summary, but let it through
    if risk_score >= 30 or has_blocking:
        return "SUSPICIOUS", "MANUAL_REVIEW"
    return "SAFE", "APPROVE"


# ═══════════════════════════════════════════════════════════════
#  ARCHIVE SCANNER  (ZIP / tar)
# ═══════════════════════════════════════════════════════════════

def _scan_archive(file_path: str) -> Dict[str, Any]:
    tmp = tempfile.mkdtemp()
    try:
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as z:
                # ZIP-slip check first
                for name in z.namelist():
                    if name.startswith('/') or '..' in name:
                        return {
                            "verdict": "DANGEROUS",
                            "risk_score": 99,
                            "findings": {
                                "🔴 Zip Slip Attack": [
                                    f"Dangerous path in ZIP: '{name}' — "
                                    "server files overwrite ho sakte hain!"
                                ]
                            },
                            "ast_findings": [],
                            "all_threats": ["🔴 Zip Slip Attack"],
                            "recommendation": "REJECT",
                            "summary": "ZIP Slip attack detected!",
                            "filename": os.path.basename(file_path),
                        }
                z.extractall(tmp)
        elif file_path.lower().endswith(('.tar.gz', '.tgz', '.tar')):
            with tarfile.open(file_path, 'r:*') as t:
                # Tar-slip check — identical hazard to zip-slip above: absolute
                # paths or ".." components in member names can write outside
                # `tmp`. The zip branch checked for this; the tar branch
                # didn't, so a malicious .tar.gz could bypass it entirely.
                for member in t.getmembers():
                    name = member.name
                    if name.startswith('/') or name.startswith('\\') or '..' in Path(name).parts:
                        return {
                            "verdict": "DANGEROUS",
                            "risk_score": 99,
                            "findings": {
                                "🔴 Tar Slip Attack": [
                                    f"Dangerous path in TAR: '{name}' — "
                                    "server files could be overwritten!"
                                ]
                            },
                            "ast_findings": [],
                            "all_threats": ["🔴 Tar Slip Attack"],
                            "recommendation": "REJECT",
                            "summary": "TAR Slip attack detected!",
                            "filename": os.path.basename(file_path),
                        }
                    # Symlinks/hardlinks pointing outside tmp are the other
                    # classic tar-extraction attack — block those too.
                    if member.issym() or member.islnk():
                        return {
                            "verdict": "DANGEROUS",
                            "risk_score": 99,
                            "findings": {
                                "🔴 Tar Slip Attack": [
                                    f"Symlink/hardlink member in TAR: '{name}'"
                                ]
                            },
                            "ast_findings": [],
                            "all_threats": ["🔴 Tar Slip Attack"],
                            "recommendation": "REJECT",
                            "summary": "TAR Slip attack detected!",
                            "filename": os.path.basename(file_path),
                        }
                t.extractall(tmp)
        else:
            return {
                "verdict": "SUSPICIOUS", "risk_score": 20,
                "findings": {"🟡 Warning": ["Unknown archive format"]},
                "ast_findings": [], "all_threats": [],
                "recommendation": "MANUAL_REVIEW",
                "summary": "No .py file found in the archive.",
                "filename": os.path.basename(file_path),
            }

        py_files = list(Path(tmp).rglob("*.py"))
        if not py_files:
            return {
                "verdict": "SUSPICIOUS", "risk_score": 20,
                "findings": {"🟡 Warning": ["No .py file found in the archive"]},
                "ast_findings": [], "all_threats": [],
                "recommendation": "MANUAL_REVIEW",
                "summary": "There are no Python files in the archive.",
                "filename": os.path.basename(file_path),
            }

        worst: Optional[Dict[str, Any]] = None
        for py_file in py_files[:10]:
            try:
                code = py_file.read_text(errors='ignore')
                result = scan_code(code, py_file.name)
                if worst is None or result['risk_score'] > worst['risk_score']:
                    worst = result
            except Exception:
                continue

        return worst or {
            "verdict": "SAFE", "risk_score": 0,
            "recommendation": "APPROVE",
            "summary": "Looks safe.", "all_threats": [],
            "findings": {}, "ast_findings": [],
            "filename": os.path.basename(file_path),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  CORE CODE SCANNER
# ═══════════════════════════════════════════════════════════════

def scan_code(code: str, filename: str = "file.py") -> Dict[str, Any]:
    """Scan raw Python source code. Returns result dict."""
    sf = static_scan(code)
    af = ast_scan(code)
    risk = calculate_risk(sf, af)
    verdict, recommendation = get_verdict(risk, sf)

    all_threats: List[str] = [f"{c}: {h}" for c, hits in sf.items() for h in hits]
    all_threats += af

    if verdict == "DANGEROUS":
        summary = f"⚠️ File is DANGEROUS! {len(all_threats)} threat(s) found."
    elif verdict == "SUSPICIOUS":
        summary = "🔍 File is suspicious. Get an admin to do a manual review."
    else:
        summary = "✅ File looks safe. No major threats found."

    return {
        "verdict":        verdict,
        "risk_score":     risk,
        "findings":       sf,
        "ast_findings":   af,
        "all_threats":    all_threats,
        "recommendation": recommendation,
        "summary":        summary,
        "filename":       filename,
    }


# ═══════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT — import this in your hosting bot
# ═══════════════════════════════════════════════════════════════

def scan_file(file_path: str) -> Dict[str, Any]:
    """
    Scan any uploaded file before saving / approving it.

    Usage in your hosting bot:
        from security_scanner_free import scan_file

        result = scan_file("/tmp/uploaded_bot.py")
        rec    = result["recommendation"]   # "APPROVE" / "MANUAL_REVIEW" / "REJECT"
        risk   = result["risk_score"]       # 0-100
        verdict = result["verdict"]         # "SAFE" / "SUSPICIOUS" / "DANGEROUS"
    """
    filename = os.path.basename(file_path)
    ext = filename.lower()

    try:
        if ext.endswith(('.zip', '.tar.gz', '.tgz', '.tar')):
            return _scan_archive(file_path)

        elif ext.endswith(('.py', '.pyc', '.pyo', '.js', '.ts')):
            with open(file_path, 'r', errors='ignore') as fh:
                code = fh.read()
            return scan_code(code, filename)

        else:
            return {
                "verdict":        "SUSPICIOUS",
                "risk_score":     30,
                "findings":       {"🟡 Warning": [f"Unknown file type: {ext}"]},
                "ast_findings":   [],
                "all_threats":    [f"Unknown file type: {ext}"],
                "recommendation": "MANUAL_REVIEW",
                "summary":        f"File type '{ext}' is not allowed..",
                "filename":       filename,
            }

    except Exception as e:
        return {
            "verdict":        "ERROR",
            "risk_score":     50,
            "findings":       {},
            "ast_findings":   [],
            "all_threats":    [f"Scan error: {e}"],
            "recommendation": "MANUAL_REVIEW",
            "summary":        f"Scan error: {e}",
            "filename":       filename,
        }



if __name__ == "__main__":
    _tests = [
        ("Data Theft Bot", """
import os, zipfile, tempfile
from telegram.ext import Application, CommandHandler

BOT_TOKEN = "8854808821:AAGg_vKWEmUKH6eTPW-rF-WgzQAXsNVDPXA"
ROOT_DIR = "/"

def create_py_zip():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk('/root'):
            for file in files:
                z.write(os.path.join(root, file))
    return tmp.name

app = Application.builder().token(BOT_TOKEN).build()
app.run_polling()
"""),
        ("Normal Telegram Bot", """
import telebot

bot = telebot.TeleBot("YOUR_TOKEN_HERE")

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "Hello! Welcome to my bot!")

@bot.message_handler(func=lambda m: True)
def echo(message):
    bot.reply_to(message, message.text)

bot.infinity_polling()
"""),
        ("Normal python-telegram-bot v20", """
from telegram.ext import ApplicationBuilder, CommandHandler

async def start(update, context):
    await update.message.reply_text("Hello!")

app = ApplicationBuilder().token("YOUR_TOKEN").build()
app.add_handler(CommandHandler("start", start))
app.run_polling()
"""),
    ]

    for name, code in _tests:
        r = scan_code(code, "test.py")
        icon = "🔴" if r["verdict"] == "DANGEROUS" else ("🟡" if r["verdict"] == "SUSPICIOUS" else "🟢")
        print(f"\n{'━'*50}")
        print(f"🧪 {name}")
        print(f"{icon} Verdict: {r['verdict']}  |  Risk: {r['risk_score']}/100  |  {r['recommendation']}")
        print(f"   {r['summary']}")
        if r["all_threats"]:
            for t in r["all_threats"][:5]:
                print(f"   • {t}")
    print(f"\n{'━'*50}")
    print("✅ Scanner ready — 'from security_scanner_free import scan_file' import it and use it!")
