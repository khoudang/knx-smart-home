# Cài đặt và triển khai

## Phát triển

Làm theo README để tạo `.venv`, cài Python và Node dependencies, tạo `.env` và migrate
SQLite. Chạy tất cả lệnh Python module và gateway từ thư mục gốc repository.

Để cài KNX bridge: `python -m pip install -r knx-bridge/requirements.txt`.
Scene engine: `python -m pip install -r scene-engine/requirements.txt`.
Hai tiến trình này dùng biến môi trường của shell; export các biến cần thiết từ `.env`
hoặc dùng `EnvironmentFile` trong systemd, không mặc định cho rằng mọi entrypoint đều tự đọc `.env`.

Không chạy bridge live khi mới cài. MQTT broker mẫu trong `infra/` chỉ dành cho mạng thử
nghiệm; đọc cấu hình xác thực, cổng và quyền mạng trước khi dùng cho nhà thật.

## Google Sheets

Giữ JSON service-account bên ngoài repo và đặt đường dẫn tuyệt đối bằng
`GOOGLE_CREDENTIALS_FILE`. Điền Sheet ID của bạn trong `.env`; không đưa URL bảng tính
vào README hoặc fixture. Có thể đặt `GOOGLE_EXPECTED_SERVICE_ACCOUNT` để giới hạn tài khoản
được dùng. Thông tin này được đọc lúc chạy, không khóa vào tài khoản của người phát triển.

```bash
python -m scripts.sync_google_sheet --database data/smart_home.db
python -m scripts.sync_homebridge --database data/smart_home.db --dry-run
```

Lệnh sync Google ghi vào SQLite; sync Homebridge mặc định chỉ lập phương án.
`--apply` sửa config Homebridge và restart dịch vụ: chỉ dùng sau khi xem phương án.

## Raspberry Pi

Giữ code trong release directory và runtime trong thư mục riêng:

```text
deployment/
  releases/<commit>/
  current -> releases/<commit>
  shared/
    .env.production
    data/
    logs/
    secrets/
```

`scripts/deploy_release.sh` có `--dry-run` và `--prepare-only`. Script không tự restart
dịch vụ; cấu hình systemd, Python environment và dependency phải được chuẩn bị theo máy.
Đặt `WorkingDirectory` vào gốc release, `EnvironmentFile` vào cấu hình runtime riêng.
Giữ release trước và backup SQLite để rollback. Không symlink secret vào file được track.

Mặc định API chỉ nghe `127.0.0.1`; thay `API_HOST` chỉ khi có lớp xác thực/mạng tin cậy.
Giữ cả hai dry-run flags bật cho đến khi kiểm chứng ID, GA, capability và quyền.
