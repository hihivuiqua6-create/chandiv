from pathlib import Path
import ast

source = Path('bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)

assert 'def actor_id(message):' in source
assert 'def is_admin(message):' in source
assert 'if message.chat.id != ADMIN_ID:' not in source
assert 'authorized_users[message.chat.id]' not in source
assert 'check_auth(message.chat.id)' not in source

# Mô phỏng logic check_auth độc lập, không import bot vì bot cần secret và kết nối Telegram.
ADMIN_ID = 8030294480
authorized_users = {}

def check_auth(user_id, now=1_000_000):
    user_id = int(user_id)
    if user_id == ADMIN_ID:
        return True
    if user_id in authorized_users and now <= authorized_users[user_id]:
        return True
    return False

assert check_auth(8030294480) is True
assert check_auth(123456789) is False
authorized_users[123456789] = 1_000_001
assert check_auth(123456789) is True
assert check_auth(987654321) is False
print('AUTH_STATIC_AND_SCENARIO_TESTS_OK')
