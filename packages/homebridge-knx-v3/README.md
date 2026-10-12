# Homebridge KNX V3

Plugin thử nghiệm từ hệ mã nguồn hợp nhất. Entry point `index.js` đăng ký
`homebridge-knx-v3` / `KNXV3`. Package khai báo Homebridge 2 và Node.js >=20;
repo chính dùng Node.js 22 trong CI.

```bash
npm ci --ignore-scripts
```

Từ môi trường Homebridge thử nghiệm, cài plugin bằng đường dẫn package này hoặc
đóng gói bằng `npm pack`. Chưa phát hành package npm từ repository hợp nhất.

Cấu hình `platforms` mẫu (IP dưới đây thuộc dải dành cho tài liệu, phải thay bằng IP của bạn):

```json
{
  "platform": "KNXV3",
  "name": "KNX",
  "knx": {"host": "192.0.2.10", "port": 3671},
  "devices": [
    {"name": "Example switch", "type": "switch", "control": "1/0/1", "status": "1/0/2"}
  ]
}
```

Switch, dimmer và temperature có logic riêng. Các service khác hiện phần lớn là
khung mã, cần hoàn thiện và thử nghiệm trước khi sử dụng. Dimmer hỗ trợ các trường
`control`, `level`, `power.control`, `cct`; CCT có phép đảo Kelvin riêng cần hiệu chỉnh
theo thiết bị. Không mặc định cấu hình mqttthing/v2 tương thích với plugin này.

`test-knx.js` gửi lệnh phần cứng thật, không phải unit test. Nó chỉ chạy khi có
`KNX_LIVE_TEST=true`, `KNX_IP`, `KNX_TEST_POWER_GA`, `KNX_TEST_CONTROL_GA` và
`KNX_TEST_BRIGHTNESS_GA`. CI chỉ kiểm tra cú pháp, không chạy file này.
