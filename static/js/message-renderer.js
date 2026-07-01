/** Agent 消息渲染（含 Deep Agent 语义类型 + HITL 内联） */

import { getApiBase, state, setSession, patchSessionRun, getSessionRun } from './config.js';
import { buildInlineHitlHtml, bindInlineHitl } from './hitl.js';

function fileIconForMime(mime) {
  if (!mime) return '📄';
  if (mime.startsWith('image/')) return '🖼️';
  if (mime.startsWith('video/')) return '🎬';
  if (mime.startsWith('audio/')) return '🎵';
  if (mime.includes('pdf')) return '📕';
  if (mime.includes('html') || mime.includes('xml')) return '🌐';
  if (mime.includes('json') || mime.includes('javascript')) return '📋';
  if (mime.includes('python') || mime.includes('x-python')) return '🐍';
  if (mime.startsWith('text/')) return '📝';
  if (mime.includes('zip') || mime.includes('tar') || mime.includes('gzip')) return '📦';
  if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return '📊';
  return '📄';
}

let currentChatEl = null;
let currentReasoningEl = null;
let reasoningText = '';
let streamedChatText = '';

/** 本轮 run 指标：run_id -> { userBlock, metricsEl, timerId, firstResponseAt, finalized } */
const runMetricsState = new Map();
/** 最近一条用户消息（等待 run_id 绑定） */
let pendingUserMessageEl = null;

function clearRunMetricsState() {
  for (const s of runMetricsState.values()) {
    if (s.timerId) clearInterval(s.timerId);
  }
  runMetricsState.clear();
  pendingUserMessageEl = null;
}

function formatTokensK(n) {
  const num = Number(n) || 0;
  return (num / 1000).toFixed(1);
}

function formatElapsedSeconds(sec) {
  const s = Math.max(0, Number(sec) || 0);
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m ${rem}s`;
}

function renderRunMetricsHtml(tokens, durationSec) {
  let html = '';
  if (tokens) {
    html += `<span class="run-token-in" title="输入 token">↑${formatTokensK(tokens.prompt)}k</span>`;
    html += `<span class="run-token-out" title="输出 token">↓${formatTokensK(tokens.completion)}k</span>`;
  }
  html += `<span class="run-duration" title="耗时">${formatElapsedSeconds(durationSec)}</span>`;
  return html;
}

function updateRunMetricsDisplay(state, tokens, durationSec) {
  if (!state?.metricsEl) return;
  state.metricsEl.hidden = false;
  state.metricsEl.innerHTML = renderRunMetricsHtml(tokens, durationSec);
}

function bindRunToPendingUser(runId) {
  if (!runId || runMetricsState.has(runId)) return;
  let userEl = pendingUserMessageEl;
  if (!userEl) {
    const box = document.getElementById('chat-messages');
    const blocks = box?.querySelectorAll('.msg-user:not([data-run-id])') || [];
    userEl = blocks.length ? blocks[blocks.length - 1] : null;
  }
  if (!userEl) return;
  userEl.dataset.runId = runId;
  if (pendingUserMessageEl === userEl) pendingUserMessageEl = null;
  let metricsEl = userEl.querySelector('.run-metrics-row');
  if (!metricsEl) {
    metricsEl = document.createElement('div');
    metricsEl.className = 'run-metrics-row';
    metricsEl.hidden = true;
    userEl.appendChild(metricsEl);
  }
  runMetricsState.set(runId, {
    userBlock: userEl,
    metricsEl,
    timerId: null,
    firstResponseAt: null,
    finalized: false,
  });
}

function startRunTimer(runId) {
  const s = runMetricsState.get(runId);
  if (!s || s.firstResponseAt || s.finalized) return;
  s.firstResponseAt = Date.now();
  s.metricsEl.hidden = false;
  updateRunMetricsDisplay(s, null, 0);
  if (s.timerId) clearInterval(s.timerId);
  s.timerId = setInterval(() => {
    if (!s.firstResponseAt || s.finalized) return;
    updateRunMetricsDisplay(s, null, (Date.now() - s.firstResponseAt) / 1000);
  }, 200);
}

function stopRunTimer(runId) {
  const s = runMetricsState.get(runId);
  if (!s?.timerId) return;
  clearInterval(s.timerId);
  s.timerId = null;
}

/** 首个 agent 响应（chat/reasoning）时绑定 run_id 并启动计时 */
function trackAgentResponseForRun(msg) {
  const runId = msg?.run_id;
  if (!runId) return;
  bindRunToPendingUser(runId);
  startRunTimer(runId);
}

function finalizeRunMetrics(msg) {
  const c = msg?.content && typeof msg.content === 'object' ? msg.content : {};
  const runId = c.run_id || msg?.run_id;
  if (!runId) return;
  bindRunToPendingUser(runId);
  const s = runMetricsState.get(runId);
  if (!s || s.finalized) return;
  s.finalized = true;
  stopRunTimer(runId);
  const usage = c.token_usage || {};
  const durationSec = s.firstResponseAt
    ? (Date.now() - s.firstResponseAt) / 1000
    : (c.duration_ms || 0) / 1000;
  updateRunMetricsDisplay(
    s,
    {
      prompt: usage.prompt_tokens || 0,
      completion: usage.completion_tokens || 0,
    },
    durationSec,
  );
}

function fallbackStopRunTimer(msg) {
  const runId = msg?.run_id;
  if (!runId) return;
  const s = runMetricsState.get(runId);
  if (!s || s.finalized) return;
  stopRunTimer(runId);
  if (s.firstResponseAt) {
    updateRunMetricsDisplay(s, null, (Date.now() - s.firstResponseAt) / 1000);
  }
}

/** Deep Agent 专用 MessageType */
const DEEP_TYPES = new Set([
  'todo_update',
  'fs_operation',
  'execute',
  'execute_result',
  'skill_catalog',
  'skill_loaded',
  'subagent_start',
  'subagent_event',
  'subagent_end',
  'workspace_artifact',
  'workspace_file',
  'deep_status',
]);

const DEEP_TYPE_LABELS = {
  todo_update: '待办更新',
  fs_operation: '文件操作',
  execute: '执行命令',
  execute_result: '执行结果',
  skill_catalog: 'Skill 目录',
  skill_loaded: 'Skill 已加载',
  subagent_start: '子 Agent 启动',
  subagent_event: '子 Agent 事件',
  subagent_end: '子 Agent 结束',
  workspace_artifact: '工作区产物',
  workspace_file: '工作区文件',
  deep_status: 'Deep 状态',
};

export function resetChatStream() {
  currentChatEl = null;
  currentReasoningEl = null;
  reasoningText = '';
  streamedChatText = '';
}

export function resetRunMetricsForNewTurn() {
  pendingUserMessageEl = null;
}

function finalizeChatStream() {
  if (currentChatEl && currentChatEl.isConnected) {
    currentChatEl.dataset.streaming = '0';
  }
  currentChatEl = null;
  streamedChatText = '';
}

function extractChatText(content) {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === 'string') return block;
        if (block && typeof block === 'object' && block.type === 'text') {
          return String(block.text ?? '');
        }
        return '';
      })
      .join('');
  }
  if (typeof content === 'object' && content.text !== undefined) {
    return String(content.text);
  }
  return '';
}

function appendAssistantChatChunk(msg, chunk) {
  if (!chunk && !(currentChatEl && currentChatEl.isConnected && currentChatEl.dataset.streaming === '1')) {
    return;
  }
  if (currentChatEl && currentChatEl.isConnected && currentChatEl.dataset.streaming === '1') {
    streamedChatText += chunk;
    const body = currentChatEl.querySelector('.msg-body');
    if (body) body.innerHTML = escapeHtml(streamedChatText);
    return;
  }
  if (!chunk) return;
  streamedChatText = chunk;
  currentChatEl = appendMessage(
    'msg-assistant',
    `${metaTags(msg)}<div class="msg-body">${escapeHtml(streamedChatText)}</div>`,
  );
  if (currentChatEl) currentChatEl.dataset.streaming = '1';
}

export function clearChatMessages() {
  const box = document.getElementById('chat-messages');
  if (box) box.innerHTML = '';
  resetChatStream();
  clearRunMetricsState();
}

/** 发送提问后立即展示用户消息（后端 USER 消息不推 WebSocket） */
export function appendUserMessage(text) {
  if (!text) return null;
  resetChatStream();
  resetRunMetricsForNewTurn();
  const el = appendMessage(
    'msg-user',
    `<div class="msg-body">${escapeHtml(text)}</div><div class="run-metrics-row" hidden></div>`,
  );
  pendingUserMessageEl = el;
  return el;
}

function appendMessage(className, html) {
  const box = document.getElementById('chat-messages');
  if (!box) return null;
  const el = document.createElement('div');
  el.className = `msg-block ${className}`;
  el.innerHTML = html;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

function formatContent(content) {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return escapeHtml(content);
  if (typeof content === 'object' && content.text !== undefined) {
    return escapeHtml(String(content.text));
  }
  return `<pre class="json-content">${escapeHtml(JSON.stringify(content, null, 2))}</pre>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}

function metaTags(msg) {
  const parts = [];
  if (msg.run_id) parts.push(`run:${msg.run_id.slice(0, 8)}`);
  if (msg.conversation_id) parts.push(`conv:${msg.conversation_id.slice(0, 8)}`);
  if (msg.step_index !== undefined) parts.push(`step:${msg.step_index}`);
  const c = msg.content;
  if (c && typeof c === 'object' && c.agent_mode === 'deep') {
    parts.push('deep');
  }
  return parts.length ? `<span class="msg-meta">${parts.join(' · ')}</span>` : '';
}

function deepBadge(content) {
  if (content && typeof content === 'object' && content.agent_mode === 'deep') {
    return '<span class="badge deep-badge">Deep</span> ';
  }
  return '';
}

function workspaceDownloadHref(virtualPath) {
  const url = new URL('/workspace/files/download', getApiBase());
  url.searchParams.set('user_id', state.userId);
  url.searchParams.set('path', virtualPath);
  return url.toString();
}

function formatDeepToolContent(content, label) {
  const c = content || {};
  const toolName = c.tool_name || c.name || 'unknown';
  const args = c.args ?? c;
  const summary = c.summary || c.tool_result;
  const refId = c.ref_id;
  let html = `${deepBadge(c)}<strong>${escapeHtml(label)}</strong> <code>${escapeHtml(toolName)}</code>`;

  if (c.workspace_path) {
    const vpath = c.workspace_path.replace(/^\//, '');
    html += `<div class="deep-path">路径: <code>${escapeHtml(c.workspace_path)}</code>`;
    html += ` <a href="${escapeHtml(workspaceDownloadHref(vpath))}" target="_blank" rel="noopener">下载</a></div>`;
  }
  if (args && typeof args === 'object' && (args.file_path || args.path || args.command)) {
    html += `<pre class="deep-args">${escapeHtml(JSON.stringify(args, null, 2))}</pre>`;
  } else if (args && typeof args === 'object' && Object.keys(args).length) {
    html += `<pre class="deep-args">${escapeHtml(JSON.stringify(args, null, 2))}</pre>`;
  }
  if (summary) {
    html += `<div class="deep-summary">${escapeHtml(String(summary))}${c.truncated ? ' <em>(已截断)</em>' : ''}</div>`;
  }
  if (refId) {
    html += `<div class="deep-ref">Artifact: <code>${escapeHtml(refId)}</code></div>`;
  }
  return html;
}

function formatSubagentName(content) {
  const c = content || {};
  return c.name || c.args?.subagent_type || c.args?.name || c.tool_name || 'task';
}

function formatSubagentInputSummary(content) {
  const c = content || {};
  if (c.input_summary) return String(c.input_summary);
  const args = c.args || {};
  return args.description || args.task || args.prompt || args.input || '';
}

function renderSubagentStartCard(content, meta, isHistory = false) {
  const c = content || {};
  const hist = isHistory ? ' msg-history' : '';
  const name = formatSubagentName(c);
  const summary = formatSubagentInputSummary(c);
  const status = c.status || 'running';
  let body = `${deepBadge(c)}<div class="subagent-card subagent-running">`;
  body += `<div class="subagent-header">`;
  body += `<strong>子 Agent 启动</strong>`;
  body += `<span class="subagent-name">${escapeHtml(name)}</span>`;
  body += `<span class="subagent-status badge status-${escapeHtml(status)}">${status === 'running' ? '运行中' : escapeHtml(status)}</span>`;
  body += `</div>`;
  if (summary) {
    body += `<div class="subagent-section"><span class="subagent-label">输入摘要</span>`;
    body += `<div class="deep-summary subagent-input">${escapeHtml(summary)}</div></div>`;
  }
  if (c.subagent_run_id) {
    body += `<div class="deep-sub-id">subagent: <code>${escapeHtml(c.subagent_run_id.slice(0, 8))}…</code></div>`;
  }
  body += `</div>`;
  return appendMessage(`msg-deep msg-deep-subagent-start${hist}`, `${meta}${body}`);
}

function renderSubagentEndCard(content, meta, isHistory = false) {
  const c = content || {};
  const hist = isHistory ? ' msg-history' : '';
  const name = formatSubagentName(c);
  const report = c.final_report || c.summary || c.tool_result || '';
  const status = c.status || 'completed';
  let body = `${deepBadge(c)}<div class="subagent-card subagent-completed">`;
  body += `<div class="subagent-header">`;
  body += `<strong>子 Agent 结束</strong>`;
  body += `<span class="subagent-name">${escapeHtml(name)}</span>`;
  body += `<span class="subagent-status badge status-${escapeHtml(status)}">${status === 'completed' ? '已完成' : escapeHtml(status)}</span>`;
  body += `</div>`;
  const inputSummary = formatSubagentInputSummary(c);
  if (inputSummary) {
    body += `<div class="subagent-section"><span class="subagent-label">输入摘要</span>`;
    body += `<div class="deep-summary subagent-input">${escapeHtml(inputSummary)}</div></div>`;
  }
  if (report) {
    body += `<div class="subagent-section"><span class="subagent-label">输出摘要</span>`;
    body += `<div class="deep-summary subagent-output">${escapeHtml(String(report))}${c.truncated ? ' <em>(已截断)</em>' : ''}</div></div>`;
  }
  if (c.subagent_run_id) {
    body += `<div class="deep-sub-id">subagent: <code>${escapeHtml(c.subagent_run_id.slice(0, 8))}…</code></div>`;
  }
  if (c.ref_id) {
    body += `<div class="deep-ref">Artifact: <code>${escapeHtml(c.ref_id)}</code></div>`;
  }
  body += `</div>`;
  return appendMessage(`msg-deep msg-deep-subagent-end${hist}`, `${meta}${body}`);
}

function renderSubagentEventCard(content, meta, isHistory = false) {
  const c = content || {};
  const hist = isHistory ? ' msg-history' : '';
  const name = formatSubagentName(c);
  const eventName = c.event || c.event_type || 'progress';
  const detail = c.summary || c.message || c.detail || '';
  let body = `${deepBadge(c)}<strong>${escapeHtml(DEEP_TYPE_LABELS.subagent_event)}</strong>`;
  body += ` <code>${escapeHtml(name)}</code> · <em>${escapeHtml(eventName)}</em>`;
  if (detail) body += `<div class="deep-summary">${escapeHtml(String(detail))}</div>`;
  if (c.subagent_run_id) {
    body += `<div class="deep-sub-id">subagent: <code>${escapeHtml(c.subagent_run_id.slice(0, 8))}…</code></div>`;
  }
  return appendMessage(`msg-deep msg-deep-subagent-event${hist}`, `${meta}${body}`);
}

function renderDeepMessage(type, content, meta, isHistory = false) {
  const label = DEEP_TYPE_LABELS[type] || type;
  const hist = isHistory ? ' msg-history' : '';
  let cls = `msg-deep msg-deep-${type.replace(/_/g, '-')}${hist}`;

  switch (type) {
    case 'todo_update':
      return appendMessage(cls, `${meta}${formatDeepToolContent(content, label)}`);
    case 'fs_operation':
      return appendMessage(cls, `${meta}${formatDeepToolContent(content, label)}`);
    case 'execute':
    case 'execute_result':
      return appendMessage(cls, `${meta}${formatDeepToolContent(content, label)}`);
    case 'subagent_start':
      return renderSubagentStartCard(content, meta, isHistory);
    case 'subagent_end':
      return renderSubagentEndCard(content, meta, isHistory);
    case 'subagent_event':
      return renderSubagentEventCard(content, meta, isHistory);
    case 'skill_catalog':
    case 'skill_loaded': {
      const skill = (content || {}).skill_name || (content || {}).name || '';
      return appendMessage(
        cls,
        `${meta}${deepBadge(content)}<strong>${escapeHtml(label)}</strong> ${escapeHtml(skill)}`,
      );
    }
    case 'workspace_artifact': {
      const c = content || {};
      if (!c.ref_id) return null;
      return appendMessage(
        cls,
        `${meta}${deepBadge(c)}<strong>${escapeHtml(label)}</strong>
         ${c.ref_id ? `<code>${escapeHtml(c.ref_id)}</code>` : ''}
         ${c.summary ? `<div class="deep-summary">${escapeHtml(c.summary)}</div>` : ''}`,
      );
    }
    case 'workspace_file': {
      const c = content || {};
      const files = c.files || [];
      if (!files.length) return null;
      const items = files.map(f => {
        const icon = fileIconForMime(f.mime_type || '');
        const existsTag = f.exists ? '' : ' <em style="color:#f66;">(已删除)</em>';
        return `<div class="ws-file-card">
          <span class="ws-file-icon">${icon}</span>
          <code class="ws-file-name">${escapeHtml(f.name || f.virtual_path || '')}</code>
          ${existsTag}
        </div>`;
      }).join('');
      return appendMessage(
        cls,
        `${meta}${deepBadge(c)}<strong>${escapeHtml(label)}</strong>
         <div class="ws-file-list">${items}</div>`,
      );
    }
    case 'deep_status': {
      const msg = (content || {}).message || JSON.stringify(content);
      return appendMessage(
        `msg-deep-status${hist}`,
        `${meta}${deepBadge(content)}<em>${escapeHtml(msg)}</em>`,
      );
    }
    default:
      return appendMessage(
        `msg-unknown${hist}`,
        `${meta}<strong>${escapeHtml(type)}</strong><pre>${escapeHtml(JSON.stringify(content, null, 2))}</pre>`,
      );
  }
}

export function renderHistoryRecords(records, sessionTitle) {
  clearChatMessages();
  if (!records || !records.length) {
    appendMessage('msg-system', '<em>该会话暂无历史消息</em>');
    resetChatStream();
    return;
  }

  const sorted = [...records].reverse();
  appendMessage(
    'msg-system',
    `<strong>已加载历史</strong> ${escapeHtml(sessionTitle || '')} · 共 ${sorted.length} 条`,
  );

  for (let i = 0; i < sorted.length; i++) {
    renderHistoryRecord(sorted[i], sorted, i);
  }
  resetChatStream();
}

/** 历史 interrupt 之后若已有后续消息，说明用户已审批并继续执行 */
function isHistoryInterruptResolved(recordIndex, sorted) {
  const RESOLVED_AFTER = new Set([
    'interrupt',
    'tool_call',
    'tool_result',
    'end',
    'execute',
    'execute_result',
    'fs_operation',
    'chat',
  ]);
  for (let i = recordIndex + 1; i < sorted.length; i++) {
    if (RESOLVED_AFTER.has(sorted[i].type)) return true;
  }
  return false;
}

function parseHistoryContent(content) {
  if (typeof content === 'string') {
    try {
      return JSON.parse(content);
    } catch (_) {
      return { raw: content };
    }
  }
  return content ?? {};
}

/** 历史 interrupt 是否仍可操作（优先读 content.hitl_status） */
function isHistoryInterruptInteractive(content, recordIndex, sorted) {
  const status = content.hitl_status;
  if (status === 'resolved' || status === 'cancelled' || status === 'expired') {
    return false;
  }
  if (status === 'pending') {
    return true;
  }
  return !isHistoryInterruptResolved(recordIndex, sorted);
}

function renderHistoryInterrupt(r, meta, sorted, recordIndex) {
  const interruptContent = parseHistoryContent(r.content);
  const msg = {
    type: 'interrupt',
    session_id: state.sessionId,
    content: interruptContent,
  };
  const interactive = isHistoryInterruptInteractive(
    interruptContent,
    recordIndex,
    sorted,
  );
  const inlineHtml = buildInlineHitlHtml(msg, {
    readonly: !interactive,
    resolvedLabel: '已处理（历史记录）',
  });
  const el = appendMessage(
    `msg-interrupt msg-hitl-inline msg-history${interactive ? '' : ' hitl-resolved'}`,
    `${meta}${inlineHtml}`,
  );
  if (interactive && el) bindInlineHitl(el, msg);
}

function renderHistoryRecord(r, sorted, recordIndex) {
  const meta = `<span class="msg-meta hist-label">${escapeHtml(r.create_time || '')} · ${escapeHtml(r.type)} · step:${r.step_index ?? 0}</span>`;
  const content = r.content;

  if (DEEP_TYPES.has(r.type)) {
    renderDeepMessage(r.type, content, meta, true);
    return;
  }

  switch (r.type) {
    case 'chat': {
      const cls = r.sender === 'user' ? 'msg-user msg-history' : 'msg-assistant msg-history';
      appendMessage(cls, `${meta}<div class="msg-body">${formatContent(content)}</div>`);
      break;
    }
    case 'reasoning':
    case 'reasoning_final':
      appendMessage(
        'msg-reasoning-final msg-history',
        `${meta}<details><summary>推理</summary><div class="msg-body">${formatContent(content)}</div></details>`,
      );
      break;
    case 'tool_call':
      appendMessage(
        'msg-tool-call msg-history',
        `${meta}<strong>工具调用</strong> <pre>${escapeHtml(JSON.stringify(content, null, 2))}</pre>`,
      );
      break;
    case 'tool_result':
      appendMessage(
        'msg-tool-result msg-history',
        `${meta}<strong>工具结果</strong> <pre class="tool-result">${escapeHtml(JSON.stringify(content, null, 2))}</pre>`,
      );
      break;
    case 'end': {
      const reason = typeof content === 'string' ? content : content?.text || JSON.stringify(content);
      appendMessage('msg-end end-ok msg-history', `${meta}<strong>结束</strong> ${escapeHtml(reason)}`);
      break;
    }
    case 'error':
      appendMessage('msg-error msg-history', `${meta}<strong>错误</strong> ${formatContent(content)}`);
      break;
    case 'interrupt':
      renderHistoryInterrupt(r, meta, sorted, recordIndex);
      break;
    case 'context_divider':
      appendMessage(
        'msg-context-divider msg-history',
        `<div class="context-divider-line">${escapeHtml(content?.text || '— 已开启新会话，以上为历史记录 —')}</div>`,
      );
      break;
    case 'status':
      appendMessage('msg-status msg-history', `${meta}${formatContent(content)}`);
      break;
    default:
      appendMessage(
        'msg-unknown msg-history',
        `${meta}<strong>${escapeHtml(r.type)}</strong><pre>${escapeHtml(JSON.stringify(content, null, 2))}</pre>`,
      );
  }
}

function handleInterrupt(msg) {
  finalizeChatStream();
  state.isStreaming = false;

  const inlineHtml = buildInlineHitlHtml(msg);
  const el = appendMessage(
    'msg-interrupt msg-hitl-inline',
    `${metaTags(msg)}${inlineHtml}`,
  );
  if (el) bindInlineHitl(el, msg);
}

export function showToast(text, level = 'info') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = text;
  el.className = `toast toast-${level} show`;
  setTimeout(() => el.classList.remove('show'), 4000);
}

function showSandboxLimitModal(payload) {
  const sessions = (payload?.running_sessions || [])
    .map(
      (id) => `
      <li class="sandbox-limit-item">
        <code>${escapeHtml(String(id).slice(0, 8))}…</code>
        <button type="button" class="btn btn-sm btn-stop-sandbox" data-session-id="${escapeHtml(String(id))}">停止沙箱</button>
      </li>`,
    )
    .join('');
  const html = `
    <div class="sandbox-limit-modal">
      <p>${escapeHtml(payload?.msg || '已达沙箱并发上限')}</p>
      ${sessions ? `<p>运行中的会话：</p><ul class="sandbox-limit-list">${sessions}</ul>` : ''}
      <button type="button" class="btn btn-primary sandbox-limit-ok">知道了</button>
    </div>`;
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay sandbox-limit-overlay';
  overlay.innerHTML = html;
  document.body.appendChild(overlay);
  overlay.querySelector('.sandbox-limit-ok')?.addEventListener('click', () => overlay.remove());
  overlay.querySelectorAll('.btn-stop-sandbox').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const sessionId = btn.dataset.sessionId;
      btn.disabled = true;
      try {
        const { sandboxApi } = await import('./api.js');
        const { state, patchSessionRun } = await import('./config.js');
        await sandboxApi.stop(state.userId, sessionId);
        patchSessionRun(sessionId, { sandboxStatus: 'stopped' });
        const { requestSandboxStatus } = await import('./ws-client.js');
        requestSandboxStatus();
        btn.textContent = '已停止';
        showToast(`会话 ${sessionId.slice(0, 8)}… 沙箱已停止`, 'info');
      } catch (e) {
        btn.disabled = false;
        showToast(e.message || '停止沙箱失败', 'error');
      }
    });
  });
}

function updateSessionRunFromMessage(msg) {
  const sid = msg.session_id;
  if (!sid) return;
  switch (msg.type) {
    case 'message_queued':
      patchSessionRun(sid, { status: 'queued' });
      break;
    case 'session_warming':
      patchSessionRun(sid, {
        status: 'warming',
        warmingStage: msg.content?.status || null,
      });
      break;
    case 'session_ready':
      patchSessionRun(sid, { status: 'idle', warmingStage: null });
      break;
    case 'chat':
      if (msg.sender !== 'user') patchSessionRun(sid, { status: 'streaming' });
      break;
    case 'end':
      patchSessionRun(sid, { status: 'idle', sandboxStatus: 'stopped' });
      break;
    case 'run_metrics':
      patchSessionRun(sid, { status: 'idle' });
      break;
    case 'interrupt':
      patchSessionRun(sid, { status: 'idle' });
      break;
    case 'error': {
      const c = msg.content;
      if (c && typeof c === 'object' && c.code === 'SANDBOX_USER_LIMIT') {
        patchSessionRun(sid, { status: 'idle', lastError: c });
        showSandboxLimitModal(c);
      } else if (c && typeof c === 'object' && c.code === 'SKILL_SELECTION_LOCKED') {
        patchSessionRun(sid, { status: 'idle', lastError: c });
        showToast(c.msg || '无法变更 Skill 勾选', 'warn');
      } else {
        patchSessionRun(sid, { status: 'idle' });
      }
      break;
    }
    default:
      break;
  }
}

export function handleAgentMessage(msg) {
  updateSessionRunFromMessage(msg);
  const sid = msg.session_id;
  const type = msg.type;

  // session_allocated 必须在 sid 过滤之前处理，否则新会话时 state.sessionId 仍为 null，
  // 后续每条 WS 消息都会误判为「其他会话」并触发 sessions-refresh 风暴。
  if (type === 'session_allocated') {
    const c = msg.content || {};
    setSession(c.session_id || sid, c.session_title || '', c.agent_id);
    state.sessionWarming = true;
    state.sessionReady = false;
    import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
    appendMessage(
      'msg-system',
      `<strong>新会话</strong> ${escapeHtml(c.session_title || '')}<br>
       <code>${escapeHtml(c.session_id || sid)}</code>
       ${c.agent_id ? `<br>Agent: <code>${escapeHtml(c.agent_id)}</code>` : ''}`,
    );
    document.dispatchEvent(new CustomEvent('sessions-refresh'));
    return;
  }

  if (sid && sid !== state.sessionId) {
    document.dispatchEvent(new CustomEvent('sessions-refresh'));
    return;
  }

  if (type === 'run_metrics') {
    finalizeRunMetrics(msg);
    return;
  }

  // 历史/异常：task 被误标为 tool_call 时仍渲染 SubAgent 卡片
  if (type === 'tool_call') {
    const c = msg.content || {};
    const name = c.tool_name || c.name || '';
    if (name === 'task' || c.args?.subagent_type || c.args?.description) {
      finalizeChatStream();
      renderDeepMessage('subagent_start', c, metaTags(msg));
      return;
    }
  }

  if (DEEP_TYPES.has(type)) {
    finalizeChatStream();
    renderDeepMessage(type, msg.content, metaTags(msg));
    return;
  }

  switch (type) {
    case 'session_warming': {
      const st = msg.content?.status || 'WARMING';
      state.sessionWarming = true;
      state.sessionReady = false;
      import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
      appendMessage(
        'msg-status session-warming',
        `${metaTags(msg)}<strong>会话预热</strong> ${escapeHtml(st)}`,
      );
      break;
    }
    case 'message_queued': {
      const pos = msg.content?.position ?? '?';
      appendMessage(
        'msg-status message-queued',
        `${metaTags(msg)}<strong>消息已排队</strong> 位置 #${escapeHtml(String(pos))}`,
      );
      showToast(`消息已排队（#${pos}），会话就绪后自动处理`, 'info');
      break;
    }
    case 'session_ready': {
      state.sessionWarming = false;
      state.sessionReady = true;
      import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
      const sandboxHint =
        msg.content?.sandbox_available === false && msg.content?.sandbox_hint
          ? `<br><span class="sandbox-hint">${escapeHtml(msg.content.sandbox_hint)}</span>`
          : '';
      appendMessage(
        'msg-status session-ready',
        `${metaTags(msg)}<strong>会话就绪</strong> 可以开始对话${sandboxHint}`,
      );
      if (msg.content?.sandbox_available === false) {
        showToast(msg.content.sandbox_hint || '沙箱不可用，execute 已降级', 'warning');
      }
      break;
    }
    case 'chat': {
      const chunk = extractChatText(msg.content);
      if (msg.sender === 'user') {
        appendMessage(
          'msg-user',
          `${metaTags(msg)}<div class="msg-body">${escapeHtml(chunk)}</div><div class="run-metrics-row" hidden></div>`,
        );
        break;
      }
      trackAgentResponseForRun(msg);
      appendAssistantChatChunk(msg, chunk);
      break;
    }
    case 'reasoning':
      trackAgentResponseForRun(msg);
      appendMessage('msg-reasoning', `${metaTags(msg)}<em>${formatContent(msg.content)}</em>`);
      break;
    case 'reasoning_final':
      trackAgentResponseForRun(msg);
      finalizeChatStream();
      reasoningText = typeof msg.content === 'object' ? msg.content.text : msg.content;
      currentReasoningEl = appendMessage(
        'msg-reasoning-final',
        `${metaTags(msg)}<details open><summary>推理</summary><div class="msg-body">${formatContent(msg.content)}</div></details>`,
      );
      break;
    case 'tool_call': {
      const c = msg.content || {};
      finalizeChatStream();
      appendMessage(
        'msg-tool-call',
        `${metaTags(msg)}<strong>工具调用</strong> <code>${escapeHtml(c.tool_name || c.name || '')}</code>
         <pre>${escapeHtml(JSON.stringify(c.args ?? c, null, 2))}</pre>
         <small>id: ${escapeHtml(c.tool_call_id || '')}</small>`,
      );
      break;
    }
    case 'tool_result': {
      const c = msg.content || {};
      finalizeChatStream();
      appendMessage(
        'msg-tool-result',
        `${metaTags(msg)}<strong>工具结果</strong> <code>${escapeHtml(c.tool_name || '')}</code>
         <pre class="tool-result">${escapeHtml(JSON.stringify(c.tool_result ?? c, null, 2))}</pre>`,
      );
      break;
    }
    case 'interrupt':
      fallbackStopRunTimer(msg);
      handleInterrupt(msg);
      break;
    case 'status':
      appendMessage('msg-status', `${metaTags(msg)}${formatContent(msg.content)}`);
      break;
    case 'error': {
      state.isStreaming = false;
      import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
      finalizeChatStream();
      fallbackStopRunTimer(msg);
      const errContent = msg.content;
      if (errContent && typeof errContent === 'object' && errContent.code === 'SANDBOX_USER_LIMIT') {
        appendMessage(
          'msg-error',
          `${metaTags(msg)}<strong>沙箱上限</strong> ${escapeHtml(errContent.msg || '')}`,
        );
        showToast(errContent.msg || '已达沙箱并发上限', 'error');
        break;
      }
      appendMessage('msg-error', `${metaTags(msg)}<strong>错误</strong> ${formatContent(msg.content)}`);
      showToast(String(typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)), 'error');
      break;
    }
    case 'end': {
      state.isStreaming = false;
      import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
      finalizeChatStream();
      fallbackStopRunTimer(msg);
      const reason = typeof msg.content === 'string' ? msg.content : msg.content?.text || 'unknown';
      const cls = reason === 'completed' ? 'end-ok' : reason === 'stop' ? 'end-stop' : 'end-error';
      appendMessage(
        `msg-end ${cls}`,
        `${metaTags(msg)}<strong>结束</strong> <span class="end-reason">${escapeHtml(reason)}</span>`,
      );
      break;
    }
    default:
      appendMessage(
        'msg-unknown',
        `${metaTags(msg)}<strong>${escapeHtml(type)}</strong><pre>${escapeHtml(JSON.stringify(msg.content, null, 2))}</pre>`,
      );
  }
}
