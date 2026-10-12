# Bảo mật và cấu hình riêng

Không commit `.env`, service-account JSON, token, Sheet ID/URL thật, dữ liệu người dùng,
IP thiết bị nội bộ, cache, database, log hoặc backup. Chỉ dùng file `.example` đã làm sạch.
Gitignore không loại bí mật đã có trong commit cũ; repo hợp nhất bắt đầu bằng lịch sử mới.

Nếu credential từng tồn tại trong repo nguồn, hãy thu hồi/thay credential tại nhà cung cấp.
Việc loại khỏi repo mới không vô hiệu hóa credential hoặc xóa nó khỏi repo nguồn.

Chạy `python scripts/check_public_tree.py` sau khi stage thay đổi và quét Gitleaks trước
khi công khai commit. Không công bố giá trị credential trong issue, PR hoặc log CI.

API agent và Siri webhook dành cho môi trường tin cậy; không expose API agent trực tiếp
ra Internet. Broker mẫu và công cụ cài đặt cũ cần cấu hình riêng trước khi triển khai.

KNX/MQTT dry-run bật mặc định. Thiết bị cửa/cổng/khóa và scene có kiểm soát riêng trong
agent chính. Plugin Homebridge trực tiếp và middleware legacy không có cùng policy;
chỉ sử dụng khi đã kiểm tra quyền, mạng và mapping thiết bị.

Nếu phát hiện credential lộ, gửi báo cáo riêng cho chủ repository qua thông tin liên hệ
trên hồ sơ GitHub; không tạo issue công khai chứa bí mật.
