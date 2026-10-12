const fs = require('fs');
const path = require('path');
const { GoogleSpreadsheet } = require('google-spreadsheet');
const { JWT } = require('google-auth-library');
// Read credentials only when explicitly connecting to Google Sheets.

const SHEET_ID_FILE = path.join(__dirname, '../config/sheet_id.txt');
const CACHE_DIR = path.join(__dirname, '../config/cache');

// Đảm bảo thư mục cache tồn tại
if (!fs.existsSync(CACHE_DIR)) {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
}

/**
 * Đọc ô dữ liệu, hỗ trợ tìm theo nhiều header (không phụ thuộc tiếng Việt)
 */
function getCell(row, candidates) {
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

/**
 * Khởi tạo Google Spreadsheet Document
 */
async function getDoc() {
    const sheetId = process.env.GOOGLE_SHEET_ID || (fs.existsSync(SHEET_ID_FILE) ? fs.readFileSync(SHEET_ID_FILE, 'utf-8').trim() : '');
    const credentialsPath = process.env.GOOGLE_CREDENTIALS_FILE;
    if (!sheetId || !credentialsPath) throw new Error('Set GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_FILE');
    const creds = JSON.parse(fs.readFileSync(credentialsPath, 'utf-8'));
    const serviceAccountAuth = new JWT({
        email: creds.client_email,
        key: creds.private_key,
        scopes: ['https://www.googleapis.com/auth/spreadsheets'],
    });
    const doc = new GoogleSpreadsheet(sheetId, serviceAccountAuth);
    await doc.loadInfo();
    return doc;
}

/**
 * Đọc dữ liệu từ 1 tab có cache
 * @param {string} tabName Tên tab (ví dụ: 'KNX', 'faq', 'users')
 * @param {object} columnMap Map các trường sang mảng header tương ứng.
 *                           VD: { id: ['ID', 'Mã'], question: ['Câu hỏi', 'question'] }
 * @param {number} ttlMinutes Thời gian sống của cache (mặc định 10 phút)
 * @param {boolean} forceRefresh Bỏ qua cache
 */
async function readTab(tabName, columnMap, ttlMinutes = 10, forceRefresh = false) {
    const cacheFile = path.join(CACHE_DIR, `${tabName}_cache.json`);
    const ttlMs = ttlMinutes * 60 * 1000;

    // Kiểm tra cache
    if (!forceRefresh && fs.existsSync(cacheFile)) {
        const stat = fs.statSync(cacheFile);
        const age = Date.now() - stat.mtimeMs;
        if (age < ttlMs) {
            try {
                const data = JSON.parse(fs.readFileSync(cacheFile, 'utf-8'));
                if (Array.isArray(data)) return data;
            } catch (e) {}
        }
    }

    // Đọc từ Sheet
    const doc = await getDoc();
    const sheet = doc.sheetsByTitle[tabName] || doc.sheetsByIndex[0]; // Fallback sheet 0 nếu sai tên
    if (!sheet) throw new Error(`Không tìm thấy tab: ${tabName}`);

    const rows = await sheet.getRows();
    const data = rows.map(row => {
        const obj = {};
        for (const [key, candidates] of Object.entries(columnMap)) {
            obj[key] = getCell(row, candidates);
        }
        return obj;
    }).filter(d => Object.values(d).some(v => v !== null)); // Bỏ qua dòng rỗng hoàn toàn

    // Lưu cache
    fs.writeFileSync(cacheFile, JSON.stringify(data, null, 2));
    return data;
}

/**
 * Thêm 1 dòng mới vào tab (không dùng cache)
 */
async function appendRow(tabName, rowData) {
    const doc = await getDoc();
    const sheet = doc.sheetsByTitle[tabName];
    if (!sheet) throw new Error(`Không tìm thấy tab: ${tabName}`);
    await sheet.addRow(rowData);

    // Xóa cache vì dữ liệu đã thay đổi
    const cacheFile = path.join(CACHE_DIR, `${tabName}_cache.json`);
    if (fs.existsSync(cacheFile)) fs.unlinkSync(cacheFile);
}

/**
 * Cập nhật một dòng trong tab
 */
async function updateRow(tabName, searchKey, searchVal, updateData) {
    const doc = await getDoc();
    const sheet = doc.sheetsByTitle[tabName];
    if (!sheet) throw new Error(`Không tìm thấy tab: ${tabName}`);

    const rows = await sheet.getRows();
    const targetRow = rows.find(r => {
        try {
            return r.get(searchKey) === searchVal;
        } catch (_) { return false; }
    });

    if (targetRow) {
        Object.assign(targetRow, updateData);
        await targetRow.save();
    } else {
        const newRow = { [searchKey]: searchVal, ...updateData };
        await sheet.addRow(newRow);
    }

    // Xóa cache
    const cacheFile = path.join(CACHE_DIR, `${tabName}_cache.json`);
    if (fs.existsSync(cacheFile)) fs.unlinkSync(cacheFile);
}

module.exports = {
    readTab,
    appendRow,
    updateRow,
    getDoc
};
