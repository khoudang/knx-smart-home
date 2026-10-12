# Homebridge

Có ba lựa chọn. Chọn một đường cho mỗi thiết bị để tránh tạo accessory trùng và lệnh trùng.

## MQTT qua agent chính

`scripts/sync_homebridge.py` sinh cấu hình **mqttthing** từ SQLite.
Bridge `knx-bridge/bridge.py` xử lý MQTT và đọc mapping KNX từ cùng registry.
Đây là đường tích hợp của agent chính. Cài plugin mqttthing riêng trong Homebridge,
kiểm tra phương án `--dry-run` trước khi dùng `--apply`.

## Plugin v3

Mã nằm trong `packages/homebridge-knx-v3/`; đăng ký platform `KNXV3`, dùng `knx`
kết nối trực tiếp. Cài dependency riêng:

```bash
npm ci --prefix packages/homebridge-knx-v3 --ignore-scripts
```

Đọc [README plugin](../packages/homebridge-knx-v3/README.md) và tạo cấu hình riêng.
Plugin không đọc trực tiếp schema SQLite/mqttthing. Một số loại accessory chỉ là khung;
không xem danh sách type trong manager là bằng chứng hỗ trợ đầy đủ.

## Plugin v2 cũ

`deploy/homebridge-v2/setup.sh` cài package v2 và patch file trong node_modules cho
ACK DALI, debounce dimmer và scene pulse. Đây là lựa chọn tương thích cũ, không phải
bộ cài v3. Vì chưa pin phiên bản v2, patch có thể không khớp bản package mới.
Không chạy tự động trong CI; đối chiếu phiên bản và backup cấu hình trước khi sử dụng.

## Chuyển hệ thống hiện có

Giữ identity và pairing HomeKit; không reset Homebridge để hợp nhất repository.
Đối chiếu nguồn riêng, mức dim, nhiệt độ màu, feedback ETS và thời gian scene pulse.
Lệnh live test v3 yêu cầu `KNX_LIVE_TEST=true`, `KNX_IP` và toàn bộ `KNX_TEST_*_GA`;
chúng không có giá trị mặc định của nhà thật.
