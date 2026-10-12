const { getDoc } = require('./sheet_reader');

async function clearTab(doc, tabName) {
    const sheet = doc.sheetsByTitle[tabName];
    if (!sheet) {
        console.log(`Không tìm thấy tab ${tabName}`);
        return;
    }
    try {
        await sheet.loadHeaderRow();
        const headers = sheet.headerValues;

        // Xóa sạch toàn bộ tab (bao gồm cả header)
        await sheet.clear();

        // Đặt lại Header
        if (headers && headers.length > 0) {
            await sheet.setHeaderRow(headers);
        }
        console.log(`✅ Đã xóa dữ liệu cũ của tab "${tabName}", chỉ giữ lại Tiêu đề.`);
    } catch (e) {
        console.error(`❌ Lỗi dọn dẹp tab ${tabName}: ${e.message}`);
    }
}

async function main() {
    console.log(`[${new Date().toISOString()}] Bắt đầu dọn dẹp Google Sheets định kỳ...`);
    try {
        const doc = await getDoc();
        await clearTab(doc, 'KNX_history');
        await clearTab(doc, 'logs');
        console.log(`[${new Date().toISOString()}] Dọn dẹp hoàn tất.`);
    } catch (e) {
        console.error("Lỗi:", e.message);
    }
}
main();
