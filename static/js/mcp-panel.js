/** MCP 工具面板 */

import { mcpApi } from './api.js';
import { state, saveConfig } from './config.js';
import { showToast } from './message-renderer.js';

let allTools = [];

function statusBadge(status) {
  const map = { available: 'ok', unavailable: 'fail', unknown: 'warn' };
  return `<span class="badge badge-${map[status] || 'warn'}">${status || 'unknown'}</span>`;
}

export async function loadMcpTools() {
  const tbody = document.getElementById('mcp-table-body');
  if (tbody) tbody.innerHTML = '<tr><td colspan="6">加载中…</td></tr>';

  try {
    const { body } = await mcpApi.tools(state.userId);
    if (!body.status && body.code !== 0) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="6">${escapeHtml(body.msg || body.detail || '失败')}</td></tr>`;
      return;
    }
    allTools = body.data || [];
    renderMcpTable();
    autoSelectDefaultMcp();
    syncMcpCheckboxes();
  } catch (e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="6">${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderMcpTable() {
  const tbody = document.getElementById('mcp-table-body');
  if (!tbody) return;
  if (!allTools.length) {
    tbody.innerHTML = '<tr><td colspan="6">无 MCP 工具</td></tr>';
    return;
  }
  tbody.innerHTML = allTools
    .map(
      (t) => `
    <tr data-id="${escapeHtml(t.id)}">
      <td><input type="checkbox" class="mcp-select" data-id="${escapeHtml(t.id)}" /></td>
      <td>${statusBadge(t.status)}</td>
      <td><code>${escapeHtml(t.name)}</code></td>
      <td class="desc">${escapeHtml((t.description || '').slice(0, 80))}</td>
      <td class="url">${escapeHtml((t.url || '').slice(0, 40))}</td>
      <td><code>${escapeHtml(String(t.id).slice(0, 12))}…</code></td>
    </tr>`,
    )
    .join('');

  tbody.querySelectorAll('.mcp-select').forEach((cb) => {
    cb.addEventListener('change', onMcpSelectChange);
  });
}

function onMcpSelectChange() {
  const selected = [];
  document.querySelectorAll('.mcp-select:checked').forEach((cb) => {
    const id = cb.dataset.id;
    const tool = allTools.find((t) => String(t.id) === id);
    if (tool) {
      selected.push({
        id: tool.id,
        name: tool.name,
        type: tool.type || 'http',
        url: tool.url || '',
        headers: tool.headers || {},
      });
    }
  });
  state.selectedMcp = selected;
  state.mcpSelectionTouched = true;
  saveConfig();
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  document.dispatchEvent(new CustomEvent('mcp-selection-changed'));
}

function syncMcpCheckboxes() {
  const ids = new Set(state.selectedMcp.map((t) => String(t.id)));
  document.querySelectorAll('.mcp-select').forEach((cb) => {
    cb.checked = ids.has(cb.dataset.id);
  });
}

/** 首次加载时默认勾选本地 mrm 单据 MCP（仅首次、且用户从未手动改过 MCP 选择） */
function autoSelectDefaultMcp() {
  if (state.selectedMcp.length > 0) return;
  if (state.mcpSelectionTouched) return;
  const mrm = allTools.find(
    (t) => t.name === 'mrm' || t.id === 1001 || (t.description || '').includes('mrm_doc_mcp'),
  );
  if (!mrm) return;
  state.selectedMcp = [
    {
      id: mrm.id,
      name: mrm.name,
      type: mrm.type || 'http',
      url: mrm.url || '',
      headers: mrm.headers || {},
    },
  ];
  state.mcpSelectionTouched = true;
  saveConfig();
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  showToast(`已默认选中 MCP: ${mrm.name}`, 'info');
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function initMcpPanel() {
  document.getElementById('btn-load-mcp')?.addEventListener('click', loadMcpTools);
  document.getElementById('btn-select-all-mcp')?.addEventListener('click', () => {
    document.querySelectorAll('.mcp-select').forEach((cb) => {
      cb.checked = true;
    });
    onMcpSelectChange();
  });
  document.getElementById('btn-clear-mcp')?.addEventListener('click', () => {
    document.querySelectorAll('.mcp-select').forEach((cb) => {
      cb.checked = false;
    });
    onMcpSelectChange();
  });
}
