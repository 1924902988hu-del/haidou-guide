/* 公共:补丁徽章 + 页脚(合规声明) */
function setPatchBadge(patch) {
  const el = document.getElementById('patchBadge');
  if (el && patch) {
    el.innerHTML = `数据版本 <b>${patch.game}</b> · ${patch.hexdataDate || patch.builtAt || ''}`;
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

renderFooter();
