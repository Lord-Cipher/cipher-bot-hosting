"""Offline regression checks for the AI Sentinel model picker and consent UI."""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_ai_sentinel_ui")
os.environ.setdefault("OWNER_ID", "9001")

import bot  # noqa: E402

settings = {}
users = {
    "9001": {"id": 9001, "plan": "lifetime", "ai_models": []},
    "42": {"id": 42, "plan": "free", "ai_models": []},
}
bot.settings_load = lambda: dict(settings)
bot.settings_load_ro = lambda: settings
bot.settings_save = lambda value: (settings.clear(), settings.update(value))
bot.db_load = lambda: {"users": copy.deepcopy(users)}
bot.db_load_ro = lambda: {"users": users}
bot.db_save = lambda value: (users.clear(), users.update(value["users"]))

bot_id = "sentinel123"
with tempfile.TemporaryDirectory(prefix="ai-sentinel-test-") as tmp:
    source_path = Path(tmp) / "main.py"
    source_path.write_text("print('original')\n", encoding="utf-8")
    sentinel_bot = {
        "id": bot_id,
        "owner": 9001,
        "name": "Test bot",
        "dir": str(Path(tmp)),
        "last_error": "NameError: name 'x' is not defined",
        "enc_files": [],
    }

    bot.find_bot = lambda requested: sentinel_bot if requested == bot_id else None
    shown_menus = []
    shown_texts = []
    acknowledgements = []
    bot.show_menu = lambda chat_id, photo, text, markup, call=None: shown_menus.append(
        (chat_id, text, markup)
    )
    bot.show_text = lambda chat_id, text, markup=None, call=None: shown_texts.append(
        (chat_id, text, markup)
    )
    bot.ack = lambda call, text="", **kwargs: acknowledgements.append((text, kwargs))
    bot.loading = lambda *args, **kwargs: None

    def callback(uid):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=uid),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=uid), message_id=1, content_type="photo"
            ),
        )

    owner_call = callback(9001)
    user_call = callback(42)
    pool = bot.get_plan_ai_models("lifetime")
    assert len(pool) > 1
    assert bot._extract_ai_diagnosis_patch("Diagnosis\n```python\nprint('ok')\n```", ".py") == "print('ok')"
    assert bot._extract_ai_diagnosis_patch("Diagnosis\n```javascript\nconsole.log('ok')\n```") == "console.log('ok')"
    assert bot._extract_ai_diagnosis_patch("```javascript\nconsole.log('wrong file type')\n```", ".py") == ""
    assert bot._extract_ai_diagnosis_patch("```\nprint('snippet only')\n```") == ""
    bot.USER_STATES[9001] = {"ai_model": pool[0]}

    # Opening Sentinel shows model choices and does not start the diagnosis.
    bot.render_ai_sentinel_model_picker(owner_call, bot_id)
    assert shown_menus
    _, picker_text, picker_markup = shown_menus[-1]
    buttons = [button for row in picker_markup.keyboard for button in row]
    assert "test bot" in picker_text.lower()
    assert "diagnosis" in " ".join(button.text for button in buttons).lower()
    assert all(len(button.callback_data.encode("utf-8")) <= 64 for button in buttons)
    model_buttons = [button for button in buttons if button.callback_data.startswith("bot_ai_model_")]
    assert {b.callback_data.removeprefix("bot_ai_model_") for b in model_buttons} == set(pool)
    active_button = next(b for b in model_buttons if b.callback_data == f"bot_ai_model_{pool[0]}")
    assert active_button.style == "success"
    assert next(b for b in buttons if b.callback_data == f"bot_ai_start_{bot_id}").style == "success"
    assert next(b for b in buttons if b.callback_data == f"bot_ai_cancel_{bot_id}").style == "danger"

    # A choice is temporary to Sentinel; Start tries that model first without
    # rewriting the user's normal chat-model selection.
    chosen_model = pool[-1]
    bot.action_bot_ai_model_pick(owner_call, chosen_model)
    assert bot.AI_SENTINEL_MODEL_SELECTIONS[9001]["model"] == chosen_model
    assert bot.USER_STATES[9001]["ai_model"] == pool[0]
    calls = []
    original_caller = bot._call_kaalix_model
    bot._call_kaalix_model = lambda model, prompt: calls.append(model) or (
        None if model == chosen_model else "Fallback model answered with relevant debugging steps."
    )
    reply, used = bot._call_ai_chain(
        "Diagnose this synthetic NameError", "lifetime", 9001, preferred_model=chosen_model
    )
    assert reply and used == pool[0]
    assert calls[:2] == [chosen_model, pool[0]]
    bot._call_kaalix_model = original_caller

    # The Start callback hands the selected model to the real diagnosis runner.
    start_calls = []
    original_runner = bot._run_bot_ai_diagnosis
    bot._run_bot_ai_diagnosis = lambda call, bid, model: start_calls.append((bid, model))
    bot.action_bot_ai_diagnosis_start(owner_call, bot_id)
    assert start_calls == [(bot_id, chosen_model)]
    bot._run_bot_ai_diagnosis = original_runner

    # Exercise the diagnosis result screen and confirm the exact green Yes / red
    # No controls are shown only with an extracted patch.
    original_status = bot.child_status
    original_snapshot = bot._bot_source_snapshot
    original_api = bot._call_ai_api
    original_save = bot.save_bot
    original_last_used = bot.AI_LAST_MODEL_USED.get(9001)
    bot.child_status = lambda bid, record: {"logs": ["NameError: x"]}
    bot._bot_source_snapshot = lambda record: [("main.py", "print('original')\n")]
    bot._call_ai_api = lambda prompt, user_plan, uid, preferred_model=None: (
        "The crash uses an undefined variable.\n```python\nprint('fixed')\n```"
    )
    bot.save_bot = lambda record: None
    bot.AI_LAST_MODEL_USED[9001] = chosen_model
    bot._run_bot_ai_diagnosis(owner_call, bot_id, chosen_model)
    assert sentinel_bot["pending_patch"]["file"] == "main.py"
    _, report_text, report_markup = shown_texts[-1]
    assert "main.py" in report_text and "print" in report_text
    report_buttons = [b for row in report_markup.keyboard for b in row]
    yes = next(b for b in report_buttons if b.text == "🟢 Yes")
    no = next(b for b in report_buttons if b.text == "🔴 No")
    assert yes.callback_data == f"bot_applyfix_{bot_id}" and yes.style == "success"
    assert no.callback_data == f"bot_rejectfix_{bot_id}" and no.style == "danger"

    # No removes the stored proposal and leaves the source file untouched.
    bot.action_bot_reject_fix(owner_call, bot_id)
    assert "pending_patch" not in sentinel_bot
    assert source_path.read_text(encoding="utf-8") == "print('original')\n"
    assert shown_texts[-1][2] is not None

    # Another user cannot start diagnosis or apply a crafted/stale callback.
    bot.render_ai_sentinel_model_picker(user_call, bot_id)
    assert len(shown_menus) == 2
    sentinel_bot["pending_patch"] = {"file": "main.py", "code": "print('attacker')"}
    bot.action_bot_apply_fix(user_call, bot_id)
    assert source_path.read_text(encoding="utf-8") == "print('original')\n"
    assert any(kwargs.get("show_alert") for _, kwargs in acknowledgements)

    bot.child_status = original_status
    bot._bot_source_snapshot = original_snapshot
    bot._call_ai_api = original_api
    bot.save_bot = original_save
    if original_last_used is None:
        bot.AI_LAST_MODEL_USED.pop(9001, None)
    else:
        bot.AI_LAST_MODEL_USED[9001] = original_last_used

print("AI Sentinel UI regression tests passed")
