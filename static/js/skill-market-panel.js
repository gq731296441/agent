/** Skill 市场：浏览 catalog、订阅/取消/更新 */

import { state } from './config.js';
import { skillsApi } from './api.js';
import { showToast } from './message-renderer.js';
import { loadInstalledSkills } from './skill-panel.js';

let marketTab = 'catalog';
let catalogPage = 1;
const pageSize = 20;
let catalogTotal = 0;
let catalogTags = [];
let selectedTag = '';

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatTags(tags, { clickable = false } = {}) {
  if (!tags || !tags.length) return '—';
  const cls = clickable ? 'skill-tag clickable' : 'skill-tag';
  return tags
    .map(
      (t) =>
        `<span class="${cls}" data-tag="${escapeHtml(t)}" title="按此标签筛选">${escapeHtml(t)}</span>`,
    )
    .join(' ');
}

function setMarketTab(tab) {
  marketTab = tab;
  document.querySelectorAll('.skill-market-tab').forEach((el) => {
    el.classList.toggle('active', el.dataset.marketTab === tab);
  });
  document.getElementById('skill-market-catalog-view')?.classList.toggle('hidden', tab !== 'catalog');
  document.getElementById('skill-market-subscribed-view')?.classList.toggle('hidden', tab !== 'subscribed');
  document.getElementById('skill-market-tag-filters')?.classList.toggle('hidden', tab !== 'catalog');
  if (tab === 'catalog') {
    refreshTagFilters().then(() => loadCatalog());
  } else {
    loadSubscribed();
  }
}

function extractCatalogPayload(body) {
  if (!body || (body.status === false && body.code !== 0)) return null;
  const data = body.data;
  if (Array.isArray(data)) {
    return { items: data, total: data.length, page: 1, page_size: data.length };
  }
  if (data && Array.isArray(data.items)) {
    return {
      items: data.items,
      total: data.total ?? data.items.length,
      page: data.page ?? 1,
      page_size: data.page_size ?? pageSize,
    };
  }
  return { items: [], total: 0, page: 1, page_size: pageSize };
}

function mergeCatalogTagsFromItems(items) {
  const tagSet = new Set(catalogTags);
  (items || []).forEach((s) => {
    const tags = Array.isArray(s.tags) ? s.tags : s.tags ? [s.tags] : [];
    tags.forEach((t) => {
      const v = String(t).trim();
      if (v) tagSet.add(v);
    });
  });
  catalogTags = [...tagSet].sort((a, b) => a.localeCompare(b, 'zh'));
}

function selectTag(tag) {
  selectedTag = tag || '';
  catalogPage = 1;
  renderTagFilters();
  loadCatalog();
}

async function refreshTagFilters() {
  try {
    const { body } = await skillsApi.catalog({
      user_id: state.userId,
      page: 1,
      page_size: 100,
    });
    const payload = extractCatalogPayload(body);
    if (payload?.items?.length) {
      mergeCatalogTagsFromItems(payload.items);
    }
  } catch (e) {
    console.warn('[skill-market] 加载标签失败', e);
  }
  renderTagFilters();
}

function renderTagFilters() {
  const container = document.getElementById('skill-market-tag-filters');
  if (!container) return;

  const buttons = [
    `<button type="button" class="btn sm skill-tag-filter${selectedTag ? '' : ' active'}" data-tag="">全部</button>`,
    ...catalogTags.map(
      (t) =>
        `<button type="button" class="btn sm skill-tag-filter${selectedTag === t ? ' active' : ''}" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`,
    ),
  ];

  container.innerHTML = `<span class="skill-market-tag-label">标签：</span>${buttons.join('')}`;
  container.querySelectorAll('.skill-tag-filter').forEach((btn) => {
    btn.addEventListener('click', () => selectTag(btn.dataset.tag || ''));
  });
}

async function loadCatalog() {
  const tbody = document.getElementById('skill-market-catalog-body');
  const pageInfo = document.getElementById('skill-market-page-info');
  if (tbody) tbody.innerHTML = '<tr><td colspan="8">加载中…</td></tr>';

  const q = document.getElementById('skill-market-search')?.value?.trim() || '';
  const tags = selectedTag || '';

  try {
    const { body } = await skillsApi.catalog({
      user_id: state.userId,
      page: catalogPage,
      page_size: pageSize,
      q,
      tags,
    });
    const payload = extractCatalogPayload(body);
    if (!payload) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="8">${escapeHtml(body?.msg || '加载失败')}</td></tr>`;
      renderTagFilters();
      return;
    }
    const { items } = payload;
    catalogTotal = payload.total || 0;
    if (catalogPage === 1 && !selectedTag && !q) {
      mergeCatalogTagsFromItems(items);
    }
    const totalPages = Math.max(1, Math.ceil(catalogTotal / pageSize));
    if (pageInfo) {
      const tagHint = selectedTag ? ` · 标签「${selectedTag}」` : '';
      pageInfo.textContent = `第 ${catalogPage} / ${totalPages} 页（共 ${catalogTotal} 条${tagHint}）`;
    }
    if (!items.length) {
      if (tbody) {
        tbody.innerHTML = selectedTag
          ? `<tr><td colspan="8">暂无包含标签「${escapeHtml(selectedTag)}」的 Skill</td></tr>`
          : '<tr><td colspan="8">暂无 Skill</td></tr>';
      }
      renderTagFilters();
      return;
    }
    if (tbody) {
      tbody.innerHTML = items
        .map((s) => {
          const id = s.skill_id || s.id || '';
          const subscribed = Boolean(s.subscribed);
          const updated = (s.updated_at || '').slice(0, 10) || '—';
          return `
        <tr data-id="${escapeHtml(id)}">
          <td>${escapeHtml(s.name || id)}</td>
          <td><code>${escapeHtml(id)}</code></td>
          <td>${escapeHtml(s.version || '—')}</td>
          <td class="skill-tags-cell">${formatTags(s.tags, { clickable: true })}</td>
          <td>${escapeHtml(s.author || '—')}</td>
          <td>${escapeHtml(String(s.subscribe_count ?? 0))}</td>
          <td>${escapeHtml(updated)}</td>
          <td class="skill-market-actions">
            ${
              subscribed
                ? `<span class="badge subscribed">已订阅</span>
                   <button type="button" class="btn sm skill-unsubscribe" data-id="${escapeHtml(id)}">取消</button>`
                : `<button type="button" class="btn sm primary skill-subscribe" data-id="${escapeHtml(id)}">订阅</button>`
            }
          </td>
        </tr>`;
        })
        .join('');
      bindCatalogActions(tbody);
    }
    renderTagFilters();
  } catch (e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="8">${escapeHtml(e.message)}</td></tr>`;
    renderTagFilters();
  }
}

function bindCatalogActions(tbody) {
  tbody.querySelectorAll('.skill-subscribe').forEach((btn) => {
    btn.addEventListener('click', () => onSubscribe(btn.dataset.id));
  });
  tbody.querySelectorAll('.skill-unsubscribe').forEach((btn) => {
    btn.addEventListener('click', () => onUnsubscribe(btn.dataset.id));
  });
  tbody.querySelectorAll('.skill-tag.clickable').forEach((el) => {
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      selectTag(el.dataset.tag || '');
    });
  });
}

async function loadSubscribed() {
  const tbody = document.getElementById('skill-market-subscribed-body');
  if (tbody) tbody.innerHTML = '<tr><td colspan="6">加载中…</td></tr>';

  try {
    const { body } = await skillsApi.subscribed(state.userId);
    if (!body.status && body.code !== 0) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="6">${escapeHtml(body.msg || '加载失败')}</td></tr>`;
      return;
    }
    const items = body.data || [];
    if (!items.length) {
      if (tbody) tbody.innerHTML = '<tr><td colspan="6">暂无已订阅 Skill</td></tr>';
      return;
    }
    if (tbody) {
      tbody.innerHTML = items
        .map((s) => {
          const id = s.skill_id || s.market_skill_id || '';
          const hasUpdate = Boolean(s.has_update);
          const installedAt = (s.installed_at || '').slice(0, 19).replace('T', ' ') || '—';
          return `
        <tr data-id="${escapeHtml(id)}">
          <td>${escapeHtml(s.skill_name || s.name || id)}</td>
          <td><code>${escapeHtml(id)}</code></td>
          <td>${escapeHtml(s.installed_version || s.version || '—')}</td>
          <td>${escapeHtml(s.latest_version || '—')}${hasUpdate ? ' <span class="badge update">有更新</span>' : ''}</td>
          <td>${escapeHtml(installedAt)}</td>
          <td class="skill-market-actions">
            ${
              hasUpdate
                ? `<button type="button" class="btn sm primary skill-upgrade" data-id="${escapeHtml(id)}">更新</button>`
                : ''
            }
            <button type="button" class="btn sm skill-unsubscribe" data-id="${escapeHtml(id)}">取消订阅</button>
          </td>
        </tr>`;
        })
        .join('');
      tbody.querySelectorAll('.skill-upgrade').forEach((btn) => {
        btn.addEventListener('click', () => onUpgrade(btn.dataset.id));
      });
      tbody.querySelectorAll('.skill-unsubscribe').forEach((btn) => {
        btn.addEventListener('click', () => onUnsubscribe(btn.dataset.id));
      });
    }
  } catch (e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="6">${escapeHtml(e.message)}</td></tr>`;
  }
}

async function onSubscribe(skillId) {
  if (!skillId) return;
  try {
    const { body } = await skillsApi.subscribe(skillId, state.userId);
    if (!body.status && body.code !== 0) {
      showToast(body.msg || '订阅失败', 'error');
      return;
    }
    showToast('订阅成功', 'success');
    await loadInstalledSkills();
    if (marketTab === 'catalog') loadCatalog();
    else loadSubscribed();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function onUnsubscribe(skillId) {
  if (!skillId) return;
  if (!window.confirm(`确定取消订阅 ${skillId}？`)) return;
  try {
    const { body } = await skillsApi.unsubscribe(skillId, state.userId);
    if (!body.status && body.code !== 0) {
      showToast(body.msg || '取消失败', 'error');
      return;
    }
    showToast('已取消订阅', 'info');
    await loadInstalledSkills();
    if (marketTab === 'catalog') loadCatalog();
    else loadSubscribed();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function onUpgrade(skillId) {
  if (!skillId) return;
  if (!window.confirm(`将 ${skillId} 更新到市场最新版本并覆盖本地安装？`)) return;
  try {
    const { body } = await skillsApi.upgrade(skillId, state.userId);
    if (!body.status && body.code !== 0) {
      showToast(body.msg || '更新失败', 'error');
      return;
    }
    showToast(body.msg || '更新完成', body.data?.upgraded ? 'success' : 'info');
    await loadInstalledSkills();
    loadSubscribed();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

export function initSkillMarketPanel() {
  renderTagFilters();
  document.querySelectorAll('.skill-market-tab').forEach((el) => {
    el.addEventListener('click', () => setMarketTab(el.dataset.marketTab));
  });
  document.getElementById('btn-skill-market-refresh')?.addEventListener('click', async () => {
    if (marketTab === 'catalog') {
      await refreshTagFilters();
      loadCatalog();
    } else {
      loadSubscribed();
    }
  });
  document.getElementById('btn-skill-market-prev')?.addEventListener('click', () => {
    if (catalogPage > 1) {
      catalogPage -= 1;
      loadCatalog();
    }
  });
  document.getElementById('btn-skill-market-next')?.addEventListener('click', () => {
    const totalPages = Math.max(1, Math.ceil(catalogTotal / pageSize));
    if (catalogPage < totalPages) {
      catalogPage += 1;
      loadCatalog();
    }
  });
  document.getElementById('skill-market-search')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      catalogPage = 1;
      loadCatalog();
    }
  });
}

export function refreshSkillMarketPanel() {
  if (marketTab === 'catalog') {
    refreshTagFilters().then(() => loadCatalog());
  } else {
    loadSubscribed();
  }
}
