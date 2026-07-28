/* 公共:补丁徽章 + 页脚(合规声明) */
function setPatchBadge(patch) {
  const el = document.getElementById('patchBadge');
  if (el && patch) {
    const versions = patch.statsGame && patch.statsGame !== patch.game
      ? `静态 <b>${patch.game}</b> · 统计 <b>${patch.statsGame}</b>`
      : `数据版本 <b>${patch.game}</b>`;
    el.innerHTML = `${versions}<span class="patch-date"> · ${patch.hexdataDate || patch.builtAt || ''}</span>`;
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

async function loadJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`加载失败 ${url}: HTTP ${r.status}`);
  return r.json();
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
    return url.href;
  } catch {
    return '#';
  }
}

renderFooter();
