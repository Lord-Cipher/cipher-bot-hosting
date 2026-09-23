import os
import re

os.environ.setdefault('BOT_TOKEN', '123456789:AA_test_token_for_gift_receipts')
os.environ.setdefault('OWNER_ID', '1')

import bot

store = {'users': {'10': {'ref_credit': 30}, '20': {'ref_credit': 5}}}
bot.db_load = lambda: store
bot.db_save = lambda value: None
bot.audit = lambda *args, **kwargs: None

ok, _, gift = bot._create_referral_gift(10, None, 20)
assert ok and gift
sender_receipt = bot._referral_gift_receipt(gift, store['users']['10']['ref_credit'])
assert 'GIFT' in sender_receipt
assert '20' in sender_receipt
assert '10' in sender_receipt
assert 'Amount Gifted' in sender_receipt

ok, _ = bot._claim_referral_gift(20, gift['id'])
assert ok
recipient_receipt = bot._referral_gift_receipt(gift, store['users']['20']['ref_credit'], claimed=True)
assert re.search(r'GIFT[A-F0-9]{6}', recipient_receipt)
assert 'Amount Redeemed' in recipient_receipt
assert '25' in recipient_receipt
assert 'Remaining Referrals' in recipient_receipt
print('gift receipt runtime checks passed')
