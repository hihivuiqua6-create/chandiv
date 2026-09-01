import os
import json
import time
import math
import hashlib
import base64
import re
import threading
import logging
from datetime import datetime
import requests
import socketio
import telebot
from flask import Flask

# ==========================================
# 👑 CẤU HÌNH HỆ THỐNG BOT & LOGGING (GIỐNG GỐC)
# ==========================================
def log_msg(level, m):
    print(f"[{level}] {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} → {m}")

logger = {
    'debug': lambda m: log_msg('DEBUG', m),
    'info':  lambda m: log_msg('INFO ', m),
    'warn':  lambda m: log_msg('WARN ', m),
    'error': lambda m: log_msg('ERROR', m),
}

# 🔐 Secrets phải được đặt trong Render Environment Variables, không commit vào GitHub.
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

# ✅ Đặt MENU LỆNH GIỐNG HỆT
bot.set_my_commands([
    telebot.types.BotCommand("start", "🏠 Mở menu chính hệ thống"),
    telebot.types.BotCommand("huongdan", "📖 Bảng hướng dẫn sử dụng"),
    telebot.types.BotCommand("nhapkey", "🔑 Nhập key kích hoạt bản quyền"),
    telebot.types.BotCommand("thongtin", "💎 Xem thông tin tài khoản & hạn dùng"),
    telebot.types.BotCommand("login", "🔐 Đăng nhập tài khoản game"),
    telebot.types.BotCommand("autobet", "⚡ Bật / tắt tự động đặt cược"),
    telebot.types.BotCommand("lichsucau", "📊 Xem lịch sử cầu gần nhất"),
    telebot.types.BotCommand("stop", "⏹️ Ngắt kết nối an toàn"),
    telebot.types.BotCommand("taokey", "👑 [ADMIN] Tạo key bản quyền"),
    telebot.types.BotCommand("danhsachkey", "📋 [ADMIN] Xem danh sách key còn lại"),
])

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ CẤU HÌNH API + THUẬT TOÁN (GIỐNG Y HỆT)                  ║
# ╚══════════════════════════════════════════════════════════════╝
HISTORY_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/lite-sessions"
MAX_HISTORY_STORE = 300  # tải tối đa 300 phiên gần nhất để phân tích đa cửa sổ
MIN_CONFIDENCE_AUTO_BET = 60
AUTO_BET_RUN_UNTIL_STOP = True
MAX_MARTINGALE_STEPS = 3  # 1x -> 2x -> 4x -> 8x; thua ở 8x thì quay về 1x

dynamic_weights = {
    'cau_rong': 10, 'cau_dut': 9, 'cau_11': 8, 'cau_22': 8,
    'cau_33': 7, 'cau_44': 7, 'cau_55': 7, 'thongke': 5,
    'markov1': 4, 'markov2': 5, 'markov3': 6, 'diem_xucxac': 4
}

active_sockets = {}
user_states = {}
valid_keys = {}
authorized_users = {}

# ✅ LƯU KEY / NGƯỜI DÙNG RA FILE → KHÔNG MẤT KHI RESTART
SAVE_FILE = './bot_save.json'

def save_data():
    try:
        with open(SAVE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'valid_keys': valid_keys, 'authorized_users': authorized_users}, f, indent=2)
    except Exception as e:
        logger['error'](f"Lỗi lưu file: {e}")

try:
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
            valid_keys = d.get('valid_keys', {})
            authorized_users = {int(k): v for k, v in d.get('authorized_users', {}).items()}
    else:
        logger['info']('Chưa có dữ liệu lưu, tạo mới')
except Exception as e:
    logger['info'](f'Chưa có dữ liệu lưu, tạo mới. Lỗi: {e}')

def init_user_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {
            'history': [], 'points_history': [], 'dice_history': [],
            'auto_bet_enabled': False, 'bet_amount': 10000, 'base_bet': 10000,
            'martingale_step': 0, 'max_martingale_steps': MAX_MARTINGALE_STEPS,
            'bet_mode': 'martingale',  # martingale hoặc flat
            'current_prediction': None, 'waiting_for_result': False,
            'has_bet_this_session': False, 'session_id': None,
            'balance': 0, 'win_streak': 0, 'lose_streak': 0,
            'total_win': 0, 'total_lose': 0,
            'lastPingAt': 0, 'betLock': False
        }

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ TẢI LỊCH SỬ TỪ API (GIỐNG GỐC)                                                 ║
# ╚══════════════════════════════════════════════════════════════╝
def fetch_history_from_api(limit=MAX_HISTORY_STORE):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://lc79b.bet",
            "Referer": "https://lc79b.bet/",
            "Accept": "application/json"
        }
        r = requests.get(HISTORY_API_URL, headers=headers, timeout=15)
        lst = r.json().get('list', [])
        if not lst:
            return [], [], []
        
        lst = list(reversed(lst))[-limit:]
        ketqua, diem, xucsac = [], [], []
        for p in lst:
            res = p.get('resultTruyenThong')
            tong = p.get('point') or sum(p.get('dices', [0,0,0]))
            if res in ['TAI', 'XIU']:
                ketqua.append(res)
                diem.append(tong)
                dices = p.get('dices', [None, None, None])
                xucsac.append(dices if isinstance(dices, list) and len(dices) == 3 else [None, None, None])
        return ketqua, diem, xucsac
    except Exception as e:
        logger['error']('LỖI TẢI LỊCH SỬ API: ' + str(e))
        return [], [], []

# ==========================================
# 🛡️ BẢO MẬT & KIỂM TRA BẢN QUYỀN
# ==========================================
def actor_id(message):
    """Lấy Telegram numeric user ID thật của người gửi, không dùng chat ID."""
    return int(message.from_user.id)

def is_admin(message):
    return actor_id(message) == ADMIN_ID

def check_auth(user_id):
    """Chỉ admin hoặc user ID đã kích hoạt key còn hạn mới được dùng chức năng VIP."""
    user_id = int(user_id)
    if user_id == ADMIN_ID:
        return True
    if user_id in authorized_users:
        if time.time() <= authorized_users[user_id]:
            return True
        else:
            del authorized_users[user_id]
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
# 🧠 THUẬT TOÁN VIP PRO MAX — DỊCH CHÍNH XÁC TỪNG DÒNG
# ==========================================
def _run_info(history):
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


def _run_lengths(history):
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

    # Nhận diện cầu bằng nhiều mẫu nhịp; chỉ cộng điểm vừa phải và yêu cầu dữ liệu hỗ trợ.
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

# 🚀 TÍNH NĂNG NÂNG CẤP X2 (MARTINGALE)
def ai_tu_hoc(chat_id, du_doan, thuc_te):
    st = user_states.get(chat_id)
    if not st: return
    if du_doan == thuc_te:
        st['win_streak'] += 1
        st['lose_streak'] = 0
        st['total_win'] += 1
        # Thắng -> reset về vốn ban đầu và bắt đầu lại chu kỳ gấp thếp.
        st['martingale_step'] = 0
        st['bet_amount'] = st['base_bet']
        for k in dynamic_weights:
            dynamic_weights[k] = min(dynamic_weights[k] + 0.05, 15)
    else:
        st['lose_streak'] += 1
        st['win_streak'] = 0
        st['total_lose'] += 1
        if st.get('bet_mode', 'martingale') == 'flat':
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
        for k in dynamic_weights:
            dynamic_weights[k] = max(dynamic_weights[k] - 0.03, 2)

# ==========================================
# 🌐 ĐĂNG NHẬP + SOCKET.IO (GIỐNG GỐC)
# ==========================================
def md5_hash(text):
    return hashlib.md5(text.encode()).hexdigest()

def login_and_get_token(u, p):
    try:
        pw = md5_hash(p)
        url = f"https://apifo88daigia.tele68.com/api?c=3&un={requests.utils.quote(u)}&pw={pw}&cp=R&cl=R&pf=web&at="
        r = requests.get(url, timeout=12)
        d = r.json()
        if not d.get('success'):
            return {'_error': 'Lỗi Game: ' + (d.get('message') or 'Sai thông tin')}
            
        sk = d.get('sessionKey', '')
        sk += '=' * ((4 - len(sk) % 4) % 4)
        sd = json.loads(base64.b64decode(sk).decode('utf-8'))
        nickname = sd.get('nickname') or sd.get('nickName')
        
        headers = {
            'authority': 'wlb.tele68.com',
            'content-type': 'application/json',
            'origin': 'https://lc79b.bet',
            'referer': 'https://lc79b.bet/'
        }
        payload = {'nickName': nickname, 'accessToken': d.get('accessToken')}
        r2 = requests.post(
            'https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=',
            json=payload, headers=headers, timeout=12
        )
        data2 = r2.json()
        token = data2.get('token')
        if not token:
            return {'_error': 'Lobby không trả token'}
        money = data2.get('remoteLoginResp', {}).get('money', 0)
        return {'token': token, 'nickname': nickname, 'money': money}
    except Exception as e:
        return {'_error': 'Lỗi kết nối: ' + str(e)}

# ⭐⭐⭐ CHỨC NĂNG MỚI: PING + WATCHDOG + KHÔNG TREO RENDER ⭐⭐⭐
def start_anti_sleep():
    def pinger():
        while True:
            try:
                requests.get('https://lc79b.bet', timeout=8)
                logger['info']('🌐 PING RENDER OK — giữ kết nối 100%')
            except:
                pass
            time.sleep(40)
            
    def watchdog():
        while True:
            now = time.time() * 1000
            for cid, sio in list(active_sockets.items()):
                st = user_states.get(cid)
                if st and (now - st['lastPingAt']) > 90000:
                    logger['warn'](f"🐶 WATCHDOG: {cid} đứng hình → ngắt & kết nối lại")
                    try: sio.disconnect()
                    except: pass
            time.sleep(30)
            
    threading.Thread(target=pinger, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

def start_websocket(chat_id, token):
    init_user_state(chat_id)
    if chat_id in active_sockets:
        try: active_sockets[chat_id].disconnect()
        except: pass

    sio = socketio.Client(
        reconnection=True, reconnection_attempts=99999,
        reconnection_delay=3, reconnection_delay_max=5
    )
    active_sockets[chat_id] = sio
    st = user_states[chat_id]

    def ping_socket():
        while chat_id in active_sockets and active_sockets[chat_id] == sio:
            try:
                if sio.connected:
                    sio.emit('ping', {}, namespace='/txmd5')
                    st['lastPingAt'] = time.time() * 1000
            except: pass
            time.sleep(25)
    
    threading.Thread(target=ping_socket, daemon=True).start()

    @sio.on('connect', namespace='/txmd5')
    def on_connect():
        logger['info'](f"[{chat_id}] ✅ SOCKET KẾT NỐI")
        st['lastPingAt'] = time.time() * 1000
        lk, ld, lx = fetch_history_from_api(MAX_HISTORY_STORE)
        tb = ''
        if lk:
            st['history'] = lk[-MAX_HISTORY_STORE:]
            st['points_history'] = ld[-MAX_HISTORY_STORE:]
            st['dice_history'] = lx[-MAX_HISTORY_STORE:]
            tb = f"\n║ 📥 TẢI LỊCH SỬ API: <b>{len(lk)}</b> PHIÊN ✅"
        else:
            tb = "\n║ ⚠️ Thu thập tự động"
            
        msg = f"""<pre>╔═══════════════════════════════╗
║     🟢 KẾT NỐI THÀNH CÔNG 🟢  ║
╠═══════════════════════════════╣
║ ✅ ĐÃ KẾT NỐI MÁY CHỦ GAME{tb}
║ ⚡ AI VIP ELITE ĐANG SẴN SÀNG ║
║ 📡 PING TỰ ĐỘNG ĐÃ BẬT        ║
╚═══════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('disconnect', namespace='/txmd5')
    def on_disconnect():
        logger['warn'](f"[{chat_id}] 🔴 NGẮT KẾT NỐI — TỰ KẾT NỐI LẠI")
        msg = """<pre>╔═══════════════════════════════╗
║     🔴 MẤT KẾT NỐI MÁY CHỦ    ║
╠═══════════════════════════════╣
║ ⚙️ TỰ ĐỘNG KẾT NỐI LẠI LIÊN TỤC║
╚═══════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('new-session', namespace='/txmd5')
    def on_new_session(data):
        st['session_id'] = data.get('id', 'N/A')
        st['has_bet_this_session'] = False
        st['betLock'] = False
        n = len(st['history'])
        dt = tinh_do_tin_cay(st['history'], st['points_history'])
        msg = f"""<pre>╔═══════════════════════════════╗
║    💎 AI AUZA ELITE 💎        ║
║       ✨ PHIÊN MỚI MỞ ✨      ║
╠═══════════════════════════════╣
║ 🎯 MÃ PHIÊN: {st['session_id']}
║ 📊 ĐÃ THU THẬP: {n}/{MAX_HISTORY_STORE} KẾT QUẢ</pre>"""
        
        if n >= 3:
            pred = make_prediction_vip(st['history'], st['points_history'])
            st['current_prediction'] = pred
            icon = '🔵 TÀI' if pred == 'TAI' else ('🔴 XỈU' if pred == 'XIU' else '⚪ CHỜ THÊM DỮ LIỆU')
            pattern_name = _pattern_label(st['history'])
            msg += f"\n<pre>╠═══════════════════════════════╣\n║ 🤖 AI: {icon} | 📈 {dt}%\n║ 🧩 NHẬN DIỆN: {pattern_name}</pre>"
            
            if st['auto_bet_enabled']:
                if dt >= MIN_CONFIDENCE_AUTO_BET:
                    msg += f"\n<pre>║ ⚡ AUTO ON — {st['bet_amount']:,} WIN</pre>"
                else:
                    msg += f"\n<pre>║ ⚠️ ĐỘ TIN <{MIN_CONFIDENCE_AUTO_BET}% → BỎ QUA</pre>"
        else:
            st['current_prediction'] = None
            msg += "\n<pre>║ ⏳ ĐANG THU DỮ LIỆU</pre>"
            
        msg += "\n<pre>╚═══════════════════════════════╝</pre>"
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('tick-update', namespace='/txmd5')
    def on_tick_update(data):
        gs = data.get('state')
        dt = tinh_do_tin_cay(st['history'], st['points_history'])
        if gs == 'BETTING' and st['auto_bet_enabled'] and st['current_prediction'] and AUTO_BET_RUN_UNTIL_STOP:
            if not st['has_bet_this_session'] and not st['betLock'] and dt >= MIN_CONFIDENCE_AUTO_BET:
                st['betLock'] = True
                pay = {'type': st['current_prediction'], 'amount': st['bet_amount']}
                try:
                    sio.emit('bet', pay, namespace='/txmd5')
                    st['has_bet_this_session'] = True
                    st['waiting_for_result'] = True
                    icon = '🔵 TÀI' if st['current_prediction'] == 'TAI' else '🔴 XỈU'
                    msg = f"""<pre>╔═══════════════════════════════╗
║      🚀 GỬI LỆNH TỰ ĐỘNG      ║
╠═══════════════════════════════╣
║ ✅ ĐẶT CƯỢC: {icon}
║ 💰 SỐ TIỀN: {st['bet_amount']:,} WIN
║ 🔄 MODE: {'CƯỢC ĐỀU' if st.get('bet_mode') == 'flat' else ('GẤP THẾP' if st['bet_amount'] > st['base_bet'] else 'GẤP THẾP - VỐN GỐC')}
║ ⏳ CHỜ KẾT QUẢ TỪ SERVER      ║
╚═══════════════════════════════╝</pre>"""
                    bot.send_message(chat_id, msg, parse_mode='HTML')
                except Exception as e:
                    st['betLock'] = False

    @sio.on('bet-result', namespace='/txmd5')
    def on_bet_result(data):
        if data.get('postBalance') is not None:
            st['balance'] = data['postBalance']
        msg = f"""<pre>╔═══════════════════════════════╗
║      ✅ XÁC NHẬN ĐẶT CƯỢC     ║
╠═══════════════════════════════╣
║ 💰 SỐ DƯ: {st['balance']:,} WIN
╚═══════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass
        try: sio.emit('get-current-my-info', None, namespace='/txmd5')
        except: pass

    @sio.on('session-result', namespace='/txmd5')
    def on_session_result(data):
        st['betLock'] = False
        d = data.get('dices', [0,0,0])
        tong = sum(d)
        kq = data.get('resultTruyenThong', 'N/A')
        
        if kq in ['TAI', 'XIU']:
            st['history'].append(kq)
            st['points_history'].append(tong)
            st['dice_history'].append(d if isinstance(d, list) and len(d) == 3 else [None, None, None])
            if len(st['history']) > MAX_HISTORY_STORE:
                st['history'].pop(0)
                st['points_history'].pop(0)
                st['dice_history'].pop(0)
            if st['current_prediction']:
                ai_tu_hoc(chat_id, st['current_prediction'], kq)
                
        icon = '🔵 TÀI' if kq == 'TAI' else ('🔴 XỈU' if kq == 'XIU' else '⚪ LỖI')
        row = f"""<pre>╔═══════════════════════════════╗
║ 🎲 {d[0]}-{d[1]}-{d[2]} = {tong} → {icon}</pre>"""
        
        if st['current_prediction']:
            ok = (st['current_prediction'] == kq)
            text_kq = f"🟢 ĐÚNG ✅ THẮNG LIÊN {st['win_streak']}" if ok else f"🔴 SAI ⚠️ THUA LIÊN {st['lose_streak']} (X2 PHIÊN SAU)"
            row += f"\n<pre>║ 📊 AI: {text_kq}</pre>"
            st['waiting_for_result'] = False
            
        ls = "".join(['🔵' if x == 'TAI' else '🔴' for x in st['history'][-12:]])
        row += f"\n<pre>║ 📈 {ls}</pre>\n<pre>╚═══════════════════════════════╝</pre>"
        try: bot.send_message(chat_id, row, parse_mode='HTML')
        except: pass

    try:
        sio.connect('https://wtxmd52.tele68.com', socketio_path='txmd5/', namespaces=['/txmd5'], auth={'token': token})
    except Exception as e:
        logger['error']("LỖI CONNECT SOCKET: " + str(e))

def parse_bet_amount(raw):
    """Đổi số tiền cược từ 2000, 2k hoặc 0.002m thành số nguyên."""
    text = raw.strip().lower().replace(',', '')
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([km]?)', text)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1000 if suffix == 'k' else 1_000_000 if suffix == 'm' else 1
    amount = int(value * multiplier)
    return amount if amount > 0 else None

# ==========================================
# 🔑 TẤT CẢ LỆNH — GIAO DIỆN AUZA VẢ CHẾT NHÀ CÁI
# ==========================================
@bot.message_handler(commands=['start'])
def send_start(message):
    cid = message.chat.id
    init_user_state(cid)
    uid = actor_id(message)
    if check_auth(uid):
        han = '👑 VĨNH VIỄN - ADMIN' if uid == ADMIN_ID else format_expire_time(authorized_users[uid])
        msg = f"""<pre>╔═══════════════════════════════╗
║    💎 CHÀO MỪNG TỚI VIP 💎    ║
║      ✨ AUZA VẢ CHẾT NHÀ CÁI ✨ ║
╠═══════════════════════════════╣
║ ✅ TRẠNG THÁI: KÍCH HOẠT      ║
║ ⏳ {han}
╠═══════════════════════════════╣
║ 📖 /huongdan | 🔐 /login      ║
║ ⚡ /autobet  | 📊 /lichsucau  ║
║ ⏹️ /stop                      ║
╚═══════════════════════════════╝</pre>"""
    else:
        msg = f"""<pre>╔═══════════════════════════════╗
║   🏠 TRANG CHỦ 💎 AUZA        ║
╠═══════════════════════════════╣
║ 🔒 YÊU CẦU KEY VIP KÍCH HOẠT  ║
║ 🔑 LỆNH: /nhapkey MÃ_KEY      ║
║ 📩 MUA TẠI: {ADMIN_USERNAME}
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['huongdan'])
def send_huongdan(message):
    msg = f"""<pre>╔═══════════════════════════════╗
║ 📖 HƯỚNG DẪN VIP | ✨ AUZA    ║
╠═══════════════════════════════╣
║ 🔑 /nhapkey KEY               ║
║ 🔐 /login TAIKHOAN MATKHAU    ║
║ ⚡ /autobet on 2k [flat|x2]   ║
║ 🔁 /autobet mode flat | x2   ║
║ 📊 /lichsucau | 💎 /thongtin  ║
║ ⏹️ /stop | 👑 /taokey 30      ║
╠═══════════════════════════════╣
║ 🚀 GẤP THẾP GIỚI HẠN 1x→8x   ║
║ 🧠 CẦU: 1-1 · 2-2 · 3-3 · 3-2-1 ║
║ 1-2-3 · 2-1-2 · ĐẢO · BẺ · BỆT ║
║ MARKOV 3 · ĐIỂM · XÚC XẮC     ║
║ 📩 HỖ TRỢ: {ADMIN_USERNAME}
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['taokey'])
def send_taokey(message):
    if not is_admin(message):
        return bot.reply_to(message, '⛔ Chỉ admin mới có quyền tạo key')
    
    parts = message.text.split()
    n = 30
    if len(parts) > 1 and parts[1].isdigit():
        n = int(parts[1])
        
    if n <= 0:
        return bot.reply_to(message, '✅ Hướng dẫn: /taokey 7 / 30 / 90')
        
    import random, string
    key = 'VIP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    valid_keys[key] = n
    save_data()
    
    het = datetime.fromtimestamp(time.time() + n * 86400).strftime('%d/%m/%Y %H:%M:%S')
    msg = f"✅ TẠO KEY THÀNH CÔNG:\n🔑 <code>{key}</code>\n⏳ Hạn: {n} NGÀY\n📅 Hết hạn: {het}\n📊 TỔNG KEY CÒN: {len(valid_keys)}"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['danhsachkey'])
def send_danhsachkey(message):
    if not is_admin(message):
        return bot.reply_to(message, '⛔ Chỉ admin')
    if not valid_keys:
        return bot.reply_to(message, '📭 Danh sách key trống')
        
    lines = [f"<code>{k}</code> → {v} NGÀY" for k, v in valid_keys.items()]
    msg = "\n".join(lines) + f"\n\n📊 TỔNG CỘNG: {len(valid_keys)} KEY"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['nhapkey'])
def send_nhapkey(message):
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ Hướng dẫn: /nhapkey VIP-XXXX')
        
    k = parts[1].strip().upper()
    if k in valid_keys:
        d = valid_keys[k]
        authorized_users[actor_id(message)] = time.time() + d * 86400
        del valid_keys[k]
        save_data()
        bot.reply_to(message, f"🎉 KÍCH HOẠT THÀNH CÔNG GÓI {d} NGÀY VIP ✅")
    else:
        bot.reply_to(message, f"❌ KEY KHÔNG HỢP LỆ HOẶC ĐÃ ĐƯỢC SỬ DỤNG\n📩 MUA TẠI: {ADMIN_USERNAME}")

@bot.message_handler(commands=['thongtin'])
def send_thongtin(message):
    cid = message.chat.id
    init_user_state(cid)
    uid = actor_id(message)
    if not check_auth(uid):
        return bot.reply_to(message, '🔒 TÀI KHOẢN CHƯA KÍCH HOẠT VIP')
        
    st = user_states[cid]
    han = '👑 VĨNH VIỄN' if uid == ADMIN_ID else format_expire_time(authorized_users[uid])
    auto_status = '🟢 ĐANG BẬT' if st['auto_bet_enabled'] else '🔴 ĐÃ TẮT'
    
    msg = f"""<pre>╔═══════════════════════════════╗
║      💎 THÔNG TIN VIP 💎      ║
╠═══════════════════════════════╣
║ 🆔 ID: <code>{cid}</code>
║ ⏳ HẠN: {han}
║ ⚡ AUTO: {auto_status}
║ 🎛️ MODE: {'CƯỢC ĐỀU' if st.get('bet_mode') == 'flat' else 'GẤP THẾP'}
║ 🔁 BƯỚC GẤP THẾP: {st['martingale_step']}/{st.get('max_martingale_steps', MAX_MARTINGALE_STEPS)}
║ 💰 VỐN GỐC: {st['base_bet']:,} WIN
║ 💸 CƯỢC HIỆN TẠI: {st['bet_amount']:,} WIN
║ 💵 SỐ DƯ: {st['balance']:,} WIN
║ ✅ THẮNG: {st['total_win']} | ❌ THUA: {st['total_lose']}
║ 📊 DỮ LIỆU: {len(st['history'])} PHIÊN
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['lichsucau'])
def send_lichsucau(message):
    if not check_auth(actor_id(message)):
        return bot.reply_to(message, locked_msg())
        
    st = user_states[message.chat.id]
    if not st['history']:
        return bot.reply_to(message, '📭 Chưa có dữ liệu phiên nào, hãy chờ AI thu thập thêm.')
        
    ls = st['history'][-20:]
    pts = st.get('points_history', [])[-20:]
    dices = st.get('dice_history', [])[-20:]
    t = ls.count('TAI')
    x = ls.count('XIU')
    icons = "".join(['🔵' if i == 'TAI' else '🔴' for i in ls])
    rows = []
    start_idx = max(0, len(st['history']) - len(ls))
    for offset, result in enumerate(ls):
        pidx = start_idx + offset
        point = pts[offset] if offset < len(pts) else '?'
        dice = dices[offset] if offset < len(dices) else [None, None, None]
        dice_text = '-'.join(str(v) for v in dice) if all(v is not None for v in dice) else 'n/a'
        rows.append(f'{offset + 1:02d}. {result} | {dice_text} = {point}')
    pattern = _pattern_label(st['history'])
    msg = (f'📊 20 PHIÊN GẦN NHẤT\n🔵 TÀI: {t} | 🔴 XỈU: {x}\n'
           f'🧩 CẦU HIỆN TẠI: {pattern}\n{icons}\n\n'
           + '\n'.join(rows))
    bot.reply_to(message, msg)

@bot.message_handler(commands=['login'])
def send_login(message):
    if not check_auth(actor_id(message)):
        return bot.reply_to(message, locked_msg())
        
    parts = message.text.split()
    if len(parts) != 3:
        return bot.reply_to(message, '✅ Hướng dẫn: /login TAIKHOAN MATKHAU')
        
    m = bot.reply_to(message, '🔄 Đang mã hóa và kết nối máy chủ...')
    r = login_and_get_token(parts[1], parts[2])
    
    if r.get('_error'):
        return bot.edit_message_text('❌ ' + r['_error'], chat_id=m.chat.id, message_id=m.message_id)
        
    init_user_state(message.chat.id)
    user_states[message.chat.id]['balance'] = r['money']
    msg_success = f"✅ ĐĂNG NHẬP THÀNH CÔNG\n👤 NICKNAME: {r['nickname']}\n💰 SỐ DƯ: {r['money']:,} WIN"
    bot.edit_message_text(msg_success, chat_id=m.chat.id, message_id=m.message_id)
    start_websocket(message.chat.id, r['token'])

@bot.message_handler(commands=['autobet'])
def send_autobet(message):
    cid = message.chat.id
    if not check_auth(actor_id(message)):
        return bot.reply_to(message, locked_msg())
    if cid not in active_sockets:
        return bot.reply_to(message, '⚠️ Bạn phải /login tài khoản game trước!')
        
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ /autobet on 2k flat|x2 | /autobet mode flat|x2 | /autobet off')

    st = user_states[cid]
    action = parts[1].lower()
    if action == 'mode':
        if len(parts) < 3 or parts[2].lower() not in ('flat', 'x2', 'martingale'):
            return bot.reply_to(message, '⚠️ Chọn mode: `/autobet mode flat` hoặc `/autobet mode x2`', parse_mode='Markdown')
        st['bet_mode'] = 'flat' if parts[2].lower() == 'flat' else 'martingale'
        st['martingale_step'] = 0
        st['bet_amount'] = st['base_bet']
        label = 'CƯỢC ĐỀU — THUA KHÔNG TĂNG TIỀN' if st['bet_mode'] == 'flat' else 'GẤP THẾP GIỚI HẠN 1x→8x'
        return bot.reply_to(message, f'✅ ĐÃ CHUYỂN SANG: {label}\n💰 MỨC HIỆN TẠI: {st["base_bet"]:,} WIN')

    if action == 'on':
        amt = 10000
        mode = st.get('bet_mode', 'martingale')
        if len(parts) >= 3:
            parsed_amount = parse_bet_amount(parts[2])
            if parsed_amount is not None:
                amt = parsed_amount
                if len(parts) >= 4:
                    mode_arg = parts[3].lower()
                else:
                    mode_arg = None
            else:
                mode_arg = parts[2].lower()
        else:
            mode_arg = None
        if mode_arg:
            if mode_arg not in ('flat', 'x2', 'martingale'):
                return bot.reply_to(message, '⚠️ Mode phải là `flat` hoặc `x2`.', parse_mode='Markdown')
            mode = 'flat' if mode_arg == 'flat' else 'martingale'
        elif len(parts) >= 3 and parse_bet_amount(parts[2]) is None:
            return bot.reply_to(message, '⚠️ Số tiền không hợp lệ. Ví dụ: `/autobet on 2k flat`', parse_mode='Markdown')

        st['auto_bet_enabled'] = True
        st['bet_mode'] = mode
        st['base_bet'] = amt
        st['bet_amount'] = amt
        st['martingale_step'] = 0
        if mode == 'flat':
            msg = f'🟢 AUTO ĐÃ BẬT — CƯỢC ĐỀU\n💰 MỖI PHIÊN: {amt:,} WIN\n🛑 Thua không tăng tiền; luôn giữ {amt:,} WIN.'
        else:
            msg = f'🟢 AUTO ĐÃ BẬT — GẤP THẾP GIỚI HẠN\n💰 LỘ TRÌNH: {amt:,} → {amt*2:,} → {amt*4:,} → {amt*8:,}\n🛑 Thua ở mức {amt*8:,} sẽ quay về {amt:,}, không tăng tiếp.'
        bot.reply_to(message, msg)
    elif action == 'off':
        st['auto_bet_enabled'] = False
        st['martingale_step'] = 0
        st['bet_amount'] = st['base_bet']
        bot.reply_to(message, '🔴 AUTO ĐÃ DỪNG LẠI AN TOÀN — ĐÃ RESET VỀ VỐN GỐC')
    else:
        bot.reply_to(message, '⚠️ Lệnh không hợp lệ. Dùng `/autobet on 2k flat`, `/autobet mode x2` hoặc `/autobet off`.', parse_mode='Markdown')

@bot.message_handler(commands=['stop'])
def send_stop(message):
    cid = message.chat.id
    if not check_auth(actor_id(message)):
        return bot.reply_to(message, locked_msg())
        
    if cid in active_sockets:
        try: active_sockets[cid].disconnect()
        except: pass
        del active_sockets[cid]
        if cid in user_states:
            user_states[cid]['auto_bet_enabled'] = False
        bot.reply_to(message, '⏹️ ĐÃ NGẮT KẾT NỐI MÁY CHỦ AN TOÀN')
    else:
        bot.reply_to(message, '⚠️ Bạn chưa kết nối nên không thể ngắt')

# ==========================================
# 🚀 FLASK SERVER - GIỮ BOT SỐNG TRÊN RENDER CỰC MƯỢT
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    return f"🚀 BOT AUZA VẢ CHẾT NHÀ CÁI ĐANG HOẠT ĐỘNG! TIMESTAMP: {int(time.time() * 1000)}"

def run_flask():
    port = int(os.environ.get('PORT', 3000))
    logger['info'](f'🌐 FLASK WEB SERVER CHẠY TRÊN PORT: {port} (CHỐNG TREO RENDER)')
    # Vô hiệu hóa debug & reloader để tránh lỗi chạy 2 luồng trong thread
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==========================================
# 🚀 CHẠY BOT + CHỐNG TREO RENDER
# ==========================================
if __name__ == '__main__':
    logger['info']('👑 AUZA VẢ CHẾT NHÀ CÁI ONLINE ✨ PYTHON + PING RENDER + FLASK')
    
    # 1. Chạy Anti-Sleep Ping Render
    start_anti_sleep()
    
    # 2. Chạy Flask Web Server ở một luồng độc lập
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 3. Vòng lặp Telegram Bot chính
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger['error'](f'BOT POLLING ERR: {e} → ĐANG KẾT NỐI LẠI...')
            time.sleep(3)