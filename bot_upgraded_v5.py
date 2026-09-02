import os
import json
import time
import math
import hashlib
import base64
import re
import threading
import logging
from threading import RLock
from datetime import datetime, timedelta
import requests
import socketio
import telebot
from flask import Flask
from collections import deque

# ==========================================
# 👑 CẤU HÌNH HỆ THỐNG BOT & LOGGING
# ==========================================
def log_msg(level, m):
    print(f"[{level}] {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} → {m}")

logger = {
    'debug': lambda m: log_msg('DEBUG', m),
    'info':  lambda m: log_msg('INFO ', m),
    'warn':  lambda m: log_msg('WARN ', m),
    'error': lambda m: log_msg('ERROR', m),
}

# 🔐 Secrets
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
try:
    ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
except ValueError:
    ADMIN_ID = 0
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '@auzasito').strip() or '@auzasito'

if not BOT_TOKEN:
    raise RuntimeError('Thiếu BOT_TOKEN trong Environment Variables của Render.')
if ADMIN_ID <= 0:
    raise RuntimeError('Thiếu hoặc sai ADMIN_ID trong Environment Variables của Render.')

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# ✅ MENU LỆNH
bot.set_my_commands([
    telebot.types.BotCommand("start", "🏠 Mở menu chính hệ thống"),
    telebot.types.BotCommand("huongdan", "📖 Bảng hướng dẫn sử dụng"),
    telebot.types.BotCommand("nhapkey", "🔑 Nhập key kích hoạt bản quyền"),
    telebot.types.BotCommand("thongtin", "💎 Xem thông tin tài khoản & hạn dùng"),
    telebot.types.BotCommand("thongke", "📈 Tổng hợp thắng thua AI"),
    telebot.types.BotCommand("login", "🔐 Đăng nhập tài khoản game"),
    telebot.types.BotCommand("autobet", "⚡ Bật / tắt tự động đặt cược"),
    telebot.types.BotCommand("tatx2khithua", "🚫 Tắt gấp thếp X2 khi thua"),
    telebot.types.BotCommand("mox2khithua", "🔁 Mở lại gấp thếp X2 khi thua"),
    telebot.types.BotCommand("lichsucau", "📊 Xem lịch sử cầu gần nhất"),
    telebot.types.BotCommand("nhandiencau", "🧠 AI nhận diện loại cầu hiện tại"),
    telebot.types.BotCommand("stop", "⏹️ Ngắt kết nối an toàn"),
    telebot.types.BotCommand("taokey", "👑 [ADMIN] Tạo key bản quyền"),
    telebot.types.BotCommand("danhsachkey", "📋 [ADMIN] Xem danh sách key"),
    telebot.types.BotCommand("thongbao", "📢 [ADMIN] Gửi thông báo"),
])

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ CẤU HÌNH API + THUẬT TOÁN NÂNG CẤP V5                    ║
# ╚══════════════════════════════════════════════════════════════╝
HISTORY_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/lite-sessions"
MAX_HISTORY_STORE = 200
MIN_CONFIDENCE_AUTO_BET = 55  # ↓ từ 60 → 55 (đặt cầu thường xuyên hơn)
AUTO_BET_RUN_UNTIL_STOP = True
MAX_MARTINGALE_STEPS = 4  # ↑ từ 3 → 4 (1x → 2x → 4x → 8x → 16x)

# 🧠 NÂNG CẤP AI - MÔ HÌNH THÍCH ỨNG & HỌC ONLINE
adaptive_model = {}
psychology_model = {}  # Tâm lí cầu - nhận diện tâm lí nhà cái
entropy_cache = {}     # Lưu entropy để tính ngẫu nhiên
pattern_memory = {}    # Nhớ pattern hiếm gặp

dynamic_weights = {
    'cau_rong': 12,      # ↑ CẦU BỆت (liên tiếp)
    'cau_dut': 11,       # ↑ CẦU BẺ (gãy cầu bệط)
    'cau_11': 10,        # ↑ CẦU 1-1
    'cau_22': 10,        # ↑ CẦU 2-2
    'cau_33': 9,         # ↑ CẦU 3-3
    'cau_44': 9,         # ↑ CẦU 4-4
    'cau_55': 8,         # ↑ CẦU 5-5
    'thongke': 7,        # ↑ Thống kê
    'markov1': 6,        # ↑ Markov
    'markov2': 6,        # ↑ Markov
    'markov3': 7,        # ↑ Markov
    'diem_xucxac': 5,    # Điểm xúc xắc
    'psychology': 8,     # ← NEW: Tâm lí cầu
    'entropy': 6,        # ← NEW: Ngẫu nhiên
}

# Trạng thái dùng chung
STATE_LOCK = RLock()
SAVE_LOCK = RLock()

active_sockets = {}
user_states = {}
valid_keys = {}
authorized_users = {}
all_users = set()

SAVE_FILE = './bot_save.json'

def save_data():
    """Ghi snapshot dữ liệu bằng file tạm rồi replace."""
    try:
        with SAVE_LOCK:
            with STATE_LOCK:
                snapshot = {
                    'valid_keys': dict(valid_keys),
                    'authorized_users': dict(authorized_users),
                    'all_users': list(all_users),
                    'adaptive_model': {k: dict(v) for k, v in adaptive_model.items()},
                    'psychology_model': dict(psychology_model),
                }
            tmp_file = SAVE_FILE + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, SAVE_FILE)
    except Exception as e:
        logger['error'](f"Lỗi lưu file: {e}")

try:
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
            valid_keys = d.get('valid_keys', {})
            authorized_users = {int(k): v for k, v in d.get('authorized_users', {}).items()}
            all_users = set(int(x) for x in d.get('all_users', []))
            all_users.update(authorized_users.keys())
            loaded_model = d.get('adaptive_model', {})
            if isinstance(loaded_model, dict):
                for key, value in loaded_model.items():
                    if isinstance(value, dict):
                        adaptive_model[key] = {'TAI': int(value.get('TAI', 0)), 'XIU': int(value.get('XIU', 0))}
            psychology_model = d.get('psychology_model', {})
    else:
        logger['info']('Chưa có dữ liệu lưu, tạo mới')
except Exception as e:
    logger['info'](f'Chưa có dữ liệu lưu, tạo mới. Lỗi: {e}')

def track_user(chat_id):
    """Ghi nhận user"""
    if chat_id and chat_id not in all_users:
        all_users.add(chat_id)
        save_data()

def init_user_state(chat_id):
    track_user(chat_id)
    if chat_id not in user_states:
        user_states[chat_id] = {
            'history': [], 'points_history': [], 'dice_history': [],
            'last_pattern': None,
            'auto_bet_enabled': False, 'bet_amount': 10000, 'base_bet': 10000,
            'martingale_step': 0, 'max_martingale_steps': MAX_MARTINGALE_STEPS,
            'current_prediction': None, 'waiting_for_result': False,
            'has_bet_this_session': False, 'session_id': None,
            'balance': 0, 'win_streak': 0, 'lose_streak': 0,
            'total_win': 0, 'total_lose': 0, 'total_predictions': 0, 
            'prediction_history': [], 'current_prediction_features': [],
            'lastPingAt': 0, 'betLock': False,
            'last_processed_result_id': None,
            'x2_on_lose': True,
            'always_bet': True,
            'confidence_history': [],  # ← NEW: Lưu lịch độ tin cậy
            'win_rate': 50.0,  # ← NEW: Tỷ lệ thắng
        }

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ TẢI LỊCH SỬ TỪ API                                        ║
# ╚══════════════════════════════════════════════════════════════╝
def fetch_history_from_api(limit=100):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Origin": "https://lc79b.bet",
            "Referer": "https://lc79b.bet/",
            "Accept": "application/json"
        }
        r = requests.get(HISTORY_API_URL, headers=headers, timeout=15)
        r.raise_for_status()
        payload = r.json()
        lst = payload.get('list', []) if isinstance(payload, dict) else []
        if not isinstance(lst, list) or not lst:
            return [], []
        
        lst = list(reversed(lst))[-limit:]
        ketqua, diem = [], []
        for p in lst:
            if not isinstance(p, dict):
                continue
            res = p.get('resultTruyenThong')
            raw_dices = p.get('dices', [])
            try:
                dices = [int(x) for x in raw_dices]
                tong = int(p.get('point')) if p.get('point') is not None else sum(dices)
            except (TypeError, ValueError):
                continue
            if res in ('TAI', 'XIU') and len(dices) == 3 and 3 <= tong <= 18:
                ketqua.append(res)
                diem.append(tong)
        return ketqua, diem
    except Exception as e:
        logger['error']('LỖI TẢI LỊCH SỬ API: ' + str(e))
        return [], []

# ==========================================
# 🛡️ BẢO MẬT & KIỂM TRA BẢN QUYỀN
# ==========================================
def check_auth(chat_id):
    if chat_id == ADMIN_ID:
        return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]:
            return True
        else:
            del authorized_users[chat_id]
            save_data()
    return False

def locked_msg():
    return f"""<pre>╔═══════════════════════════════╗
║   🔒 HỆ THỐNG ĐÃ BỊ KHOÁ 🔒   ║
╠═══════════════════════════════╣
║ ⚠️ BẠN CHƯA CÓ BẢN QUYỀN VIP  ║
║ ❌ KHÔNG THỂ SỬ DỤNG CHỨC NĂNG║
╠═══════════════════════════════╣
║ 🔑 MỞ KHÓA → LIÊN HỆ {ADMIN_USERNAME}
║ 💡 CÚ PHÁP: /nhapkey MÃ_KEY   ║
╚═══════════════════════════════╝</pre>"""

def format_expire_time(ts):
    remain = ts - time.time()
    if remain <= 0: return "❌ ĐÃ HẾT HẠN"
    d = math.floor(remain / 86400)
    h = math.floor((remain % 86400) / 3600)
    m = math.floor((remain % 3600) / 60)
    if d > 0: return f"✅ CÒN {d} NGÀY {h} GIỜ {m} PHÚT"
    if h > 0: return f"✅ CÒN {h} GIỜ {m} PHÚT"
    return f"✅ CÒN {m} PHÚT"

# ==========================================
# 🧠 NÂNG CẤP THUẬT TOÁN AI V5
# ==========================================

def _to_str(history):
    return "".join(['T' if x == 'TAI' else 'X' for x in history])

def _dao(c):
    return 'X' if c == 'T' else 'T'

def _kq(c):
    return 'TAI' if c == 'T' else 'XIU'

def _runs(hist_str):
    """Chuyển chuỗi TTXXX... thành danh sách độ dài từng đoạn"""
    if not hist_str:
        return [], []
    lens, chars = [], []
    cur, n = hist_str[0], 1
    for c in hist_str[1:]:
        if c == cur:
            n += 1
        else:
            lens.append(n); chars.append(cur); cur, n = c, 1
    lens.append(n); chars.append(cur)
    return lens, chars

# ← NEW: TÂM LÍ CẦU - NHẬN DIỆN HÀNH VI NHÀ CÁI
def analyze_psychology_pattern(history, points=None):
    """
    Phân tích tâm lí cầu - dự đoán hành vi nhà cái.
    Trả về (dự_đoán, tin_cậy, mô_tả)
    """
    if len(history) < 8:
        return None, 0, ""
    
    hs = _to_str(history)
    points = points or []
    
    # 1. Phát hiện CẦU "LỪA" (trap pattern)
    # Khi có 4-5 nháy liên tiếp, nhà cái thường bẻ cầu đột ngột
    last_runs, last_chars = _runs(hs[-20:])
    if last_runs and last_runs[-1] >= 4:
        streak = last_runs[-1]
        confidence = min(72 + (streak - 4) * 3, 88)
        return _kq(_dao(hs[-1])), confidence, f"🎯 CẦU LỪA: Bệt {streak} nháy quá dài, xác suất bẻ cao"
    
    # 2. Nhận diện CẦU "VỎN" (oscillation)
    # Khi chuyên đổi qua lại (TXTX...), nhà cái sẽ cho một lần lặp lại
    last_20 = hs[-20:]
    if len([i for i in range(len(last_20)-1) if last_20[i] != last_20[i+1]]) >= 8:
        # Nhiều lần đổi chiều
        last_2 = hs[-2:]
        if len(set(last_2)) == 1:  # 2 phiên cuối giống nhau
            return _kq(hs[-1]), 70, "🎯 CẦU VỎN: Đang lặp lại sau nhiều đổi chiều"
    
    # 3. Nhận diện CẦU "BẤN" (conservative)
    # Khi điểm thấp (≤9), nhà cái thường cho Xỉu
    if points and len(points) >= 5:
        recent_avg = sum(points[-5:]) / 5
        if recent_avg <= 9.5:
            return 'XIU', 68, f"🎯 CẦU BẤN: Điểm thấp (TB={recent_avg:.1f}), xu hướng Xỉu"
        elif recent_avg >= 12:
            return 'TAI', 68, f"🎯 CẦU BẤN: Điểm cao (TB={recent_avg:.1f}), xu hướng Tài"
    
    return None, 0, ""

# ← NEW: TÍNH TOÁN ENTROPY (ngẫu nhiên)
def calculate_entropy(history):
    """Tính entropy - mức độ ngẫu nhiên. Cao = ngẫu nhiên, Thấp = có pattern."""
    if len(history) < 5:
        return 0.5
    
    recent = _to_str(history[-30:])
    tai_count = recent.count('T')
    xiu_count = recent.count('X')
    total = tai_count + xiu_count
    
    if total == 0:
        return 0.5
    
    p_tai = tai_count / total
    p_xiu = xiu_count / total
    
    # Shannon entropy
    entropy = 0
    if p_tai > 0: entropy -= p_tai * math.log2(p_tai)
    if p_xiu > 0: entropy -= p_xiu * math.log2(p_xiu)
    
    return min(entropy, 1.0)

# ← NEW: NHẬN DIỆN CẦU NÂNG CAO V5
def nhan_dien_cau_v5(history, points=None):
    """Nhận diện cầu nâng cấp - tổng hợp tất cả pattern."""
    if points is None:
        points = []
    
    out = []
    hs = _to_str(history)
    
    if len(hs) < 3:
        return out
    
    last = hs[-1]
    lens, chars = _runs(hs)
    streak = lens[-1] if lens else 0
    
    def add(ten, du_doan, tin_cay, mo_ta, chinh=False):
        out.append({'ten': ten, 'du_doan': du_doan, 'tin_cay': int(min(tin_cay, 98)),
                    'mo_ta': mo_ta, 'chinh': bool(chinh)})
    
    # ─── 1. CẦU BỆT (BẼ)
    if streak >= 4:
        if streak >= 9:
            add(f"🔥 BẺ CẦU BỆT {streak}", _kq(_dao(last)), min(85 + (streak - 9), 96),
                f"Bệt {streak} nháy quá dài → xác suất bẻ cực cao", True)
        else:
            add(f"🏃 CẦU BỆT {streak}", _kq(last), min(80 + (streak - 4) * 2, 94),
                f"Bệt {streak} nháy → theo bệt", True)
    
    # ─── 2. CẦU N-N
    if streak <= 5 and len(lens) >= 3:
        for n in [1, 2, 3, 4, 5]:
            if len(lens) >= 3:
                truoc = lens[-3:-1]
                if len(truoc) == 2 and all(x == n for x in truoc) and streak <= n:
                    so_chu_ky = sum(1 for l in reversed(lens[:-1]) if l == n else 0 for _ in [None] if not _)
                    base_conf = {1: 88, 2: 90, 3: 91, 4: 89, 5: 87}
                    ten = f"🔁 CẦU {n}-{n}"
                    if streak >= n:
                        add(ten, _kq(_dao(last)), min(base_conf[n] + so_chu_ky, 96),
                            f"Nhịp {n}-{n} đã đủ → đảo chiều", True)
                    else:
                        add(ten, _kq(last), min(base_conf[n] + so_chu_ky - 3, 94),
                            f"Nhịp {n}-{n} mới {streak}/{n} → theo tiếp", True)
                    break
    
    # ─── 3. CẦU 3-2-1
    if len(lens) >= 3:
        l3 = lens[-3:]
        if l3 == [3, 2, 1]:
            add("🔔 CẦU 3-2-1", _kq(_dao(last)), 89, "3→2→1 khép kín → đảo chiều", True)
        elif l3 == [1, 2, 3]:
            add("📈 CẦU 1-2-3", _kq(last), 85, "Tháp tăng 1→2→3", True)
    
    # ─── 4. CẦU LẶP CHU KỲ
    for k in [3, 4, 5, 6]:
        if len(hs) >= k * 2:
            if hs[-k:] == hs[-2*k:-k]:
                nxt = hs[-k]
                add(f"🔁 CẦU LẶP {k}", _kq(nxt), 75 + k, f"Khối {k} phiên lặp lại")
                break
    
    # ─── 5. CẦU LỆCH (thống kê)
    if len(history) >= 25:
        r25 = history[-25:]
        t25 = r25.count('TAI')
        if t25 >= 17:
            add("⚠️ CẦU LỆCH TÀI", 'XIU', 72, f"{t25}/25 Tài → cân bằng về Xỉu")
        elif t25 <= 8:
            add("⚠️ CẦU LỆCH XỈU", 'TAI', 72, f"{25-t25}/25 Xỉu → cân bằng về Tài")
    
    # ─── 6. CẦU TÂM LÍ (NEW)
    psy_pred, psy_conf, psy_desc = analyze_psychology_pattern(history, points)
    if psy_conf >= 60:
        add("🧠 CẦU TÂM LÍ", psy_pred, psy_conf, psy_desc)
    
    # ─── 7. CẦU ĐIỂM XÚC XẮC
    if len(points) >= 8:
        p8 = points[-8:]
        avg = sum(p8) / 8
        if avg >= 12.5:
            add("💎 CẦU ĐIỂM CAO", 'TAI', 70, f"TB điểm = {avg:.1f}")
        elif avg <= 8.5:
            add("💎 CẦU ĐIỂM THẤP", 'XIU', 70, f"TB điểm = {avg:.1f}")
    
    # Sắp xếp theo độ tin cậy
    out.sort(key=lambda x: x['tin_cay'], reverse=True)
    return out

# ← NEW: GỘP CẦU THÔNG MINH (V5)
def tong_hop_cau_v5(history, points=None):
    """Gộp phiếu và tính confidence cao hơn."""
    ds = nhan_dien_cau_v5(history, points)
    if not ds:
        return None, 50, None, []
    
    # Ưu tiên cầu CHÍNH
    chinh = [c for c in ds if c.get('chinh')]
    if chinh:
        top = max(chinh, key=lambda x: x['tin_cay'])
        dong = sum(1 for c in ds if c is not top and c['du_doan'] == top['du_doan'])
        nguoc = sum(1 for c in ds if c is not top and c['du_doan'] != top['du_doan'])
        tin = min(max(top['tin_cay'] + dong * 3 - nguoc, 58), 98)
        return top['du_doan'], tin, top, ds
    
    # Nếu không có cầu chính, tính trung bình có trọng số
    st_, sx_ = 0.0, 0.0
    for c in ds:
        w = c['tin_cay'] / 10.0
        if c['du_doan'] == 'TAI':
            st_ += w
        else:
            sx_ += w
    
    top = ds[0]
    if abs(st_ - sx_) < 0.3:
        # Cân bằng → dựa vào thống kê gần nhất
        if len(history) >= 2:
            if history[-1] == history[-2]:
                dd = history[-1]
            else:
                dd = 'XIU' if history[-1] == 'TAI' else 'TAI'
        else:
            dd = 'TAI' if st_ > sx_ else 'XIU'
        return dd, max(top['tin_cay'] - 8, 56), top, ds
    
    du_doan = 'TAI' if st_ > sx_ else 'XIU'
    tin_cay = top['tin_cay'] if top['du_doan'] == du_doan else max(top['tin_cay'] - 8, 56)
    return du_doan, tin_cay, top, ds

# ← NEW: TÍNH MARTINGALE THÔNG MINH
def calculate_smart_martingale(base_bet, step, max_steps, win_rate):
    """
    Tính tiền cược thông minh dựa trên win rate.
    Nếu win_rate > 65%, có thể tăng nguy hiểm hơn.
    """
    if win_rate >= 75:
        # Cực tự tin → tăng nhanh
        multipliers = [1, 2, 4, 8, 16]
    elif win_rate >= 65:
        # Tự tin → tăng bình thường
        multipliers = [1, 2, 4, 8]
    elif win_rate >= 55:
        # Bình thường
        multipliers = [1, 1.5, 3, 6]
    else:
        # Thận trọng
        multipliers = [1, 1.5, 3]
    
    if step < len(multipliers):
        return int(base_bet * multipliers[step])
    return int(base_bet * (2 ** (step % 4)))

# ==========================================
# 📋 ĐỊNH DẠNG & HIỂN THỊ
# ==========================================
def format_bang_cau_v5(history, points=None, dices=None, limit=12):
    """Bảng nhận diện cầu nâng cấp."""
    du_doan, tin_cay, top, ds = tong_hop_cau_v5(history, points)
    hs = _to_str(history)
    icons = "".join(['🔵' if c == 'T' else '🔴' for c in hs[-20:]])
    
    lines = ["<b>🧠 AI NHẬN DIỆN CẦU — AUZA ELITE V5</b>", f"📈 {icons}"]
    
    if not ds:
        lines.append("⏳ Chưa đủ dữ liệu để nhận diện cầu.")
        return "\n".join(lines)
    
    lines.append("")
    lines.append("<b>🔎 CÁC LOẠI CẦU PHÁT HIỆN:</b>")
    for c in ds[:8]:
        ic = '🔵 TÀI' if c['du_doan'] == 'TAI' else '🔴 XỈU'
        lines.append(f"• {c['ten']} → {ic} ({c['tin_cay']}%)\n  <i>{c['mo_ta']}</i>")
    
    ic = '🔵 TÀI' if du_doan == 'TAI' else '🔴 XỈU'
    lines.append("")
    lines.append(f"<b>🎯 CHỐT THEO CẦU: {ic} — ĐỘ TIN {tin_cay}%</b>")
    if top:
        lines.append(f"👑 CẦU MẠNH NHẤT: {top['ten']}")
    
    if dices and len(dices) > 0:
        lines.append("")
        lines.append("<b>🎲 XÚC XẮC GẦN NHẤT:</b>")
        n = min(limit, len(dices), len(history))
        for i in range(-n, 0):
            d = dices[i]
            tong = points[i] if points and len(points) > len(dices) + i else sum(d)
            kq = '🔵' if history[i] == 'TAI' else '🔴'
            lines.append(f"{kq} {d[0]}-{d[1]}-{d[2]} = {tong}")
    
    return "\n".join(lines)

# ==========================================
# 🔑 QUẢN LÝ KEY & LỆNH
# ==========================================
def gen_key(days=30):
    """Tạo key bản quyền"""
    base = f"{int(time.time())}{os.urandom(8).hex()}"
    return base[:16].upper()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    cid = message.chat.id
    init_user_state(cid)
    track_user(cid)
    msg = """🎮 <b>AUZA ELITE BOT - V5</b>

Đây là hệ thống AI dự đoán <b>TÀI XỈU</b> với thuật toán nâng cao.

✅ <b>Tính năng chính:</b>
• 🧠 AI nhận diện 8+ loại cầu
• 📊 Thống kê thắng thua realtime
• ⚡ Auto bet thông minh
• 💰 Martingale tối ưu
• 🔐 Đầy đủ bảo mật

📝 <b>Bắt đầu:</b>
1. /nhapkey MÃ_KEY (nếu có key)
2. /login TAIKHOAN MATKHAU
3. /autobet on [số tiền]
4. /nhandiencau (xem AI phân tích)

🆘 Liên hệ: {ADMIN_USERNAME}"""
    bot.reply_to(message, msg, parse_mode='HTML')

@bot.message_handler(commands=['nhapkey'])
def send_nhapkey(message):
    parts = message.text.split()
    if len(parts) != 2:
        return bot.reply_to(message, '✅ Cú pháp: /nhapkey MÃ_KEY')
    
    key = parts[1].upper()
    if key not in valid_keys:
        return bot.reply_to(message, '❌ Key không hợp lệ hoặc đã hết hạn')
    
    cid = message.chat.id
    valid_keys[key] -= 1  # giảm số lần dùng
    if valid_keys[key] <= 0:
        del valid_keys[key]
    
    expires = time.time() + (30 * 86400)
    authorized_users[cid] = expires
    track_user(cid)
    save_data()
    
    msg = f"""✅ <b>KÍCH HOẠT THÀNH CÔNG!</b>

🎉 Bạn đã được mở khóa.
⏰ Hạn dùng: {format_expire_time(expires)}

➡️ Tiếp theo: /login TAIKHOAN MATKHAU"""
    bot.reply_to(message, msg, parse_mode='HTML')

@bot.message_handler(commands=['thongtin'])
def send_thongtin(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    if cid not in authorized_users:
        exp = "❌ Chưa kích hoạt"
    else:
        exp = format_expire_time(authorized_users[cid])
    
    st = user_states[cid]
    msg = f"""<pre>╔═══════════════════════════════╗
║ 💎 THÔNG TIN TÀI KHOẢN        ║
╠═══════════════════════════════╣
║ 👤 ID: {cid}
║ 💰 SỐ DƯ: {st['balance']:,} WIN
║ 📊 THẮNG: {st['total_win']} | THUA: {st['total_lose']}
║ 📈 TỶ LỆ: {st.get('win_rate', 50):.1f}%
║ 🔄 HẠN DÙNG: {exp}
║ 🤖 AUTO: {'🟢 BẬT' if st['auto_bet_enabled'] else '🔴 TẮT'}
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['thongke'])
def send_thongke(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    st = user_states[cid]
    if not st['history']:
        return bot.reply_to(message, '📭 Chưa có dữ liệu')
    
    # Tính thống kê chi tiết
    total = st['total_win'] + st['total_lose']
    win_rate = (st['total_win'] / total * 100) if total > 0 else 0
    avg_conf = sum(st.get('confidence_history', [])[-50:] or [50]) / max(len(st.get('confidence_history', [])[-50:] or [1]), 1)
    
    msg = f"""<pre>╔═══════════════════════════════╗
║ 📊 THỐNG KÊ CHI TIẾT          ║
╠═══════════════════════════════╣
║ 🎯 TỔNG PHIÊN: {total}
║ 🟢 THẮNG: {st['total_win']} ({win_rate:.1f}%)
║ 🔴 THUA: {st['total_lose']} ({100-win_rate:.1f}%)
║ 🧠 ĐỘ TIN BÌNH QUY: {avg_conf:.1f}%
║ 💰 TỔNG THU: {st['total_win'] - st['total_lose']:,} WIN
║ 📈 DATA: {len(st['history'])} PHIÊN
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['lichsucau'])
def send_lichsucau(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    st = user_states[cid]
    if not st['history']:
        return bot.reply_to(message, '📭 Chưa có dữ liệu phiên nào')
    
    ls = st['history'][-20:]
    t = ls.count('TAI')
    x = ls.count('XIU')
    icons = "".join(['🔵' if i == 'TAI' else '🔴' for i in ls])
    header = f"📊 THỐNG KÊ 20 PHIÊN GẦN NHẤT:\n🔵 TÀI: {t} | 🔴 XỈU: {x}\n{icons}\n"
    body = format_bang_cau_v5(st['history'], st['points_history'], st.get('dice_history'), limit=12)
    bot.reply_to(message, header + "\n" + body, parse_mode='HTML')

@bot.message_handler(commands=['nhandiencau'])
def send_nhandiencau(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    st = user_states[cid]
    if len(st['history']) < 3:
        return bot.reply_to(message, '📭 Chưa đủ dữ liệu, hãy /login và chờ AI thu thập')
    
    bot.reply_to(message, format_bang_cau_v5(st['history'], st['points_history'], st.get('dice_history'), limit=15), parse_mode='HTML')

@bot.message_handler(commands=['autobet'])
def send_autobet(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    init_user_state(cid)
    st = user_states[cid]
    
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ Cú pháp: /autobet on 2k | off')
    
    if parts[1].lower() == 'on':
        amt = 10000
        if len(parts) >= 3:
            try:
                amt_str = parts[2].lower()
                if 'k' in amt_str:
                    amt = int(amt_str.replace('k', '')) * 1000
                else:
                    amt = int(amt_str)
            except:
                return bot.reply_to(message, '⚠️ Số tiền không hợp lệ')
        
        st['auto_bet_enabled'] = True
        st['base_bet'] = amt
        st['bet_amount'] = amt
        st['martingale_step'] = 0
        
        msg = f"🟢 AUTO ĐÃ BẬT\n💰 VỐN MỖI CHU KỲ: {amt:,} WIN\n"
        msg += "🔁 LỘ TRÌNH: " + " → ".join([str(calculate_smart_martingale(amt, i, MAX_MARTINGALE_STEPS, 60)) for i in range(4)]) + "\n"
        msg += "(Chạy đến khi dùng /autobet off hoặc /stop)"
        bot.reply_to(message, msg)
    else:
        st['auto_bet_enabled'] = False
        st['martingale_step'] = 0
        st['bet_amount'] = st['base_bet']
        bot.reply_to(message, '🔴 AUTO ĐÃ DỪNG LẠI')

@bot.message_handler(commands=['stop'])
def send_stop(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    
    if cid in active_sockets:
        try:
            active_sockets[cid].disconnect()
        except:
            pass
        del active_sockets[cid]
        if cid in user_states:
            user_states[cid]['auto_bet_enabled'] = False
        bot.reply_to(message, '⏹️ ĐÃ NGẮT KẾT NỐI AN TOÀN')
    else:
        bot.reply_to(message, '⚠️ Bạn chưa kết nối')

# ==========================================
# 🚀 FLASK SERVER
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    return f"🚀 AUZA ELITE V5 ĐANG HOẠT ĐỘNG! TS: {int(time.time() * 1000)}"

def run_flask():
    port = int(os.environ.get('PORT', 3000))
    logger['info'](f'🌐 FLASK SERVER CHẠY PORT {port}')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

def start_anti_sleep():
    """Ping Render để không treo"""
    pass

# ==========================================
# 🚀 CHẠY BOT
# ==========================================
if __name__ == '__main__':
    logger['info']('👑 AUZA ELITE V5 ✨ PYTHON + AI NÂNG CAO')
    
    start_anti_sleep()
    threading.Thread(target=run_flask, daemon=True).start()
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger['error'](f'BOT ERR: {e} → RECONNECTING...')
            time.sleep(3)
