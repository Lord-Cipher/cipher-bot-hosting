# 🛡️ VPS Deployment Guide (Systemd-Enabled Linux Environments)

Deploying on a standard Virtual Private Server (VPS) running Ubuntu or Debian gives you full kernel control, dedicated resources, and persistent background process management via `systemd`.

---

### Step 1: How to Check if Your VPS Has `systemd`
Before proceeding, verify whether your VPS uses `systemd` as its init system. Run this command in your VPS terminal:

```bash
ps -p 1 -o comm=
```

- **If the output is `systemd`**: Your VPS fully supports systemd services (Proceed with Step 2).
- **If the output is `init`, `openrc`, or anything else**: Your VPS uses a legacy init system; you will need to run the bot inside a `screen` or `tmux` session instead of a systemd service.

You can also check systemd status directly:
```bash
systemctl --version
```

---

### Step 2: System Setup & Dependencies
Log into your VPS via SSH as `root` (or a sudo-enabled user) and install Python 3 and pip:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl openssh-client
```

---

### Step 3: Clone and Setup the Repository
```bash
git clone https://github.com/Lord-Cipher/cipher-bot-hosting.git /opt/cipher-bot
cd /opt/cipher-bot

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 4: Configure Environment Variables
Create a `.env` file in `/opt/cipher-bot/.env`:

```env
BOT_TOKEN=your_main_bot_token_here
OWNER_ID=your_telegram_user_id_here
FORCE_POLLING=true
```

---

### Step 5: Create a Systemd Service (For Background Persistence)
Create a systemd service file to ensure your bot starts automatically on boot and restarts if it crashes:

```bash
sudo nano /etc/systemd/system/cipherbot.service
```

Paste the following configuration (adjust paths and user if necessary):

```ini
[Unit]
Description=Lord Cipher Bot Hosting Platform
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cipher-bot
ExecStart=/opt/cipher-bot/venv/bin/python3 /opt/cipher-bot/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

### Step 6: Enable and Start the Service
Reload systemd, enable the service to start on boot, and start it immediately:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cipherbot
sudo systemctl start cipherbot
```

### Step 7: Monitor Logs & Status
To check if your bot is running smoothly:
```bash
sudo systemctl status cipherbot
```

To view live real-time logs:
```bash
sudo journalctl -u cipherbot -f
```


---

## Connecting an IPv6 VPS as a remote Sandbox worker

These steps are for adding a **separate VPS as a worker node** from the running Telegram control panel. They are different from installing the control panel itself above. The control panel initiates SSH **outbound** to the worker's IPv6 address; the worker must accept SSH **inbound** from the control panel.

### 1. Prepare the worker VPS

On an Ubuntu/Debian worker, install SSH and Docker:

```bash
sudo apt update
sudo apt install -y openssh-server docker.io
sudo systemctl enable --now ssh docker
```

Use a dedicated non-root SSH account. It needs Docker access to launch the isolated worker containers; membership in the `docker` group is effectively root-level access to that VPS, so only grant it to a trusted control panel:

```bash
sudo adduser --disabled-password --gecos "" cipherbot
sudo usermod -aG docker cipherbot
sudo install -d -m 700 -o cipherbot -g cipherbot /home/cipherbot/.ssh
```

Create a dedicated key on a trusted machine with `ssh-keygen -t ed25519 -f ~/.ssh/cipher-sandbox -N '' -C cipher-sandbox`. The current panel accepts unencrypted Ed25519, ECDSA, and RSA private keys; it does not accept a passphrase-protected private key. Keep the private key private and use it only for this worker.

Add the one-line public key from `~/.ssh/cipher-sandbox.pub` to `/home/cipherbot/.ssh/authorized_keys`, then set permissions:

```bash
echo 'ssh-ed25519 AAAA... cipher-sandbox' | sudo tee -a /home/cipherbot/.ssh/authorized_keys >/dev/null
sudo chown cipherbot:cipherbot /home/cipherbot/.ssh/authorized_keys
sudo chmod 600 /home/cipherbot/.ssh/authorized_keys
```

Ensure `sshd` listens on IPv6 and your provider firewall/UFW permits TCP 22 from the control panel. If UFW is used, confirm IPv6 is enabled in `/etc/default/ufw` (`IPV6=yes`) before enabling the firewall. Prefer limiting port 22 to the control panel's known source address. Do not open port 10460 for Sandbox SSH; it is not used for this connection.

The control panel's SSH client checks host keys. On the **machine running the control panel**, add the worker's SSH host key to that service user's `~/.ssh/known_hosts`. First compare the worker's fingerprint on its console (`sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) with the fingerprint from this scan; do not trust an unverified `ssh-keyscan` result. Then, for example:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keyscan -6 -p 22 -t ed25519 2001:db8::1234 > /tmp/worker-hostkey
ssh-keygen -lf /tmp/worker-hostkey
# Only after the fingerprint matches the worker console:
cat /tmp/worker-hostkey >> ~/.ssh/known_hosts
rm -f /tmp/worker-hostkey
chmod 600 ~/.ssh/known_hosts
```

Replace the documentation-only IPv6 address with your worker's real global IPv6. If the control panel runs as a systemd service, use the home directory of its configured `User=` (the original guide uses `root`, so that would be `/root/.ssh/known_hosts`). The controller host itself must have a working IPv6 route to the worker; having IPv6 only on the worker is not enough.

### 2. Configure encrypted credential storage on the control panel

The panel needs a stable Fernet key before it can save SSH credentials. First check whether `CIPHER_VAULT_KEY` is already set or whether the panel has created its Vault-managed runtime key. Reuse the active key; do not replace it. Only if neither exists, generate a key once:

```bash
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Set the generated result as `CIPHER_VAULT_KEY` in the **control panel's** environment or `.env`, then restart the panel. Keep the active key backed up and unchanged: changing or losing it makes previously stored node credentials unreadable. Do not paste it into Telegram or commit `.env` to Git.

### 3. Register and test the node in Telegram

As the panel owner, open **Admin → Settings → Infrastructure Nodes → Add Node**. Send exactly one valid JSON object—only the text inside the braces, with no Markdown code fences or labels before/after it. For a direct IPv6 connection, enter the address as a plain, unbracketed string:

```json
{
  "name": "My IPv6 VPS",
  "connection_type": "ssh",
  "provider": "self-hosted",
  "ipv6": "2001:db8::1234",
  "ssh_port": 22,
  "username": "cipherbot",
  "auth_method": "key",
  "enabled": true
}
```

If the worker is a **Freestyle VM reached through its SSH gateway**, use `hostname` instead of `ipv6`. Replace the username placeholder with the Freestyle VM ID:

```json
{
  "name": "My Freestyle VM",
  "connection_type": "ssh",
  "provider": "freestyle",
  "hostname": "beta-ssh.freestyle.sh",
  "ssh_port": 22,
  "username": "YOUR_FREESTYLE_VM_ID",
  "auth_method": "password",
  "enabled": true
}
```

Do not put a password, access token, or private key in the JSON. After adding the node, open its **Credentials** button and submit the SSH credential separately: the Freestyle access token for `auth_method: "password"`, or the complete unencrypted private key for `auth_method: "key"`. The credential is encrypted at rest and the submitted message is deleted after capture. Then press **Test**. A successful SSH check shows `AUTHENTICATED`; verify Docker is listed in the detected capabilities too. The control-panel service user must also trust the SSH host key in its `known_hosts` file; do not disable host-key validation.

#### Trust Freestyle's SSH gateway from the control-panel VPS

If the health check reports `Server 'beta-ssh.freestyle.sh' not found in known_hosts`, SSH authentication has not started yet: the panel's service account does not trust the gateway host key. Freestyle's documented SSH gateway is `beta-ssh.freestyle.sh` ([Freestyle SSH docs](https://www.freestyle.sh/docs/vms/ssh)). The docs do not publish its host-key fingerprint, so verify the fingerprint with Freestyle through an independent trusted channel before adding it. **Do not trust an unverified `ssh-keyscan` result.**

Run these commands on the control-panel VPS. The example assumes the systemd service runs as `cipherbot`; use the actual `User=` from `/etc/systemd/system/cipherbot.service` if different:

```bash
sudo -u cipherbot mkdir -p /home/cipherbot/.ssh
sudo chmod 700 /home/cipherbot/.ssh

ssh-keyscan -p 22 beta-ssh.freestyle.sh > /tmp/freestyle-hostkey
ssh-keygen -lf /tmp/freestyle-hostkey
```

Compare **each displayed fingerprint** with Freestyle's independently verified value. Only after it matches, install the key for the service user and restart the panel:

```bash
sudo sh -c 'cat /tmp/freestyle-hostkey >> /home/cipherbot/.ssh/known_hosts'
sudo chown cipherbot:cipherbot /home/cipherbot/.ssh/known_hosts
sudo chmod 600 /home/cipherbot/.ssh/known_hosts
rm -f /tmp/freestyle-hostkey

sudo systemctl restart cipherbot
```

Then press **Test** again in Infrastructure Nodes. If `User=` is not `cipherbot`, put `known_hosts` in that service user's home directory with matching ownership and permissions. Do not disable strict host-key checking.

Turn **Admin → Bot Config → Sandbox → Network** on only if workloads are allowed internet access. Sandbox starts blocked by default per bot: open the bot's action menu and toggle **Sandbox Network** for that bot. Both the global admin switch and per-bot switch must be on before a hosted bot container receives network access.

Finally, turn **Admin → Sandbox** on, open the bot's **Node** menu, select this VPS, and start/restart the bot. Python and Node dependencies are installed in a constrained Docker job during remote deployment; the running user container stays network-isolated unless both network controls above allow it.

### IPv6 connectivity checks

Run these checks from the **control panel host** to test the actual SSH path, and from the **worker** to test its outbound path:

```bash
# On the control panel host: DNS/IPv6 route to the Telegram API (for the panel itself)
curl -6 -sS -o /dev/null -w 'HTTP %{http_code} via %{remote_ip}\n' --connect-timeout 8 --max-time 15 https://api.telegram.org/

# On the worker: can the SSH account use Docker and pull the runtimes?
sudo -u cipherbot docker run --rm python:3.11-slim python --version
sudo -u cipherbot docker run --rm node:22-slim node --version
```

For an IPv6-only worker whose hosted bots need outbound IPv6, also test IPv6 **inside Docker's bridge network**; host IPv6 does not automatically guarantee container IPv6. If that fails, enable Docker bridge IPv6 using the [official Docker IPv6 guide](https://docs.docker.com/engine/daemon/ipv6/) and a valid subnet suitable for your provider; do not copy the guide's documentation-only `2001:db8::` example. If the control panel cannot reach the worker's IPv6 address, or Docker cannot pull images/reach registries, the VPS provider must supply working IPv6 routing (or IPv4 egress/NAT64) before this node can run workloads.
