from pathlib import Path

path = Path('bot.py')
source = path.read_text(encoding='utf-8')

source = source.replace("'martingale_step': 0, 'max_martingale_steps': MAX_MARTINGALE_STEPS,", "'martingale_step': 0, 'max_martingale_steps': MAX_MARTINGALE_STEPS,\n            'bet_mode': 'martingale',  # martingale hoặc flat")
source = source.replace('def fetch_history_from_api(limit=50):', 'def fetch_history_from_api(limit=MAX_HISTORY_STORE):')
source = source.replace("lk, ld = fetch_history_from_api(50)", "lk, ld = fetch_history_from_api(MAX_HISTORY_STORE)")

start = source.index('def make_prediction_vip(')
end = source.index('\n# 🚀 TÍNH NĂNG NÂNG CẤP X2 (MARTINGALE)', start)

new_predictor = r'''def _run_info(history):
    if not history:
        return None, 0
    last = history[-1]
    run = 1
    for item in reversed(history[:-1]):
        if item == last:
            run += 1
        else:
            break
    return last, run


def _pattern_label(history):
    """Nhận diện cầu gần nhất theo chu kỳ, không tự coi mẫu là chắc thắng."""
    if len(history) < 4:
        return 'THIEU_DU_LIEU'
    tail = history[-12:]
    symbols = ''.join('T' if x == 'TAI' else 'X' for x in tail)
    last, run = _run_info(history)
    if run >= 3:
        return 'CAU_RONG'
    if len(symbols) >= 6 and all(symbols[i] != symbols[i - 1] for i in range(1, len(symbols))):
        return 'CAU_1_1'
    for block in (2, 3, 4, 5):
        if len(history) >= block * 2:
            a = history[-block:]
            b = history[-2 * block:-block]
            if a == b:
                return f'CAU_{block}_{block}'
    return 'HON_HOP'


def _predict_scores(history, points=None):
    """Ensemble điểm hóa: tần suất, chuyển tiếp Markov, khớp hậu tố và cầu."""
    points = points or []
    clean = [x for x in history if x in ('TAI', 'XIU')]
    if not clean:
        return {'TAI': 0.0, 'XIU': 0.0, 'pattern': 'THIEU_DU_LIEU'}

    scores = {'TAI': 0.0, 'XIU': 0.0}
    last = clean[-1]
    pattern = _pattern_label(clean)

    # Tần suất theo nhiều cửa sổ, cửa sổ ngắn hơn có trọng số lớn hơn.
    for window, weight in ((5, 0.8), (10, 0.65), (20, 0.45), (40, 0.25)):
        sample = clean[-window:]
        if not sample:
            continue
        for outcome in ('TAI', 'XIU'):
            scores[outcome] += sample.count(outcome) / len(sample) * weight

    # Chuyển tiếp Markov bậc 1-3, có shrinkage để mẫu ít không làm lệch mạnh.
    for order, weight in ((1, 1.0), (2, 1.25), (3, 1.45)):
        if len(clean) <= order:
            continue
        key = tuple(clean[-order:])
        counts = {'TAI': 0, 'XIU': 0}
        total = 0
        for i in range(order, len(clean)):
            if tuple(clean[i - order:i]) == key:
                counts[clean[i]] += 1
                total += 1
        if total:
            support = min(total / 8.0, 1.0)
            for outcome in ('TAI', 'XIU'):
                scores[outcome] += (counts[outcome] / total) * weight * support

    # Khớp các hậu tố trong toàn bộ lịch sử; ưu tiên mẫu dài và mẫu xuất hiện gần đây.
    for order, weight in ((2, 0.7), (3, 0.9), (4, 1.1), (5, 1.25)):
        if len(clean) <= order:
            continue
        suffix = tuple(clean[-order:])
        votes = {'TAI': 0.0, 'XIU': 0.0}
        occurrences = 0
        for i in range(order, len(clean)):
            if tuple(clean[i - order:i]) == suffix:
                age_weight = 1.0 + (i / max(len(clean), 1))
                votes[clean[i]] += age_weight
                occurrences += 1
        if occurrences:
            total = votes['TAI'] + votes['XIU']
            support = min(occurrences / 5.0, 1.0)
            for outcome in ('TAI', 'XIU'):
                scores[outcome] += votes[outcome] / total * weight * support

    # Cầu chỉ là tín hiệu phụ, tránh các quy tắc regex cứng lấn át dữ liệu.
    pattern_bonus = {
        'CAU_RONG': (last, 0.65),
        'CAU_1_1': ('XIU' if last == 'TAI' else 'TAI', 0.55),
        'CAU_2_2': (last, 0.45),
        'CAU_3_3': (last, 0.40),
        'CAU_4_4': (last, 0.35),
        'CAU_5_5': (last, 0.30),
    }
    if pattern in pattern_bonus:
        outcome, bonus = pattern_bonus[pattern]
        scores[outcome] += bonus

    # Điểm tổng chỉ hỗ trợ nhẹ; không dùng ngưỡng tùy tiện để kết luận chắc chắn.
    if len(points) >= 8:
        recent_points = [float(x) for x in points[-12:] if isinstance(x, (int, float))]
        if recent_points:
            avg = sum(recent_points) / len(recent_points)
            if avg >= 11.5:
                scores['TAI'] += 0.22
            elif avg <= 9.5:
                scores['XIU'] += 0.22

    return {**scores, 'pattern': pattern}


def make_prediction_vip(history, points=None):
    clean = [x for x in history if x in ('TAI', 'XIU')]
    if len(clean) < 5:
        return None
    scores = _predict_scores(clean, points)
    if scores['TAI'] == scores['XIU']:
        return None
    return 'TAI' if scores['TAI'] > scores['XIU'] else 'XIU'


def tinh_do_tin_cay(history, points=None):
    clean = [x for x in history if x in ('TAI', 'XIU')]
    if len(clean) < 5:
        return 0
    scores = _predict_scores(clean, points)
    total = scores['TAI'] + scores['XIU']
    if total <= 0:
        return 0
    margin = abs(scores['TAI'] - scores['XIU']) / total
    # Confidence là độ đồng thuận của tín hiệu, không phải xác suất thắng.
    support = min(len(clean) / 40.0, 1.0)
    confidence = 50 + margin * 45 * (0.55 + 0.45 * support)
    return max(50, min(89, int(confidence)))
'''

source = source[:start] + new_predictor + source[end:]

old_loss = """        # Gấp thếp tối đa 3 lần: 1x -> 2x -> 4x -> 8x.
        # Nếu thua ở mức 8x, phiên sau quay về 1x thay vì tăng lên 16x.
        if st['martingale_step'] < st.get('max_martingale_steps', MAX_MARTINGALE_STEPS):
            st['martingale_step'] += 1
            st['bet_amount'] = int(st['base_bet'] * (2 ** st['martingale_step']))
        else:
            st['martingale_step'] = 0
            st['bet_amount'] = st['base_bet']
"""
new_loss = """        if st.get('bet_mode', 'martingale') == 'flat':
            # Chế độ cược đều: thua không làm tăng tiền và luôn giữ vốn gốc.
            st['martingale_step'] = 0
            st['bet_amount'] = st['base_bet']
        else:
            # Gấp thếp tối đa 3 lần: 1x -> 2x -> 4x -> 8x.
            # Nếu thua ở mức 8x, phiên sau quay về 1x thay vì tăng lên 16x.
            if st['martingale_step'] < st.get('max_martingale_steps', MAX_MARTINGALE_STEPS):
                st['martingale_step'] += 1
                st['bet_amount'] = int(st['base_bet'] * (2 ** st['martingale_step']))
            else:
                st['martingale_step'] = 0
                st['bet_amount'] = st['base_bet']
"""
if old_loss not in source:
    raise SystemExit('loss block not found')
source = source.replace(old_loss, new_loss, 1)

path.write_text(source, encoding='utf-8')
