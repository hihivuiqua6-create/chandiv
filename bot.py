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
from bridge_analyzer import analyze_bridges, format_analysis

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
MAX_HISTORY_STORE = 100
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
            'history': [], 'points_history': [], 'session_records': [],
            'auto_bet_enabled': False, 'bet_amount': 10000, 'base_bet': 10000,
            'martingale_step': 0, 'max_martingale_steps': MAX_MARTINGALE_STEPS,
            'current_prediction': None, 'waiting_for_result': False,
            'has_bet_this_session': False, 'session_id': None,
            'balance': 0, 'win_streak': 0, 'lose_streak': 0,
            'total_win': 0, 'total_lose': 0,
            'lastPingAt': 0, 'betLock': False
        }

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ TẢI LỊCH SỬ TỪ API (GIỐNG GỐC)                                                 ║
# ╚══════════════════════════════════════════════════════════════╝
def fetch_history_from_api(limit=50):
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
            return [], []
        
        lst = list(reversed(lst))[-limit:]
        ketqua, diem = [], []
        for p in lst:
            res = p.get('resultTruyenThong')
            tong = p.get('point') or sum(p.get('dices', [0,0,0]))
            if res in ['TAI', 'XIU']:
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
# 🧠 THUẬT TOÁN VIP PRO MAX — DỊCH CHÍNH XÁC TỪNG DÒNG
# ==========================================
def make_prediction_vip(history, points=None):
    if points is None: points = []
    if len(history) < 3:
        import random
        return 'TAI' if random.random() < 0.5 else 'XIU'
    
    hist_str = "".join(['T' if x == 'TAI' else 'X' for x in history[-30:]])
    last = hist_str[-1]
    score_tai = 0
    score_xiu = 0
    ket_qua_chinh = None
    do_tin_cay = 0

    # 💎 1. CẦU RỒNG
    if re.search(r'TTTTTTT$|XXXXXXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = 95
    elif re.search(r'TTTTTT$|XXXXXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = 90
    elif re.search(r'TTTTT$|XXXXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = 85
    elif re.search(r'TTTT$|XXXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = 80
    elif re.search(r'TTT$|XXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = 72

    # 💎 3. CẦU ĐỨT
    if re.search(r'TTTTTTTT$|XXXXXXXX$', hist_str):
        ket_qua_chinh = 'XIU' if last == 'T' else 'TAI'
        do_tin_cay = 88
    if len(history) >= 15:
        cnt_t = history[-15:].count('TAI')
        cnt_x = 15 - cnt_t
        if re.search(r'TTTTT$', hist_str) and cnt_t > 11:
            ket_qua_chinh = 'XIU'
            do_tin_cay = max(do_tin_cay, 80)
        if re.search(r'XXXXX$', hist_str) and cnt_x > 11:
            ket_qua_chinh = 'TAI'
            do_tin_cay = max(do_tin_cay, 80)

    # 💎 4→7. CẦU 1-1 / 2-2 / 3-3 / 4-4
    if re.search(r'TXTXTX$|XTXTXT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'X' else 'XIU'
        do_tin_cay = max(do_tin_cay, 87)
    elif re.search(r'TXTX$|XTXT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'X' else 'XIU'
        do_tin_cay = max(do_tin_cay, 78)
        
    if re.search(r'TTXXTTXX$|XXTTXXTT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 86)
    elif re.search(r'TTXX$|XXTT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 76)
        
    if re.search(r'TTTXXXTTT$|XXXTTTXXX$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 84)
    elif re.search(r'TTTXXX$|XXXTTT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 75)
        
    if re.search(r'TTTTXXXX$|XXXXTTTT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 82)
        
    # ✅ CẦU 5-5
    if re.search(r'TTTTTXXXXX$|XXXXXTTTTT$', hist_str):
        ket_qua_chinh = 'TAI' if last == 'T' else 'XIU'
        do_tin_cay = max(do_tin_cay, 83)

    # 💎 8. CẦU BÁM ĐUÔI 2
    if re.search(r'TXT$', hist_str):
        ket_qua_chinh = 'TAI'
        do_tin_cay = max(do_tin_cay, 68)
    if re.search(r'XTX$', hist_str):
        ket_qua_chinh = 'XIU'
        do_tin_cay = max(do_tin_cay, 68)

    # 💎 9. THỐNG KÊ
    r10 = history[-10:]
    r5 = history[-5:]
    t10 = r10.count('TAI')
    x10 = 10 - t10
    t5 = r5.count('TAI')
    x5 = 5 - t5
    
    if t5 >= 4: score_tai += dynamic_weights['thongke']
    elif x5 >= 4: score_xiu += dynamic_weights['thongke']
    
    if t10 > x10 + 2: score_tai += dynamic_weights['thongke'] - 1
    elif x10 > t10 + 2: score_xiu += dynamic_weights['thongke'] - 1
    elif t10 > x10: score_tai += 1
    elif x10 > t10: score_xiu += 1

    # ✅ ĐIỂM XÚC XẮC
    if len(points) >= 10:
        p = points[-10:]
        avg = sum(p) / 10
        if avg > 11.2: score_tai += dynamic_weights['diem_xucxac']
        elif avg < 9.8: score_xiu += dynamic_weights['diem_xucxac']
        v = sum([(x - avg)**2 for x in p]) / 10
        if v < 2.5:
            if avg > 10.5: score_tai += 2
            else: score_xiu += 2

    # 💎 10+ ✅ MARKOV 1+2+3
    m1 = {'TAI': {'TAI': 0, 'XIU': 0}, 'XIU': {'TAI': 0, 'XIU': 0}}
    m2 = {}
    m3 = {}
    
    for i in range(len(history)-1):
        m1[history[i]][history[i+1]] += 1
        
    for i in range(len(history)-2):
        k = history[i] + history[i+1]
        if k not in m2: m2[k] = {'TAI': 0, 'XIU': 0}
        m2[k][history[i+2]] += 1
        
    for i in range(len(history)-3):
        k = history[i] + history[i+1] + history[i+2]
        if k not in m3: m3[k] = {'TAI': 0, 'XIU': 0}
        m3[k][history[i+3]] += 1

    cur = 'TAI' if last == 'T' else 'XIU'
    t1 = m1[cur]
    if t1['TAI'] > t1['XIU'] * 1.2: score_tai += dynamic_weights['markov1']
    elif t1['XIU'] > t1['TAI'] * 1.2: score_xiu += dynamic_weights['markov1']
    else:
        if cur == 'TAI': score_tai += 1
        else: score_xiu += 1

    if len(hist_str) >= 2:
        prev = 'TAI' if hist_str[-2] == 'T' else 'XIU'
        k2 = prev + cur
        if k2 in m2:
            if m2[k2]['TAI'] > m2[k2]['XIU'] * 1.3: score_tai += dynamic_weights['markov2']
            elif m2[k2]['XIU'] > m2[k2]['TAI'] * 1.3: score_xiu += dynamic_weights['markov2']
            
    if len(hist_str) >= 3:
        p3 = 'TAI' if hist_str[-3] == 'T' else 'XIU'
        p2 = 'TAI' if hist_str[-2] == 'T' else 'XIU'
        k3 = p3 + p2 + cur
        if k3 in m3:
            if m3[k3]['TAI'] > m3[k3]['XIU'] * 1.3: score_tai += dynamic_weights['markov3']
            elif m3[k3]['XIU'] > m3[k3]['TAI'] * 1.3: score_xiu += dynamic_weights['markov3']

    # 💎 11. TỔNG HỢP BỔ SUNG: BỎ PHIẾU THEO ĐỘ MỚI CỦA DỮ LIỆU
    # Các phiên gần nhất có trọng số cao hơn nhưng không được phép lấn át hoàn toàn
    # các tín hiệu Markov/thống kê, giúp giảm việc bám mù quáng vào một mẫu ngắn.
    for idx, result in enumerate(history[-12:], start=1):
        weight = 0.35 + (idx / 12) * 1.15
        if result == 'TAI':
            score_tai += weight
        else:
            score_xiu += weight

    # 💎 11b. BỘ NHẬN DIỆN CẦU MỞ RỘNG
    # Tín hiệu được cộng điểm theo độ mạnh; khi các cầu xung đột, điểm cộng nhỏ
    # để tránh bám mù quáng vào một mẫu ngắn.
    bridge = analyze_bridges(history, points)
    for signal in bridge.get('signals', []):
        direction = signal.get('direction')
        if direction not in ('TAI', 'XIU'):
            continue
        vote = max(1.0, float(signal.get('strength', 50)) / 25.0)
        if direction == 'TAI':
            score_tai += vote
        else:
            score_xiu += vote

    # Khi dữ liệu quá cân bằng, giảm độ tự tin thay vì tạo dự đoán giả chắc chắn.
    if abs(score_tai - score_xiu) < 1.0:
        ket_qua_chinh = None

    # 💎 12. QUYẾT ĐỊNH
    if ket_qua_chinh:
        b = math.floor(do_tin_cay / 8)
        if ket_qua_chinh == 'TAI': score_tai += b
        else: score_xiu += b
        
    if score_tai > score_xiu: return 'TAI'
    if score_xiu > score_tai: return 'XIU'
    return history[-1]

def tinh_do_tin_cay(history, points=None):
    if len(history) < 5: return 50
    hs = "".join(['T' if x == 'TAI' else 'X' for x in history[-20:]])
    base = 62
    if re.search(r'TTTTTT$|XXXXXX$', hs): base = 92
    elif re.search(r'TTTTT$|XXXXX$', hs): base = 88
    elif re.search(r'TXTXTX$|XTXTXT$', hs): base = 85
    elif re.search(r'TTTT$|XXXX$', hs): base = 82
    elif re.search(r'TTXXTTXX$|XXTTXXTT$', hs): base = 80
    elif re.search(r'TTTXXX$|XXXTTT$', hs): base = 78
    elif re.search(r'TXTX$|XTXT$', hs): base = 75
    elif re.search(r'TTXX$|XXTT$', hs): base = 73
    elif re.search(r'TTT$|XXX$', hs): base = 70
    
    if len(history) >= 10:
        r10 = history[-10:]
        base += min(abs(r10.count('TAI') - r10.count('XIU')) * 2, 8)
    bridge = analyze_bridges(history, points)
    if bridge.get('signals'):
        base = max(base, min(bridge.get('confidence', 0), 90))
    return min(base, 98)

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
        lk, ld = fetch_history_from_api(50)
        tb = ''
        if lk:
            st['history'] = lk[-MAX_HISTORY_STORE:]
            st['points_history'] = ld[-MAX_HISTORY_STORE:]
            st['session_records'] = [
                {'session_id': None, 'dices': [], 'point': point, 'result': result, 'timestamp': None}
                for result, point in zip(st['history'], st['points_history'])
            ]
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
║ 📊 ĐÃ THU THẬP: {n}/20 KẾT QUẢ</pre>"""
        
        if n >= 3:
            pred = make_prediction_vip(st['history'], st['points_history'])
            st['current_prediction'] = pred
            bridge = analyze_bridges(st['history'], st['points_history'])
            icon = '🔵 TÀI' if pred == 'TAI' else '🔴 XỈU'
            msg += f"\n<pre>╠═══════════════════════════════╣\n║ 🤖 AI: {icon} | 📈 {dt}%</pre>"
            
            if st['auto_bet_enabled']:
                if dt >= MIN_CONFIDENCE_AUTO_BET:
                    msg += f"\n<pre>║ ⚡ AUTO ON — {st['bet_amount']:,} WIN</pre>"
                else:
                    msg += f"\n<pre>║ ⚠️ ĐỘ TIN <{MIN_CONFIDENCE_AUTO_BET}% → BỎ QUA</pre>"
            if bridge.get('signals'):
                msg += f"\n{format_analysis(bridge, max_signals=4)}"
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
║ 🔄 CƠ CHẾ X2: {'KÍCH HOẠT' if st['bet_amount'] > st['base_bet'] else 'GỐC'}
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
            st['session_records'].append({
                'session_id': st.get('session_id'),
                'dices': list(d) if isinstance(d, list) else [],
                'point': tong,
                'result': kq,
                'timestamp': datetime.now().strftime('%d/%m %H:%M:%S')
            })
            if len(st['history']) > MAX_HISTORY_STORE:
                st['history'].pop(0)
                st['points_history'].pop(0)
            if len(st['session_records']) > MAX_HISTORY_STORE:
                st['session_records'].pop(0)
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
    if check_auth(cid):
        han = '👑 VĨNH VIỄN - ADMIN' if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
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
║ ⚡ /autobet on 10000 | off    ║
║ 📊 /lichsucau | 💎 /thongtin  ║
║ ⏹️ /stop | 👑 /taokey 30      ║
╠═══════════════════════════════╣
║ 🚀 GẤP THẾP GIỚI HẠN 1x→8x   ║
║ 🧠 AI: RỒNG · ĐỨT · 1-1→5-5   ║
║ 321 · 123 · 212 · 2112 · 1212 ║
║ ĐẢO · NGHỊCH ĐẢO · BẺ · CHU KỲ ║
║ MARKOV 3 · THỐNG KÊ · XÚC XẮC ║
║ 📩 HỖ TRỢ: {ADMIN_USERNAME}
╚═══════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['taokey'])
def send_taokey(message):
    if message.chat.id != ADMIN_ID:
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
    if message.chat.id != ADMIN_ID:
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
        authorized_users[message.chat.id] = time.time() + d * 86400
        del valid_keys[k]
        save_data()
        bot.reply_to(message, f"🎉 KÍCH HOẠT THÀNH CÔNG GÓI {d} NGÀY VIP ✅")
    else:
        bot.reply_to(message, f"❌ KEY KHÔNG HỢP LỆ HOẶC ĐÃ ĐƯỢC SỬ DỤNG\n📩 MUA TẠI: {ADMIN_USERNAME}")

@bot.message_handler(commands=['thongtin'])
def send_thongtin(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, '🔒 TÀI KHOẢN CHƯA KÍCH HOẠT VIP')
        
    st = user_states[cid]
    han = '👑 VĨNH VIỄN' if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
    auto_status = '🟢 ĐANG BẬT' if st['auto_bet_enabled'] else '🔴 ĐÃ TẮT'
    
    msg = f"""<pre>╔═══════════════════════════════╗
║      💎 THÔNG TIN VIP 💎      ║
╠═══════════════════════════════╣
║ 🆔 ID: <code>{cid}</code>
║ ⏳ HẠN: {han}
║ ⚡ AUTO: {auto_status}
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
    if not check_auth(message.chat.id):
        return bot.reply_to(message, locked_msg())
        
    st = user_states[message.chat.id]
    if not st['history']:
        return bot.reply_to(message, '📭 Chưa có dữ liệu phiên nào, hãy chờ AI thu thập thêm.')
        
    ls = st['history'][-20:]
    t = ls.count('TAI')
    x = ls.count('XIU')
    icons = "".join(['🔵' if i == 'TAI' else '🔴' for i in ls])
    analysis = analyze_bridges(st['history'], st['points_history'])
    rows = []
    for rec in st.get('session_records', [])[-12:]:
        sid = rec.get('session_id') or 'API'
        dice = '-'.join(str(v) for v in rec.get('dices', [])) or '?'
        rows.append(f"{sid} | {dice} = {rec.get('point', '?')} | {rec.get('result', '?')}")
    detail = "\n".join(rows) if rows else "Chưa có chi tiết phiên realtime."
    msg = (f"📊 <b>LỊCH SỬ 20 PHIÊN GẦN NHẤT</b>\n🔵 TÀI: {t} | 🔴 XỈU: {x}\n"
           f"{icons}\n\n{format_analysis(analysis)}\n\n"
           f"🎲 <b>PHIÊN / XÚC XẮC / ĐIỂM / KẾT QUẢ</b>\n<code>{detail}</code>\n\n"
           "⚠️ Đây là nhận diện thống kê theo dữ liệu đã thấy, không bảo đảm phiên kế tiếp.")
    bot.reply_to(message, msg)

@bot.message_handler(commands=['login'])
def send_login(message):
    if not check_auth(message.chat.id):
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
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    if cid not in active_sockets:
        return bot.reply_to(message, '⚠️ Bạn phải /login tài khoản game trước!')
        
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ Cú pháp: /autobet on 2k | off')
        
    st = user_states[cid]
    if parts[1].lower() == 'on':
        amt = 10000
        if len(parts) >= 3:
            parsed_amount = parse_bet_amount(parts[2])
            if parsed_amount is None:
                return bot.reply_to(message, '⚠️ Số tiền không hợp lệ. Ví dụ: /autobet on 2000 hoặc /autobet on 2k')
            amt = parsed_amount

        st['auto_bet_enabled'] = True
        st['base_bet'] = amt
        st['bet_amount'] = amt
        st['martingale_step'] = 0
        msg = f"🟢 AUTO ĐÃ BẬT\n💰 VỐN MỖI CHU KỲ: {amt:,} WIN\n🔁 LỘ TRÌNH: {amt:,} → {amt*2:,} → {amt*4:,} → {amt*8:,}\n🛑 Thua ở mức {amt*8:,} sẽ quay về {amt:,}, không tăng lên {amt*16:,}.\n(Chạy đến khi dùng /autobet off hoặc /stop)"
        bot.reply_to(message, msg)
    else:
        st['auto_bet_enabled'] = False
        st['martingale_step'] = 0
        st['bet_amount'] = st['base_bet']
        bot.reply_to(message, '🔴 AUTO ĐÃ DỪNG LẠI AN TOÀN — ĐÃ RESET VỀ VỐN GỐC')

@bot.message_handler(commands=['stop'])
def send_stop(message):
    cid = message.chat.id
    if not check_auth(cid):
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