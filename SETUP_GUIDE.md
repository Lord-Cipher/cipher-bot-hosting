# 🚀 Cipher Tech Hosting Platform - Setup Guide

This platform is a professional Telegram bot hosting solution with automated CI/CD, security scanning, and backup features.

## 🛠️ Installation Steps

### 1. Deploy to Railway
*   Connect this repository to your **Railway.app** account.
*   The platform uses the provided `Procfile` and `railway.json` for automatic configuration.

### 2. Configure Environment Variables
Add the following variables in the **Variables** tab of your Railway project:
*   `BOT_TOKEN`: Your main bot token from [@BotFather](https://t.me/BotFather).
*   `OWNER_ID`: Your Telegram User ID (to grant you Admin access).

### 3. Claim Admin Access
*   Once deployed, send `/start` to your bot on Telegram.
*   Because your `OWNER_ID` is set, the bot will automatically recognize you as the owner and open the **Admin Panel**.

## 🛡️ Key Features
*   **GitHub CI/CD**: Auto-deploy bots on every push (Requires `public_url` to be set in Admin Settings).
*   **Pro Security Scanner**: Shannon Entropy analysis to detect malware/backdoors.
*   **Vault Archiver**: Automatic unencrypted ZIP backups sent to a recovery bot.
*   **Package Installer**: Grid-based UI for installing common Telegram libraries.

## 📁 File Structure
*   `bot.py`: Main application logic.
*   `security_scanner_free.py`: Advanced security scanning engine.
*   `storage/`: Contains all UI assets and the database (keep this folder!).

---
*Created by Cipher Tech*
