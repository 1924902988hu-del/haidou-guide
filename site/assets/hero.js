/* 英雄详情页:天赋 / 技能加点 / 召唤师技能 / 海克斯 / 出装 / 抖音跳转 */
const alias = new URLSearchParams(location.search).get('c');
const content = document.getElementById('content');

function runeBlock(runes) {
  if (!runes.pages.length) return '';
  const pages = runes.pages.map((p, i) => `
    <div class="rune-page">
      <div class="rune-styles">
        <img src="${p.primaryStyle?.icon}" alt="${p.primaryStyle?.name ?? ''}"><b>${p.primaryStyle?.name ?? ''}</b>
        <span style="color:var(--muted)">+</span>
        <img src="${p.subStyle?.icon}" alt="${p.subStyle?.name ?? ''}">${p.subStyle?.name ?? ''}
        <span class="pr">${i === 0 ? '主流' : '备选'} · 采用率 ${pct(p.pickRate ?? null, 0)}</span>
      </div>
      <div class="rune-row">
        ${p.primary.map((r, j) => `<span class="rune${j === 0 ? ' keystone' : ''}"><img src="${r.icon}" alt="">${r.name}</span>`).join('')}
      </div>
      <div class="rune-row">
        ${p.sub.map(r => `<span class="rune"><img src="${r.icon}" alt="">${r.name}</span>`).join('')}
      </div>
      <div class="shards">${p.shards.map(s => `<span class="chip">${s}</span>`).join('')}</div>
    </div>`).join('');
  return `<section class="block"><h2>天赋符文 <small>${runes.source}</small></h2>${pages}</section>`;
}

function skillBlock(skills) {
  if (!skills.priority.length) return '';
  const pri = skills.priority.map(l => `<span class="skill-letter sl-${l}">${l}</span>`).join('<span class="gt">›</span>');
  const seq = skills.sequence.length ? `
    <div class="skill-seq">
      ${skills.sequence.map((l, i) => `<div class="cell sl-${l}"><small>${i + 1}</small>${l}</div>`).join('')}
    </div>` : '';
  return `<section class="block"><h2>技能加点 <small>主升顺序 + 1~15 级序列</small></h2>
    <div class="skill-priority">${pri}</div>${seq}</section>`;
}

function spellBlock(spells) {
  if (!spells.length) return '';
  return `<section class="block"><h2>召唤师技能</h2>
    ${spells.map((pair, i) => `<div class="spell-pair">
      ${pair.map(s => `<img src="${s.icon}" alt="${s.name}" title="${s.name}">`).join('')}
      <span>${pair.map(s => s.name).join(' + ')}</span>
      <span style="margin-left:auto;color:var(--muted);font-size:11px">${i === 0 ? '主流' : '备选'}</span>
    </div>`).join('')}</section>`;
}

function augBlock(augments, combos) {
  if (!augments.length) return '';
  const rows = augments.map(a => `
    <div class="aug">
      ${a.icon ? `<img src="${a.icon}" alt="${a.name}" loading="lazy">` : ''}
      <div class="body">
        <div class="t">${a.name}
          ${a.rarity ? `<span class="rarity rarity-${a.rarity}">${a.rarity}</span>` : ''}
          ${a.hexLabel ? `<span class="hexlabel">${a.hexLabel}</span>` : ''}
          ${a.opgg ? `<span class="opgg-mark">▲ op.gg 推荐</span>` : ''}
        </div>
        ${a.desc ? `<div class="desc">${a.desc}${a.needsGameCheck ? '<span class="game-check">数值以游戏内为准</span>' : ''}</div>` : ''}
      </div>
    </div>`).join('');
  const comboRows = combos.length ? `
    <h2 style="margin-top:14px">三强化组合 <small>实战搭配参考</small></h2>
    ${combos.map(c => `<div class="combo">
      ${c.augments.map(a => `<img src="${a.icon}" alt="${a.name}" title="${a.name}">`).join('')}
      <span class="names">${c.augments.map(a => a.name).join(' + ')}</span>
      <span class="meta">T${c.tier ?? '?'} · ${c.games ?? '?'} 场</span>
    </div>`).join('')}` : '';
  return `<section class="block"><h2>海克斯强化推荐 <small>按推荐度排序,不展示胜率</small></h2>
    <div class="aug-list">${rows}</div>${comboRows}</section>`;
}

function videoBlock(videos, searchUrl, heroName) {
  const patchLabels = {
    current: '当前版本',
    'needs-game-check': '需核对版本',
    obsolete: '已过时',
  };
  const nameList = (rows, formatter) => (rows || []).map(formatter).filter(Boolean);
  const strategyBlock = strategy => {
    if (!strategy) return '';
    const groups = [
      ['强化', nameList(strategy.augments, row => escapeHTML(typeof row === 'string' ? row : row?.name))],
      ['出装', nameList(strategy.items, row => escapeHTML(typeof row === 'string' ? row : row?.name))],
      ['符文', nameList(strategy.runes, escapeHTML)],
      ['加点', nameList(strategy.skillOrder, escapeHTML)],
      ['召唤师技能', nameList(strategy.summonerSpells, escapeHTML)],
    ].filter(([, values]) => values.length);
    const playstyle = nameList(strategy.playstyle, escapeHTML);
    if (!groups.length && !playstyle.length) return '';
    return `<div class="video-strategy">
      ${groups.map(([label, values]) => `<div class="strategy-row"><b>${label}</b><span>${values.join(' · ')}</span></div>`).join('')}
      ${playstyle.length ? `<div class="strategy-row"><b>打法</b><span>${playstyle.join('；')}</span></div>` : ''}
    </div>`;
  };
  const evidenceBlock = evidence => {
    if (!(evidence || []).length) return '';
    const kindLabels = { frame: '画面', subtitle: '字幕', audio: '语音' };
    return `<details class="video-evidence">
      <summary>查看 ${evidence.length} 条时间戳证据</summary>
      <ol>${evidence.map(row => `<li>
        <time>${escapeHTML(row.timestamp)}</time>
        <span class="evidence-kind">${kindLabels[row.kind] || escapeHTML(row.kind)}</span>
        ${escapeHTML(row.claim)}
      </li>`).join('')}</ol>
    </details>`;
  };
  const cards = (videos || []).map(v => {
    const legacyPoints = (v.keyPoints || []).length
      ? `<ul>${v.keyPoints.map(point => `<li>${escapeHTML(point)}</li>`).join('')}</ul>`
      : '';
    const confidence = Number.isFinite(v.confidence)
      ? `<span>可信度 ${Math.round(v.confidence * 100)}%</span>`
      : '';
    return `
    <article class="video-card">
      <div class="video-meta">
        <span class="review-badge">✓ ${escapeHTML(v.analysisLabel)}</span>
        <span class="patch-status patch-${escapeHTML(v.patchStatus)}">${patchLabels[v.patchStatus] || '版本未知'}</span>
        <span>${escapeHTML(v.publishedAt)}</span>
        <span>${escapeHTML(v.creator)}</span>
        ${confidence}
      </div>
      <h3>${escapeHTML(v.title)}</h3>
      <p>${escapeHTML(v.summary)}</p>
      ${strategyBlock(v.strategy)}
      ${legacyPoints}
      ${evidenceBlock(v.evidence)}
      <div class="video-caveat">${escapeHTML(v.caveat)}</div>
      <a class="source-link" href="${safeDouyinURL(v.url)}" target="_blank" rel="noopener">在抖音查看原视频 ↗</a>
    </article>`;
  }).join('');
  const label = cards ? '继续找当前版本视频' : `去抖音搜「${heroName} 海克斯大乱斗」`;
  return `<section class="block video-block">
    <h2>博主实战 <small>AI 必须读过画面、字幕与语音；结论按视频逐条归属</small></h2>
    ${cards || '<p class="video-empty">暂时没有完成画面核对的视频，不会用搜索摘要冒充攻略。</p>'}
    <a class="douyin-btn" href="${searchUrl}" target="_blank" rel="noopener">${label}<span>↗</span></a>
  </section>`;
}

function itemBlock(items) {
  const itemIcons = list => list.map(i => `<div class="item"><img src="${i.icon}" alt="${i.name}" loading="lazy"><span>${i.name}</span></div>`).join('');
  const cores = items.opggCores.length ? `
    <div class="item-sub">op.gg 核心三件套(按采用排序)</div>
    ${items.opggCores.map(row => `<div class="core-row">
      ${row.map((i, j) => `${j > 0 ? '<span class="arrow">›</span>' : ''}<img src="${i.icon}" alt="${i.name}" title="${i.name}">`).join('')}
    </div>`).join('')}` : '';
  const hexTop = items.hexTop.length ? `
    <div class="item-sub">单件强度榜(hexdata 推荐度)</div>
    <div class="item-row">${items.hexTop.map(r => `
      <div class="item"><img src="${r.item.icon}" alt="${r.item.name}" loading="lazy"><span>${r.item.name}<br><b style="color:var(--blue)">${r.hexLabel ?? ''}</b></span></div>`).join('')}
    </div>` : '';
  return `<section class="block"><h2>出装</h2>
    ${items.starter.length ? `<div class="item-sub">起始装</div><div class="item-row">${itemIcons(items.starter)}</div>` : ''}
    ${items.core.length ? `<div class="item-sub">核心成装(公式装)</div><div class="item-row">${itemIcons(items.core)}</div>` : ''}
    ${items.boots.length ? `<div class="item-sub">鞋子</div><div class="item-row">${itemIcons(items.boots)}</div>` : ''}
    ${cores}${hexTop}</section>`;
}

(async () => {
  if (!alias) throw new Error('缺少英雄参数');
  const h = await loadJSON(`data/heroes/${alias}.json`);
  document.title = `${h.name} 海克斯大乱斗攻略 · 海斗速查`;
  setPatchBadge(h.patch);

  content.innerHTML = [
    `<div class="hero-summary">
      <div class="hero-head">
        <img class="avatar" src="${h.icon}" alt="${h.name}">
        <div>
          <p class="eyebrow">${h.patch.game} HERO GUIDE</p>
          <h1>${h.name} <span class="ep">${h.epithet}</span></h1>
          <div class="chips">
            ${h.roles.map(r => `<span class="chip">${({fighter:'战士',mage:'法师',assassin:'刺客',marksman:'射手',tank:'坦克',support:'辅助'})[r] ?? r}</span>`).join('')}
            <span class="chip tier-chip">T${h.stats.tier ?? '?'}</span>
          </div>
        </div>
      </div>
      <div class="stat-row" aria-label="英雄统计">
        <div class="stat"><b>${pct(h.stats.winRate)}</b>胜率</div>
        <div class="stat"><b>${pct(h.stats.pickRate)}</b>登场率</div>
        <div class="stat"><b>${(h.stats.games ?? 0).toLocaleString()}</b>样本场次</div>
        <div class="stat"><b>${h.stats.kda?.toFixed(2) ?? '—'}</b>KDA</div>
      </div>
    </div>`,
    videoBlock(h.videos, h.douyinUrl, h.name),
    `<div class="guide-layout">
      <div class="guide-primary">
        ${augBlock(h.augments, h.combos)}
        ${itemBlock(h.items)}
      </div>
      <div class="guide-secondary">
        ${runeBlock(h.runes)}
        ${skillBlock(h.skills)}
        ${spellBlock(h.spells)}
      </div>
    </div>`,
  ].join('');
})().catch(err => {
  content.innerHTML = `<div class="empty-tip">${err.message}</div>`;
});
