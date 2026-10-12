const fs = require('fs');
const path = require('path');

const CACHE_FILE = path.join(__dirname, '../config/devices_cache.json');
const CACHE_TTL_MS = 10 * 60 * 1000; // 10 phút

function cell(row, candidates) {
    for (const key of candidates) {
        try {
            const value = row.get(key);
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                return value;
            }
        } catch (_) {}
    }
    return null;
}

// Kiểm tra cache còn hợp lệ không
function isCacheValid() {
    try {
        if (!fs.existsSync(CACHE_FILE)) return false;
        const stat = fs.statSync(CACHE_FILE);
        const age = Date.now() - stat.mtimeMs;
        if (age < CACHE_TTL_MS) {
            const data = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf-8'));
            if (Array.isArray(data) && data.length > 0) return true;
        }
        return false;
    } catch (e) {
        return false;
    }
}

// Đọc cache
function readCache() {
    try {
        return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf-8'));
    } catch (e) {
        return null;
    }
}

// Fetch từ Google Sheets bằng bộ đọc chung
async function fetchFromSheet() {
    const { readTab } = require('./sheet_reader');

    const COLUMN_MAP = {
        id: ['device_id', 'Device ID', 'ID'],
        name: ['Tên line đèn', 'Tên thiết bị', 'name', 'Name'],
        room: ['Khu vực', 'Phòng', 'room', 'Room'],
        control: ['G.A Control', 'GA Control', 'ga_control'],
        status: ['G.A status', 'GA status', 'ga_status'],
        gaOn: ['G.A ON', 'GA ON', 'ga_on'],
        gaOff: ['G.A OFF', 'GA OFF', 'ga_off'],
        dim: ['G.A dim 1 byte value', 'GA dim 1 byte value', 'ga_dim_value'],
        dimStatus: ['G.A dim 1 byte value status', 'GA dim 1 byte value status', 'ga_dim_status'],
        color: ['G.A color value', 'GA color value', 'ga_color_value'],
        colorStatus: ['G.A color value status', 'GA color value status', 'ga_color_status']
    };

    // Gọi module chung, đọc sheet đầu tiên (KNX) với cache 10 phút
    const devices = await readTab('KNX', COLUMN_MAP, 10, true);

    // Lọc bỏ dòng không có id
    const validDevices = devices.filter(d => d.id);
    fs.writeFileSync(CACHE_FILE, JSON.stringify(validDevices, null, 2));
    return validDevices;
}

async function main() {
    const forceRefresh = process.argv.includes('--refresh') || process.argv.includes('-r');

    if (!forceRefresh && isCacheValid()) {
        const cached = readCache();
        const rooms = new Set(cached.map(d => d.room).filter(Boolean));
        console.error('✓ Google Sheet loaded (from cache)');
        console.error(`✓ Loaded ${cached.length} devices`);
        console.error(`✓ Loaded ${rooms.size} rooms`);
        console.log(JSON.stringify(cached, null, 2));
        return;
    }

    try {
        const devices = await fetchFromSheet();
        const rooms = new Set(devices.map(d => d.room).filter(Boolean));
        console.error('✓ Google Sheet loaded');
        console.error(`✓ Loaded ${devices.length} devices`);
        console.error(`✓ Loaded ${rooms.size} rooms`);
        console.log(JSON.stringify(devices, null, 2));
    } catch (e) {
        // Fallback: dùng cache cũ dù hết hạn
        const stale = readCache();
        if (stale) {
            console.error(`[warn] Lỗi sync: ${e.message}. Dùng cache cũ.`);
            console.log(JSON.stringify(stale, null, 2));
        } else {
            console.error(`[error] Lỗi sync và không có cache: ${e.message}`);
            process.exit(1);
        }
    }
}

main().catch(err => {
    console.error('Lỗi:', err.message);
    process.exit(1);
});
