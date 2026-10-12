const fs = require('fs');
const path = require('path');

const SHEET_ID_FILE = path.join(__dirname, '../config/sheet_id.txt');

function extractSheetId(input) {
    // Nếu là full URL dạng .../d/<ID>/edit...
    const match = input.match(/\/d\/([a-zA-Z0-9-_]+)/);
    if (match) return match[1];
    // Nếu user đưa thẳng ID (không có URL)
    return input.trim();
}

function main() {
    const arg = process.argv[2];
    if (!arg) {
        console.error("Usage: update_sheet_link.js <url_or_id>");
        process.exit(1);
    }
    const newId = extractSheetId(arg);
    fs.writeFileSync(SHEET_ID_FILE, newId + "\n");
    console.log(`✅ Đã cập nhật Sheet ID mới: ${newId}`);
}

main();
