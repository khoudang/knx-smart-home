const { appendRow } = require('./sheet_reader');

const device_id = process.argv[2];
const ga = process.argv[3];
const command = process.argv[4];
const status = process.argv[5];
const user_name = process.argv[6] || 'Hệ thống';

async function main() {
    try {
        await appendRow('KNX_history', {
            'Thời gian': new Date().toISOString(),
            'User ID': user_name === 'Hệ thống' ? 'Hệ thống' : 'User',
            'Tên': user_name,
            'GA': `${device_id} (${ga})`,
            'Hành động': command,
            'Kết quả': status
        });
    } catch (e) {
        console.error("Lỗi cập nhật KNX_history:", e.message);
    }
}
main();
