import ast
import math
from pathlib import Path

source = Path('bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {'_run_info', '_run_lengths', '_pattern_label', '_predict_scores', 'make_prediction_vip', 'tinh_do_tin_cay'}
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
ns = {'math': math}
exec(compile(ast.Module(body=selected, type_ignores=[]), 'bot.py', 'exec'), ns)

def seq(lengths):
    side = 'TAI'
    out = []
    for length in lengths:
        out.extend([side] * length)
        side = 'XIU' if side == 'TAI' else 'TAI'
    return out

cases = {
    '1-1': seq([1, 1, 1, 1, 1, 1]),
    '2-2': seq([2, 2, 2, 2]),
    '3-3': seq([3, 3, 3, 3]),
    '3-2-1': seq([3, 2, 1, 3]),
    '1-2-3': seq([1, 2, 3, 1]),
    '2-1-2': seq([2, 1, 2, 1]),
    '2-1-1-2': seq([2, 1, 1, 2]),
    '1-2-1-2': seq([1, 2, 1, 2]),
    'bet': seq([1, 1, 1, 5]),
}
for label, history in cases.items():
    pattern = ns['_pattern_label'](history)
    pred = ns['make_prediction_vip'](history, [10, 11, 12, 13] * 8)
    conf = ns['tinh_do_tin_cay'](history, [10, 11, 12, 13] * 8)
    assert isinstance(pattern, str), (label, pattern)
    assert pred in ('TAI', 'XIU', None), (label, pred)
    assert 0 <= conf <= 89, (label, conf)
    print(label, pattern, pred, conf)
print('PATTERN_TESTS_OK')
