# KNX -> HomeKit (Raspberry Pi + Homebridge + homebridge-knx-v2)

## Cai dat may moi
1. `sudo bash setup.sh`
2. `sudo cp config.template.json /var/lib/homebridge/config.json`
3. Sua `ip` (dia chi KNX IP Interface, mac dinh 192.0.2.10) va `pin` neu can
4. `sudo systemctl restart homebridge`

## 3 bug da fix (xem comment trong setup.sh)
1. DALI Gateway im lang bo qua lenh -> fix bit ack-request
2. Dim nhay ve 100% -> bo lenh On du thua
3. Scene khong tu tat -> auto OFF sau 800ms

## Dia chi Group Address (KNX) dang dung
- Switch thuong: 0/0/x (control) + 0/0/(x+1) (status)
- Dimmer G1 Dan Tran: 0/1/0 (switch, khong dung), 0/1/2 (brightness control), 0/1/1 (status on/off), 0/1/3 (status brightness)
- Scene: 0/4/0, 0/4/2

## Van de con ton dong (chua fix)
- HomeKit doi khi khong tu cap nhat trang thai real-time khi ETS ghi truc tiep len bus (phai thoat app vao lai). Nghi ngo do characteristic thieu quyen NOTIFY, chua dieu tra sau.
