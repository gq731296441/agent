/** 已安装 Skill 勾选面板（capabilities.skills） */

import { skillsApi } from './api.js';
import { state, saveConfig, isSkillSelectionLocked } from './config.js';
import { showToast } from './message-renderer.js';

let allSkills = [];

export async function loadInstalledSkills() {
  const tbody = document.getElementById('skill-table-body');
  if (tbody) tbody.innerHTML = '<tr><td colspan="5">加载中…</td></tr>';

  try {
    const { body } = await skillsApi.installed(state.userId);
    if (!body.status && body.code !== 0) {
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(body.msg || '加载失败')}</td></tr>`;
      }
      return;
    }
    allSkills = body.data || [];
    pruneStaleSkillSelection();
    renderSkillTable();
    autoSelectAllSkillsIfEmpty();
    syncSkillCheckboxes();
  } catch (e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderSkillTable() {
  const tbody = document.getElementById('skill-table-body');
  if (!tbody) return;
  if (!allSkills.length) {
    tbody.innerHTML = '<tr><td colspan="5">暂无已安装 Skill</td></tr>';
    return;
  }
  tbody.innerHTML = allSkills
    .map((s) => {
      const id = s.market_skill_id || s.id || '';
      return `
    <tr data-id="${escapeHtml(id)}">
      <td><input type="checkbox" class="skill-select" data-id="${escapeHtml(id)}" /></td>
      <td><code>${escapeHtml(id)}</code></td>
      <td>${escapeHtml(s.skill_name || id)}</td>
      <td>${escapeHtml(s.version || '—')}</td>
      <td class="desc">${escapeHtml((s.source || 'db').slice(0, 12))}</td>
    </tr>`;
    })
    .join('');

  tbody.querySelectorAll('.skill-select').forEach((cb) => {
    cb.addEventListener('change', onSkillSelectChange);
  });
}

function updateSkillSelectionLockUI() {
  const locked = isSkillSelectionLocked();
  document.querySelectorAll('.skill-select').forEach((cb) => {
    cb.disabled = locked;
  });
  const hint = document.getElementById('skill-lock-hint');
  if (hint) {
    hint.classList.toggle('hidden', !locked);
    if (locked) {
      hint.textContent =
        'Agent 运行中或沙箱活跃时无法变更 Skill 勾选，请先停止对话或等待沙箱释放。';
    }
  }
}

function onSkillSelectChange() {
  if (isSkillSelectionLocked()) {
    showToast('当前无法变更 Skill 勾选（Agent 或沙箱运行中）', 'warn');
    syncSkillCheckboxes();
    return;
  }
  const selected = [];
  document.querySelectorAll('.skill-select:checked').forEach((cb) => {
    const id = cb.dataset.id;
    const skill = allSkills.find(
      (s) => String(s.market_skill_id || s.id) === id,
    );
    if (skill) {
      selected.push({
        market_skill_id: skill.market_skill_id || skill.id,
        skill_name: skill.skill_name,
        install_dir: skill.install_dir,
      });
    }
  });
  state.selectedSkills = selected;
  saveConfig();
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  document.dispatchEvent(new CustomEvent('skill-selection-changed'));
}

function syncSkillCheckboxes() {
  const ids = new Set(
    state.selectedSkills.map((s) => String(s.market_skill_id || s.id)),
  );
  document.querySelectorAll('.skill-select').forEach((cb) => {
    cb.checked = ids.has(cb.dataset.id);
  });
}

function pruneStaleSkillSelection() {
  if (!state.selectedSkills.length) return;
  const installedIds = new Set(
    allSkills.map((s) => String(s.market_skill_id || s.id)),
  );
  const before = state.selectedSkills.length;
  state.selectedSkills = state.selectedSkills.filter((s) =>
    installedIds.has(String(s.market_skill_id || s.id)),
  );
  if (state.selectedSkills.length !== before) {
    saveConfig();
    import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  }
}

function autoSelectAllSkillsIfEmpty() {
  if (state.selectedSkills.length > 0 || !allSkills.length) return;
  state.selectedSkills = allSkills.map((s) => ({
    market_skill_id: s.market_skill_id || s.id,
    skill_name: s.skill_name,
    install_dir: s.install_dir,
  }));
  saveConfig();
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  showToast(`已默认勾选 ${state.selectedSkills.length} 个 Skill`, 'info');
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function initSkillPanel() {
  document.getElementById('btn-load-skills')?.addEventListener('click', loadInstalledSkills);
  document.addEventListener('session-run-changed', updateSkillSelectionLockUI);
  updateSkillSelectionLockUI();
  document.getElementById('btn-select-all-skills')?.addEventListener('click', () => {
    document.querySelectorAll('.skill-select').forEach((cb) => {
      cb.checked = true;
    });
    onSkillSelectChange();
  });
  document.getElementById('btn-clear-skills')?.addEventListener('click', () => {
    document.querySelectorAll('.skill-select').forEach((cb) => {
      cb.checked = false;
    });
    onSkillSelectChange();
  });
}
