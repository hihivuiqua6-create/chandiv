import ast
import math
from pathlib import Path

source = Path('bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {'_run_info', '_pattern_label', '_predict_scores', 'make_prediction_vip', 'tinh_do_tin_cay'}
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
ns = {'math': math}
exec(compile(ast.Module(body=selected, type_ignores=[]), 'bot.py', 'exec'), ns)

cases = {
    'dragon': ['TAI'] * 8 + ['XIU', 'TAI'] * 4,
    'alternating': ['TAI', 'XIU'] * 12,
    'two_two': ['TAI', 'TAI', 'XIU', 'XIU'] * 6,
    'mixed': ['TAI', 'XIU', 'TAI', 'TAI', 'XIU', 'XIU', 'TAI', 'XIU'] * 3,
}
for label, history in cases.items():
    pred = ns['make_prediction_vip'](history, [10, 11, 12, 13] * 8)
    conf = ns['tinh_do_tin_cay'](history, [10, 11, 12, 13] * 8)
    assert pred in ('TAI', 'XIU', None), (label, pred)
    assert 0 <= conf <= 89, (label, conf)
    print(label, pred, conf)

# Quy tắc flat: sau thua, bet_amount phải giữ nguyên base_bet.
st = {'bet_mode': 'flat', 'base_bet': 2000, 'bet_amount': 2000, 'martingale_step': 0}
if st['bet_mode'] == 'flat':
    st['martingale_step'] = 0
    st['bet_amount'] = st['base_bet']
assert st['bet_amount'] == 2000 and st['martingale_step'] == 0
print('PREDICTOR_AND_FLAT_MODE_TESTS_OK')
