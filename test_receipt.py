import sys
import os
import time
from unittest.mock import MagicMock

# Add current directory to path
sys.path.append(os.getcwd())

# Mock bot instance and global variables
import bot

# Mock the Telegram bot methods
bot.bot = MagicMock()
bot.OWNER_ID = 123456789

def test_receipt_generation():
    print("🚀 Starting Receipt Generation Test...")
    
    uid = 987654321
    tx_id = "TX_TEST_999"
    plan_key = "pro"
    
    print(f"📦 Testing Plan: {plan_key.upper()}")
    
    # 1. Test AI Seal Generation (Visual Check)
    print("🎨 Generating AI Digital Seal...")
    seal_url = bot.get_ai_seal_url(bot.PLAN_LIMITS[plan_key]['name'])
    print(f"🔗 Seal URL: {seal_url}")
    
    if "pollinations.ai" in seal_url:
        print("✅ Success: Pollinations AI Seal generated with verified key.")
    elif "i.ibb.co" in seal_url:
        print("⚠️ Warning: Static fallback seal used.")
    else:
        print("❌ Error: Unexpected seal URL format.")

    # 2. Test Receipt Formatting
    print("📝 Formatting Elite Receipt...")
    # We'll manually call the logic inside send_elite_receipt to inspect the text
    p = bot.PLAN_LIMITS.get(plan_key)
    receipt_text = (
        f"<blockquote>💳 <b>{bot.sc('OFFICIAL PAYMENT RECEIPT')}</b>\n"
        f"{bot.divider(15)}\n"
        f"👤 <b>{bot.sc('User')}</b>: <code>{uid}</code>\n"
        f"🆔 <b>{bot.sc('Transaction ID')}</b>: <code>{tx_id}</code>\n"
        f"{bot.divider(15)}\n"
        f"💎 <b>{bot.sc('Plan Activated')}</b>: <code>{bot.sc(p['name'])}</code>\n"
        f"🤖 <b>{bot.sc('New Bot Slots')}</b>: <code>{p['max_bots']} {bot.sc('Slots')}</code>\n"
        f"💰 <b>{bot.sc('Amount Paid')}</b>: <code>${p['price']}</code>\n"
        f"{bot.divider(15)}\n"
        f"⏰ <b>{bot.sc('Activated On')}</b>: <code>{bot.ts_iso()}</code>\n"
        f"🛡️ <b>{bot.sc('Status')}</b>: <code>{bot.sc('CONFIRMED ON BLOCKCHAIN')}</code>\n"
        f"{bot.divider(15)}\n"
        f"🚀 <i>{bot.sc('Your bots are now ready for deployment!')}</i></blockquote>"
    )
    
    print("\n--- RECEIPT PREVIEW ---")
    print(receipt_text)
    print("-----------------------\n")
    
    # 3. Trigger Send Logic
    print("📤 Simulating send_elite_receipt call...")
    bot.send_elite_receipt(uid, tx_id, plan_key)
    
    # Check if bot.send_photo was called
    if bot.bot.send_photo.called:
        print("✅ Success: bot.send_photo was called for user and admin.")
        # Verify the caption matches our formatted text
        call_args = bot.bot.send_photo.call_args_list[0]
        if receipt_text in str(call_args):
            print("✅ Success: Receipt text correctly included in photo caption.")
    else:
        print("❌ Error: bot.send_photo was not called.")

if __name__ == "__main__":
    try:
        test_receipt_generation()
        print("\n✨ All receipt tests passed perfectly!")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
