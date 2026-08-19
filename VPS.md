# 🛡️ ᶜᴵᴾᴴᴱᴿ ᵀᴱᶜᴴ ᴴᴼˢᵀ v2.1 — VPS DEPLOYMENT MANUAL

> **Author / Creator:** 👾 𓆩𖣂𝙻𝙾𝚁𝙳 𝙲𝙸𝙿𝙷𝙴𝚁𖣂𓅓  
> **Platform:** Cipher Bot Hosting Infrastructure v2.1  
> **Environment:** Debian / Ubuntu Linux VPS (Zero-Config / Auto-Stability)

---

## ⚡ OVERVIEW & ZERO-CONFIG ARCHITECTURE

**ᶜᴵᴾᴴᴱᴿ ᵀᴱᶜᴴ ᴴᴼˢᵀ** is engineered with a **Zero-Config Self-Healing Hybrid Architecture**. 
- **No Domain Required to Start**: If no public URL or webhook domain is configured, the bot **automatically defaults to High-Stability Long Polling**. It will never crash or go offline due to missing networking flags.
- **Dynamic Webhook Migration**: Once your VPS has Nginx, Apache, or a public IP/domain configured, you can instantly activate high-speed webhook mode directly from the **Admin Panel > 🌐 Public URL** without restarting the process.

---

## 🛠️ STEP-BY-STEP VPS DEPLOYMENT GUIDE

### 1. System Prerequisites & Update
Connect to your VPS via SSH and ensure your package manager is up to date:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl build-essential
```

### 2. Clone the Repository
Clone your official repository into your working directory:
```bash
git clone https://github.com/Lord-Cipher/cipher-bot-hosting.git
cd cipher-bot-hosting
```

### 3. Create Python Virtual Environment
Isolate dependencies within a clean virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create your secure `.env` configuration file in the root directory:
```bash
nano .env
```
Paste your production credentials into the file:
```env
BOT_TOKEN=your_telegram_bot_token_here
OWNER_ID=your_telegram_user_id_here
OXAPAY_API_KEY=your_oxapay_api_key_here
ANNOUNCE_CHANNEL=your_channel_username_or_id
PORT=10000
```
*(Save and exit with `CTRL+O`, `Enter`, then `CTRL+X`)*

---

## 🚀 RUNNING AS A SYSTEMD BACKGROUND SERVICE (24/7 UPTIME)

To ensure the bot restarts automatically on server reboots and runs continuously in the background, create a dedicated systemd service:

### 1. Create Service File
```bash
sudo nano /etc/systemd/system/cipherhost.service
```

### 2. Paste Configuration
```ini
[Unit]
Description=Cipher Tech Host v2.1 Bot & Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/cipher-bot-hosting
ExecStart=/root/cipher-bot-hosting/venv/bin/python /root/cipher-bot-hosting/bot.py
Restart=always
RestartSec=10
EnvironmentFile=/root/cipher-bot-hosting/.env

[Install]
WantedBy=multi-user.target
```
*(Note: If your working directory is `/home/ubuntu/cipher-bot-hosting`, adjust the paths accordingly).*

### 3. Enable and Start Service
```bash
sudo systemctl daemon-reload
sudo systemctl enable cipherhost
sudo systemctl start cipherhost
```

### 4. Check Status & Live Logs
To verify that the panel is online and running smoothly:
```bash
sudo systemctl status cipherhost
journalctl -u cipherhost -f
```

---

## 🌐 MIGRATING TO WEBHOOK MODE (OPTIONAL)

If you want to run your VPS behind a domain with SSL (e.g., via Nginx reverse proxy pointing to port `10000`):
1. Open your Telegram bot.
2. Navigate to **Admin Panel > 🌐 Public URL**.
3. Send your domain (e.g., `https://your-domain.com`).
4. The bot will instantly save the URL and link the Telegram webhook live with zero downtime.

---
*“Obey the system. Respect the hierarchy.”*  
**ᶜᴵᴾᴴᴱᴿ ᵀᴱᶜᴴ ᴴᴼˢᵀ v2.1**
