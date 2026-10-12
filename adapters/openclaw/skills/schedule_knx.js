const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const device_id = process.argv[2];
const ga = process.argv[3];
const command = process.argv[4];
const delayMinutes = parseFloat(process.argv[5]);

if (!device_id || !ga || !command || isNaN(delayMinutes)) {
    console.error("Lỗi: Thiếu tham số. Cú pháp: node schedule_knx.js <device_id> <ga> <command> <delay_minutes>");
    process.exit(1);
}

const delayMs = Math.round(delayMinutes * 60 * 1000);

// Viết một script python nhỏ (worker) để chờ và thực thi
const workerScript = path.join(__dirname, '..', 'temp_worker_' + Date.now() + '.py');
const middlewarePath = path.join(__dirname, '..', 'middleware.py');

const pyCode = `
import time
import subprocess
import os

time.sleep(${delayMinutes * 60})
subprocess.run(['python3', '${middlewarePath}', '${device_id}', '${ga}', '${command}'])
os.remove(__file__)
`;

fs.writeFileSync(workerScript, pyCode);

// Khởi chạy script python chạy ngầm (detached)
const child = spawn('python3', [workerScript], {
    detached: true,
    stdio: 'ignore'
});
child.unref(); // Gỡ bỏ ràng buộc với tiến trình hiện tại để nó có thể thoát ngay lập tức

const timeStr = new Date(Date.now() + delayMs + 7*3600*1000).toISOString().split('T')[1].substring(0, 5);
console.log(`Đã lên lịch thành công! Lệnh [${command}] tới [${device_id}] sẽ tự động thực thi sau ${delayMinutes} phút (Lúc ${timeStr}).`);
