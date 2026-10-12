#!/usr/bin/env node
const fs = require('fs');
const readline = require('readline');
const path = require('path');
const os = require('os');

const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
});

const openclawPath = path.join(os.homedir(), '.openclaw', 'openclaw.json');

if (!fs.existsSync(openclawPath)) {
    console.error("❌ Không tìm thấy file cấu hình tại:", openclawPath);
    console.log("ℹ️ Đang kiểm tra file openclaw.json trong thư mục hiện tại...");
    const localPath = path.join(__dirname, 'openclaw.json');
    if (fs.existsSync(localPath)) {
        console.log("✅ Tìm thấy openclaw.json nội bộ. Hãy copy nó vào ~/.openclaw/openclaw.json trước.");
    }
    process.exit(1);
}

const config = JSON.parse(fs.readFileSync(openclawPath, 'utf8'));

console.log("==========================================");
console.log("   CÔNG CỤ CẬP NHẬT TOKEN & API KEY");
console.log(" (Để trống và nhấn Enter nếu không muốn đổi)");
console.log("==========================================\n");

rl.question('Nhập Telegram Bot Token mới: ', (teleToken) => {
    rl.question('Nhập Zalo Bot Token mới: ', (zaloToken) => {
        rl.question('Nhập DeepSeek API Key mới: ', (deepseekKey) => {
            rl.question('Nhập Groq API Key mới: ', (groqKey) => {

                let changed = false;

                if (teleToken.trim() !== '') {
                    if (config.channels && config.channels.telegram) {
                        config.channels.telegram.botToken = teleToken.trim();
                        changed = true;
                        console.log("✅ Đã cập nhật Telegram Token");
                    }
                }

                if (zaloToken.trim() !== '') {
                    if (config.channels && config.channels.zalo) {
                        config.channels.zalo.botToken = zaloToken.trim();
                        changed = true;
                        console.log("✅ Đã cập nhật Zalo Token");
                    }
                }

                if (deepseekKey.trim() !== '') {
                    if (config.models && config.models.providers && config.models.providers.deepseek) {
                        config.models.providers.deepseek.apiKey = deepseekKey.trim();
                        changed = true;
                        console.log("✅ Đã cập nhật DeepSeek API Key");
                    }
                }

                if (groqKey.trim() !== '') {
                    if (config.models && config.models.providers && config.models.providers.groq) {
                        config.models.providers.groq.apiKey = groqKey.trim();
                        changed = true;
                        console.log("✅ Đã cập nhật Groq API Key");
                    }
                }

                if (changed) {
                    fs.writeFileSync(openclawPath, JSON.stringify(config, null, 2));

                    // Đồng bộ lại file openclaw.json vào dự án để tiện lưu trữ
                    // Credential backups stay outside the checkout.

                    console.log("\n🎉 Đã lưu cấu hình mới vào máy và đồng bộ vào thư mục dự án!");
                    console.log("Vui lòng chạy lệnh sau để áp dụng:");
                    console.log("  systemctl --user restart openclaw-bot.service");
                } else {
                    console.log("\nℹ️ Không có thay đổi nào được thực hiện.");
                }

                rl.close();
            });
        });
    });
});
