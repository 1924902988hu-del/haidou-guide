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
const searchStatus = document.getElementById('searchStatus');
const searchInput = document.getElementById('search');
const resultMeta = document.getElementById('resultMeta');
const loadError = document.getElementById('loadError');

function normalizeSearchTerm(value) {
  return String(value || '').normalize('NFKC').trim().toLocaleLowerCase('zh-CN');
}

function renderTabs() {
  if (!tabs.childElementCount) {
    tabs.innerHTML = ROLES.map(([k, label]) =>
      `<button class="role-tab" data-role="${k}" aria-pressed="false">${label}</button>`).join('');
  }
  tabs.querySelectorAll('.role-tab').forEach(button => {
    const isActive = button.dataset.role === activeRole;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', String(isActive));
  });
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

function videoBadge(h) {
  const visibleVideos = (h.videoAvailability || [])
    .filter(video => videoIsWithinPublicationWindow(video));
  if (!visibleVideos.length) return '';
  const matchesSnapshot = visibleVideos.some(video => video.patchStatus === 'current');
  return `<span class="video-count${matchesSnapshot ? ' current' : ''}" title="${matchesSnapshot ? '有视频明确提及站点客户端资料快照版本' : '有视频思路，需核对版本'}">▶ ${visibleVideos.length}</span>`;
}

function render() {
  const needsMoreLatinInput = /^[a-z]$/u.test(query);
  const scored = needsMoreLatinInput ? [] : heroes.map(h => [matchScore(h), h]);
  const bestScore = Math.max(...scored.map(([score]) => score));
  const minimumScore = query && bestScore === 3 ? 3 : (query ? 1 : 0);
  const list = scored
    .filter(([score]) => score >= minimumScore)
    .sort((a, b) => b[0] - a[0])
    .map(([, h]) => h);
  grid.innerHTML = list.map(h => {
    const name = escapeHTML(h.name);
    const icon = escapeHTML(h.icon);
    const tier = escapeHTML(h.tier ?? '?');
    return `
    <a class="hero-card" href="hero.html?c=${encodeURIComponent(h.alias)}" aria-label="${name}，T${tier}，胜率 ${pct(h.winRate)}">
      <img src="${icon}" alt="" loading="lazy" width="44" height="44">
      <div class="info">
        <div class="n">${name}</div>
        <div class="sub">
          <span class="tier-badge ${tierClass(h.tier)}">T${tier}</span>
          <span>胜率 ${pct(h.winRate)}</span>
          ${videoBadge(h)}
        </div>
      </div>
    </a>`;
  }).join('');
  empty.textContent = needsMoreLatinInput
    ? '英文请至少输入 2 个字母；中文昵称可以只输入 1 个字。'
    : '没有匹配的英雄，换个昵称试试？';
  empty.hidden = list.length > 0;
  if (resultMeta) resultMeta.textContent = `${list.length} / ${heroes.length} 位英雄`;
  if (searchStatus) {
    searchStatus.textContent = needsMoreLatinInput
      ? '英文搜索至少输入两个字母。当前未显示英雄；中文昵称可以输入一个字。'
      : `已显示 ${list.length} 位英雄，共 ${heroes.length} 位。`;
  }
}

tabs.addEventListener('click', e => {
  const btn = e.target.closest('.role-tab');
  if (!btn) return;
  activeRole = btn.dataset.role;
  renderTabs();
  render();
});

searchInput.addEventListener('input', e => {
  query = normalizeSearchTerm(e.target.value);
  render();
});

function showLoadError() {
  const title = document.createElement('strong');
  title.textContent = '英雄数据加载失败';
  const help = document.createElement('span');
  help.textContent = '请检查网络后重新加载。';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'retry-btn';
  retry.textContent = '重新加载';
  retry.addEventListener('click', () => window.location.reload());
  loadError.replaceChildren(title, help, retry);
}

(async () => {
  const data = await loadJSON('data/index.json');
  heroes = data.heroes;
  heroes.forEach(h => {
    h.terms = [...new Set(
      h.search.split(',')
        .concat([h.name, h.epithet])
        .map(normalizeSearchTerm)
        .filter(Boolean)
    )];
  });
  setPatchBadge(data.patch);
  renderTabs();
  render();
})().catch(() => {
  grid.replaceChildren();
  empty.hidden = true;
  tabs.hidden = true;
  searchInput.disabled = true;
  searchInput.closest('.searchbox')?.classList.add('is-disabled');
  if (resultMeta) resultMeta.textContent = '加载失败';
  if (searchStatus) searchStatus.textContent = '';
  const patchBadge = document.getElementById('patchBadge');
  if (patchBadge) {
    patchBadge.textContent = '资料暂不可用';
    patchBadge.title = '英雄资料加载失败';
  }
  showLoadError();
});
