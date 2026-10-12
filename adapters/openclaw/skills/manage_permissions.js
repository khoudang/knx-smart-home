/**
 * manage_permissions.js — Quản lý quyền điều khiển nhà thông minh (Dùng Google Sheets)
 *
 * Usage (bot gọi qua shell):
 *   node manage_permissions.js list
 *   node manage_permissions.js check <channel:userId>
 *   node manage_permissions.js ban <channel:userId|name_search> [displayName]
 *   node manage_permissions.js unban <channel:userId|name_search>
 *   node manage_permissions.js add-admin <channel:userId> [displayName]
 *   node manage_permissions.js remove-admin <channel:userId|name_search>
 *   node manage_permissions.js find <name_search>
 */

const fs = require('fs');
const path = require('path');
const { readTab, updateRow } = require('./sheet_reader');

const LOG_FILE   = path.join(__dirname, '..', 'logs', 'chat_logs.jsonl');
const LOG_FILE2  = path.join(__dirname, '..', 'chat_logs.jsonl');

const COLUMN_MAP = {
  key: ['User ID', 'ID', 'Key', 'Mã'],
  name: ['Name', 'Tên', 'Họ Tên'],
  role: ['Role', 'Quyền', 'Vai trò'],
  status: ['Status', 'Trạng thái', 'Banned']
};

// ── helpers ──────────────────────────────────────────────────────────────────

function readLogLines() {
  const logFile = fs.existsSync(LOG_FILE) ? LOG_FILE : LOG_FILE2;
  if (!fs.existsSync(logFile)) return [];
  return fs.readFileSync(logFile, 'utf-8').split('\n').filter(Boolean);
}

function inferExactUserMatch(e, q) {
  const name = (e.senderName || '').trim().toLowerCase();
  const from = (e.from || '').trim().toLowerCase();
  const senderId = (e.senderId || '').trim().toLowerCase();
  return name === q || from === q || senderId === q;
}

/** Tìm user trong chat log theo tên (case-insensitive, partial) */
function findUserByName(query) {
  const q = query.toLowerCase().trim();
  const seen = new Map();

  const lines = readLogLines();
  for (const line of lines) {
    try {
      const e = JSON.parse(line);
      const senderId = e.senderId || '';
      const channel  = e.channel || e.channelId || '';
      if (!senderId || !channel) continue;

      const key  = `${channel}:${senderId}`;
      const name = (e.senderName || '').toLowerCase();
      const from = (e.from || '').toLowerCase();
      const exact = inferExactUserMatch(e, q);

      if (exact || name.includes(q) || from.includes(q) || senderId.toLowerCase().includes(q)) {
        if (!seen.has(key)) seen.set(key, { key, name: e.senderName, channel, senderId, count: 0, exact: false });
        const item = seen.get(key);
        item.count++;
        if (exact) item.exact = true;
      }
    } catch (_) {}
  }

  return [...seen.values()].sort((a, b) => Number(b.exact) - Number(a.exact) || b.count - a.count || String(a.name).localeCompare(String(b.name)));
}

/** Resolve arg: nếu đã có dạng "channel:id" dùng luôn; nếu không, tìm theo tên */
function resolveTarget(arg) {
  if (!arg) return { err: 'Thiếu thông tin user. Ví dụ: telegram:1000000001 hoặc tên của họ.' };
  if (arg.includes(':')) {
    return { key: arg, name: arg };
  }
  const matches = findUserByName(arg);
  if (matches.length === 0) return { err: `Không tìm thấy "${arg}" trong lịch sử chat. Dùng "find ${arg}" để kiểm tra hoặc dùng key đầy đủ channel:userId.` };
  const exactMatches = matches.filter(m => m.exact);
  if (exactMatches.length === 1) return { key: exactMatches[0].key, name: exactMatches[0].name };
  if (matches.length > 1) {
    const list = matches.slice(0,5).map(m => `  • ${m.name} → ${m.key} (${m.count} tin)`).join('\n');
    return { err: `Tìm thấy nhiều người khớp với "${arg}":\n${list}\n\nDùng key đầy đủ để chắc chắn, VD: ban telegram:1000000001` };
  }
  return { key: matches[0].key, name: matches[0].name };
}

// ── actions ──────────────────────────────────────────────────────────────────

async function main() {
  const [,, action, ...rawArgs] = process.argv;
  const arg0 = rawArgs[0] || '';
  const arg1 = rawArgs.slice(1).join(' ') || '';

  // Đọc danh sách permissions từ Sheet (cache 5 phút)
  let permissionsData = [];
  try {
    permissionsData = await readTab('permissions', COLUMN_MAP, 5);
  } catch (e) {
    console.log(`Lỗi đọc sheet permissions: ${e.message}`);
    process.exit(1);
  }

  // Chuyển thành map cho dễ xử lý
  const permMap = {};
  for (const row of permissionsData) {
    if (row.key) {
      permMap[row.key] = {
        name: row.name || 'Unknown',
        role: (row.role || 'user').toLowerCase(),
        status: (row.status || 'active').toLowerCase()
      };
    }
  }

  switch (action) {

    case 'check': {
      if (!arg0) { console.log(JSON.stringify({ allowed:false, reason:'missing-id' })); return; }
      const key = arg0.includes(':') ? arg0 : null;
      if (!key) { console.log(JSON.stringify({ allowed:false, reason:'invalid-key', hint:'Dùng channel:userId' })); return; }

      const userPerm = permMap[key];

      // Mặc định OpenClaw theo Allowlist cho mọi kênh:
      // Nhưng nếu Boss muốn mở công khai, ta sẽ sửa luồng. Hiện tại ta kiểm tra nếu bị banned.
      if (userPerm && userPerm.status === 'banned') {
        console.log(JSON.stringify({ allowed:false, reason:'banned', name:userPerm.name }));
        return;
      }

      const isAdmin = userPerm && userPerm.role === 'admin';

      // Nếu không có trong Sheet, coi như chưa được cấp quyền (Allowlist strict mode)
      if (!userPerm) {
        console.log(JSON.stringify({ allowed:false, reason:'not-in-allowlist' }));
        return;
      }

      console.log(JSON.stringify({ allowed:true, isAdmin, key }));
      break;
    }

    case 'find': {
      if (!arg0) { console.log('Thiếu từ khoá tìm kiếm.'); return; }
      const results = findUserByName(arg0);
      if (!results.length) { console.log(`Không tìm thấy ai tên "${arg0}" trong lịch sử chat.`); return; }
      console.log(`Kết quả tìm "${arg0}":`);
      for (const r of results.slice(0,10)) {
        const up = permMap[r.key];
        const isAdmin  = (up && up.role === 'admin') ? ' 👑Admin' : '';
        const isBanned = (up && up.status === 'banned') ? ' 🚫Cấm' : '';
        console.log(`  • ${r.name} → ${r.key} (${r.count} tin)${isAdmin}${isBanned}`);
      }
      break;
    }

    case 'ban': {
      const { key, name, err } = resolveTarget(arg0);
      if (err) { console.log(`❌ ${err}`); return; }
      const displayName = arg1 || name;

      if (permMap[key] && permMap[key].role === 'admin') {
        console.log(`⚠️ Không thể cấm Admin! Xóa admin trước bằng: remove-admin ${key}`);
        return;
      }

      if (permMap[key] && permMap[key].status === 'banned') {
        console.log(`ℹ️ ${displayName} (${key}) đã bị cấm rồi.`);
        return;
      }

      await updateRow('permissions', 'User ID', key, { 'User ID': key, 'Name': displayName, 'Status': 'banned' });
      console.log(`✅ Đã cấm **${displayName}** (${key}) trên Google Sheets.`);
      break;
    }

    case 'unban': {
      const { key, name, err } = resolveTarget(arg0);
      if (err) { console.log(`❌ ${err}`); return; }

      if (!permMap[key] || permMap[key].status !== 'banned') {
        console.log(`ℹ️ ${name} (${key}) không có trong danh sách cấm.`);
        return;
      }

      const dn = permMap[key].name;
      await updateRow('permissions', 'User ID', key, { 'User ID': key, 'Status': 'active' });
      console.log(`✅ Đã bỏ cấm **${dn}** (${key}).`);
      break;
    }

    case 'add-admin': {
      const { key, name, err } = resolveTarget(arg0);
      if (err) { console.log(`❌ ${err}`); return; }
      const displayName = arg1 || name;

      await updateRow('permissions', 'User ID', key, { 'User ID': key, 'Name': displayName, 'Role': 'admin', 'Status': 'active' });
      console.log(`✅ Đã thêm **${displayName}** (${key}) làm Admin trên Google Sheets.`);
      break;
    }

    case 'remove-admin': {
      const { key, name, err } = resolveTarget(arg0);
      if (err) { console.log(`❌ ${err}`); return; }

      if (!permMap[key] || permMap[key].role !== 'admin') {
        console.log(`ℹ️ ${name} (${key}) không phải Admin.`);
        return;
      }

      const dn = permMap[key].name;
      await updateRow('permissions', 'User ID', key, { 'User ID': key, 'Role': 'user' });
      console.log(`✅ Đã xóa quyền Admin của **${dn}** (${key}).`);
      break;
    }

    case 'allow-user': {
      // Cho phép một người dùng vào danh sách được phép
      const { key, name, err } = resolveTarget(arg0);
      if (err) { console.log(`❌ ${err}`); return; }
      const displayName = arg1 || name;

      await updateRow('permissions', 'User ID', key, { 'User ID': key, 'Name': displayName, 'Role': 'user', 'Status': 'active' });
      console.log(`✅ Đã thêm **${displayName}** (${key}) vào danh sách được phép điều khiển.`);
      break;
    }

    case 'list': {
      let admins = [];
      let banned = [];
      let allowed = [];
      for (const [k, v] of Object.entries(permMap)) {
        if (v.role === 'admin') admins.push(`  👑 ${v.name} (${k})`);
        else if (v.status === 'banned') banned.push(`  🚫 ${v.name} (${k})`);
        else allowed.push(`  ✅ ${v.name} (${k})`);
      }

      console.log(`🔐 Phân quyền (Google Sheets)`);
      console.log(`\n👑 Admin:\n${admins.join('\n') || '  (trống)'}`);
      console.log(`\n🚫 Bị cấm:\n${banned.join('\n') || '  (không ai)'}`);
      console.log(`\n✅ Được phép (Users):\n${allowed.join('\n') || '  (trống)'}`);
      break;
    }

    default:
      console.log(`Lệnh không hợp lệ: ${action}\nDùng: check|find|ban|unban|add-admin|remove-admin|allow-user|list`);
  }
}

main().catch(err => {
  console.error("Lỗi:", err.message);
  process.exit(1);
});
