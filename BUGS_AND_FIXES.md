# Cipher Bot Hosting - Bugs and Fixes

## Bug 1: Feature Flags toggle/reset uses wrong storage key
- **Location**: Lines 5151-5163 (router)
- **Bug**: Toggle writes `ff_{key}` via `set_setting(f"ff_{ff_key}", not cur)` and reads `ff_{key}` via `get_setting(f"ff_{ff_key}", ...)`. But `_ff_get()` reads from nested `feature_flags` dict via `get_setting("feature_flags", {})`. **MISMATCH**.
- **Fix**: Change the router to use `_ff_toggle(ff_key)` and `_ff_reset_all()` instead of direct `set_setting`.

## Bug 2: Preset currency buttons (BDT ৳, USD $, etc.) caught by wrong handler
- **Location**: Lines 5020-5022 (catch-all) fires BEFORE lines 5041-5052 (specific preset handler)
- **Bug**: `adm_bc_set_currency_BDT_৳` is caught by `if data.startswith("adm_bc_set_"):` at line 5020, setting `bc_key=currency_BDT_৳` and waiting for user input instead of immediately setting the currency.
- **Fix**: Move the `adm_bc_set_currency_` and `adm_bc_set_currency_symbol` specific handlers BEFORE the catch-all.

## Bug 3: Log key mismatch - logs always show empty
- **Location**: `start_child` stores log under key `"log"` (line 2663), but `action_bot_logs` (line 14029) and `render_adm_bc_logs` (line 8675) read `"log_ring"`.
- **Fix**: Change `start_child` to store under `"log_ring"` key, OR change the readers to use `"log"`.

## Bug 4: GitHub user clone feature not implemented
- **Location**: Lines 9115-9126 (await_gh_repo_url/await_gh_user_token handlers)
- **Bug**: The handlers just say "not available yet" and abort.
- **Fix**: Implement proper GitHub clone-as-bot functionality using `_clone_gh_repo()` and `store_uploaded_file()`.

## Bug 5: plans_kb() uses hardcoded ৳ symbol
- **Location**: Line 1959
- **Bug**: `price = "Free" if v["price"] == 0 else f"{v['price']}\u09F3"` - hardcoded ৳
- **Fix**: Use `cur_sym()` instead of `\u09F3`.

## Bug 6: Add Secret Name button has wrong behavior
- **Location**: Line 7209 - `adm_bc_set_add_secret_name` caught by catch-all
- **Bug**: When user types a name, it calls `set_setting("add_secret_name", value)` instead of adding to SECRET_ENV_NAMES.
- **Fix**: Add special handling in `await_adm_bc_set` for `bc_key == "add_secret_name"` to add to SECRET_ENV_NAMES.

## Bug 7: pip install uses global pip instead of bot's .deps dir
- **Location**: Lines 15405-15423 (_handle_pip_install)
- **Bug**: Uses `pip install` globally (or venv pip) instead of `pip install --target {bot_dir}/.deps`
- **Fix**: Use `sys.executable -m pip install --target {deps_dir}` like `install_deps` does.

## Bug 8: GitHub backup sizeBytes key mismatch
- **Location**: Line 15328 - `fmt_bytes(res.get('sizeBytes', 0))` but `gh_backup_now()` returns `sizeMB` not `sizeBytes`
- **Fix**: Use `res.get('sizeMB', 0)` or fix the key in gh_backup_now.

## Summary of all fixes needed:
1. Feature flags: use `_ff_toggle()` and `_ff_reset_all()` in router
2. Currency preset buttons: move specific handlers before catch-all
3. Log key: change `"log"` to `"log_ring"` in start_child
4. GitHub clone: implement the flow
5. plans_kb: use cur_sym() 
6. Add Secret Name: special handling in await_adm_bc_set
7. pip install: use --target deps_dir
8. GitHub backup: fix sizeBytes key
