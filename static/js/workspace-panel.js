/** 工作区配额与文件管理面板 */

import { workspaceApi, sessionsApi } from './api.js';
import { state } from './config.js';
import { showToast } from './message-renderer.js';

let panelBound = false;
let sessionsLoaded = false;

const FILE_KIND_LABEL = {
  workspace: 'workspace',
  skill: 'skill（用户级）',
  sandbox: '沙箱系统',
  artifact: 'artifact',
  upload: 'upload',
};

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(2)} MB`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function downloadUrl(path) {
  return workspaceApi.downloadUrl(path, state.userId);
}

function isFileDeletable(file) {
  if (file.deletable === false) return false;
  const kind = file.file_kind || '';
  if (kind === 'skill' || kind === 'sandbox') return false;
  const path = file.path || '';
  if (path.startsWith('skills/') || path.includes('/.sandbox/skills/')) return false;
  return true;
}

function getSelectedSessionId() {
  return document.getElementById('workspace-session-select')?.value?.trim() || '';
}

async function loadSessionOptions() {
  const select = document.getElementById('workspace-session-select');
  if (!select) return;
  const prev = getSelectedSessionId() || state.sessionId || '';
  select.innerHTML = '<option value="">全部会话 workspace</option><option value="" disabled>加载中…</option>';
  try {
    const { body } = await sessionsApi.list(state.userId);
    const items = body?.data || [];
    const options = ['<option value="">全部会话 workspace</option>'];
    items.forEach((s) => {
      const title = s.title || '未命名';
      const label = `${title} (${String(s.id).slice(0, 8)}…)`;
      options.push(
        `<option value="${escapeHtml(s.id)}">${escapeHtml(label)}</option>`,
      );
    });
    select.innerHTML = options.join('');
    sessionsLoaded = true;
    if (prev && items.some((s) => s.id === prev)) {
      select.value = prev;
    } else if (state.sessionId && items.some((s) => s.id === state.sessionId)) {
      select.value = state.sessionId;
    }
  } catch (e) {
    select.innerHTML = '<option value="">全部会话 workspace</option>';
    showToast(e.message || '会话列表加载失败', 'error');
  }
}

function syncSessionSelectFromState() {
  const select = document.getElementById('workspace-session-select');
  if (!select || !state.sessionId) return;
  if ([...select.options].some((o) => o.value === state.sessionId)) {
    select.value = state.sessionId;
  }
}

async function loadQuota() {
  const bar = document.getElementById('workspace-quota-bar');
  const detail = document.getElementById('workspace-quota-detail');
  if (bar) bar.textContent = '加载配额…';
  try {
    const { ok, body } = await workspaceApi.quota(state.userId);
    if (!ok) {
      throw new Error(body.detail || body.msg || '配额加载失败');
    }
    const used = body.used_bytes ?? 0;
    const limit = body.limit_bytes ?? 0;
    const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    if (bar) {
      bar.innerHTML = `
        <div class="quota-progress"><div class="quota-fill" style="width:${pct}%"></div></div>
        <span>${formatBytes(used)} / ${formatBytes(limit)} (${pct}%) · 用户总配额</span>`;
    }
    const bd = body.breakdown || {};
    const sessionRows = (body.sessions || [])
      .slice(0, 5)
      .map((s) => `${String(s.session_id).slice(0, 8)}… ${formatBytes(s.bytes)}`)
      .join(' · ');
    if (detail) {
      detail.textContent = [
        `分类: workspace ${formatBytes(bd.workspace)} · skills ${formatBytes(bd.skills)} · uploads ${formatBytes(bd.uploads)} · artifacts ${formatBytes(bd.artifacts)}`,
        sessionRows ? `各会话 workspace（Top5）: ${sessionRows}` : '',
        getSelectedSessionId()
          ? '当前列表：所选会话 workspace（不含 skill 与 skill 镜像）'
          : '当前列表：workspace / uploads / artifacts；用户级 skill 请选 scope=skills',
      ]
        .filter(Boolean)
        .join(' | ');
    }
  } catch (e) {
    const msg = e.message || String(e);
    if (bar) bar.textContent = msg;
    showToast(msg, 'error');
  }
}

async function loadFiles() {
  const tbody = document.getElementById('workspace-files-body');
  if (tbody) tbody.innerHTML = '<tr><td colspan="5">加载中…</td></tr>';
  const scope = document.getElementById('workspace-scope')?.value || 'all';
  const sessionId = getSelectedSessionId();
  try {
    const { ok, body } = await workspaceApi.list(state.userId, {
      scope,
      session_id: sessionId || undefined,
    });
    if (!ok) {
      throw new Error(body.detail || body.msg || '文件列表加载失败');
    }
    const files = body.files || [];
    if (!files.length) {
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="5">${
          sessionId ? '该会话暂无 workspace 文件' : '暂无文件'
        }</td></tr>`;
      }
      return;
    }
    if (tbody) {
      tbody.innerHTML = files
        .map((f) => {
          const path = f.path || '';
          const kind = FILE_KIND_LABEL[f.file_kind] || f.file_kind || '';
          const deletable = isFileDeletable(f);
          const delBtn = deletable
            ? `<button type="button" class="btn sm workspace-del" data-path="${escapeHtml(path)}">删除</button>`
            : '<span class="text-muted" title="请前往 Skill 管理卸载">—</span>';
          return `<tr>
            <td><code>${escapeHtml(path)}</code></td>
            <td>${escapeHtml(kind)}</td>
            <td>${formatBytes(f.size_bytes)}</td>
            <td>${escapeHtml((f.updated_at || '').slice(0, 19))}</td>
            <td>
              <a href="${escapeHtml(downloadUrl(path))}" target="_blank" rel="noopener">下载</a>
              ${delBtn}
            </td>
          </tr>`;
        })
        .join('');
      tbody.querySelectorAll('.workspace-del').forEach((btn) => {
        btn.addEventListener('click', () => deleteFile(btn.dataset.path));
      });
    }
  } catch (e) {
    const msg = e.message || String(e);
    if (tbody) tbody.innerHTML = `<tr><td colspan="5">${escapeHtml(msg)}</td></tr>`;
    showToast(msg, 'error');
  }
}

async function deleteFile(path) {
  if (!path || !window.confirm(`删除 ${path}？`)) return;
  try {
    const { ok, body } = await workspaceApi.deleteFile(state.userId, path);
    if (!ok) {
      throw new Error(body.detail || body.msg || '删除失败');
    }
    showToast('已删除', 'info');
    await loadFiles();
    await loadQuota();
  } catch (e) {
    showToast(e.message || String(e), 'error');
  }
}

async function reconcile() {
  const btn = document.getElementById('btn-workspace-reconcile');
  if (btn) btn.disabled = true;
  try {
    const { ok, body } = await workspaceApi.reconcile(state.userId);
    if (!ok) {
      throw new Error(body.detail || body.msg || '对账失败');
    }
    showToast(`对账完成，已用 ${formatBytes(body.used_bytes)}`, 'info');
    await loadQuota();
    await loadFiles();
  } catch (e) {
    showToast(e.message || String(e), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function refreshAll() {
  if (!sessionsLoaded) {
    await loadSessionOptions();
  }
  syncSessionSelectFromState();
  await loadQuota();
  await loadFiles();
}

export function initWorkspacePanel() {
  if (panelBound) return;
  panelBound = true;

  document.getElementById('btn-workspace-refresh')?.addEventListener('click', () => {
    sessionsLoaded = false;
    refreshAll();
  });
  document.getElementById('btn-workspace-reconcile')?.addEventListener('click', () => {
    reconcile();
  });
  document.getElementById('workspace-scope')?.addEventListener('change', () => {
    loadFiles();
  });
  document.getElementById('workspace-session-select')?.addEventListener('change', () => {
    loadQuota();
    loadFiles();
  });

  document.addEventListener('session-changed', () => {
    syncSessionSelectFromState();
    loadFiles();
  });
}

export async function refreshWorkspacePanel() {
  sessionsLoaded = false;
  await refreshAll();
}
