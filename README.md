# AICONTROL Smart Home

Một repository cho hệ thống nhà thông minh KNX: bot Telegram/Zalo, agent Python,
registry SQLite, Google Sheets và các lựa chọn tích hợp Homebridge.

Repository này hợp nhất mã nguồn của bốn dự án. Hệ thống `aiot-care` (BLE/ESP32/camera)
được giữ riêng. Bản công khai dùng lịch sử Git mới và cấu hình mẫu; không chứa token,
khóa Google, link bảng tính thật, danh sách người dùng hoặc backup của hệ thống cũ.

## Các thành phần

| Thư mục | Chức năng | Trạng thái |
| --- | --- | --- |
| `smart_home_agent/`, `ai-agent/` | Agent, phân quyền, xử lý yêu cầu và HTTP API | Thành phần chính; mặc định dry-run |
| `chatbot-gateway/` | Telegram/Zalo → API agent | Bật từng kênh bằng cấu hình riêng |
| `knx-bridge/` | MQTT → KNX và phản hồi trạng thái | Mặc định không ghi KNX |
| `scene-engine/` | Engine scene | Agent chưa bật thực thi scene |
| `packages/homebridge-knx-v3/` | Plugin Homebridge kết nối KNX trực tiếp | Bản thử nghiệm; một số loại accessory mới có khung mã |
| `adapters/openclaw/` | Middleware, Siri webhook và skills từ OpenClaw | Tùy chọn độc lập; cần cấu hình OpenClaw cục bộ |
| `deploy/homebridge-v2/` | Công cụ cài/patch plugin Homebridge v2 | Tùy chọn tương thích cũ; không phải bộ cài v3 |

Các thành phần cùng nằm trong một repo nhưng vẫn có tiến trình và dependency riêng.
Không chạy nhiều backend Homebridge cho cùng một thiết bị.

## Luồng chính

```text
Telegram / Zalo → HTTP API → Agent → SQLite registry + kiểm tra quyền
                                        ↓
                                  MQTT → KNX bridge → KNX
Google Sheets → sync → SQLite → Homebridge mqttthing
```

LLM phân loại ý định; ID thiết bị, địa chỉ KNX và quyền được xác minh bằng mã và registry.

## Bắt đầu

Yêu cầu: Python 3.11+, Node.js 22+, Git. Raspberry Pi/Linux là môi trường triển khai;
Windows có thể chạy kiểm thử và phát triển. Dùng WSL/Linux cho script Bash.

```bash
git clone https://github.com/khoudang/aicontrol-smart-home.git
cd aicontrol-smart-home
python -m venv .venv
```

Kích hoạt `.venv`: `source .venv/bin/activate` trên Linux/macOS hoặc
`.venv\Scripts\Activate.ps1` trong PowerShell. Sau đó:

```bash
python -m pip install -r ai-agent/requirements.txt
npm ci --ignore-scripts
npm ci --prefix chatbot-gateway --ignore-scripts
```

Sao chép `.env.example` thành `.env` (`cp` trên Linux; `Copy-Item` trong PowerShell).
Giữ `KNX_DRY_RUN=true`, `MQTT_DRY_RUN=true`, `TELEGRAM_ENABLED=false` và
`ZALO_ENABLED=false` trong lần chạy đầu. Chỉ điền thông tin cá nhân vào `.env`
hoặc thư mục ngoài Git. API đọc `.env` khi khởi động; gateway đọc `.env` từ thư mục chạy.

```bash
python -m scripts.migrate --db data/smart_home.db
python -m unittest discover -s tests
python ai-agent/api.py
```

Kiểm tra `http://127.0.0.1:5000/health`. API mặc định chỉ lắng nghe loopback.
`/chat` dành cho gateway tin cậy; không mở API này trực tiếp ra Internet vì người gọi
có thể cung cấp danh tính người dùng. Nếu triển khai nhiều máy, đặt sau một lớp xác thực
và kiểm soát mạng phù hợp.

Chạy gateway từ thư mục gốc sau khi điền token và bật các kênh cần dùng:

```bash
node chatbot-gateway/index.js
```

Tạo owner đầu tiên bằng công cụ CLI với ID tài khoản của chính bạn:

```bash
python -m scripts.manage_users --database data/smart_home.db add --channel telegram --user-id YOUR_USER_ID --role owner
```

Không có sẵn owner hoặc thiết bị nhà thật. Người chưa có quyền phải được cấp quyền.

## Cấu hình tùy chọn

- [Google Sheets và registry](docs/google-sheets-sync.md): tự tạo bảng tính và service account;
  `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_FILE`, `GOOGLE_EXPECTED_SERVICE_ACCOUNT`.
- [Các lựa chọn Homebridge](docs/homebridge.md): MQTT, plugin v3 và công cụ v2.
- [OpenClaw](adapters/openclaw/README.md): dependency, dữ liệu mẫu và giới hạn tương thích.
- [Cài đặt và triển khai](docs/setup.md): môi trường, dry-run và runtime riêng.
- [Nguồn mã và thay đổi khi hợp nhất](docs/consolidation.md).
- [Chính sách bảo mật](SECURITY.md).

## Kiểm tra trước khi commit

```bash
python -m unittest discover -s tests
python scripts/check_public_tree.py
git diff --check
```

Lệnh kiểm tra công khai đọc danh sách file đã track. Thêm file mới bằng `git add` rồi
kiểm tra trước khi commit. CI chạy test, kiểm tra cú pháp JavaScript/Bash, kiểm tra file
riêng tư và Gitleaks. Không bài kiểm tra nào được phép tự chạy live hardware test.

## Giới hạn

Đây là bản hợp nhất mã nguồn, chưa xác minh trên phần cứng hay phiên OpenClaw cụ thể.
Chưa khóa đầy đủ dependency Python/OpenClaw và phiên bản plugin Homebridge v2.
Giữ môi trường đang chạy ổn trên Pi; kiểm tra riêng từng tích hợp trước khi chuyển runtime.
Không có token mặc định hoạt động hoặc đường dẫn tới tài nguyên riêng tư để chạy ngay.
