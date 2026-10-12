#!/bin/bash
set -e
sudo -u homebridge npm install --prefix /var/lib/homebridge homebridge-knx-v2
PLUGIN_DIR="/var/lib/homebridge/node_modules/homebridge-knx-v2"

# Fix 1: DALI Gateway bo qua telegram co ack-request bit
sudo sed -i "s/this.connection = knx.Connection({ ipAddr : gatewayIP, ipPort : 3671, loglevel: 'info',/this.connection = knx.Connection({ ipAddr : gatewayIP, ipPort : 3671, loglevel: 'info', suppress_ack_ldatareq: true,/" \
  "$PLUGIN_DIR/src/device-manager.js"

# Fix 2: dimmer debounce 200ms, tranh gui lenh On du thua giua luc dim
sudo sed -i 's/}, 10);/}, 200);/' \
  "$PLUGIN_DIR/lib/syntex-dynamic-platform/src/accessories/dimmedBulb.js"
sudo sed -i 's/else if(this.changedBrightness)/if(this.changedBrightness)/' \
  "$PLUGIN_DIR/lib/syntex-dynamic-platform/src/accessories/dimmedBulb.js"

# Fix 3: Scene tu OFF sau 800ms (KNX scene can pulse, khong phai held state)
sudo python3 << 'PYEOF'
path = "/var/lib/homebridge/node_modules/homebridge-knx-v2/lib/syntex-dynamic-platform/src/accessories/switch.js"
with open(path, encoding="utf-8") as f:
    c = f.read()
old = "this.service.getCharacteristic(this.Characteristic.On).on('get', this.getState.bind(this)).on('set', this.setState.bind(this));"
new = """this.service.getCharacteristic(this.Characteristic.On).on('get', this.getState.bind(this)).on('set', (value, callback) => {
			this.setState(value, callback);
			if(value === true && deviceConfig.name && deviceConfig.name.indexOf('Scene') === 0)
			{
				setTimeout(() => {
					this.setState(false, () => {
						this.service.getCharacteristic(this.Characteristic.On).updateValue(false);
					});
				}, 800);
			}
		});"""
if old in c:
    with open(path, "w", encoding="utf-8") as f:
        f.write(c.replace(old, new))
PYEOF

sudo mkdir -p /var/homebridge/SynTex
sudo chmod -R u+rwX,go-rwx /var/homebridge/SynTex
sudo chown -R homebridge /var/homebridge/SynTex
sudo systemctl restart homebridge
