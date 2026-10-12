const fs = require('fs');
const path = require('path');
const os = require('os');
const { exec } = require('child_process');

const args = process.argv.slice(2);
if (args.length < 2) {
    console.log("Thiếu tham số. Cú pháp: node update_system_token.js <type> <new_token>");
    process.exit(1);
}

const type = args[0].toLowerCase();
const newToken = args.slice(1).join(' ').trim();

const openclawPath = path.join(os.homedir(), '.openclaw', 'openclaw.json');

if (!fs.existsSync(openclawPath)) {
    console.log("Lỗi: Không tìm thấy file openclaw.json của hệ thống.");
    process.exit(1);
}

try {
    const config = JSON.parse(fs.readFileSync(openclawPath, 'utf8'));
    let changed = false;

    if (type === 'telegram') {
        if (!config.channels) config.channels = {};
        if (!config.channels.telegram) config.channels.telegram = {};
        config.channels.telegram.botToken = newToken;
        changed = true;
    } else if (type === 'zalo') {
        if (!config.channels) config.channels = {};
        if (!config.channels.zalo) config.channels.zalo = {};
        config.channels.zalo.botToken = newToken;
        changed = true;
    } else if (type === 'deepseek') {
        if (!config.models) config.models = { providers: {} };
        if (!config.models.providers) config.models.providers = {};
        if (!config.models.providers.deepseek) config.models.providers.deepseek = {};
        config.models.providers.deepseek.apiKey = newToken;
        changed = true;
    } else if (type === 'groq') {
        if (!config.models) config.models = { providers: {} };
        if (!config.models.providers) config.models.providers = {};
        if (!config.models.providers.groq) config.models.providers.groq = {};
        config.models.providers.groq.apiKey = newToken;
        changed = true;
    } else {
        console.log(`Lỗi: Loại token '${type}' không được hỗ trợ (chỉ hỗ trợ: telegram, zalo, deepseek, groq).`);
        process.exit(1);
    }

    if (changed) {
        fs.writeFileSync(openclawPath, JSON.stringify(config, null, 2));

        // Đồng bộ vào repo local để backup
        const localPath = null; // Never back up credentials into the source checkout.
        try {
            if (localPath) fs.writeFileSync(localPath, JSON.stringify(config, null, 2));
        } catch (err) {}

        console.log(`Đã cập nhật Token cho ${type.toUpperCase()} thành công! Hệ thống sẽ tự động khởi động lại sau 5 giây để áp dụng cấu hình mới.`);

        exec('(sleep 5 && systemctl --user restart openclaw-bot.service) >/dev/null 2>&1 &');
    }

} catch (e) {
    console.log("Lỗi khi xử lý cấu hình:", e.message);
}
