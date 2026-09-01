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
    telebot.types.BotCommand("tatx2khithua", "🚫 Tắt gấp thếp X2 khi thua (vẫn cược)"),
    telebot.types.BotCommand("mox2khithua", "🔁 Mở lại gấp thếp X2 khi thua"),
    telebot.types.BotCommand("lichsucau", "📊 Xem lịch sử cầu gần nhất"),
    telebot.types.BotCommand("nhandiencau", "🧠 AI nhận diện loại cầu hiện tại"),
    telebot.types.BotCommand("stop", "⏹️ Ngắt kết nối an toàn"),
    telebot.types.BotCommand("taokey", "👑 [ADMIN] Tạo key bản quyền"),
    telebot.types.BotCommand("danhsachkey", "📋 [ADMIN] Xem danh sách key còn lại"),
    telebot.types.BotCommand("thongbao", "📢 [ADMIN] Gửi thông báo tới toàn bộ user"),
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
all_users = set()   # ✅ toàn bộ user từng dùng bot → dùng cho /thongbao

# ✅ LƯU KEY / NGƯỜI DÙNG RA FILE → KHÔNG MẤT KHI RESTART
SAVE_FILE = './bot_save.json'

def save_data():
    try:
        with open(SAVE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'valid_keys': valid_keys, 'authorized_users': authorized_users,
                       'all_users': list(all_users)}, f, indent=2)
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
    else:
        logger['info']('Chưa có dữ liệu lưu, tạo mới')
except Exception as e:
    logger['info'](f'Chưa có dữ liệu lưu, tạo mới. Lỗi: {e}')

def track_user(chat_id):
    """Ghi nhận user để dùng cho /thongbao"""
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
            'total_win': 0, 'total_lose': 0,
            'lastPingAt': 0, 'betLock': False,
            'x2_on_lose': True,       # gấp thếp khi thua (bật/tắt bằng /tatx2khithua, /mox2khithua)
            'always_bet': True        # LUÔN cược mỗi phiên, không bao giờ tự tắt auto
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

# ╔══════════════════════════════════════════════════════════════╗
# ║  🧠 AI NHẬN DIỆN CẦU NÂNG CAO (BỆT · 1-1 · 2-2 · 3-3 · 321 · ║
# ║  123 · 212 · 2112 · 1212 · NGHỊCH ĐẢO · BẺ CẦU · CHU KỲ...)  ║
# ╚══════════════════════════════════════════════════════════════╝
def _to_str(history):
    return "".join(['T' if x == 'TAI' else 'X' for x in history])

def _dao(c):
    return 'X' if c == 'T' else 'T'

def _kq(c):
    return 'TAI' if c == 'T' else 'XIU'

def _runs(hist_str):
    """Chuyển chuỗi TTXXX... thành danh sách độ dài từng đoạn: [2,3,...]"""
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

def nhan_dien_cau(history, points=None):
    """
    Trả về danh sách các cầu nhận diện được, mỗi phần tử:
    {'ten': str, 'du_doan': 'TAI'/'XIU', 'tin_cay': int, 'mo_ta': str}
    Sắp xếp theo độ tin cậy giảm dần.
    """
    if points is None:
        points = []
    out = []
    hs = _to_str(history)
    if len(hs) < 3:
        return out
    last = hs[-1]
    lens, chars = _runs(hs)

    def add(ten, du_doan, tin_cay, mo_ta, chinh=False):
        out.append({'ten': ten, 'du_doan': du_doan, 'tin_cay': int(tin_cay),
                    'mo_ta': mo_ta, 'chinh': bool(chinh)})

    streak = lens[-1]

    # ─── 1. CẦU BỆT = TỪ 4 NHÁY TRỞ LÊN ──────────────────────────
    if streak >= 4:
        ten = f"CẦU BỆT {streak} {'TÀI' if last == 'T' else 'XỈU'}"
        if streak >= 9:
            add(f"BẺ CẦU BỆT {streak}", _kq(_dao(last)), 88,
                f"Bệt {streak} nháy quá dài, xác suất gãy rất cao — bẻ cầu", True)
        else:
            add(ten, _kq(last), min(78 + (streak - 4) * 4, 94),
                f"Bệt {streak} nháy liên tiếp (≥4 = bệt) → theo bệt", True)

    # ─── 2. CẦU N-N (1-1, 2-2, 3-3, 4-4, 5-5) ────────────────────
    # Quy tắc: nếu các đoạn gần nhất đều dài đúng n (ít nhất 2 đoạn trước
    # + đoạn hiện tại), thì:
    #   • đoạn hiện tại đã đủ n  → phiên sau ĐẢO CHIỀU (vd 33: vừa xong 3 xỉu → đặt TÀI)
    #   • đoạn hiện tại chưa đủ n → phiên sau THEO đoạn hiện tại
    if streak <= 5 and len(lens) >= 3:
        for n, base in ((1, 90), (3, 90), (2, 89), (4, 86), (5, 85)):
            truoc = lens[-3:-1]                      # 2 đoạn liền trước
            if len(truoc) == 2 and all(x == n for x in truoc) and streak <= n:
                so_chu_ky = 0
                for L in reversed(lens[:-1]):
                    if L == n:
                        so_chu_ky += 1
                    else:
                        break
                ten = f"CẦU {n}-{n}" if n > 1 else "CẦU 1-1"
                ten += f" ({so_chu_ky} đoạn liên tiếp)"
                if streak >= n:
                    add(ten, _kq(_dao(last)), min(base + so_chu_ky, 96),
                        f"Nhịp {n}-{n}: đoạn hiện tại đã đủ {n} → phiên sau đảo chiều", True)
                else:
                    add(ten, _kq(last), min(base + so_chu_ky - 2, 95),
                        f"Nhịp {n}-{n}: đoạn hiện tại mới {streak}/{n} → theo tiếp", True)
                break

    # ─── 3. CẦU 3-2-1 (và biến thể 1-2-3) ────────────────────────
    if len(lens) >= 3:
        l3 = lens[-3:]
        if l3 == [3, 2, 1]:
            add("CẦU 3-2-1", _kq(_dao(last)), 88,
                "3 nháy → 2 nháy → 1 nháy: chu kỳ khép, phiên sau đảo chiều mở đoạn 3 mới", True)
        elif l3 == [1, 2, 3]:
            add("CẦU 1-2-3", _kq(last), 84, "Tháp tăng 1→2→3, đoạn 3 còn kéo dài", True)

        if l3 == [2, 1, 2] and streak >= 2:
            add("CẦU 2-1-2", _kq(_dao(last)), 80, "Mẫu 2-1-2 hoàn tất → bẻ")
        if l3 == [1, 2, 1]:
            add("CẦU 1-2-1", _kq(last), 76, "Mẫu 1-2-1 → chuẩn bị đoạn 2")
    # đang dở nhịp 3-2-1 (vừa xong 3, đang ở đoạn 2)
    if len(lens) >= 2 and lens[-2] == 3 and streak <= 2:
        if streak == 2:
            add("CẦU 3-2-1 (chờ nhịp 1)", _kq(_dao(last)), 82,
                "Vừa 3 nháy, đoạn 2 đã đủ → phiên sau nhịp 1 đảo chiều", True)
        else:
            add("CẦU 3-2-1 (đoạn 2)", _kq(last), 80,
                "Vừa 3 nháy, đang mở đoạn 2 → theo tiếp cho đủ 2", True)

    if len(lens) >= 4:
        l4 = lens[-4:]
        if l4 == [2, 1, 1, 2] and streak >= 2:
            add("CẦU 2-1-1-2", _kq(_dao(last)), 82, "Mẫu 2112 khép kín → đảo")
        if l4 == [1, 2, 1, 2] and streak >= 2:
            add("CẦU 1-2-1-2", _kq(_dao(last)), 81, "Mẫu 1212 → sang nhịp 1")
        if l4 == [4, 3, 2, 1]:
            add("CẦU THÁP GIẢM 4-3-2-1", _kq(_dao(last)), 83, "Tháp giảm dần → tiếp tục đảo")
        if l4 == [1, 2, 3, 4]:
            add("CẦU THÁP TĂNG 1-2-3-4", _kq(last), 80, "Tháp tăng dần → giữ đoạn")


    # ─── 6. CẦU NGHỊCH ĐẢO (soi gương) ───────────────────────────
    if len(hs) >= 12:
        a, b = hs[-12:-6], hs[-6:]
        if all(_dao(x) == y for x, y in zip(a, b)):
            nxt = _dao(hs[-12 + 6 - 6 + 6])  # phần tử kế tiếp theo gương
            nxt = _dao(hs[-6])
            add("CẦU NGHỊCH ĐẢO", _kq(nxt), 85, "6 phiên gần nhất là ảnh đảo của 6 phiên trước")
    if len(hs) >= 8:
        a, b = hs[-8:-4], hs[-4:]
        if all(_dao(x) == y for x, y in zip(a, b)):
            add("CẦU NGHỊCH ĐẢO NGẮN", _kq(_dao(hs[-4])), 76, "4 phiên gần nhất đảo ngược 4 phiên trước")

    # ─── 7. CẦU LẶP CHU KỲ (pattern repeat) ──────────────────────
    for k in (3, 4, 5, 6):
        if len(hs) >= k * 2:
            if hs[-k:] == hs[-2 * k:-k]:
                nxt = hs[-k]
                add(f"CẦU LẶP CHU KỲ {k}", _kq(nxt), 74 + k, f"Khối {k} phiên lặp lại y hệt → dự đoán theo chu kỳ")
                break

    # ─── 8. CẦU BẺ / GÃY ─────────────────────────────────────────
    if len(lens) >= 3 and lens[-1] == 1 and lens[-2] >= 4:
        add("CẦU VỪA BẺ", _kq(last), 75, f"Bệt {lens[-2]} vừa gãy → theo cầu mới")
    if len(lens) >= 4 and lens[-4:-1] == [1, 1, 1]:
        add("CẦU NHỊP 1 LIÊN TIẾP", _kq(_dao(last)), 72, "Đang chạy nhịp lẻ 1-1-1")

    # ─── 9. CẦU KÈO ĐIỂM XÚC XẮC ─────────────────────────────────
    if len(points) >= 6:
        p6 = points[-6:]
        avg = sum(p6) / 6
        if avg >= 12.0:
            add("CẦU ĐIỂM CAO", 'TAI', 70, f"Điểm TB 6 phiên = {avg:.1f} (nghiêng Tài)")
        elif avg <= 9.0:
            add("CẦU ĐIỂM THẤP", 'XIU', 70, f"Điểm TB 6 phiên = {avg:.1f} (nghiêng Xỉu)")
        if len(points) >= 3:
            d1, d2 = points[-1] - points[-2], points[-2] - points[-3]
            if d1 > 0 and d2 > 0:
                add("CẦU ĐIỂM TĂNG DẦN", 'TAI', 66, "Tổng điểm 3 phiên tăng liên tiếp")
            elif d1 < 0 and d2 < 0:
                add("CẦU ĐIỂM GIẢM DẦN", 'XIU', 66, "Tổng điểm 3 phiên giảm liên tiếp")

    # ─── 10. CẦU LỆCH (thống kê 20 phiên) ────────────────────────
    if len(history) >= 20:
        r20 = history[-20:]
        t20 = r20.count('TAI')
        if t20 >= 14:
            add("CẦU LỆCH TÀI", 'XIU', 68, f"{t20}/20 phiên ra Tài → cân bằng về Xỉu")
        elif t20 <= 6:
            add("CẦU LỆCH XỈU", 'TAI', 68, f"{20 - t20}/20 phiên ra Xỉu → cân bằng về Tài")

    out.sort(key=lambda x: x['tin_cay'], reverse=True)
    return out

def tong_hop_cau(history, points=None):
    """Gộp phiếu tất cả cầu nhận diện được → (du_doan, tin_cay, cau_manh_nhat, danh_sach)

    ƯU TIÊN 1: cầu CHÍNH đã định nghĩa rõ (BỆT ≥4, 1-1, 2-2, 3-3, 4-4, 5-5, 3-2-1)
               → chốt thẳng theo luật cầu đó, không cho các cầu phụ pha loãng.
    ƯU TIÊN 2: các cầu còn lại → gộp phiếu tính toán theo phiên trước + gần nhất.
    """
    ds = nhan_dien_cau(history, points)
    if not ds:
        return None, 0, None, []

    chinh = [c for c in ds if c.get('chinh')]
    if chinh:
        top = max(chinh, key=lambda x: x['tin_cay'])
        # cầu phụ đồng thuận → cộng thêm tin cậy, ngược chiều → trừ nhẹ
        dong = sum(1 for c in ds if c is not top and c['du_doan'] == top['du_doan'])
        nguoc = sum(1 for c in ds if c is not top and c['du_doan'] != top['du_doan'])
        tin = min(max(top['tin_cay'] + dong * 2 - nguoc, 55), 97)
        return top['du_doan'], tin, top, ds

    st_, sx_ = 0.0, 0.0
    for c in ds:
        w = c['tin_cay'] / 10.0
        if c['du_doan'] == 'TAI':
            st_ += w
        else:
            sx_ += w
    top = ds[0]
    if abs(st_ - sx_) < 0.5:
        # cân bằng → nghiêng theo phiên gần nhất (bám bệt nếu 2 phiên cuối trùng, ngược lại bẻ)
        if len(history) >= 2 and history[-1] == history[-2]:
            dd = history[-1]
        else:
            dd = 'XIU' if history[-1] == 'TAI' else 'TAI'
        return dd, max(top['tin_cay'] - 10, 55), top, ds
    du_doan = 'TAI' if st_ > sx_ else 'XIU'
    tin_cay = top['tin_cay'] if top['du_doan'] == du_doan else max(top['tin_cay'] - 10, 55)
    return du_doan, tin_cay, top, ds


def format_bang_cau(history, points=None, dices=None, limit=12):
    """Bảng nhận diện cầu + lịch sử phiên + xúc xắc."""
    du_doan, tin_cay, top, ds = tong_hop_cau(history, points)
    hs = _to_str(history)
    icons = "".join(['🔵' if c == 'T' else '🔴' for c in hs[-20:]])
    lines = ["<b>🧠 AI NHẬN DIỆN CẦU — AUZA ELITE</b>", f"📈 {icons}"]
    if not ds:
        lines.append("⏳ Chưa đủ dữ liệu để nhận diện cầu.")
        return "\n".join(lines)
    lines.append("")
    lines.append("<b>🔎 CÁC LOẠI CẦU PHÁT HIỆN:</b>")
    for c in ds[:6]:
        ic = '🔵 TÀI' if c['du_doan'] == 'TAI' else '🔴 XỈU'
        lines.append(f"• {c['ten']} → {ic} ({c['tin_cay']}%)\n  <i>{c['mo_ta']}</i>")
    ic = '🔵 TÀI' if du_doan == 'TAI' else '🔴 XỈU'
    lines.append("")
    lines.append(f"<b>🎯 CHỐT THEO CẦU: {ic} — ĐỘ TIN {tin_cay}%</b>")
    lines.append(f"👑 CẦU MẠNH NHẤT: {top['ten']}")
    if dices:
        lines.append("")
        lines.append("<b>🎲 XÚC XẮC & ĐIỂM CÁC PHIÊN GẦN NHẤT:</b>")
        n = min(limit, len(dices), len(history))
        for i in range(-n, 0):
            d = dices[i]
            tong = points[i] if points and len(points) >= n else sum(d)
            kq = '🔵 TÀI' if history[i] == 'TAI' else '🔴 XỈU'
            lines.append(f"🎲 {d[0]}-{d[1]}-{d[2]} = {tong} → {kq}")
    return "\n".join(lines)

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

    # Khi dữ liệu quá cân bằng, giảm độ tự tin thay vì tạo dự đoán giả chắc chắn.
    if abs(score_tai - score_xiu) < 1.0:
        ket_qua_chinh = None

    # 💎 11.5 ✅ BỘ NHẬN DIỆN CẦU NÂNG CAO (321/123/212/2112/1212/nghịch đảo/bệt/bẻ...)
    cau_pred, cau_conf, cau_top, cau_all = tong_hop_cau(history, points)
    if cau_pred:
        boost = cau_conf / 6.0
        if cau_pred == 'TAI':
            score_tai += boost
        else:
            score_xiu += boost
        if cau_conf >= 80:
            ket_qua_chinh = cau_pred
            do_tin_cay = max(do_tin_cay, cau_conf)

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

    # ✅ Ưu tiên độ tin cậy từ bộ nhận diện cầu nâng cao
    _p, _c, _t, _ds = tong_hop_cau(history, points)
    if _p and _c > base:
        base = _c
    return min(base, 98)

# 🤖 AI DỰ ĐOÁN BẮT BUỘC — LUÔN TRẢ VỀ TÀI/XỈU ĐỂ KHÔNG BỎ PHIÊN NÀO
def ai_du_doan_bat_buoc(st):
    h = st.get('history') or []
    p = st.get('points_history') or []
    try:
        # ưu tiên bộ nhận diện cầu nâng cao
        cau_pred, cau_conf, _top, _all = tong_hop_cau(h, p)
        if cau_pred:
            return cau_pred
    except Exception:
        pass
    try:
        if len(h) >= 3:
            return make_prediction_vip(h, p)
    except Exception:
        pass
    if h:
        # bám bệt nếu 2 phiên cuối giống nhau, ngược lại bẻ cầu
        if len(h) >= 2 and h[-1] == h[-2]:
            return h[-1]
        return 'XIU' if h[-1] == 'TAI' else 'TAI'
    return 'TAI'

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
        if not st.get('x2_on_lose', True):
            # 🚫 Đã tắt X2 khi thua → giữ nguyên vốn gốc nhưng VẪN TIẾP TỤC CƯỢC
            st['martingale_step'] = 0
            st['bet_amount'] = st['base_bet']
        elif st['martingale_step'] < st.get('max_martingale_steps', MAX_MARTINGALE_STEPS):
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
            icon = '🔵 TÀI' if pred == 'TAI' else '🔴 XỈU'
            msg += f"\n<pre>╠═══════════════════════════════╣\n║ 🤖 AI: {icon} | 📈 {dt}%</pre>"
            _p, _c, _top, _ds = tong_hop_cau(st['history'], st['points_history'])
            if _top:
                st['last_pattern'] = _top['ten']
                _ten = " · ".join([c['ten'] for c in _ds[:2]])
                msg += f"\n<pre>║ 🧠 CẦU: {_ten}</pre>"
            
            if st['auto_bet_enabled']:
                _x2 = 'BẬT' if st.get('x2_on_lose', True) else 'TẮT'
                msg += f"\n<pre>║ ⚡ AUTO ON — {st['bet_amount']:,} WIN | X2: {_x2}</pre>"
        else:
            # 🤖 AI vẫn tự quyết định để KHÔNG BỎ PHIÊN NÀO
            st['current_prediction'] = ai_du_doan_bat_buoc(st)
            _icon2 = '🔵 TÀI' if st['current_prediction'] == 'TAI' else '🔴 XỈU'
            msg += f"\n<pre>║ 🤖 AI (ít dữ liệu): {_icon2}</pre>"
            
        msg += "\n<pre>╚═══════════════════════════════╝</pre>"
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('tick-update', namespace='/txmd5')
    def on_tick_update(data):
        gs = data.get('state')
        dt = tinh_do_tin_cay(st['history'], st['points_history'])
        if gs == 'BETTING' and st['auto_bet_enabled'] and AUTO_BET_RUN_UNTIL_STOP:
            # 🤖 AI luôn có dự đoán → KHÔNG BAO GIỜ bỏ phiên, kể cả khi tắt X2 hay độ tin thấp
            if not st.get('current_prediction'):
                st['current_prediction'] = ai_du_doan_bat_buoc(st)
            if not st['has_bet_this_session'] and not st['betLock']:
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
            st.setdefault('dice_history', []).append(list(d))
            if len(st['history']) > MAX_HISTORY_STORE:
                st['history'].pop(0)
                st['points_history'].pop(0)
            if len(st['dice_history']) > MAX_HISTORY_STORE:
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
║ 🚫 /tatx2khithua 🔁 /mox2khithua║
║ 🧠 /nhandiencau               ║
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
    track_user(message.chat.id)
    msg = f"""<pre>╔═══════════════════════════════╗
║ 📖 HƯỚNG DẪN VIP | ✨ AUZA    ║
╠═══════════════════════════════╣
║ 🔑 /nhapkey KEY               ║
║ 🔐 /login TAIKHOAN MATKHAU    ║
║ ⚡ /autobet on 10000 | off    ║
║ 🚫 /tatx2khithua (vẫn cược)   ║
║ 🔁 /mox2khithua               ║
║ 📊 /lichsucau | 💎 /thongtin  ║
║ 🧠 /nhandiencau (soi cầu AI)  ║
║ 📢 /thongbao ND [ADMIN]       ║
║ ⏹️ /stop | 👑 /taokey 30      ║
╠═══════════════════════════════╣
║ 🚀 GẤP THẾP GIỚI HẠN 1x→8x   ║
║ 🧠 AI: BỆT · BẺ · 1-1→5-5     ║
║ 321 · 123 · 212 · 2112 · 1212 ║
║ NGHỊCH ĐẢO · CHU KỲ · ĐIỂM XX ║
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
    x2_status = '🟢 BẬT' if st.get('x2_on_lose', True) else '🔴 TẮT'
    
    msg = f"""<pre>╔═══════════════════════════════╗
║      💎 THÔNG TIN VIP 💎      ║
╠═══════════════════════════════╣
║ 🆔 ID: <code>{cid}</code>
║ ⏳ HẠN: {han}
║ ⚡ AUTO: {auto_status}
║ 🔁 X2 KHI THUA: {x2_status}
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
    header = f"📊 THỐNG KÊ 20 PHIÊN GẦN NHẤT:\n🔵 TÀI: {t} | 🔴 XỈU: {x}\n{icons}\n"
    body = format_bang_cau(st['history'], st['points_history'], st.get('dice_history'), limit=12)
    bot.reply_to(message, header + "\n" + body, parse_mode='HTML')

@bot.message_handler(commands=['nhandiencau'])
def send_nhandiencau(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    st = user_states[cid]
    if len(st['history']) < 3:
        return bot.reply_to(message, '📭 Chưa đủ dữ liệu, hãy /login và chờ AI thu thập vài phiên.')
    bot.reply_to(message,
                 format_bang_cau(st['history'], st['points_history'], st.get('dice_history'), limit=15),
                 parse_mode='HTML')

@bot.message_handler(commands=['thongbao'])
def send_thongbao(message):
    if message.chat.id != ADMIN_ID:
        return bot.reply_to(message, '⛔ Chỉ admin mới có quyền gửi thông báo')

    noi_dung = message.text.split(' ', 1)
    if len(noi_dung) < 2 or not noi_dung[1].strip():
        return bot.reply_to(message, '✅ Cú pháp: /thongbao NỘI DUNG CẦN GỬI')

    text = noi_dung[1].strip()
    msg = f"""📢 <b>THÔNG BÁO TỪ ADMIN</b>

{text}

📩 Liên hệ: {ADMIN_USERNAME}"""

    targets = set(all_users) | set(authorized_users.keys()) | set(user_states.keys())
    targets.discard(0)
    ok, fail = 0, 0
    for uid in targets:
        try:
            bot.send_message(uid, msg, parse_mode='HTML')
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    bot.reply_to(message, f"✅ ĐÃ GỬI THÔNG BÁO\n👥 Thành công: {ok} | ❌ Lỗi: {fail}")

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

@bot.message_handler(commands=['tatx2khithua'])
def send_tat_x2(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    init_user_state(cid)
    st = user_states[cid]
    st['x2_on_lose'] = False
    st['martingale_step'] = 0
    st['bet_amount'] = st['base_bet']
    auto_txt = '🟢 ĐANG BẬT' if st['auto_bet_enabled'] else '🔴 CHƯA BẬT (/autobet on)'
    bot.reply_to(
        message,
        f"🚫 ĐÃ TẮT GẤP THẾP X2 KHI THUA\n"
        f"💰 MỖI PHIÊN CƯỢC CỐ ĐỊNH: {st['base_bet']:,} WIN\n"
        f"✅ AUTO VẪN CHẠY BÌNH THƯỜNG — AI VẪN TỰ DỰ ĐOÁN & CƯỢC MỌI PHIÊN\n"
        f"⚡ AUTO: {auto_txt}"
    )

@bot.message_handler(commands=['mox2khithua'])
def send_mo_x2(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    init_user_state(cid)
    st = user_states[cid]
    st['x2_on_lose'] = True
    st['martingale_step'] = 0
    st['bet_amount'] = st['base_bet']
    b = st['base_bet']
    bot.reply_to(
        message,
        f"🔁 ĐÃ MỞ LẠI GẤP THẾP X2 KHI THUA\n"
        f"📈 LỘ TRÌNH: {b:,} → {b*2:,} → {b*4:,} → {b*8:,}\n"
        f"🛑 Thua ở {b*8:,} sẽ quay về {b:,}\n"
        f"✅ AI VẪN TỰ DỰ ĐOÁN & CƯỢC MỌI PHIÊN"
    )

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
