/** HITL 审批 — 聊天内联 + 底部高级面板 */

import { sendResumeHitl } from './ws-client.js';
import { state } from './config.js';

let pendingRequestId = null;
let pendingActions = [];

const RISK_LABEL = { low: '低', medium: '中', high: '高' };
const DECISION_LABEL = { approve: '已批准', reject: '已拒绝', edit: '已编辑' };

function decisionBadge(decision) {
  if (!decision) return '';
  const type = decision.type || 'approve';
  const cls =
    type === 'reject' ? 'hitl-decision-reject' : type === 'edit' ? 'hitl-decision-edit' : 'hitl-decision-approve';
  return `<span class="hitl-decision-badge ${cls}">${escapeHtml(DECISION_LABEL[type] || type)}</span>`;
}

function findDecisionForIndex(decisions, idx) {
  if (!Array.isArray(decisions)) return null;
  return decisions.find((d) => d.index === idx) || decisions[idx] || null;
}

function formatResolvedSummary(content, fallback) {
  const decisions = content.decisions || [];
  if (!decisions.length) return fallback;
  return decisions
    .map((d) => {
      const label = DECISION_LABEL[d.type] || d.type;
      return `${d.tool_name || `#${d.index}`}：${label}`;
    })
    .join(' · ');
}

export function normalizeAction(action) {
  const args = action.args || action.arguments || {};
  const preview = action.preview || {};
  const mergedArgs = Object.keys(args || {}).length ? args : preview;
  return {
    ...action,
    tool_name: action.tool_name || action.name || 'unknown',
    args: mergedArgs,
  };
}

export function normalizeActions(actions) {
  return (actions || []).map(normalizeAction);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatPreview(action) {
  const preview = action.preview;
  if (preview && typeof preview === 'object' && Object.keys(preview).length) {
    return `<pre class="hitl-preview">${escapeHtml(JSON.stringify(preview, null, 2))}</pre>`;
  }
  return `<pre class="hitl-preview">${escapeHtml(JSON.stringify(action.args || {}, null, 2))}</pre>`;
}

function riskBadge(level) {
  const cls = `hitl-risk hitl-risk-${level || 'low'}`;
  return `<span class="${cls}">${escapeHtml(RISK_LABEL[level] || level || '低')}风险</span>`;
}

/** 聊天区内联 HITL 卡片 HTML */
export function buildInlineHitlHtml(msg, options = {}) {
  const { readonly = false, resolvedLabel = '已处理（历史记录）' } = options;
  const content = msg.content || {};
  const requestId = content.request_id || '';
  const actions = normalizeActions(content.actions || []);
  const decisions = content.decisions || [];
  const isResolved = readonly || content.hitl_status === 'resolved';
  if (!readonly) {
    pendingRequestId = requestId;
    pendingActions = actions;
  }

  const actionBlocks = actions
    .map(
      (action, idx) => {
        const decision = isResolved ? findDecisionForIndex(decisions, idx) : null;
        return `
    <div class="hitl-inline-action" data-idx="${idx}">
      <div class="hitl-inline-head">
        <strong>${escapeHtml(action.tool_name)}</strong>
        ${riskBadge(action.risk_level)}
        <span class="hitl-cat">${escapeHtml(action.tool_category || 'tool')}</span>
        ${decision ? decisionBadge(decision) : ''}
      </div>
      ${formatPreview(action)}
      ${
        readonly || decision
          ? ''
          : `<div class="hitl-inline-btns">
        <button type="button" class="btn primary sm hitl-approve-one" data-idx="${idx}">批准</button>
        <button type="button" class="btn warn sm hitl-reject-one" data-idx="${idx}">拒绝</button>
      </div>`
      }
    </div>`;
      },
    )
    .join('');

  const summary = isResolved
    ? formatResolvedSummary(content, resolvedLabel)
    : resolvedLabel;
  const barHtml = readonly || isResolved
    ? `<div class="hitl-inline-bar"><span class="hitl-resolved-note">${escapeHtml(summary)}</span></div>`
    : `<div class="hitl-inline-bar">
        <button type="button" class="btn primary hitl-approve-all">全部批准</button>
        <button type="button" class="btn warn hitl-reject-all">全部拒绝</button>
        <button type="button" class="btn sm hitl-open-advanced">高级编辑…</button>
      </div>`;

  return `
    <div class="hitl-inline${readonly ? ' hitl-resolved hitl-history' : ''}" data-request-id="${escapeHtml(requestId)}">
      <div class="hitl-inline-title">
        <strong>${readonly ? '人工确认（历史）' : '需要人工确认'}</strong>
        <code class="hitl-req-id">${escapeHtml(requestId.slice(0, 8))}…</code>
        ${content.agent_mode === 'deep' ? '<span class="badge deep-badge">Deep</span>' : ''}
      </div>
      <p class="hitl-inline-hint">${
        readonly
          ? '以下为历史审批记录，已处理过的请求不可再次提交。'
          : '以下工具调用需您批准或拒绝后方可继续执行。'
      }</p>
      ${actionBlocks || '<p>无待审批操作</p>'}
      ${barHtml}
    </div>`;
}

/** 绑定聊天区内联按钮 */
export function bindInlineHitl(containerEl, msg) {
  if (!containerEl || containerEl.classList.contains('hitl-resolved')) return;
  const content = msg.content || {};
  pendingRequestId = content.request_id;
  pendingActions = normalizeActions(content.actions || []);

  containerEl.querySelector('.hitl-approve-all')?.addEventListener('click', () => {
    submitDecisions(pendingActions.map(() => ({ type: 'approve' })));
  });
  containerEl.querySelector('.hitl-reject-all')?.addEventListener('click', () => {
    submitDecisions(pendingActions.map(() => ({ type: 'reject' })));
  });
  containerEl.querySelector('.hitl-open-advanced')?.addEventListener('click', () => {
    showHitlPanel(msg);
  });

  containerEl.querySelectorAll('.hitl-approve-one').forEach((btn) => {
    btn.addEventListener('click', () => {
      const idx = Number(btn.dataset.idx);
      submitDecisions(buildSingleDecision(idx, 'approve'));
    });
  });
  containerEl.querySelectorAll('.hitl-reject-one').forEach((btn) => {
    btn.addEventListener('click', () => {
      const idx = Number(btn.dataset.idx);
      submitDecisions(buildSingleDecision(idx, 'reject'));
    });
  });
}

function buildSingleDecision(targetIdx, type) {
  return pendingActions.map((action, idx) => {
    if (idx !== targetIdx) return { type: 'approve' };
    if (type === 'reject') return { type: 'reject' };
    return { type: 'approve' };
  });
}

export function showHitlPanel(msg) {
  const content = msg.content || {};
  pendingRequestId = content.request_id;
  pendingActions = normalizeActions(content.actions || []);

  const panel = document.getElementById('hitl-panel');
  const body = document.getElementById('hitl-actions');
  if (!panel || !body) return;

  panel.classList.remove('hidden');
  document.getElementById('hitl-request-id').textContent = pendingRequestId || '';

  body.innerHTML = pendingActions
    .map((action, idx) => renderActionForm(action, idx))
    .join('');

  body.querySelectorAll('.hitl-decision').forEach((sel) => {
    sel.addEventListener('change', (e) => {
      const idx = e.target.dataset.idx;
      const editBox = document.getElementById(`hitl-edit-${idx}`);
      if (editBox) editBox.classList.toggle('hidden', e.target.value !== 'edit');
    });
  });

  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderActionForm(action, idx) {
  const allowed = action.allowed_decisions || ['approve', 'reject', 'edit'];
  const opts = allowed.map((d) => `<option value="${d}">${d}</option>`).join('');
  return `
    <div class="hitl-action-card" data-idx="${idx}">
      <div class="hitl-inline-head">
        <h4>${escapeHtml(action.tool_name)}</h4>
        ${riskBadge(action.risk_level)}
      </div>
      ${formatPreview(action)}
      <label>决策
        <select class="hitl-decision" data-idx="${idx}">${opts}</select>
      </label>
      <div id="hitl-edit-${idx}" class="hitl-edit hidden">
        <label>编辑参数 (JSON)
          <textarea class="hitl-edit-args" data-idx="${idx}" rows="4">${escapeHtml(JSON.stringify(action.args, null, 2))}</textarea>
        </label>
      </div>
    </div>`;
}

function collectDecisions() {
  const decisions = [];
  pendingActions.forEach((action, idx) => {
    const sel = document.querySelector(`#hitl-actions .hitl-decision[data-idx="${idx}"]`);
    const type = sel?.value || 'approve';
    if (type === 'edit') {
      const ta = document.querySelector(`#hitl-actions .hitl-edit-args[data-idx="${idx}"]`);
      let args = action.args;
      try {
        args = JSON.parse(ta?.value || '{}');
      } catch {
        import('./message-renderer.js').then(({ showToast }) =>
          showToast('编辑参数 JSON 无效', 'error'),
        );
        throw new Error('invalid edit json');
      }
      decisions.push({
        type: 'edit',
        edited_action: { name: action.tool_name, args },
      });
    } else if (type === 'reject') {
      decisions.push({ type: 'reject' });
    } else {
      decisions.push({ type: 'approve' });
    }
  });
  return decisions;
}

export function submitDecisions(decisions) {
  if (!pendingRequestId) {
    import('./message-renderer.js').then(({ showToast }) =>
      showToast('无待处理的 HITL 请求', 'error'),
    );
    return;
  }
  const requestId = pendingRequestId;
  if (state.transport === 'sse') {
    import('./sse-client.js').then(({ sendResumeHitlSse }) =>
      sendResumeHitlSse(requestId, decisions),
    );
  } else {
    sendResumeHitl(requestId, decisions);
  }
  hideHitlPanel();
  markInlineHitlResolved(requestId, '已提交决策，Agent 继续执行…');
  import('./message-renderer.js').then(({ showToast }) =>
    showToast('已提交 HITL 决策', 'info'),
  );
  state.isStreaming = true;
}

function markInlineHitlResolved(requestId, note) {
  const selector = requestId
    ? `.hitl-inline[data-request-id="${CSS.escape(requestId)}"]:not(.hitl-resolved)`
    : '.hitl-inline:not(.hitl-resolved)';
  document.querySelectorAll(selector).forEach((el) => {
    el.classList.add('hitl-resolved');
    el.querySelectorAll('button').forEach((b) => {
      b.disabled = true;
    });
    const bar = el.querySelector('.hitl-inline-bar');
    if (bar && note) {
      bar.innerHTML = `<span class="hitl-resolved-note">${escapeHtml(note)}</span>`;
    }
  });
}

export function initHitl() {
  document.getElementById('btn-hitl-submit')?.addEventListener('click', () => {
    if (!pendingRequestId) return;
    try {
      submitDecisions(collectDecisions());
    } catch (_) {
      /* validation error */
    }
  });

  document.getElementById('btn-hitl-cancel')?.addEventListener('click', hideHitlPanel);
}

function hideHitlPanel() {
  document.getElementById('hitl-panel')?.classList.add('hidden');
}
