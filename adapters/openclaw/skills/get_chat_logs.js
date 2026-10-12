const fs = require('fs');
const readline = require('readline');
const path = require('path');

// Đường dẫn log (hỗ trợ cả đường dẫn cũ và mới)
const LOG_FILES = [
  path.join(__dirname, '..', 'logs', 'chat_logs.jsonl'),
  path.join(__dirname, '..', 'chat_logs.jsonl'), // legacy
];

function getLogFile() {
  return LOG_FILES.find(f => fs.existsSync(f)) || LOG_FILES[0];
}

function getToday() {
  const d = new Date(Date.now() + 7 * 3600 * 1000);
  return d.toISOString().split('T')[0];
}

function toMs(ts) {
  if (!ts) return 0;
  if (typeof ts === 'number') return ts;
  const parsed = Date.parse(ts);
  return isNaN(parsed) ? 0 : parsed;
}

function formatTime(ts) {
  const ms = toMs(ts);
  if (!ms) return '??:??';
  return new Date(ms + 7 * 3600 * 1000).toISOString().split('T')[1].substring(0, 5);
}

function inferIsGroup(entry) {
  if (entry.isGroup === true) return true;
  return typeof entry.from === 'string' && /:group:/i.test(entry.from);
}

function inferGroupId(entry) {
  if (typeof entry.groupId === 'string' && entry.groupId.trim()) return entry.groupId.trim();
  if (typeof entry.from !== 'string') return '';
  const parts = entry.from.split(':');
  const groupIndex = parts.indexOf('group');
  return groupIndex >= 0 ? parts.slice(groupIndex + 1).join(':').trim() : '';
}

function normalizeEntry(entry) {
  const isGroup = inferIsGroup(entry);
  const groupId = inferGroupId(entry);
  const groupName = (entry.groupName || entry.conversation_label || '').trim();
  return {
    ...entry,
    isGroup,
    groupId: entry.groupId || groupId || undefined,
    groupName: entry.groupName || (isGroup ? groupId : '') || groupName || undefined,
  };
}

const ACTION_LABELS = {
  knx_control_on:  '💡 Bật thiết bị',
  knx_control_off: '🔴 Tắt thiết bị',
  knx_dim:         '🌗 Chỉnh độ sáng',
  arrive_home:     '🏠 Về nhà',
  leave_home:      '🚪 Rời nhà',
  status_check:    '🔍 Kiểm tra trạng thái',
  schedule:        '⏰ Hẹn giờ / Lịch',
  weather:         '🌤 Hỏi thời tiết',
  report:          '📊 Báo cáo',
  chat:            '💬 Trò chuyện',
};

async function readLogs(targetDate) {
  const logFile = getLogFile();
  if (!fs.existsSync(logFile)) return [];

  const entries = [];
  const stream = fs.createReadStream(logFile);
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      const date = e.date || e.localDate;
      if (date === targetDate) entries.push(e);
    } catch (_) {}
  }

  return entries.sort((a, b) => toMs(a.ts || a.timestamp) - toMs(b.ts || b.timestamp));
}

function matchesTarget(entry, target) {
  const q = (target || '').trim().toLowerCase();
  if (!q) return true;

  const senderName = (entry.senderName || '').trim().toLowerCase();
  const senderId = (entry.senderId || '').trim().toLowerCase();
  const from = (entry.from || '').trim().toLowerCase();
  const channel = (entry.channel || entry.channelId || '').trim().toLowerCase();
  const groupName = (entry.groupName || '').trim().toLowerCase();
  const groupId = inferGroupId(entry).toLowerCase();

  return (
    senderName === q ||
    senderId === q ||
    from === q ||
    channel === q ||
    groupName === q ||
    groupId === q ||
    senderName.includes(q) ||
    senderId.includes(q) ||
    from.includes(q) ||
    channel.includes(q) ||
    groupName.includes(q) ||
    groupId.includes(q)
  );
}

function buildStats(entries) {
  const byPerson = {};     // senderName -> { count, actions: {}, events: [] }
  const byAction = {};     // action -> count
  const timeline = [];     // [{time, senderName, action, content}]

  for (const e of entries) {
    const name = e.senderName || e.from || 'Unknown';
    const action = e.action || 'chat';
    const ts = e.ts || e.timestamp || 0;
    const time = ts ? formatTime(ts) : (e.time || '??:??');
    const channel = e.channel || e.channelId || '?';
    const isGroup = inferIsGroup(e);
    const groupId = inferGroupId(e);
    const groupLabel = isGroup ? `[${e.groupName || groupId || 'Group'}]` : '[DM]';

    // Theo người
    if (!byPerson[name]) byPerson[name] = { count: 0, actions: {}, channel, groups: new Set() };
    byPerson[name].count++;
    byPerson[name].actions[action] = (byPerson[name].actions[action] || 0) + 1;
    if (isGroup) byPerson[name].groups.add(e.groupName || groupId || 'Group');

    // Theo hành động
    byAction[action] = (byAction[action] || 0) + 1;

    // Timeline (chỉ hành động quan trọng)
    if (action !== 'chat') {
      timeline.push({ time, name, action, content: e.content, channel, groupLabel });
    }
  }

  return { byPerson, byAction, timeline, total: entries.length };
}

function renderReport(stats, targetDate, entries, target) {
  const { byPerson, byAction, timeline, total } = stats;

  if (total === 0) {
    return target ? `Không có tin nhắn nào khớp "${target}" trong ngày ${targetDate}.` : `Không có tin nhắn nào trong ngày ${targetDate}.`;
  }

  const lines = [];
  lines.push(`📊 Thống kê chat ngày ${targetDate}`);
  if (target) lines.push(`Bộ lọc: ${target}`);
  lines.push(`Tổng: ${total} tin nhắn từ ${Object.keys(byPerson).length} người\n`);

  // === Sự kiện quan trọng ===
  if (timeline.length > 0) {
    lines.push('🔑 Sự kiện nổi bật:');
    for (const ev of timeline) {
      const label = ACTION_LABELS[ev.action] || ev.action;
      lines.push(`  ${ev.time} | ${ev.name} — ${label}`);
      if (['arrive_home','leave_home','knx_control_on','knx_control_off'].includes(ev.action)) {
        lines.push(`         "${ev.content.substring(0, 60)}"`);
      }
    }
    lines.push('');
  }

  // === Theo người ===
  lines.push('👥 Theo người:');
  for (const [name, info] of Object.entries(byPerson)) {
    const topActions = Object.entries(info.actions)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([a, c]) => `${ACTION_LABELS[a] || a} (${c})`)
      .join(', ');
    const groups = [...info.groups].join(', ') || 'DM';
    lines.push(`  • ${name} [${info.channel}/${groups}]: ${info.count} tin — ${topActions}`);
  }
  lines.push('');

  // === Tóm tắt hành động ===
  const importantActions = Object.entries(byAction)
    .filter(([a]) => a !== 'chat')
    .sort((a, b) => b[1] - a[1]);

  if (importantActions.length > 0) {
    lines.push('📋 Tóm tắt hành động:');
    for (const [action, count] of importantActions) {
      lines.push(`  ${ACTION_LABELS[action] || action}: ${count} lần`);
    }
  }

  return lines.join('\n');
}

async function main() {
  let targetDate = process.argv[2];
  const arg3 = process.argv[3] || '';
  const arg4 = process.argv[4] || '';
  let target = '';
  let mode = 'summary'; // summary | raw

  if (!targetDate || targetDate === '{date}' || targetDate.trim() === '') {
    targetDate = getToday();
  } else {
    targetDate = targetDate.trim();
  }

  if (arg3 === 'summary' || arg3 === 'raw') {
    mode = arg3;
  } else {
    target = arg3.trim();
    if (arg4 === 'summary' || arg4 === 'raw') mode = arg4;
  }

  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) {
    console.log('Lỗi: Nhập định dạng YYYY-MM-DD (VD: 2026-06-24)');
    return;
  }

  const entries = (await readLogs(targetDate)).map(normalizeEntry).filter((entry) => matchesTarget(entry, target));

  if (mode === 'raw') {
    // Raw mode: trả toàn bộ cho bot xử lý
    console.log(JSON.stringify({ date: targetDate, target: target || undefined, count: entries.length, entries }));
    return;
  }

  // Summary mode: báo cáo đẹp
  const stats = buildStats(entries);
  console.log(renderReport(stats, targetDate, entries, target));
}

main().catch(err => {
  console.error('Lỗi:', err.message);
  process.exit(1);
});
