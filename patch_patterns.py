from pathlib import Path

path = Path('bot.py')
s = path.read_text(encoding='utf-8')
s = s.replace("'history': [], 'points_history': [],", "'history': [], 'points_history': [], 'dice_history': [],")
s = s.replace("ketqua, diem = [], []", "ketqua, diem, xucsac = [], [], []")
s = s.replace("                ketqua.append(res)\n                diem.append(tong)\n        return ketqua, diem", "                ketqua.append(res)\n                diem.append(tong)\n                dices = p.get('dices', [None, None, None])\n                xucsac.append(dices if isinstance(dices, list) and len(dices) == 3 else [None, None, None])\n        return ketqua, diem, xucsac")
s = s.replace("        return [], []", "        return [], [], []", 1)
s = s.replace("lk, ld = fetch_history_from_api(MAX_HISTORY_STORE)", "lk, ld, lx = fetch_history_from_api(MAX_HISTORY_STORE)")
s = s.replace("            st['points_history'] = ld[-MAX_HISTORY_STORE:]\n            tb", "            st['points_history'] = ld[-MAX_HISTORY_STORE:]\n            st['dice_history'] = lx[-MAX_HISTORY_STORE:]\n            tb")

start = s.index('def _pattern_label(history):')
end = s.index('\n\ndef _predict_scores', start)
new = r'''def _run_lengths(history):
    """Nén T/X thành các nhịp liên tiếp, ví dụ TTXT -> [('TAI',2),...]."""
    result = []
    for item in history:
        if not result or result[-1][0] != item:
            result.append([item, 1])
        else:
            result[-1][1] += 1
    return [(side, length) for side, length in result]


def _pattern_label(history):
    """Nhận diện nhiều dạng cầu từ chuỗi kết quả và trả về nhãn chính."""
    if len(history) < 4:
        return 'THIEU_DU_LIEU'
    runs = _run_lengths(history)
    lengths = [length for _, length in runs]
    sides = [side for side, _ in runs]
    tail = history[-20:]
    symbols = ''.join('T' if x == 'TAI' else 'X' for x in tail)

    if lengths and lengths[-1] >= 4:
        return 'CAU_BET_4_PLUS'
    if len(runs) >= 3 and lengths[-1] >= 3 and lengths[-2] >= 3:
        return 'CAU_3_TAI_3_XIU' if lengths[-1] == 3 and lengths[-2] == 3 else 'CAU_BET_DAI'
    if len(runs) >= 4 and lengths[-4:] == [1, 2, 3, 1]:
        return 'CAU_1_2_3'
    if len(runs) >= 4 and lengths[-4:] == [3, 2, 1, 3]:
        return 'CAU_3_2_1'
    if len(runs) >= 4 and lengths[-4:] == [2, 1, 2, 1]:
        return 'CAU_2_1_2'
    if len(runs) >= 4 and lengths[-4:] == [2, 1, 1, 2]:
        return 'CAU_2_1_1_2'
    if len(runs) >= 4 and lengths[-4:] == [1, 2, 1, 2]:
        return 'CAU_1_2_1_2'
    if len(symbols) >= 8 and all(symbols[i] != symbols[i - 1] for i in range(1, len(symbols))):
        return 'CAU_1_1_DAO'
    if len(runs) >= 3 and all(length == 2 for length in lengths[-3:]):
        return 'CAU_2_2'
    if len(runs) >= 3 and all(length == 3 for length in lengths[-3:]):
        return 'CAU_3_3'
    if len(runs) >= 3 and all(length == 1 for length in lengths[-4:]):
        return 'CAU_1_1'
    if len(runs) >= 3 and lengths[-1] == lengths[-2] and lengths[-1] >= 2:
        return f'CAU_{lengths[-1]}_{lengths[-1]}'
    if len(runs) >= 2 and lengths[-1] >= 4:
        return 'CAU_BET_4_PLUS'
    if len(runs) >= 3 and sides[-1] != sides[-2] and lengths[-1] < lengths[-2]:
        return 'CAU_BE'
    if len(runs) >= 3 and sides[-1] != sides[-2]:
        return 'CAU_DAO'
    return 'HON_HOP'
'''
s = s[:start] + new + s[end:]

needle = "    # Cầu chỉ là tín hiệu phụ, tránh các quy tắc regex cứng lấn át dữ liệu.\n    pattern_bonus = {"
replacement = r'''    # Nhận diện cầu bằng nhiều mẫu nhịp; chỉ cộng điểm vừa phải và yêu cầu dữ liệu hỗ trợ.
    pattern_bonus = {
        'CAU_BET_4_PLUS': (last, 0.85),
        'CAU_3_TAI_3_XIU': (last, 0.70),
        'CAU_3_2_1': ('XIU' if last == 'TAI' else 'TAI', 0.48),
        'CAU_1_2_3': (last, 0.48),
        'CAU_2_1_2': (last, 0.52),
        'CAU_2_1_1_2': (last, 0.50),
        'CAU_1_2_1_2': ('XIU' if last == 'TAI' else 'TAI', 0.44),
        'CAU_1_1_DAO': ('XIU' if last == 'TAI' else 'TAI', 0.58),
        'CAU_1_1': ('XIU' if last == 'TAI' else 'TAI', 0.55),
        'CAU_2_2': (last, 0.45),
        'CAU_3_3': (last, 0.55),
        'CAU_DAO': ('XIU' if last == 'TAI' else 'TAI', 0.36),
        'CAU_BE': (last, 0.32),
    }
'''
if needle not in s:
    raise SystemExit('pattern insertion point missing')
s = s.replace(needle, replacement, 1)

# Add dice details to every new-session/result and expand history command later.
s = s.replace("║ 📊 ĐÃ THU THẬP: {n}/20 KẾT QUẢ</pre>", "║ 📊 ĐÃ THU THẬP: {n}/{MAX_HISTORY_STORE} KẾT QUẢ</pre>")
s = s.replace("            st['points_history'].append(tong)\n            if len(st['history']) > MAX_HISTORY_STORE:", "            st['points_history'].append(tong)\n            st['dice_history'].append(d if isinstance(d, list) and len(d) == 3 else [None, None, None])\n            if len(st['history']) > MAX_HISTORY_STORE:")
s = s.replace("                st['points_history'].pop(0)\n            if st['current_prediction']:", "                st['points_history'].pop(0)\n                st['dice_history'].pop(0)\n            if st['current_prediction']:")
path.write_text(s, encoding='utf-8')
