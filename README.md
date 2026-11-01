# Cipher Bot Hosting

Telegram bot hosting/control panel. The full VPS setup, IPv6 Sandbox worker configuration, credential handling, and Freestyle SSH host-key instructions are in [INSTALL_VPS.md](INSTALL_VPS.md).

## Deploy the control panel on Ubuntu/Debian

These commands assume a fresh VPS with `systemd`, a sudo-capable account, and outbound connectivity to Telegram. They create a dedicated service account. The bot needs Docker access for Sandbox workloads; Docker-group access is effectively root-level, so grant it only on a VPS you trust.

### 1. Install prerequisites and prepare the service account

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git docker.io openssh-client
sudo systemctl enable --now docker

sudo adduser --disabled-password --gecos "" cipherbot
sudo usermod -aG docker cipherbot
sudo mkdir -p /opt/cipher-bot
sudo chown cipherbot:cipherbot /opt/cipher-bot
```

### 2. Clone the latest main branch and install dependencies

```bash
sudo -u cipherbot git clone --depth 1 --branch main \
  https://github.com/Lord-Cipher/cipher-bot-hosting.git \
  /opt/cipher-bot

sudo -u cipherbot python3 -m venv /opt/cipher-bot/venv
sudo -u cipherbot /opt/cipher-bot/venv/bin/pip install --upgrade pip
sudo -u cipherbot /opt/cipher-bot/venv/bin/pip install \
  -r /opt/cipher-bot/requirements.txt
```

### 3. Configure secrets on the VPS

Create the private environment file and edit it **on the VPS**:

```bash
sudo -u cipherbot install -m 600 /dev/null /opt/cipher-bot/.env
sudo -u cipherbot nano /opt/cipher-bot/.env
```

Add these entries, replacing the placeholders locally:

```dotenv
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
OWNER_ID=YOUR_NUMERIC_TELEGRAM_USER_ID
FORCE_POLLING=true
```

Save in nano with **Ctrl+O**, press **Enter**, then exit with **Ctrl+X**. The
panel generates and persists its Fernet key automatically when encrypted
credentials or Cipher Vault are first configured. Keep `/opt/cipher-bot/storage`
on the VPS filesystem and preserve it across upgrades. Set `CIPHER_VAULT_KEY`
only when importing an existing encrypted vault. Never commit `.env` or send
secrets in chat.

### 4. Create and start the systemd service

```bash
sudo tee /etc/systemd/system/cipherbot.service >/dev/null <<'EOF'
[Unit]
Description=Cipher Bot Hosting Platform
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=cipherbot
SupplementaryGroups=docker
WorkingDirectory=/opt/cipher-bot
ExecStart=/opt/cipher-bot/venv/bin/python /opt/cipher-bot/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cipherbot
sudo systemctl status --no-pager cipherbot
```

View logs:

```bash
sudo journalctl -u cipherbot -n 100 --no-pager
sudo journalctl -u cipherbot -f
```

The bot uses Telegram polling; an inbound public port is generally not required. If this is an IPv6-only VPS, check that it can reach Telegram over IPv6:

```bash
curl -6 -sS -o /dev/null -w 'HTTP %{http_code} via %{remote_ip}\n' \
  --connect-timeout 8 --max-time 15 https://api.telegram.org/
```

For adding a separate VPS as an Infrastructure/Sandbox node—including the Freestyle `known_hosts` fix—follow [INSTALL_VPS.md](INSTALL_VPS.md).

## Node.js and WhatsApp bots

Node.js bots are supported alongside Python bots. A project should contain an
`index.js` (or another `.js` entry file) and, when it uses packages, a
`package.json`; `package-lock.json` is recommended. Trusted non-Sandbox runs
install dependencies with `npm ci` when a lockfile is present, otherwise
`npm install`. Sandboxed runs use the pinned `node:22-slim` image and install
packages in an isolated dependency volume, so npm does not need to be
installed on the control-panel host.

When Docker is unavailable, the panel can run an owner-owned or explicitly
approved bot directly with the host's Python/Node runtime. This is the mode for
Railway, Render, and Replit. It is not an isolation boundary: untrusted bots
remain blocked unless a Docker Sandbox or authenticated remote worker is
available. Railway builds include both Python and Node.js through
`nixpacks.toml`; Render and Replit should use their native runtimes.

WhatsApp clients need outbound network access. Keep the global Sandbox network
setting and the individual bot's **Sandbox Network** switch enabled only for
bots that need it. The container filesystem remains read-only, but
`/app/.cipher-runtime-data` is a dedicated writable persistent directory for
WhatsApp authentication/session files. In native host mode, the bot also
receives `CIPHER_RUNTIME_DATA` pointing to its equivalent writable directory.
Configure libraries such as Baileys or `whatsapp-web.js` to store their auth
state there; do not write credentials to the source tree. The directory
survives a container restart, but the current
remote worker workspace is under `/tmp/cipher-bots` and is removed on a fresh
deployment or a host cleanup/reboot. For long-lived WhatsApp sessions, attach
a persistent worker volume and move the worker workspace there before using
production accounts; otherwise expect to scan a new QR code after cleanup.

## Update

```bash
sudo -u cipherbot git -C /opt/cipher-bot pull --ff-only
sudo systemctl restart cipherbot
```
