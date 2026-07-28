/* 首页:昵称搜索 + 定位分类 + 英雄网格 */
const ROLES = [
  ['all', '全部'], ['marksman', '射手'], ['mage', '法师'], ['fighter', '战士'],
  ['assassin', '刺客'], ['tank', '坦克'], ['support', '辅助'],
];

let heroes = [];
let activeRole = 'all';
let query = '';

const grid = document.getElementById('grid');
const empty = document.getElementById('empty');
const tabs = document.getElementById('roleTabs');

function renderTabs() {
  tabs.innerHTML = ROLES.map(([k, label]) =>
    `<button class="role-tab${k === activeRole ? ' active' : ''}" data-role="${k}" aria-pressed="${k === activeRole}">${label}</button>`).join('');
}

/* 匹配打分:精确昵称 3 > 词首前缀 2 > 词中包含 1;避免"ez"误中拼音中段却把真 EZ 排后 */
function matchScore(h) {
  if (activeRole !== 'all' && !h.roles.includes(activeRole)) return -1;
  if (!query) return 0;
  let best = -1;
  for (const term of h.terms) {
    if (term === query) return 3;
    if (term.startsWith(query)) best = Math.max(best, 2);
    else if (term.includes(query)) best = Math.max(best, 1);
  }
  return best;
}

function tierClass(t) { return `tier-${t >= 1 && t <= 5 ? t : 0}`; }

function render() {
  const list = heroes
    .map(h => [matchScore(h), h])
    .filter(([s]) => s >= (query ? 1 : 0))
    .sort((a, b) => b[0] - a[0])
    .map(([, h]) => h);
  grid.innerHTML = list.map(h => `
    <a class="hero-card" href="hero.html?c=${h.alias}" aria-label="${h.name}，T${h.tier ?? '未知'}，胜率 ${pct(h.winRate)}">
      <img src="${h.icon}" alt="${h.name}" loading="lazy" width="44" height="44">
      <div class="info">
        <div class="n">${h.name}</div>
        <div class="sub">
          <span class="tier-badge ${tierClass(h.tier)}">T${h.tier ?? '?'}</span>
          <span>胜率 ${pct(h.winRate)}</span>
        </div>
      </div>
    </a>`).join('');
  empty.hidden = list.length > 0;
  const meta = document.getElementById('resultMeta');
  if (meta) meta.textContent = `${list.length} / ${heroes.length} 位英雄`;
}

tabs.addEventListener('click', e => {
  const btn = e.target.closest('.role-tab');
  if (!btn) return;
  activeRole = btn.dataset.role;
  renderTabs();
  render();
});

document.getElementById('search').addEventListener('input', e => {
  query = e.target.value.trim().toLowerCase();
  render();
});

(async () => {
  const data = await loadJSON('data/index.json');
  heroes = data.heroes;
  heroes.forEach(h => {
    h.terms = h.search.split(',').concat([h.name, h.epithet]).filter(Boolean);
  });
  setPatchBadge(data.patch);
  renderTabs();
  render();
})().catch(err => {
  grid.innerHTML = `<div class="empty-tip">${err.message}</div>`;
});
