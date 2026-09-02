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

## Nâng cấp AI đa khuôn và học online

Bản nâng cấp sử dụng bộ trích xuất đặc trưng cho nhiều dạng cầu gồm cầu bệt, cầu đảo/luân phiên, cầu 1-1, 2-2, 3-3, các khuôn block 1-2-1-2, 2-1-2, 3-2-1, 1-2-3, 2-1-1-2, cầu lặp chu kỳ, cầu nghịch đảo và các mẫu đuôi gần nhất. Khuôn rõ ràng được ưu tiên trước tín hiệu thống kê, còn khi chưa khớp khuôn thì bộ học online bỏ phiếu từ dữ liệu thực tế đã quan sát.

Sau mỗi phiên, bot ghi nhận các đặc trưng đã dùng cho dự đoán cùng kết quả Tài/Xỉu thực tế. Bộ đếm thích nghi được lưu trong `bot_save.json` dưới khóa `adaptive_model`, vì vậy bot có thể tiếp tục học sau khi Render khởi động lại. Dữ liệu được giới hạn để tránh phình bộ nhớ; đây là cơ chế học thống kê online, không phải cam kết dự đoán chắc chắn kết quả trò chơi may rủi.

Lệnh `/thongke` hiển thị tổng thắng, tổng thua, tỷ lệ đúng, chuỗi hiện tại, 20 lượt gần nhất và số đặc trưng AI đã học.

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

Sau khi bot online, dùng `/login TAIKHOAN MATKHAU`, sau đó chỉ bật `/autobet on 2k` sau khi đã kiểm thử không đặt cược. Bản vá mới bỏ qua phiên có tín hiệu dưới ngưỡng và tắt gấp thếp mặc định; dùng `/autobet off` để tắt và `/stop` để ngắt socket. Không xem điểm AI là bảo đảm thắng hoặc căn cứ để tăng tiền cược.

## Tài liệu Render

[Deploys](https://render.com/docs/deploys), [Background Workers](https://render.com/docs/background-workers), [Environment Variables and Secrets](https://render.com/docs/configure-environment-variables), và [Free Instances](https://render.com/docs/free).

## Bản nâng cấp ổn định

Bản source hiện tại đã bổ sung kiểm tra trạng thái HTTP và dữ liệu xúc xắc trước khi đưa vào lịch sử, cơ chế bỏ qua `session-result` trùng khi socket reconnect, cùng cơ chế ghi `bot_save.json` theo kiểu file tạm rồi `os.replace` để giảm nguy cơ JSON bị hỏng khi nhiều luồng cùng lưu.

Điểm `tin_cay` chỉ là **điểm xếp hạng nội bộ**, không phải xác suất thắng và không thể bảo đảm kết quả lượt kế tiếp. Các mẫu “cầu” có thể trùng khớp trong dữ liệu lịch sử nhưng không chứng minh được rằng kết quả ngẫu nhiên sẽ tiếp diễn. Cần chạy thử ở chế độ không đặt cược và đánh giá walk-forward trên dữ liệu thật trước khi bật tự động.

Kiểm tra phát hành đã thực hiện: phân tích AST, `python3 -m py_compile bot.py`, xác nhận các hàm lõi, khóa chống xử lý kết quả trùng, kiểm tra HTTP và ghi file nguyên tử.

## Ghi chú quan trọng về bản vá đánh giá

Các điểm `tin_cay` trong bot là **điểm xếp hạng nội bộ**, không phải phần trăm xác suất thắng. Trò chơi Tài/Xỉu không cho phép suy ra chắc chắn kết quả kế tiếp từ lịch sử; vì vậy bản vá không quảng cáo hoặc cam kết tỷ lệ thắng trên 70%.

Bản vá đã bổ sung hàm `walk_forward_evaluate(history, points=None, min_history=12)`. Hàm dự đoán từng phiên chỉ bằng dữ liệu xuất hiện trước phiên đó, sau đó mới cập nhật mô hình bằng kết quả vừa quan sát. Có thể chạy smoke test bằng:

```bash
python3 -m py_compile bot.py test_predictor.py
python3 test_predictor.py
```

Khi người dùng bật `/autobet on`, auto-bet mặc định gửi đúng một lệnh mỗi phiên như cấu trúc gốc (`AUTO_BET_REQUIRE_CONFIDENCE = False`); có thể đổi cờ này thành `True` nếu muốn lọc theo `MIN_CONFIDENCE_AUTO_BET = 60`. Gấp thếp vẫn tắt mặc định (`MAX_MARTINGALE_STEPS = 0`, `x2_on_lose = False`) để không khuếch đại tổn thất. Nên kiểm thử ở chế độ không đặt cược và đặt giới hạn ngân sách trước khi vận hành.
