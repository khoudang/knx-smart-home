require('dotenv').config();

const axios = require('axios');
const crypto = require('crypto');
const TelegramBot = require('node-telegram-bot-api');

const ZALO_TOKEN = process.env.ZALO_BOT_TOKEN;
const TELEGRAM_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const AI_AGENT_URL = process.env.AI_AGENT_URL || 'http://localhost:5000/chat';
const TELEGRAM_ENABLED = (process.env.TELEGRAM_ENABLED || 'true').toLowerCase() !== 'false';
const ZALO_ENABLED = (process.env.ZALO_ENABLED || 'true').toLowerCase() !== 'false';
const ZALO_MODE = (process.env.ZALO_MODE || 'polling').toLowerCase();
const ZALO_SENDER_FIELDS = ['sender_id', 'user_id', 'from_id', 'uid', 'id'];
const ZALO_DEDUP_TTL_MS = Number(process.env.ZALO_DEDUP_TTL_MS || 10 * 60 * 1000);
const processedZaloUpdates = new Map();
const activeZaloRequests = new Set();
let teleBot = null;

if ((TELEGRAM_ENABLED && !TELEGRAM_TOKEN) || (ZALO_ENABLED && !ZALO_TOKEN)) {
  console.warn('One or more chatbot channel tokens are not configured; missing channels will stay disabled.');
}

if ((!TELEGRAM_ENABLED || !TELEGRAM_TOKEN) && (!ZALO_ENABLED || !ZALO_TOKEN)) {
  if (process.env.ADAPTER_ALLOW_NO_TOKENS === 'true') {
    console.warn('No chatbot adapters enabled; running adapter health mode only.');
  } else {
    console.error('No chatbot adapters enabled.');
    process.exit(1);
  }
}

async function sendToAgent({ channel, userId, sourceMessageId, text, displayName, username }) {
  const response = await axios.post(AI_AGENT_URL, {
    message: text,
    source: channel,
    source_message_id: sourceMessageId,
    user_id: userId,
    display_name: displayName,
    username
  });
  return response.data;
}

function maskId(value) {
  const text = String(value || '');
  if (text.length <= 6) return '***';
  return `${text.slice(0, 3)}***${text.slice(-3)}`;
}

function textHash(text) {
  return crypto.createHash('sha256').update(normalizeForHash(text)).digest('hex').slice(0, 12);
}

function normalizeForHash(text) {
  return String(text || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function telegramProfile(msg) {
  const from = msg && msg.from ? msg.from : {};
  const userId = from.id ? String(from.id) : (msg && msg.chat && msg.chat.id ? String(msg.chat.id) : '');
  const firstName = String(from.first_name || '').trim();
  const lastName = String(from.last_name || '').trim();
  const username = String(from.username || '').trim().replace(/^@+/, '');
  let displayName = [firstName, lastName].filter(Boolean).join(' ').trim();
  if (!displayName && username) displayName = `@${username}`;
  if (!displayName) displayName = 'Không rõ tên';
  return {
    userId,
    displayName,
    username,
    hasFirstName: Boolean(firstName),
    hasLastName: Boolean(lastName),
    hasUsername: Boolean(username)
  };
}

if (TELEGRAM_ENABLED && TELEGRAM_TOKEN) {
  console.log('Initializing Telegram Bot...');
  teleBot = new TelegramBot(TELEGRAM_TOKEN, { polling: true });

  teleBot.getMe().then(me => {
    console.log(`[Telegram] Authentication successful. Bot Name: ${me.first_name} (@${me.username})`);
  }).catch(err => {
    console.error(`[Telegram] Authentication failed! Error: ${err.message}`);
    if (err.response && [401, 403].includes(err.response.statusCode)) {
      console.error('[Telegram] Fatal: Token is invalid or expired. Stopping.');
      process.exit(1);
    }
  });

  teleBot.on('message', async (msg) => {
    const chatId = msg.chat.id;
    const profile = telegramProfile(msg);
    const text = msg.text;
    if (!text) return;
    const sourceMessageId = msg.message_id ? msg.message_id.toString() : `${profile.userId}:${msg.date}:${textHash(text)}`;
    const requestId = `telegram:${sourceMessageId}`;

    console.log(
      `[Telegram] Received message from ${maskId(profile.userId)} ` +
      `profile first=${profile.hasFirstName} last=${profile.hasLastName} username=${profile.hasUsername}`
    );

    try {
      const response = await sendToAgent({
        channel: 'telegram',
        userId: profile.userId,
        sourceMessageId,
        text,
        displayName: profile.displayName,
        username: profile.username
      });
      console.log(`[Telegram] result request_id=${requestId} status=${response && response.status ? response.status : ''}`);
      if (response && response.reply) {
        await teleBot.sendMessage(chatId, response.reply);
      }
      await dispatchNotifications(response);
    } catch (error) {
      console.error(`[Telegram] error request_id=${requestId} message=${error.message}`);
      await teleBot.sendMessage(chatId, 'He thong AI dang ban hoac co loi, vui long thu lai sau.');
    }
  });
}

const ZALO_API_BASE = `https://bot-api.zaloplatforms.com/bot${ZALO_TOKEN}`;

async function checkZaloAuth() {
  try {
    const res = await axios.get(`${ZALO_API_BASE}/getMe`);
    if (res.data.ok) {
      console.log(`[Zalo] Authentication successful. Bot Name: ${res.data.result.display_name} (@${res.data.result.account_name})`);
      return res.data.result;
    }
    console.error('[Zalo] getMe returned not ok:', res.data);
    process.exit(1);
  } catch (err) {
    console.error('[Zalo] Authentication failed! Error:', err.message);
    if (err.response && [401, 403].includes(err.response.status)) {
      console.error('[Zalo] Fatal: Token is invalid or expired. Stopping.');
      process.exit(1);
    }
  }
}

async function sendZaloMessage(chatId, text) {
  try {
    await axios.post(`${ZALO_API_BASE}/sendMessage`, {
      chat_id: chatId,
      text
    }, {
      headers: { 'Content-Type': 'application/json; charset=utf-8' }
    });
  } catch (err) {
    console.error('[Zalo] Error sending message:', err.message);
  }
}

async function dispatchNotifications(response) {
  const notifications = response && Array.isArray(response.notifications) ? response.notifications : [];
  for (const notification of notifications) {
    const channel = String(notification.channel || '').toLowerCase();
    const userId = String(notification.user_id || '');
    const text = String(notification.text || '');
    if (!channel || !userId || !text) continue;
    try {
      if (channel === 'telegram' && teleBot) {
        await teleBot.sendMessage(userId, text);
        console.log(`[Notify] sent channel=telegram user=${maskId(userId)}`);
      } else if (channel === 'zalo' && ZALO_ENABLED && ZALO_TOKEN) {
        await sendZaloMessage(userId, text);
        console.log(`[Notify] sent channel=zalo user=${maskId(userId)}`);
      }
    } catch (err) {
      console.error(`[Notify] Failed channel=${channel} user=${maskId(userId)}: ${err.message}`);
    }
  }
}

function extractZaloSender(message) {
  const candidates = [];
  for (const key of ['from', 'sender', 'user', 'chat']) {
    if (message && message[key] && typeof message[key] === 'object') {
      candidates.push(message[key]);
    }
  }
  candidates.push(message);
  for (const candidate of candidates) {
    for (const field of ZALO_SENDER_FIELDS) {
      if (candidate && candidate[field]) {
        return { userId: String(candidate[field]), field };
      }
    }
  }
  return null;
}

function extractZaloChatId(message, sender) {
  if (message && message.chat && message.chat.id) return String(message.chat.id);
  if (sender && sender.userId) return sender.userId;
  return null;
}

function zaloUpdateId(update) {
  if (!update) return null;
  return update.update_id || update.id || null;
}

function zaloMessageId(update) {
  if (update && update.message && update.message.message_id) return update.message.message_id;
  if (update && update.message_id) return update.message_id;
  return null;
}

function zaloMessageTimestamp(update) {
  if (update && update.message && update.message.date) return update.message.date;
  if (update && update.date) return update.date;
  return Date.now();
}

function zaloDedupKey(update, sender, text) {
  const updateId = zaloUpdateId(update);
  if (updateId) return `zalo:${updateId}`;
  const messageId = zaloMessageId(update);
  if (messageId) return `zalo:${messageId}`;
  const senderId = sender && sender.userId ? sender.userId : 'unknown';
  return `zalo:${senderId}:${zaloMessageTimestamp(update)}:${textHash(text)}`;
}

function seenZaloUpdate(key) {
  if (!key) return false;
  const now = Date.now();
  for (const [storedKey, expiresAt] of processedZaloUpdates.entries()) {
    if (expiresAt <= now) processedZaloUpdates.delete(storedKey);
  }
  if (processedZaloUpdates.has(key)) return true;
  processedZaloUpdates.set(key, now + ZALO_DEDUP_TTL_MS);
  return false;
}

let zaloOffset = 0;
async function pollZalo() {
  try {
    const res = await axios.post(`${ZALO_API_BASE}/getUpdates`, {
      offset: zaloOffset,
      timeout: 30
    });

    if (res.data && res.data.ok && res.data.result) {
      let updates = res.data.result;
      if (!Array.isArray(updates)) {
        updates = [updates];
      }
      for (const update of updates) {
        if (update.message && update.message.text) {
          const sender = extractZaloSender(update.message);
          const chatId = extractZaloChatId(update.message, sender);
          if (!sender || !chatId) {
            console.warn('[Zalo] Skipping message without sender id');
            continue;
          }
          const text = update.message.text;
          if (zaloUpdateId(update)) {
            zaloOffset = Number(zaloUpdateId(update)) + 1;
          }
          const dedupKey = zaloDedupKey(update, sender, text);
          const messageId = zaloMessageId(update);
          console.log(`[Zalo] update_id=${zaloUpdateId(update) || ''} message_id=${messageId || ''} sender=${maskId(sender.userId)} text_hash=${textHash(text)} dedup_key=${dedupKey} sender_field=${sender.field}`);
          if (seenZaloUpdate(dedupKey)) {
            console.log(`[Zalo] Duplicate update skipped dedup_key=${dedupKey}`);
            continue;
          }
          if (activeZaloRequests.has(dedupKey)) {
            console.log(`[Zalo] Active request already running dedup_key=${dedupKey}`);
            await sendZaloMessage(chatId, 'Yêu cầu trước vẫn đang được xử lý. Bạn vui lòng chờ một chút.');
            continue;
          }

          activeZaloRequests.add(dedupKey);
          console.log(`[Zalo] lock acquired request_id=${dedupKey}`);
          try {
            const aiRes = await sendToAgent({
              channel: 'zalo',
              userId: sender.userId,
              sourceMessageId: dedupKey,
              text
            });
            console.log(`[Zalo] result request_id=${dedupKey} status=${aiRes && aiRes.status ? aiRes.status : ''}`);
            if (aiRes && aiRes.reply) {
              await sendZaloMessage(chatId, aiRes.reply);
            }
            await dispatchNotifications(aiRes);
          } catch (error) {
            console.error('[Zalo] Error calling AI Agent:', error.message);
            await sendZaloMessage(chatId, 'Hệ thống AI đang bận hoặc có lỗi, vui lòng thử lại sau.');
          } finally {
            activeZaloRequests.delete(dedupKey);
            console.log(`[Zalo] lock released request_id=${dedupKey}`);
          }
        }
      }
    }
  } catch (err) {
    console.error('[Zalo] Polling error:', err.message);
  } finally {
    setTimeout(pollZalo, 1000);
  }
}

if (ZALO_ENABLED && ZALO_TOKEN) {
  if (ZALO_MODE !== 'polling') {
    console.warn(`[Zalo] ZALO_MODE=${ZALO_MODE}; polling adapter disabled.`);
  } else {
    console.log('Initializing Zalo Bot in polling mode...');
    checkZaloAuth().then(() => {
      pollZalo();
    });
  }
}

console.log('Chatbot Gateway is running as adapter-only.');
