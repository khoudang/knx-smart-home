# OpenClaw adapter và công cụ tương thích

Các skills, middleware KNX, generator Homebridge và Siri webhook được giữ tại đây
để phát triển/chuyển hệ thống cũ. Chúng không tự khởi động cùng agent chính và chưa
được chuyển thành client của API agent. Không chạy cùng đường điều khiển chính trên
cùng thiết bị nếu chưa thiết kế việc phối hợp.

## Thiết lập cục bộ

```bash
npm ci --ignore-scripts
python -m pip install -r requirements.txt
mkdir -p config/cache logs
```

Tạo `.env` từ `.env.example`; giữ `KNX_DRY_RUN=true`. Các lệnh Node/Python trực tiếp
cần biến được export trong shell; script `start_bot.sh` tự nạp `.env`.
Đặt `GOOGLE_SHEET_ID` và `GOOGLE_CREDENTIALS_FILE` (đường dẫn tuyệt đối tới JSON ngoài Git).
Tự tạo các tab cần dùng: `KNX`, `permissions`, `faq`, `KNX_history`; xem column map trong skills.

`node skills/fetch_devices.js --refresh` tạo cache thiết bị riêng của bạn.
`config/devices.example.json` là mảng trống để chỉ schema cấp cao; lệnh điều khiển bị
từ chối khi chưa có cache. `config/scenes.example.json` và `permissions.example.json`
là template trống, không chứa dữ liệu hay quyền của nhà cũ.

OpenClaw CLI và runtime phải được cấu hình bằng công cụ setup của phiên bản bạn cài.
Repository không kèm `openclaw.json`, `agent.json`, prompt cá nhân hoặc memory backup.
Đăng ký skills phù hợp với phiên bản đó; JSON schema OpenClaw cần kiểm tra riêng.

## Quyền và Siri

`OPENCLAW_ADMIN_USERS` là danh sách `channel:userId` phân cách bằng dấu phẩy của chính bạn.
Không có admin mặc định. `init_permissions_sheet.js` chỉ thêm admin từ biến này.

Siri webhook cần `SIRI_SECRET_TOKEN`, nghe loopback và từ chối yêu cầu khi thiếu token.
`generate_hb.py` tạo config chứa token runtime; file đầu ra bị gitignore, không chia sẻ.
Middleware mặc định chặn kết nối KNX, yêu cầu cấu hình IP và registry cục bộ hợp lệ.
Đây vẫn là đường thực thi cũ, chưa có đầy đủ policy của agent chính.

Các lệnh quản lý permission, xóa lịch sử và cập nhật token có tác động thật khi bạn tự
chạy. Token chỉ lưu trong runtime OpenClaw ở home directory, không backup vào source checkout.
