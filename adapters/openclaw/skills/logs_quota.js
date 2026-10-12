const fs = require('fs');
const path = require('path');
const { appendRow } = require('./sheet_reader');

const QUOTA_FILE = path.join(__dirname, '../config/user_quota.json');
const MAX_MSG_PER_MINUTE = 5;
const PENALTY_MINUTES = 5;

// Khởi tạo file quota nếu chưa có
if (!fs.existsSync(QUOTA_FILE)) {
    fs.writeFileSync(QUOTA_FILE, JSON.stringify({}));
}

/**
 * Kiểm tra xem user có bị giới hạn (rate-limit) không.
 * Gọi lệnh này TRƯỚC KHI xử lý tin nhắn public.
 */
function checkQuota(userId) {
    if (!userId) return { allowed: true };

    const now = Date.now();
    const data = JSON.parse(fs.readFileSync(QUOTA_FILE, 'utf-8'));

    if (!data[userId]) {
        data[userId] = { count: 0, firstMsgTime: now, penaltyUntil: 0 };
    }

    const user = data[userId];

    // Đang bị phạt
    if (user.penaltyUntil > now) {
        const remainingTime = Math.ceil((user.penaltyUntil - now) / 60000);
        return { allowed: false, reason: `Bạn đang bị chặn tạm thời. Vui lòng thử lại sau ${remainingTime} phút.` };
    }

    // Reset counter nếu đã qua 1 phút
    if (now - user.firstMsgTime > 60000) {
        user.count = 0;
        user.firstMsgTime = now;
    }

    user.count += 1;

    // Vượt hạn mức -> Phạt
    if (user.count > MAX_MSG_PER_MINUTE) {
        user.penaltyUntil = now + (PENALTY_MINUTES * 60 * 1000);
        fs.writeFileSync(QUOTA_FILE, JSON.stringify(data, null, 2));
        return { allowed: false, reason: `Cảnh báo SPAM: Bạn đã nhắn quá nhanh. Bot sẽ tạm ngưng phục vụ bạn trong ${PENALTY_MINUTES} phút.` };
    }

    fs.writeFileSync(QUOTA_FILE, JSON.stringify(data, null, 2));
    return { allowed: true };
}

/**
 * Đẩy log lên Google Sheet.
 * (Chạy ngầm không đợi để không chặn luồng)
 */
async function pushLogToSheet(userId, name, message) {
    try {
        await appendRow('logs', {
            'Thời gian': new Date().toISOString(),
            'User ID': userId,
            'Tên': name || 'Unknown',
            'Nội dung': message || ''
        });
    } catch (e) {
        console.error("Lỗi đẩy log lên Sheet:", e.message);
    }
}

// Chạy qua CLI
if (require.main === module) {
    const action = process.argv[2];
    const userId = process.argv[3];

    if (action === 'check') {
        const result = checkQuota(userId);
        console.log(JSON.stringify(result));
    } else if (action === 'log') {
        const name = process.argv[4];
        const message = process.argv.slice(5).join(' ');

        // Check quota trước
        const quota = checkQuota(userId);
        if (!quota.allowed) {
            console.log(JSON.stringify({ status: "BLOCKED", message: quota.reason }));
            // Vẫn log lại hành vi spam
            pushLogToSheet(userId, name, `[SPAM BLOCKED] ${message}`);
        } else {
            console.log(JSON.stringify({ status: "OK" }));
            pushLogToSheet(userId, name, message);
        }
    }
}

module.exports = { checkQuota, pushLogToSheet };
