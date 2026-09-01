# Auza Vả Chết Nhà Cái Bot

Đây là source bot Telegram Python đã được chuẩn hóa để triển khai từ GitHub lên Render. Bot dùng polling Telegram và kết nối Socket.IO tới máy chủ game.

> **Cảnh báo:** Đây là bot có chức năng tự động đặt cược. Dự đoán không bảo đảm kết quả và có thể gây mất tiền. Chỉ bật `/autobet` khi bạn hiểu rõ rủi ro và giới hạn số dư.

## Cấu trúc repository

| Tệp | Mục đích |
|---|---|
| `bot.py` | Source chính của bot |
| `requirements.txt` | Thư viện Python cần cài |
| `render.yaml` | Cấu hình Background Worker cho Render |
| `.env.example` | Mẫu biến môi trường; không chứa secret thật |
| `.gitignore` | Ngăn secret và dữ liệu runtime bị commit |

## Chuẩn bị bảo mật

Token Telegram từng xuất hiện trong source cũ phải được thu hồi và tạo token mới bằng BotFather trước khi deploy. Không đưa token mới vào GitHub. Source hiện tại chỉ đọc `BOT_TOKEN`, `ADMIN_ID` và `ADMIN_USERNAME` từ biến môi trường.

## Đưa lên GitHub

Tạo một repository mới, ví dụ `auza-vachet-nha-cai-bot`, đặt ở chế độ **Private** nếu source không muốn công khai. Từ thư mục này chạy:

```bash
git init
git add bot.py requirements.txt render.yaml .env.example .gitignore README.md
git commit -m "Prepare bot for Render deployment"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

Không chạy `git add .` nếu thư mục có backup hoặc file dữ liệu cũ; trước khi push hãy kiểm tra bằng `git status` và `git grep -n "AA\|BOT_TOKEN = '\|password"`.

## Thiết lập trên Render

Trong Render chọn **New → Background Worker**, kết nối GitHub và chọn repository/branch `main`. Nếu repository có `render.yaml`, có thể chọn **New → Blueprint** để Render tự đọc cấu hình. Các thông số thủ công tương ứng là:

| Trường | Giá trị |
|---|---|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python bot.py` |
| Auto-Deploy | `Yes`, theo branch `main` |
| Service type | **Background Worker** |

Tại **Environment → Environment Variables**, thêm ba biến sau:

| Key | Value |
|---|---|
| `BOT_TOKEN` | Token mới lấy từ BotFather |
| `ADMIN_ID` | Telegram numeric user ID của admin, ví dụ `8030294480` |
| `ADMIN_USERNAME` | `@auzasito` |

Lưu biến môi trường, bấm **Manual Deploy → Deploy latest commit**, rồi mở **Logs**. Khi thấy log polling Telegram và Flask server khởi động, gửi `/start` cho bot để kiểm tra.

## Chạy 24/7

Để process không bị dừng khi không có request, dùng **Background Worker**, không dùng Web Service miễn phí. Theo tài liệu Render, worker là service chạy liên tục nhưng không nhận traffic vào; tuy nhiên gói miễn phí không nên được xem là cam kết uptime 24/7. Cần chọn instance trả phí phù hợp trong Render để bot chạy liên tục và kiểm tra giá hiện hành tại Dashboard.

Render tự động deploy lại khi có commit mới trên branch đã liên kết. Vì `bot_save.json` nằm trên filesystem tạm thời của service, key và user được cấp quyền có thể mất sau restart/redeploy nếu không gắn persistent disk hoặc chuyển dữ liệu sang database. Nếu cần giữ dữ liệu bản quyền lâu dài, nên dùng database ngoài hoặc persistent disk theo gói Render hỗ trợ.

## Cách dùng

Sau khi bot online, dùng `/login TAIKHOAN MATKHAU`. Predictor tải tối đa 300 phiên gần nhất và kết hợp nhiều cửa sổ tần suất, Markov bậc 1–3, khớp hậu tố lịch sử, run-length và nhận diện cầu 1-1, 2-2, 3-3, cầu 3-2-1, 1-2-3, 2-1-2, 2-1-1-2, 1-2-1-2, cầu đảo, cầu bẻ, cầu bệt 4+ và các chuỗi 3 Tài/3 Xỉu. Lệnh `/lichsucau` hiển thị 20 phiên gần nhất với Tài/Xỉu, ba mặt xúc xắc và tổng điểm. Đây là hệ thống chấm điểm, không phải cam kết thắng.

Có hai mode cược theo từng user. Lệnh `/autobet on 2k x2` bật gấp thếp giới hạn `2k → 4k → 8k → 16k`; nếu thua ở 16k, phiên kế tiếp quay lại 2k. Lệnh `/autobet on 2k flat` bật **cược đều**, mỗi phiên luôn 2.000 WIN và thua không làm tăng tiền. Có thể chuyển mode khi bot đã bật bằng `/autobet mode flat` hoặc `/autobet mode x2`. Dùng `/autobet off` để tắt và reset về vốn gốc; dùng `/stop` để ngắt socket.

## Tài liệu Render

[Deploys](https://render.com/docs/deploys), [Background Workers](https://render.com/docs/background-workers), [Environment Variables and Secrets](https://render.com/docs/configure-environment-variables), và [Free Instances](https://render.com/docs/free).
