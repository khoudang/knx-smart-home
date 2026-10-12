const { KNXClient } = require('knxultimate');

const CONFIG = {
  ipAddr: process.env.KNX_IP,
  ipPort: 3671,
  hostProtocol: 'TunnelUDP',
  loglevel: 'debug',
};

const GA = {
  power: process.env.KNX_TEST_POWER_GA,
  control: process.env.KNX_TEST_CONTROL_GA,
  brightness: process.env.KNX_TEST_BRIGHTNESS_GA,
};

const DELAY_POWER_TO_CONTROL = 400;
const DELAY_CONTROL_TO_BRIGHTNESS = 300;
const TARGET_BRIGHTNESS = 60;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  if (process.env.KNX_LIVE_TEST !== 'true' || !process.env.KNX_IP) {
    throw new Error('Live hardware test: set KNX_LIVE_TEST=true and KNX_IP explicitly');
  }
  if (Object.values(GA).some(v => !v)) throw new Error('Set all KNX_TEST_*_GA variables');
  const client = new KNXClient(CONFIG);

  client.on('error', (err) => {
    console.error('[ERROR]', err);
  });

  client.on('disconnected', () => {
    console.log('[DISCONNECTED]');
  });

  client.on('close', () => {
    console.log('[CLOSE]');
  });

  client.on('indication', (datagram) => {
    try {
      const src = datagram?.cEMIMessage?.srcAddress?.toString?.();
      const dst = datagram?.cEMIMessage?.dstAddress?.toString?.();
      const npdu = datagram?.cEMIMessage?.npdu;
      console.log('[INDICATION]', { src, dst, npdu });
    } catch (e) {
      console.log('[INDICATION] (raw)', datagram);
    }
  });

  client.on('ackReceived', (evt) => {
    console.log('[ACK]', evt);
  });

  const connected = new Promise((resolve, reject) => {
    client.on('connected', resolve);
    setTimeout(() => reject(new Error('Timeout ket noi KNX/IP')), 10000);
  });

  console.log(`[CONNECT] ${CONFIG.hostProtocol} -> ${CONFIG.ipAddr}:${CONFIG.ipPort}`);
  client.Connect();

  try {
    await connected;
    console.log('[CONNECTED] Ket noi KNX/IP thanh cong.\n');
  } catch (e) {
    console.error('[FAIL] Khong ket noi duoc:', e.message);
    process.exit(1);
  }

  try {
    console.log(`[WRITE] ${GA.power} = true (DPT 1.001)`);
    await client.write(GA.power, true, '1.001');
    await sleep(DELAY_POWER_TO_CONTROL);

    console.log(`[WRITE] ${GA.control} = true (DPT 1.001)`);
    await client.write(GA.control, true, '1.001');
    await sleep(DELAY_CONTROL_TO_BRIGHTNESS);

    console.log(`[WRITE] ${GA.brightness} = ${TARGET_BRIGHTNESS} (DPT 5.001)`);
    await client.write(GA.brightness, TARGET_BRIGHTNESS, '5.001');

    console.log('\n[OK] Da gui du 3 telegram. Kiem tra den DALI ngay bay gio.');

    await sleep(3000);
  } catch (e) {
    console.error('[ERROR khi ghi telegram]', e);
  } finally {
    client.Disconnect();
    await sleep(500);
    process.exit(0);
  }
}

main();
