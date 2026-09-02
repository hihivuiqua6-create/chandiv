import ast
import re
from pathlib import Path

source = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
assert 'AUTO_BET_REQUIRE_CONFIDENCE = False' in source
assert 'not AUTO_BET_REQUIRE_CONFIDENCE or dt >= MIN_CONFIDENCE_AUTO_BET' in source
assert "'always_bet': True" in source
names = {
    '_to_str', '_dao', '_kq', '_runs', '_pattern_prediction', 'nhan_dien_cau', 'tong_hop_cau',
    'extract_cau_features', 'adaptive_predict', 'make_prediction_vip',
    'walk_forward_evaluate', 'tinh_do_tin_cay'
}
selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
ns = {'re': re, 'adaptive_model': {}}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(Path('bot.py')), 'exec'), ns)

for history in [
    ['TAI', 'XIU', 'TAI', 'XIU', 'TAI', 'XIU'],
    ['TAI'] * 8 + ['XIU'] * 3,
    ['XIU', 'TAI', 'TAI', 'XIU', 'XIU', 'TAI'],
]:
    pred = ns['make_prediction_vip'](history, [11] * len(history))
    assert pred in ('TAI', 'XIU'), (history, pred)
    assert 50 <= ns['tinh_do_tin_cay'](history, []) <= 65

history = ['TAI', 'XIU'] * 20
result = ns['walk_forward_evaluate'](history, [11] * len(history), min_history=12)
assert result['samples'] == len(history) - 12
assert 0 <= result['correct'] <= result['samples']
assert 0 <= result['accuracy'] <= 1

print('OK: predictor smoke tests and walk-forward evaluation passed')
print(result)
