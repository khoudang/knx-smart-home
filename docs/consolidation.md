# Hợp nhất mã nguồn

Ngày hợp nhất: 12/10/2026. Bốn thành phần nguồn:

| Thành phần | Commit nguồn | Vị trí mới |
| --- | --- | --- |
| KNX agent | `4827463` | Các thư mục lõi ở gốc |
| OpenClaw tools | `55f1f6a` | `adapters/openclaw/` |
| Homebridge v3 | `4ea53db` | `packages/homebridge-knx-v3/` |
| Homebridge v2 setup | `1e4e37b` | `deploy/homebridge-v2/` |

Repo công khai được tạo từ snapshot đã kiểm tra với lịch sử mới. Không nhập lịch sử
nguồn chứa credential. Các repo nguồn không bị sửa, xóa hoặc thay đổi visibility.

Đã bỏ: environment thật, Google credential, Sheet ID, cache thiết bị, user/permission/quota
runtime, cấu hình Homebridge sinh tự động, config/prompt OpenClaw cá nhân, backup bộ nhớ,
file `.bak`/`.bad` và file chỉ chứa đường dẫn máy cũ.

Đã thay: IP nhà thật, ID admin cá nhân, tài khoản Google cố định, token Siri mặc định và
đường dẫn người dùng. Dùng biến môi trường, dữ liệu giả và dải IP dành cho tài liệu.
Các API endpoint công khai của Google/Telegram/Zalo/LLM vẫn được giữ vì là một phần chức năng.

Đã thêm: tài liệu từng thành phần, gitignore cho runtime ở mọi thư mục, kiểm tra publication,
CI và kiểm tra account Google tùy cấu hình. Middleware cũ chặn live KNX mặc định và
không cho điều khiển khi registry vắng mặt.

Một test kiến trúc Siri cũ trỏ tới file không có trong snapshot agent được cập nhật để
kiểm tra adapter ở đường dẫn mới, thiếu token bị từ chối và KNX live mặc định bị chặn.

Phạm vi kiểm tra là mã nguồn và test mô phỏng. Không xác nhận hệ Pi/Homebridge đang chạy,
không đổi token ngoài Git và không triển khai phần cứng trong quá trình hợp nhất.
