const { appendRow } = require('./sheet_reader');

/**
 * Ghi nhận user mới vào Sheet
 * Tham số truyền từ shell: node onboarding.js <userId> <name> <purpose> <source> <needsRequest> <serial>
 */
async function registerUser(userId, name, purpose, source, needsRequest, serial) {
    if (!userId) {
        console.log("Lỗi: Thiếu userId.");
        return;
    }

    try {
        const timestamp = new Date().toISOString();

        // 1. Ghi vào tab users
        await appendRow('users', {
            'User ID': userId,
            'Tên': name || 'Unknown',
            'Mục đích': purpose || '',
            'Nguồn': source || '',
            'Mã Serial': serial || '',
            'Ngày tham gia': timestamp
        });
        console.log(`✅ Đã ghi nhận user mới ${name} vào tab 'users'.`);



        // 3. Nếu có yêu cầu mở quyền, ghi vào tab requests
        if (String(needsRequest).toLowerCase() === 'true' || needsRequest === '1') {
            await appendRow('requests', {
                'User ID': userId,
                'Tên': name || 'Unknown',
                'Nội dung yêu cầu': `Xin cấp quyền truy cập. Mục đích: ${purpose} | Serial: ${serial || 'Không có'}`,
                'Trạng thái': 'Pending',
                'Ngày yêu cầu': timestamp
            });
            console.log(`✅ Đã tạo một Request xin cấp quyền cho ${name}. Chờ Admin duyệt.`);
        }
    } catch (error) {
        console.error(`❌ Lỗi ghi dữ liệu onboarding: ${error.message}`);
    }
}

// Nếu gọi qua CLI
if (require.main === module) {
    const args = process.argv.slice(2);
    const [userId, name, purpose, source, needsRequest, serial] = args;
    registerUser(userId, name, purpose, source, needsRequest, serial);
}

module.exports = { registerUser };
