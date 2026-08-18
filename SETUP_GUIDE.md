# 🚀 Cipher Tech Hosting Platform - Setup Guide

This platform is a professional Telegram bot hosting solution with automated CI/CD, security scanning, and backup features.

## 🛠️ Installation Steps

### Option A: One-Line VPS Installer (Fastest)
Run this command on any fresh VPS (Ubuntu, Debian, CentOS, Arch) to install everything automatically:
```bash
curl -sSL https://raw.githubusercontent.com/Lord-Cipher/cipher-bot-hosting/main/vps_installer.sh | bash
```

### Option B: Manual VPS Setup
1.  **Clone & Enter**:
    ```bash
    git clone https://github.com/Lord-Cipher/cipher-bot-hosting.git && cd cipher-bot-hosting
    ```
2.  **Run Setup**:
    ```bash
    chmod +x setup.sh && ./setup.sh
    ```
3.  **Configure & Start**:
    *   Edit `.env` with your tokens.
    *   Start: `source venv/bin/activate && python3 bot.py`

### Option B: Docker Deployment
For users who prefer containerization:
```bash
docker build -t cipher-host .
docker run -d --env-file .env -p 10000:10000 cipher-host
```

### Option C: PaaS (Railway / Render)
*   **Railway**: Connect this repo and add `BOT_TOKEN` and `OWNER_ID` to the **Variables** tab.
*   **Render**: Connect this repo, add variables, and set the build command to `pip install -r requirements.txt`.

### 3. Claim Admin Access
*   Once deployed, send `/start` to your bot on Telegram.
*   Because your `OWNER_ID` is set, the bot will automatically recognize you as the owner and open the **Admin Panel**.

## 🛡️ Key Features
*   **Elite AI Tiering**: Tiered AI intelligence (OpenRouter, Pollinations, DuckDuckGo) based on user subscription plans.
*   **AI Vision Verification**: Automated scanning of payment screenshots to verify transaction amounts.
*   **GitHub CI/CD**: Auto-deploy bots on every push (Requires `public_url` to be set in Admin Settings).
*   **Pro Security Scanner**: Shannon Entropy analysis to detect malware/backdoors.
*   **Vault Archiver**: Automatic unencrypted ZIP backups sent to a recovery bot.
*   **Package Installer**: Grid-based UI for installing common Telegram libraries.

## 🤖 AI Configuration
To enable elite AI features, add these to your environment variables:
*   `OPENROUTER_KEY`: Unlocks Frontier models (Poolside Laguna, Gemma 4) for Enterprise/Lifetime users.
*   `POLLINATIONS_KEY`: Unlocks High-Speed coding (Qwen 2.5) for Pro users.
*   `AI_API_DOMAIN`: (Optional) Point the bot to your own private AI gateway.

## 📁 File Structure
*   `bot.py`: Main application logic.
*   `security_scanner_free.py`: Advanced security scanning engine.
*   `storage/`: Contains all UI assets and the database (keep this folder!).

---
*Created by Cipher Tech*
