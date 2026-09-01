from pathlib import Path
import re

source = Path('bot.py').read_text(encoding='utf-8')
checks = {
    'admin_exact_comparison_present': 'message.chat.id != ADMIN_ID' in source,
    'auth_uses_chat_id_directly': bool(re.search(r'check_auth\(message\.chat\.id\)', source)),
    'key_activation_uses_chat_id': 'authorized_users[message.chat.id]' in source,
    'protected_login': 'def send_login(message):' in source and 'if not check_auth(message.chat.id)' in source,
    'protected_autobet': 'def send_autobet(message):' in source and 'if not check_auth(cid)' in source,
}
for name, value in checks.items():
    print(f'{name}={value}')
if checks['auth_uses_chat_id_directly'] or checks['key_activation_uses_chat_id']:
    raise SystemExit('AUTH_ID_SCOPE_ISSUE_FOUND')
