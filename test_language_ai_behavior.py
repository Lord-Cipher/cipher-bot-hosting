import os
os.environ.setdefault('BOT_TOKEN', '123456789:AA_test_language_ai_behavior')
os.environ.setdefault('OWNER_ID', '1')
import bot

assert bot._is_lord_cipher_profile_request('brag about me and my skills')
assert bot._is_lord_cipher_profile_request('tell me about Lord Cipher')
assert not bot._is_lord_cipher_profile_request('explain Python decorators')
assert bot._build_ai_request('explain Python decorators') == 'explain Python decorators'
profile_prompt = bot._build_ai_request('brag about me')
assert 'roughly 500-800 words' in profile_prompt
assert 'do not invent private facts' in profile_prompt
source = open('bot.py', encoding='utf-8').read()
assert 'adm_languages_back' in source
assert 'callback_data="adm_languages_back"' in source
assert 'callback_data="menu_language"' not in source
print('language and opt-in AI behavior checks passed')
