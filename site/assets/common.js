/* 公共:数据快照徽章 + 页脚(合规声明) */
function setPatchBadge(patch) {
  const el = document.getElementById('patchBadge');
  if (el && patch) {
    const versions = [
      patch.game && `客户端资料 <b>${escapeHTML(patch.game)}</b>`,
      patch.opggGame && `OP.GG 攻略 <b>${escapeHTML(patch.opggGame)}</b>`,
      patch.statsGame && `统计快照 <b>${escapeHTML(patch.statsGame)}</b>`,
    ].filter(Boolean).join(' · ');
    const snapshotDate = patch.hexdataDate || patch.builtAt || '';
    const age = calendarAgeLabel(snapshotDate);
    const date = snapshotDate
      ? `<span class="patch-date"> · 统计截至 <time datetime="${escapeHTML(snapshotDate)}">${escapeHTML(snapshotDate)}</time>${age ? ` · ${age}` : ''}</span>`
      : '';
    el.innerHTML = `${versions}${date}`;
    el.title = '各来源独立快照，版本可能不同；请以游戏内为准';
  }
}

function renderFooter() {
  const el = document.getElementById('footer');
  if (!el) return;
  el.innerHTML = `
    <div>数据来源:op.gg · hexdata.com.cn · Riot Data Dragon · CommunityDragon,仅供参考,请以游戏内为准</div>
    <div>海克斯强化仅展示推荐度,不展示胜率数据(遵循 Riot 第三方数据政策)</div>
    <div class="src">海斗速查 was created under Riot Games' "Legal Jibber Jabber" policy using assets owned by Riot Games.
    Riot Games does not endorse or sponsor this project.</div>`;
}

function pct(x, digits = 1) {
  return x == null ? '—' : (x * 100).toFixed(digits) + '%';
}

const JSON_LOAD_TIMEOUT_MS = 20000;

async function loadJSON(url, timeoutMs = JSON_LOAD_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      cache: 'default',
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`加载失败 ${url}: HTTP ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(`加载超时 ${url}`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

function localCalendarDateISO(date = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function calendarDateEpochDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ''));
  if (!match) return null;
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const milliseconds = Date.UTC(year, month - 1, day);
  const parsed = new Date(milliseconds);
  if (
    parsed.getUTCFullYear() !== year
    || parsed.getUTCMonth() + 1 !== month
    || parsed.getUTCDate() !== day
  ) return null;
  return Math.floor(milliseconds / 86400000);
}

function calendarAgeLabel(value, referenceDate = localCalendarDateISO()) {
  const valueDay = calendarDateEpochDay(value);
  const referenceDay = calendarDateEpochDay(referenceDate);
  if (valueDay == null || referenceDay == null || referenceDay < valueDay) return '';
  const ageDays = referenceDay - valueDay;
  return ageDays === 0 ? '今天' : `${ageDays}天前`;
}

function videoIsWithinPublicationWindow(video, referenceDate = localCalendarDateISO()) {
  const expiresAt = String(video?.expiresAt ?? '');
  return /^\d{4}-\d{2}-\d{2}$/.test(expiresAt)
    && /^\d{4}-\d{2}-\d{2}$/.test(referenceDate)
    && expiresAt >= referenceDate;
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function safeDouyinURL(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return '#';
    if (url.hostname !== 'www.douyin.com' && url.hostname !== 'v.douyin.com') return '#';
    if (url.username || url.password) return '#';
    return url.href;
  } catch {
    return '#';
  }
}

function safeRiotURL(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return '#';
    if (url.hostname !== 'www.leagueoflegends.com') return '#';
    return url.href;
  } catch {
    return '#';
  }
}

renderFooter();
