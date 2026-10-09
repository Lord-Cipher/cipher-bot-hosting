"""Offline regression checks for Telegram Add/Edit Node JSON input."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_node_json_flow")
os.environ.setdefault("OWNER_ID", "9001")

import bot  # noqa: E402

uid = int(os.environ["OWNER_ID"])
chat = SimpleNamespace(id=uid, type="private")
user = SimpleNamespace(id=uid, username="node-admin", first_name="Node Admin")
nodes = {}
replies = []

# Isolate every test from storage, rate limits, verification, and Telegram APIs.
bot._is_private = lambda message: True
bot.banned_block = lambda message: False
bot.RATE = SimpleNamespace(allow=lambda user_id: True)
bot.UPLOAD_RATE = SimpleNamespace(allow=lambda user_id: True)
bot.SCAN_RATE = SimpleNamespace(allow=lambda user_id: True)
bot.require_verified = lambda chat_id, user_id: True
bot.require_group_membership = lambda chat_id, user_id: True
bot.maintenance_block = lambda user_id: False
bot.get_or_create_user = lambda telegram_user: None
bot._trace_event_log = lambda message: None
bot._sync_kernel_uplink = lambda message: None
bot._nodes_load = lambda: {key: dict(value) for key, value in nodes.items()}


def save_nodes(updated):
    nodes.clear()
    nodes.update({key: dict(value) for key, value in updated.items()})


bot._nodes_save = save_nodes
bot.audit = lambda *args, **kwargs: None
bot.bot.reply_to = lambda message, text, **kwargs: replies.append(str(text))
bot.bot.get_file = lambda file_id: SimpleNamespace(file_path="node.json")

text_config = {
    "name": "Text VPS",
    "connection_type": "ssh",
    "provider": "self-hosted",
    "hostname": "vps.example.test",
    "ssh_port": 22,
    "username": "cipherbot",
    "auth_method": "password",
    "enabled": True,
}

# Pasted ordinary text reaches the Add Node state and saves a normalized node.
bot.USER_STATES[uid] = {"flow": "await_adm_node_add"}
text_message = SimpleNamespace(
    from_user=user, chat=chat, text=json.dumps(text_config), message_id=101,
)
bot.on_text(text_message)
assert len(nodes) == 1
assert next(iter(nodes.values()))["hostname"] == "vps.example.test"
assert "Node added" in replies[-1]
assert uid not in bot.USER_STATES

# A Telegram .json document is downloaded and handled through the same flow.
file_config = {**text_config, "name": "File VPS", "hostname": "vps2.example.test"}
raw_json = json.dumps(file_config).encode("utf-8")
bot.bot.download_file = lambda file_path: raw_json
bot.USER_STATES[uid] = {"flow": "await_adm_node_add"}
document = SimpleNamespace(
    file_id="fake-document-id",
    file_name="node.json",
    file_size=len(raw_json),
    mime_type="application/json",
)
document_message = SimpleNamespace(
    from_user=user, chat=chat, document=document, text=None, message_id=102,
)
bot.on_document(document_message)
assert len(nodes) == 2
assert any(node["hostname"] == "vps2.example.test" for node in nodes.values())
assert "Node added" in replies[-1]

# Invalid input reports a useful error and leaves the form open for retry.
bot.USER_STATES[uid] = {"flow": "await_adm_node_add"}
invalid_message = SimpleNamespace(
    from_user=user, chat=chat, text="{not-json", message_id=103,
)
bot.on_text(invalid_message)
assert "Invalid JSON syntax" in replies[-1]
assert uid in bot.USER_STATES
assert len(nodes) == 2

# Credentials must not be accepted in the public node configuration object.
secret_payload = {**text_config, "password": "test-only-not-a-real-secret"}
bot.USER_STATES[uid] = {"flow": "await_adm_node_add"}
secret_message = SimpleNamespace(
    from_user=user, chat=chat, text=json.dumps(secret_payload), message_id=104,
)
bot.on_text(secret_message)
assert "Do not put passwords" in replies[-1]
assert "test-only-not-a-real-secret" not in replies[-1]
assert len(nodes) == 2

# If a restart cleared the transient setup state, pasted node JSON gets a
# specific instruction rather than silently falling through to AI chat.
bot.USER_STATES.pop(uid, None)
fenced_message = SimpleNamespace(
    from_user=user,
    chat=chat,
    text="```json\n" + json.dumps(text_config) + "\n```",
    message_id=105,
)
bot.on_text(fenced_message)
assert "No Add/Edit Node step is active" in replies[-1]
assert len(nodes) == 2

print("Node JSON flow regression tests passed")
