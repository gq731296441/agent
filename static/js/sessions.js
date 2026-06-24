/** 会话与存储面板 */



import { sessionsApi, sandboxApi } from './api.js';

import {

  state,

  setSession,

  getSessionRun,

  patchSessionRun,

  setDefaultChatSession,

  setSubscriptionSession,

} from './config.js';

import { showToast, renderHistoryRecords, clearChatMessages } from './message-renderer.js';



const FULL_SESSION_LIST_ID = 'session-list';

const CHAT_SESSION_LIST_IDS = ['chat-session-list-default', 'chat-session-list-subscription'];

const CHAT_SESSION_REFRESH_ALIASES = ['chat-session-list', ...CHAT_SESSION_LIST_IDS];



let fullSessionListCache = null;

let chatDefaultCache = null;

let chatSubCache = null;

let fullSessionListInflight = null;

let chatSessionListInflight = null;

let lastSandboxStatusBody = null;
let sandboxStatusWaiters = [];

let sessionsRefreshTimer = null;



function sessionRunBadge(sessionId) {

  const run = getSessionRun(sessionId);

  if (!run) return '';

  const tags = [];

  if (run.status === 'streaming') {

    tags.push(

      '<span class="session-badge badge-agent-running badge-pulse" title="Agent 运行中">运行中</span>',

    );

  } else if (run.status === 'queued') {

    tags.push('<span class="session-badge badge-queued" title="消息排队中">排队</span>');

  } else if (run.status === 'warming') {

    const stage =

      run.warmingStage === 'WARMING_SANDBOX'

        ? '沙箱预热'

        : run.warmingStage === 'WARMING_AGENT'

          ? 'Agent 预热'

          : '预热中';

    tags.push(

      `<span class="session-badge badge-warming" title="${escapeHtml(stage)}">预热</span>`,

    );

  } else if (run.sandboxStatus === 'running') {

    tags.push(

      '<span class="session-badge badge-sandbox-live" title="沙箱运行中">沙箱</span>',

    );

  }

  if (!tags.length) return '';

  return `<span class="session-tags">${tags.join('')}</span>`;

}



function sessionKindBadges(s) {

  const tags = [];

  if (s.agent_kind === 'subscription') {

    tags.push('<span class="session-badge badge-sub" title="订阅智能体">订阅</span>');

  }

  if (s.subscription_frozen) {

    tags.push('<span class="session-badge badge-warn" title="已取消订阅或下架">只读</span>');

  }

  if (s.archived) {

    tags.push('<span class="session-badge badge-warn" title="智能体已下架">已下架</span>');

  }

  return tags.join('');

}



function sessionDisplayTitle(s) {

  if (s.agent_kind === 'subscription') {

    return s.agent_name || s.title || '订阅智能体';

  }

  return s.title || '未命名';

}



export function applySandboxStatus(body) {
  if (!body) return null;
  lastSandboxStatusBody = body;
  (body.sessions || []).forEach((s) => {
    if (s.session_id) {
      patchSessionRun(s.session_id, { sandboxStatus: s.status || 'none' });
    }
  });
  repaintAllSessionLists();
  const waiters = sandboxStatusWaiters.splice(0);
  waiters.forEach((fn) => fn(body));
  return body;
}

function waitForSandboxStatus(timeoutMs = 3000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(lastSandboxStatusBody), timeoutMs);
    sandboxStatusWaiters.push((body) => {
      clearTimeout(timer);
      resolve(body);
    });
  });
}

async function refreshSandboxStatus({ waitMs = 0 } = {}) {
  if (state.transport === 'ws') {
    const { requestSandboxStatusWhenReady } = await import('./ws-client.js');
    const sent = await requestSandboxStatusWhenReady();
    if (!sent) return lastSandboxStatusBody;
    if (waitMs > 0) return await waitForSandboxStatus(waitMs);
    return lastSandboxStatusBody;
  }
  try {
    const { body } = await sandboxApi.status(state.userId);
    return applySandboxStatus(body);
  } catch (_) {
    return lastSandboxStatusBody;
  }
}

async function warnIfSandboxFull(sessionId) {
  const body = await refreshSandboxStatus({ waitMs: 2000 });

  if (!body || !sessionId) return;

  const hasRunning = (body.sessions || []).some(

    (s) => s.session_id === sessionId && s.status === 'running',

  );

  if (body.available_slots === 0 && !hasRunning) {

    showToast('并发沙箱已满，在本会话提问可能无法启动沙箱', 'warning');

  }

}



function repaintAllSessionLists() {

  if (fullSessionListCache) {

    paintSessionList(FULL_SESSION_LIST_ID, fullSessionListCache, { loadIntoChat: false });

  }

  if (chatDefaultCache) {

    paintSessionList('chat-session-list-default', chatDefaultCache, {

      loadIntoChat: true,

      defaultAgentKind: 'default',

    });

  }

  if (chatSubCache) {

    paintSessionList('chat-session-list-subscription', chatSubCache, {

      loadIntoChat: true,

      defaultAgentKind: 'subscription',

    });

  }

}



export function startSandboxStatusPolling() {
  refreshSandboxStatus({ waitMs: 0 });
}



function updateChatSessionLabel() {

  const el = document.getElementById('chat-current-session');

  if (!el) return;

  if (state.sessionId) {

    const run = getSessionRun(state.sessionId);

    let sub = '';

    if (run?.status === 'streaming') sub = ' · Agent 运行中';

    else if (run?.status === 'warming') sub = ' · 预热中';

    else if (run?.sandboxStatus === 'running') sub = ' · 沙箱活跃';

    else if (run?.sandboxStatus === 'stopped') sub = ' · 沙箱已休眠，下次提问自动唤醒';

    const kindLabel = state.agentKind === 'subscription' ? '[订阅] ' : '';

    const ro = state.sessionReadOnly ? ' · 只读' : '';

    el.textContent = `当前: ${kindLabel}${state.sessionTitle || '未命名'} (${state.sessionId.slice(0, 8)}…)${sub}${ro}`;

    el.title = state.sessionId;

  } else {

    el.textContent = '当前: (新会话，下次提问将自动创建)';

    el.title = '';

  }

}



async function fetchFullSessionList() {

  if (!fullSessionListInflight) {

    fullSessionListInflight = sessionsApi

      .list(state.userId)

      .then((res) => {

        fullSessionListCache = res.body;

        return res.body;

      })

      .finally(() => {

        fullSessionListInflight = null;

      });

  }

  return fullSessionListInflight;

}



async function fetchChatSessionLists() {

  if (!chatSessionListInflight) {

    chatSessionListInflight = Promise.all([

      sessionsApi.list(state.userId, 'default'),

      sessionsApi.list(state.userId, 'subscription'),

    ])

      .then(([defRes, subRes]) => {

        chatDefaultCache = defRes.body;

        chatSubCache = subRes.body;

        return { default: chatDefaultCache, subscription: chatSubCache };

      })

      .finally(() => {

        chatSessionListInflight = null;

      });

  }

  return chatSessionListInflight;

}



function shouldRefreshChat(listId) {

  return !listId || CHAT_SESSION_REFRESH_ALIASES.includes(listId);

}



function shouldRefreshFull(listId) {

  return !listId || listId === FULL_SESSION_LIST_ID;

}



/** 拉取列表并刷新会话 UI（合并并发请求） */

export async function refreshSessionList(listId) {

  if (shouldRefreshChat(listId)) {

    await fetchChatSessionLists();

    paintSessionList('chat-session-list-default', chatDefaultCache, {

      loadIntoChat: true,

      defaultAgentKind: 'default',

    });

    paintSessionList('chat-session-list-subscription', chatSubCache, {

      loadIntoChat: true,

      defaultAgentKind: 'subscription',

    });

    updateChatSessionLabel();

  }

  if (shouldRefreshFull(listId)) {

    const body = await fetchFullSessionList();

    paintSessionList(FULL_SESSION_LIST_ID, body, { loadIntoChat: false });

  }

}



function paintSessionList(listId, body, opts = {}) {

  const { loadIntoChat = false, defaultAgentKind = 'default' } = opts;

  const listEl = document.getElementById(listId);

  if (!listEl) return;



  if (!body?.status && body?.code !== 0) {

    listEl.innerHTML = `<li class="error">${escapeHtml(body?.msg || '加载失败')}</li>`;

    return;

  }

  const items = body?.data || [];

  if (!items.length) {

    listEl.innerHTML = '<li class="empty">暂无会话</li>';

    return;

  }

  listEl.innerHTML = items

    .map((s) => {

      const title = sessionDisplayTitle(s);

      const kind = s.agent_kind || defaultAgentKind;

      return `

      <li class="session-item ${s.id === state.sessionId ? 'active' : ''}"

          data-id="${escapeHtml(s.id)}"

          data-title="${escapeHtml(title)}"

          data-agent-kind="${escapeHtml(kind)}"

          data-agent-id="${escapeHtml(s.agent_id || '')}"

          data-context-epoch="${s.context_epoch ?? 1}"

          data-readonly="${s.subscription_frozen ? 'true' : 'false'}">

        <div class="session-title">${escapeHtml(title)} ${sessionKindBadges(s)} ${sessionRunBadge(s.id)}</div>

        <div class="session-meta">${escapeHtml(s.last_active_at || s.updated_at || '')}</div>

      </li>`;

    })

    .join('');



  listEl.querySelectorAll('.session-item').forEach((el) => {

    el.addEventListener('click', () => {

      selectSession(

        el.dataset.id,

        el.dataset.title,

        loadIntoChat,

        {

          agentKind: el.dataset.agentKind || defaultAgentKind,

          agentId: el.dataset.agentId || null,

          contextEpoch: parseInt(el.dataset.contextEpoch, 10) || 1,

          readOnly: el.dataset.readonly === 'true',

        },

      );

    });

  });

}



async function selectSession(id, title, loadIntoChat = false, meta = {}) {

  const agentKind = meta.agentKind || 'default';

  if (agentKind === 'subscription') {

    setSubscriptionSession(id, title, meta.agentId || null, {

      contextEpoch: meta.contextEpoch ?? 1,

      readOnly: Boolean(meta.readOnly),

    });

  } else {

    setDefaultChatSession(id, title);

  }

  await refreshSessionList();

  await warnIfSandboxFull(id);

  if (loadIntoChat) {

    await loadHistoryIntoChat(id, title);

  } else {

    await loadHistory(id);

  }

  showToast(`已切换会话 ${id.slice(0, 8)}…`, 'info');

}



/** 将 chat_history 渲染到对话区（用于测试历史上下文） */

export async function loadHistoryIntoChat(sessionId, sessionTitle) {

  const id = sessionId || state.sessionId;

  if (!id) {

    showToast('请先选择会话', 'error');

    return;

  }



  const box = document.getElementById('chat-messages');

  if (box) box.innerHTML = '<div class="msg-block msg-system"><em>加载历史中…</em></div>';



  try {

    const { body } = await sessionsApi.history(state.userId, id, 1, 200);

    if (body.code === 403 || body.status === false) {

      clearChatMessages();

      showToast(body.msg || '无权访问该会话 (403)', 'error');

      return;

    }

    const records = body.data || [];

    const title = sessionTitle || state.sessionTitle;

    renderHistoryRecords(records, title);

    const histEl = document.getElementById('history-json');

    if (histEl) histEl.textContent = JSON.stringify(body, null, 2);

    showToast(`已加载 ${records.length} 条历史消息`, 'info');

  } catch (e) {

    clearChatMessages();

    showToast(e.message, 'error');

  }

}



export async function loadHistory(sessionId) {

  const id = sessionId || state.sessionId;

  if (!id) return;



  const histEl = document.getElementById('history-json');

  if (histEl) histEl.textContent = '加载中…';



  try {

    const histRes = await sessionsApi.history(state.userId, id);

    if (histEl) {

      histEl.textContent = JSON.stringify(histRes.body, null, 2);

      if (histRes.body.code === 403) showToast('无权访问该会话历史 (403)', 'error');

    }

  } catch (e) {

    if (histEl) histEl.textContent = e.message;

  }

}



/** 仅调试面板：手动加载 checkpointer（后端未实现时可能 404） */

export async function loadCheckpointer(sessionId) {

  const id = sessionId || state.sessionId;

  if (!id) return;

  const cpEl = document.getElementById('checkpointer-json');

  if (cpEl) cpEl.textContent = '加载中…';

  try {

    const cpRes = await sessionsApi.checkpointerHistory(state.userId, id);

    if (cpEl) {

      cpEl.textContent = JSON.stringify(cpRes.body, null, 2);

      if (cpRes.body.code === 403) showToast('无权访问 checkpointer (403)', 'error');

    }

  } catch (e) {

    if (cpEl) cpEl.textContent = e.message;

  }

}



export async function testCrossUserAccess() {

  if (!state.sessionId) {

    showToast('请先选择或创建一个会话', 'error');

    return;

  }

  const el = document.getElementById('cross-user-result');

  if (el) el.textContent = '测试中…';

  try {

    const { body } = await sessionsApi.history(state.compareUserId, state.sessionId);

    if (el) el.textContent = JSON.stringify(body, null, 2);

    if (body.code === 403 || body.status === false) {

      showToast('越权测试通过：对比用户被拒绝', 'info');

    } else {

      showToast('越权测试异常：对比用户可访问', 'error');

    }

  } catch (e) {

    if (el) el.textContent = e.message;

  }

}



async function deleteSession(permanent) {

  if (!state.sessionId) {

    showToast('无当前会话', 'error');

    return;

  }

  if (!confirm(`${permanent ? '永久' : '软'}删除会话 ${state.sessionId}?`)) return;

  const fn = permanent ? sessionsApi.deletePermanent : sessionsApi.deleteSoft;

  const { body } = await fn(state.userId, state.sessionId);

  showToast(body.msg || '已删除', body.status ? 'info' : 'error');

  setSession(null, '');

  clearChatMessages();

  updateChatSessionLabel();

  refreshSessionList();

}



function startNewSession(clearChat = true) {

  setSession(null, '');

  updateChatSessionLabel();

  refreshSessionList();

  if (clearChat) clearChatMessages();

  showToast('下次提问将创建新会话', 'info');

}



function escapeHtml(s) {

  return String(s ?? '')

    .replace(/&/g, '&amp;')

    .replace(/</g, '&lt;')

    .replace(/>/g, '&gt;');

}



export function initSessions() {

  document.getElementById('btn-refresh-sessions')?.addEventListener('click', () => refreshSessionList());

  document.getElementById('btn-chat-refresh-sessions')?.addEventListener('click', () => refreshSessionList());

  document.getElementById('btn-load-history')?.addEventListener('click', () => loadHistory());

  document.getElementById('btn-load-checkpointer')?.addEventListener('click', () => loadCheckpointer());

  document.getElementById('btn-reload-chat-history')?.addEventListener('click', () => loadHistoryIntoChat());

  document.getElementById('btn-delete-soft')?.addEventListener('click', () => deleteSession(false));

  document.getElementById('btn-delete-permanent')?.addEventListener('click', () => deleteSession(true));

  document.getElementById('btn-cross-user-test')?.addEventListener('click', testCrossUserAccess);

  document.getElementById('btn-new-session')?.addEventListener('click', () => startNewSession(false));

  document.getElementById('btn-chat-new-session')?.addEventListener('click', () => startNewSession(true));



  document.addEventListener('sessions-refresh', () => {

    if (sessionsRefreshTimer) clearTimeout(sessionsRefreshTimer);

    sessionsRefreshTimer = setTimeout(() => {

      sessionsRefreshTimer = null;

      refreshSessionList();

    }, 300);

  });

  document.addEventListener('session-run-changed', () => repaintAllSessionLists());

  document.addEventListener('session-changed', () => {

    updateChatSessionLabel();

    import('./config.js').then(({ updateStatusBar }) => updateStatusBar());

    repaintAllSessionLists();

  });



  updateChatSessionLabel();

  startSandboxStatusPolling();

}


