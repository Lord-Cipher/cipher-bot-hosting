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

# The guided VPS flow accepts DNS/IPv4/IPv6 endpoints and never asks for a
# password or private key in its node-details JSON/text fields.
wizard_messages = []
health_cards = []
bot.admin_only_call = lambda call, action: True
bot.ack = lambda *args, **kwargs: None
bot.bot.send_message = lambda chat_id, text, **kwargs: wizard_messages.append((chat_id, str(text), kwargs))
bot.show_menu = lambda chat_id, photo, text, markup, call=None: wizard_messages.append((chat_id, str(text), {"markup": markup}))
bot.show_text = lambda chat_id, text, markup=None, call=None: health_cards.append((str(text), markup))
callback = SimpleNamespace(from_user=user, message=SimpleNamespace(chat=chat, message_id=500))
bot.action_adm_vps_wizard_start(callback)
assert bot.USER_STATES[uid]["step"] == "name"

def wizard_reply(text, message_id):
    message = SimpleNamespace(from_user=user, chat=chat, text=text, message_id=message_id)
    bot.on_text(message)

wizard_reply("IPv6 VPS", 501)
wizard_reply("[2001:db8::1234]", 502)
wizard_reply("22", 503)
wizard_reply("cipherbot", 504)
assert bot.USER_STATES[uid]["step"] == "auth"
auth_buttons = [b for row in wizard_messages[-1][2]["reply_markup"].keyboard for b in row]
assert {b.callback_data for b in auth_buttons} >= {"adm_vps_wizard_auth:password", "adm_vps_wizard_auth:key", "adm_vps_wizard_cancel"}
bot.action_adm_vps_wizard_auth(callback, "key")
assert uid not in bot.USER_STATES
vps_node = next(node for node in nodes.values() if node["name"] == "IPv6 VPS")
assert vps_node["connection_type"] == "ssh"
assert vps_node["ipv6"] == "2001:db8::1234" and vps_node["ssh_port"] == 22
assert vps_node["username"] == "cipherbot" and vps_node["auth_method"] == "key"
assert "password" not in vps_node and "private_key" not in vps_node
assert "node added" in wizard_messages[-1][1].lower()
credential_buttons = [b for row in wizard_messages[-1][2]["reply_markup"].keyboard for b in row]
assert any(b.callback_data == f"adm_node_cred:{vps_node['id']}" for b in credential_buttons)

# Node cards present visible success/danger health buttons, based on stored
# readiness, and the read-only probe updates the status asynchronously.
ready_node = bot.new_node("Ready VPS", "ssh", hostname="vps.example.test", username="cipherbot", auth_method="key")
ready_node.update(status="AUTHENTICATED", capabilities={"docker": True, "dockerVersion": "29.1.3"})
nodes[ready_node["id"]] = ready_node
bot.render_adm_nodes(callback)
node_markup = wizard_messages[-1][2]["markup"]
node_buttons = [b for row in node_markup.keyboard for b in row]
assert any(b.callback_data == f"adm_node_health:{ready_node['id']}" and b.style == "success" for b in node_buttons)
assert any(b.callback_data == f"adm_node_health:{vps_node['id']}" and b.style == "danger" for b in node_buttons)
bot.action_adm_node_health(callback, ready_node["id"])
assert "Ready VPS health" in health_cards[-1][0]
assert "READY" in health_cards[-1][0]

class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target
    def start(self):
        self.target()

original_thread = bot.threading.Thread
original_test_node = bot.test_node
bot.threading.Thread = ImmediateThread
bot.test_node = lambda node, secret="", timeout=8: {
    "state": "AUTHENTICATED", "capabilities": {"docker": True, "dockerVersion": "29.1.3"}
}
bot.action_adm_node_test(callback, vps_node["id"])
assert nodes[vps_node["id"]]["status"] == "AUTHENTICATED"
assert nodes[vps_node["id"]]["capabilities"]["docker"] is True
assert nodes[vps_node["id"]]["last_test"]
assert "READY" in health_cards[-1][0]
health_buttons = [b for row in health_cards[-1][1].keyboard for b in row]
assert any(b.style == "success" and b.callback_data == "noop" for b in health_buttons)
bot.threading.Thread = original_thread
bot.test_node = original_test_node

print("Node JSON and VPS wizard/health regressions passed")
