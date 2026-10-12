const fs = require('fs');
const readline = require('readline');

const path = require('path');
const LOG_FILE = path.join(__dirname, '..', 'logs', 'knx_history.jsonl');

function getToday() {
  const d = new Date(Date.now() + 7 * 3600 * 1000);
  return d.toISOString().split('T')[0];
}

async function readKnxLogs(targetDate) {
  if (!fs.existsSync(LOG_FILE)) return [];

  const entries = [];
  const stream = fs.createReadStream(LOG_FILE);
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      if (e.localDate === targetDate) {
        entries.push(e);
      }
    } catch (_) {}
  }

  return entries;
}

function buildSummary(entries) {
  if (entries.length === 0) return "Không có sự kiện bật/tắt thiết bị nào.";

  let total_commands = entries.length;
  let success_count = entries.filter(e => e.status === 'OK').length;
  let fail_count = total_commands - success_count;

  // Group by user
  let by_user = {};
  // Timeline
  let timeline = [];

  for (const e of entries) {
    let user = e.user_name || 'Hệ thống';
    let dev = e.device_id || e.ga;

    if (!by_user[user]) by_user[user] = { on: 0, off: 0, other: 0, errors: 0 };

    if (e.status !== 'OK') {
        by_user[user].errors++;
    } else {
        if (e.command === 'ON') by_user[user].on++;
        else if (e.command === 'OFF') by_user[user].off++;
        else by_user[user].other++;
    }

    timeline.push(`[${e.time}] ${user}: Lệnh ${e.command} tới ${dev} -> ${e.status}`);
  }

  let lines = [];
  lines.push(`📊 Báo cáo KNX`);
  lines.push(`Tổng lệnh: ${total_commands} | Thành công: ${success_count} | Thất bại: ${fail_count}`);
  lines.push(`\n👤 Thống kê theo Người dùng:`);
  for (const [user, stat] of Object.entries(by_user)) {
      lines.push(`  - ${user}: ${stat.on} lần Bật, ${stat.off} lần Tắt, ${stat.other} lệnh khác, ${stat.errors} lỗi.`);
  }

  lines.push(`\n🕒 Timeline chi tiết (10 sự kiện gần nhất):`);
  const recent = timeline.slice(-10);
  for (const t of recent) {
      lines.push(`  ${t}`);
  }

  return lines.join('\n');
}

async function main() {
  let targetDate = process.argv[2];
  if (!targetDate || targetDate === '{date}' || targetDate.trim() === '') {
    targetDate = getToday();
  } else {
    targetDate = targetDate.trim();
  }

  const entries = await readKnxLogs(targetDate);
  console.log(buildSummary(entries));
}

main().catch(err => {
  console.error('Lỗi:', err.message);
  process.exit(1);
});
