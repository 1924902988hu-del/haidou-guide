/* 英雄详情页:天赋 / 技能加点 / 召唤师技能 / 海克斯 / 出装 / 抖音跳转 */
const alias = new URLSearchParams(location.search).get('c');
const content = document.getElementById('content');
const loadError = document.getElementById('loadError');
const hasValidAlias = Boolean(alias)
  && /^[A-Za-z][A-Za-z0-9]{0,31}$/.test(alias);

function showLoadError(canRetry) {
  const title = document.createElement('strong');
  title.textContent = canRetry
    ? '英雄攻略加载失败'
    : '未找到要查看的英雄';
  const help = document.createElement('span');
  help.textContent = canRetry
    ? '请检查网络后重新加载，或返回英雄列表。'
    : '请返回英雄列表重新选择。';
  const children = [title, help];

  if (canRetry) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'retry-btn';
    retry.textContent = '重新加载';
    retry.addEventListener('click', () => window.location.reload());
    children.push(retry);
  }

  loadError.replaceChildren(...children);
}

function showUnavailableState(canRetry) {
  content.replaceChildren();
  const patchBadge = document.getElementById('patchBadge');
  if (patchBadge) {
    patchBadge.textContent = '资料暂不可用';
    patchBadge.title = '英雄攻略加载失败';
  }
  showLoadError(canRetry);
}

function runeBlock(runes) {
  if (!runes.pages.length) return '';
  const pages = runes.pages.map((p, i) => `
    <div class="rune-page">
      <div class="rune-styles">
        <img src="${escapeHTML(p.primaryStyle?.icon)}" alt=""><b>${escapeHTML(p.primaryStyle?.name ?? '')}</b>
        <span style="color:var(--muted)">+</span>
        <img src="${escapeHTML(p.subStyle?.icon)}" alt="">${escapeHTML(p.subStyle?.name ?? '')}
        <span class="pr">${i === 0 ? '主流' : '备选'} · 采用率 ${pct(p.pickRate ?? null, 0)}</span>
      </div>
      <div class="rune-row">
        ${p.primary.map((r, j) => `<span class="rune${j === 0 ? ' keystone' : ''}"><img src="${escapeHTML(r.icon)}" alt="">${escapeHTML(r.name)}</span>`).join('')}
      </div>
      <div class="rune-row">
        ${p.sub.map(r => `<span class="rune"><img src="${escapeHTML(r.icon)}" alt="">${escapeHTML(r.name)}</span>`).join('')}
      </div>
      <div class="shards">${p.shards.map(s => `<span class="chip">${escapeHTML(s)}</span>`).join('')}</div>
    </div>`).join('');
  return `<section class="block"><h2>天赋符文 <small>${escapeHTML(runes.source)}</small></h2>${pages}</section>`;
}

function skillBlock(skills) {
  if (!skills.priority.length) return '';
  const pri = skills.priority.map(l => {
    const letter = escapeHTML(l);
    return `<span class="skill-letter sl-${letter}">${letter}</span>`;
  }).join('<span class="gt">›</span>');
  const seq = skills.sequence.length ? `
    <div class="skill-seq">
      ${skills.sequence.map((l, i) => {
        const letter = escapeHTML(l);
        return `<div class="cell sl-${letter}"><small>${i + 1}</small>${letter}</div>`;
      }).join('')}
    </div>` : '';
  return `<section class="block"><h2>技能加点 <small>主升顺序 + 1~15 级序列</small></h2>
    <div class="skill-priority">${pri}</div>${seq}</section>`;
}

function spellBlock(spells) {
  if (!spells.length) return '';
  return `<section class="block"><h2>召唤师技能</h2>
    ${spells.map((pair, i) => `<div class="spell-pair">
      ${pair.map(s => `<img src="${escapeHTML(s.icon)}" alt="">`).join('')}
      <span>${pair.map(s => escapeHTML(s.name)).join(' + ')}</span>
      <span style="margin-left:auto;color:var(--muted);font-size:11px">${i === 0 ? '主流' : '备选'}</span>
    </div>`).join('')}</section>`;
}

function augBlock(augments, combos) {
  if (!augments.length) return '';
  const rows = augments.map(a => {
    const icon = escapeHTML(a.icon);
    const name = escapeHTML(a.name);
    const rarity = escapeHTML(a.rarity);
    const hexLabel = escapeHTML(a.hexLabel);
    const desc = escapeHTML(a.desc);
    return `<div class="aug">
      ${a.icon ? `<img src="${icon}" alt="" loading="lazy">` : ''}
      <div class="body">
        <div class="t">${name}
          ${a.rarity ? `<span class="rarity rarity-${rarity}">${rarity}</span>` : ''}
          ${a.hexLabel ? `<span class="hexlabel">${hexLabel}</span>` : ''}
          ${a.opgg ? `<span class="opgg-mark">▲ op.gg 推荐</span>` : ''}
        </div>
        ${a.desc ? `<div class="desc">${desc}${a.needsGameCheck ? '<span class="game-check">数值以游戏内为准</span>' : ''}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  const comboRows = combos.length ? `
    <h2 style="margin-top:14px">三强化组合 <small>实战搭配参考</small></h2>
    ${combos.map(c => `<div class="combo">
      ${c.augments.map(a => `<img src="${escapeHTML(a.icon)}" alt="">`).join('')}
      <span class="names">${c.augments.map(a => escapeHTML(a.name)).join(' + ')}</span>
      <span class="meta">样本 ${escapeHTML(c.games ?? '?')} 场</span>
    </div>`).join('')}` : '';
  return `<section class="block"><h2>海克斯强化推荐 <small>按推荐度排序,不展示胜率</small></h2>
    <div class="aug-list">${rows}</div>${comboRows}</section>`;
}

function videoBlock(videos, searchUrl, heroName) {
  const patchLabel = video => ({
    current: video.patchMentioned
      ? `视频提及 ${video.patchMentioned}`
      : '命中客户端资料快照',
    'needs-game-check': '需核对版本',
    obsolete: '已过时',
  })[video.patchStatus] || '版本未知';
  const nameList = (rows, formatter) => (rows || []).map(formatter).filter(Boolean);
  const strategyName = row => escapeHTML(typeof row === 'string' ? row : row?.name);
  const strategyBlock = (strategy, showLabel = false) => {
    if (!strategy) return '';
    const groups = [
      ['强化', nameList(strategy.augments, strategyName)],
      ['出装', nameList(strategy.items, strategyName)],
      ['符文', nameList(strategy.runes, strategyName)],
      ['加点', nameList(strategy.skillOrder, escapeHTML)],
      ['召唤师技能', nameList(strategy.summonerSpells, escapeHTML)],
    ].filter(([, values]) => values.length);
    const playstyle = nameList(strategy.playstyle, escapeHTML);
    if (!groups.length && !playstyle.length) return '';
    return `<div class="video-strategy">
      ${showLabel ? `<h4>${escapeHTML(strategy.label || strategy.id || '方案')}</h4>` : ''}
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
    const strategies = Array.isArray(v.strategies) && v.strategies.length
      ? v.strategies
      : (v.strategy ? [v.strategy] : []);
    const legacyPoints = (v.keyPoints || []).length
      ? `<ul>${v.keyPoints.map(point => `<li>${escapeHTML(point)}</li>`).join('')}</ul>`
      : '';
    const patchImpactLink = v.patchImpact
      ? `<a class="patch-impact-link" href="${safeRiotURL(v.patchImpact.source)}" target="_blank" rel="noopener">查看 ${escapeHTML(v.patchImpact.patch)} 官方改动 ↗</a>`
      : '';
    return `
    <article class="video-card">
      <div class="video-meta">
        <span class="review-badge">✓ ${escapeHTML(v.analysisLabel)}</span>
        <span class="patch-status patch-${escapeHTML(v.patchStatus)}">${escapeHTML(patchLabel(v))}</span>
        <span>${escapeHTML(v.publishedAt)}</span>
        <span>${escapeHTML(v.creator)}</span>
      </div>
      <h3>${escapeHTML(v.title)}</h3>
      <p>${escapeHTML(v.summary)}</p>
      <div class="video-strategies">
        ${strategies.map(strategy => strategyBlock(strategy, strategies.length > 1)).join('')}
      </div>
      ${legacyPoints}
      ${evidenceBlock(v.evidence)}
      <div class="video-caveat">${escapeHTML(v.caveat)}${patchImpactLink}</div>
      <a class="source-link" href="${safeDouyinURL(v.url)}" target="_blank" rel="noopener">在抖音查看原视频 ↗</a>
    </article>`;
  }).join('');
  const label = cards ? '继续找更新视频' : `去抖音搜「${escapeHTML(heroName)} 海克斯大乱斗」`;
  return `<section class="block video-block">
    <h2>博主实战 <small>AI 多模态提炼；徽标只声明已公开的时间戳证据类型</small></h2>
    ${cards || '<p class="video-empty">暂无近期且完成画面核对的视频，以下继续以 OP.GG 为基线。</p>'}
    <a class="douyin-btn" href="${safeDouyinURL(searchUrl)}" target="_blank" rel="noopener">${label}<span>↗</span></a>
  </section>`;
}

function itemBlock(items) {
  const itemIcons = list => list.map(i => `<div class="item"><img src="${escapeHTML(i.icon)}" alt="" loading="lazy"><span>${escapeHTML(i.name)}</span></div>`).join('');
  const cores = items.opggCores.length ? `
    <div class="item-sub">op.gg 核心三件套(按采用排序)</div>
    ${items.opggCores.map(row => `<div class="core-row">
      ${row.map((i, j) => `${j > 0 ? '<span class="arrow">›</span>' : ''}<img src="${escapeHTML(i.icon)}" alt="${escapeHTML(i.name)}" title="${escapeHTML(i.name)}">`).join('')}
    </div>`).join('')}` : '';
  const hexTop = items.hexTop.length ? `
    <div class="item-sub">单件强度榜(hexdata 推荐度)</div>
    <div class="item-row">${items.hexTop.map(r => `
      <div class="item"><img src="${escapeHTML(r.item.icon)}" alt="" loading="lazy"><span>${escapeHTML(r.item.name)}<br><b style="color:var(--blue)">${escapeHTML(r.hexLabel ?? '')}</b></span></div>`).join('')}
    </div>` : '';
  return `<section class="block"><h2>出装</h2>
    ${items.starter.length ? `<div class="item-sub">起始装</div><div class="item-row">${itemIcons(items.starter)}</div>` : ''}
    ${items.core.length ? `<div class="item-sub">核心成装(公式装)</div><div class="item-row">${itemIcons(items.core)}</div>` : ''}
    ${items.boots.length ? `<div class="item-sub">鞋子</div><div class="item-row">${itemIcons(items.boots)}</div>` : ''}
    ${cores}${hexTop}</section>`;
}

if (!hasValidAlias) {
  showUnavailableState(false);
} else {
  (async () => {
    const h = await loadJSON(`data/heroes/${encodeURIComponent(alias)}.json`);
    document.title = `${h.name} 海克斯大乱斗攻略 · 海斗速查`;
    setPatchBadge(h.patch);
    const visibleVideos = (h.videos || [])
      .filter(video => videoIsWithinPublicationWindow(video));

    content.innerHTML = [
      `<div class="hero-summary">
        <div class="hero-head">
          <img class="avatar" src="${escapeHTML(h.icon)}" alt="">
          <div>
            <p class="eyebrow">客户端资料 ${escapeHTML(h.patch.game)} · OP.GG 攻略 ${escapeHTML(h.patch.opggGame)}</p>
            <h1>${escapeHTML(h.name)} <span class="ep">${escapeHTML(h.epithet)}</span></h1>
            <div class="chips">
              ${h.roles.map(r => `<span class="chip">${escapeHTML(({fighter:'战士',mage:'法师',assassin:'刺客',marksman:'射手',tank:'坦克',support:'辅助'})[r] ?? r)}</span>`).join('')}
              <span class="chip tier-chip">T${escapeHTML(h.stats.tier ?? '?')}</span>
            </div>
          </div>
        </div>
        <div class="stats-panel">
          <p class="stats-provenance" id="stats-source">
            <span>海克斯模式统计快照</span>
            <b>hexdata ${escapeHTML(h.patch.statsGame)}</b>
            <time datetime="${escapeHTML(h.patch.hexdataDate)}">${escapeHTML(h.patch.hexdataDate)}</time>
            ${calendarAgeLabel(h.patch.hexdataDate) ? `<span class="stats-age">${calendarAgeLabel(h.patch.hexdataDate)}</span>` : ''}
          </p>
          <div class="stat-row" aria-label="英雄统计" aria-describedby="stats-source">
            <div class="stat"><b>${pct(h.stats.winRate)}</b>胜率</div>
            <div class="stat"><b>${pct(h.stats.pickRate)}</b>登场率</div>
            <div class="stat"><b>${(h.stats.games ?? 0).toLocaleString()}</b>样本场次</div>
            <div class="stat"><b>${h.stats.kda?.toFixed(2) ?? '—'}</b>KDA</div>
          </div>
        </div>
      </div>`,
      videoBlock(visibleVideos, h.douyinUrl, h.name),
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
  })().catch(() => {
    showUnavailableState(true);
  });
}
