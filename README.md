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

Multiple VPS workers can be registered under **Admin → Infrastructure Nodes**.
The panel shows live CPU-load, RAM, disk, Docker, and load-average data with
colored bars. **Refresh All Metrics** probes every enabled worker, while new
automatically placed bots prefer the healthy worker with the lowest measured
resource pressure. An explicit bot-to-node assignment remains authoritative.

For disaster recovery, use **Admin → Import / Export → Set Recovery Password**
and choose a stable password from 10 to 15 characters before selecting **Full
Recovery**. The `.cipher-backup` bundle is passphrase-protected and
contains the database, settings, encrypted key caches, uploaded bot state, and
sandbox state. Import it with **Import Recovery** after a clean deployment to
restore the panel as it was. The recovery secret is independent of `BOT_TOKEN`
and `OWNER_ID`, so rotating the bot token or moving to a new Telegram account
does not invalidate new bundles. Keep the passphrase in a password manager;
without it, an encrypted bundle cannot be opened.

New GitHub Vault snapshots use the same Admin-panel Recovery Password for
encryption. The GitHub token is used only for repository access. Set the same
Recovery Password in the new panel, configure the GitHub repository/token, and
restore snapshots without needing the old Telegram bot token. Existing legacy
snapshots made before password mode was enabled still require their original
`CIPHER_VAULT_KEY` or an earlier Full Recovery restore. The optional
`CIPHER_RECOVERY_PASSPHRASE` environment variable can provide the same password
for automated deployments.

### Firebase primary disaster recovery

Firebase Cloud Storage can be enabled as the primary automatic recovery
provider. The panel uploads an encrypted `.cipher-backup` object and then
atomically updates a small `LATEST.json` pointer. It retains the newest 30
snapshot objects by default, and the recovery password is never uploaded
separately. Firebase Storage requires the Firebase project to use the Blaze
plan. The Firebase Admin SDK uses a service account from either a deployment
secret or the encrypted Admin-panel configuration; never commit its JSON key
to this repository.

Set these deployment variables on the control-panel host:

```dotenv
FIREBASE_STORAGE_BUCKET=your-project.firebasestorage.app
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/firebase-service-account.json
CIPHER_RECOVERY_PASSPHRASE=your-10-to-15-character-password
```

`FIREBASE_SERVICE_ACCOUNT_JSON` may be used instead of
`GOOGLE_APPLICATION_CREDENTIALS` when the hosting provider only supports
secret text variables. On startup, Firebase is attempted first, but only when
the local database and state directories are empty. If Firebase is unavailable
or its integrity check fails, the existing GitHub restore is attempted. Manual
`.cipher-backup` import remains the final fallback. Firebase performs its first
automatic sync immediately at startup, then creates another snapshot every 30
minutes; GitHub continues its own configured backup schedule as a second
independent copy.

If environment variables are not available yet, open **Admin → Vault
Management → Configure Firebase** and send the complete service-account JSON,
then set the Storage bucket name. The JSON is encrypted with the panel's
existing keyring and is never echoed back. Deployment environment variables
take precedence over Admin-panel values. **Clear Firebase** removes only the
Admin-panel credential and bucket; it does not alter deployment secrets.
Use **Test Firebase Connection** to verify authenticated bucket access without
writing data, and **Firebase Sync Now** to create and publish an encrypted
snapshot immediately.

Firestore being enabled in the Firebase project does not automatically back up
this application's files. The implementation uses authenticated Cloud Storage
for the encrypted binary recovery bundle because it contains the database,
uploaded files, key cache, and sandbox state. Firestore can store other
application data, but the recovery password still protects the full bundle.

After an SSH VPS is registered and its host key and credentials are saved, the
owner can open **Admin → Infrastructure Nodes**, select the node, and press
**Prepare Docker**. The idempotent bootstrap uses the VPS package manager,
starts Docker, verifies the daemon, adds a non-root SSH user to the Docker
group when possible, and pulls the pinned `node:22-slim` and `python:3.11-slim`
runtime images. It requires root or passwordless sudo and supports apt, dnf,
and yum. For a non-root SSH account it elevates with `sudo -n su -c`; it never
uses an unverified `curl | sh` installer.
After a runtime image receives an upstream update, use the same node's
**Update Images** button to pull and verify the newest image digests for those
pinned tags. The refresh time is stored on the node and recorded in the audit
log; it does not switch to an unreviewed floating `latest` tag.

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

Native multi-user Node.js hosting is controlled from **Admin → Bot Config →
Sandbox → Native Node** and is OFF by default. When enabled, unapproved Node.js
bots may run on the host if Docker and a remote worker are unavailable. This is
not isolation; use the switch only when you accept that risk. Python and shell
workloads remain blocked in this mode. Docker Sandbox or a dedicated remote
worker remains the secure multi-user option.

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
