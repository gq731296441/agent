/** 原始事件日志 */

const MAX_LOG_ENTRIES = 200;
const entries = [];

export function logEvent(source, data, direction = 'in') {
  const entry = {
    ts: new Date().toISOString(),
    source,
    direction,
    data: typeof data === 'string' ? data : JSON.parse(JSON.stringify(data)),
  };
  entries.unshift(entry);
  if (entries.length > MAX_LOG_ENTRIES) entries.pop();
  renderLog();
}

export function clearLog() {
  entries.length = 0;
  renderLog();
}

function highlightJson(obj) {
  const str = JSON.stringify(obj, null, 2);
  return str
    .replace(/"(run_id|conversation_id|session_id|request_id)":/g, '"<mark>$1</mark>":')
    .replace(/"type":/g, '"<strong>type</strong>":');
}

function renderLog() {
  const container = document.getElementById('event-log-body');
  if (!container) return;
  if (!entries.length) {
    container.innerHTML = '<div class="log-empty">暂无事件</div>';
    return;
  }
  container.innerHTML = entries
    .map(
      (e) => `
    <div class="log-entry log-${e.direction}">
      <div class="log-meta">
        <span class="log-ts">${e.ts.slice(11, 23)}</span>
        <span class="log-src">${e.source}</span>
        <span class="log-dir">${e.direction === 'in' ? '←' : '→'}</span>
      </div>
      <pre class="log-json">${escapeHtml(JSON.stringify(e.data, null, 2))}</pre>
    </div>`,
    )
    .join('');
}

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function initEventLog() {
  document.getElementById('btn-clear-log')?.addEventListener('click', clearLog);
  document.getElementById('btn-toggle-log')?.addEventListener('click', () => {
    document.getElementById('event-log-panel')?.classList.toggle('collapsed');
    syncLogPanelHeight();
  });
  syncLogPanelHeight();
  renderLog();
}

function syncLogPanelHeight() {
  const panel = document.getElementById('event-log-panel');
  const h = panel?.classList.contains('collapsed') ? 36 : 200;
  document.documentElement.style.setProperty('--event-log-h', `${h}px`);
}
