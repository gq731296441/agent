/** 全局配置与状态（localStorage 持久化） */

const STORAGE_KEY = 'agent_test_console_v1';

export const state = {
  userId: 'test_user_1',
  userToken: '',
  compareUserId: 'test_user_2',
  sessionId: null,
  sessionTitle: '',
  agentId: null,
  agentKind: 'default',
  subscriptionAgentId: null,
  contextEpoch: 1,
  sessionReadOnly: false,
  selectedMcp: [],
  mcpSelectionTouched: false,
  selectedSkills: [],
  transport: 'ws', // 'ws' | 'sse'
  wsStatus: 'disconnected',
  pingCount: 0,
  pongCount: 0,
  healthStatus: null,
  isStreaming: false,
  sessionWarming: false,
  sessionReady: true,
  /** @type {Map<string, {status: string, warmingStage: string|null, sandboxStatus: string, lastError: object|null}>} */
  sessionRuns: new Map(),
};

export function getSessionRun(sessionId) {
  if (!sessionId) return null;
  return state.sessionRuns.get(sessionId) || null;
}

export function patchSessionRun(sessionId, patch) {
  if (!sessionId) return;
  const prev = state.sessionRuns.get(sessionId) || {
    status: 'idle',
    warmingStage: null,
    sandboxStatus: 'none',
    lastError: null,
  };
  const next = { ...prev, ...patch };
  if (
    prev.status === next.status &&
    prev.warmingStage === next.warmingStage &&
    prev.sandboxStatus === next.sandboxStatus &&
    prev.lastError === next.lastError
  ) {
    return;
  }
  state.sessionRuns.set(sessionId, next);
  document.dispatchEvent(
    new CustomEvent('session-run-changed', { detail: { sessionId, run: next } }),
  );
  if (sessionId === state.sessionId) {
    if (next.status === 'streaming') state.isStreaming = true;
    if (next.status === 'idle' || next.status === 'queued') {
      state.isStreaming = false;
      state.sessionWarming = false;
    }
    if (next.status === 'warming') state.sessionWarming = true;
    updateChatInputState();
    updateStatusBar();
  }
}

export function isSkillSelectionLocked() {
  if (state.isStreaming || state.sessionWarming) return true;
  for (const run of state.sessionRuns.values()) {
    if (run.status === 'streaming' || run.status === 'warming') return true;
    if (run.sandboxStatus === 'running' || run.sandboxStatus === 'paused') return true;
  }
  return false;
}

export function isActiveSessionBusy() {
  const sid = state.sessionId;
  if (state.sessionReadOnly) return true;
  if (!sid) return state.isStreaming || state.sessionWarming;
  const run = getSessionRun(sid);
  return run?.status === 'streaming' || run?.status === 'warming';
}

export function updateChatInputState() {
  const sendBtn = document.getElementById('btn-send');
  const input = document.getElementById('input-question');
  const banner = document.getElementById('chat-readonly-banner');
  const busy = isActiveSessionBusy();
  if (banner) {
    banner.classList.toggle('hidden', !state.sessionReadOnly);
    if (state.sessionReadOnly) {
      banner.textContent = '当前会话为只读（已取消订阅或智能体已下架），仅可查看历史';
    }
  }
  if (sendBtn) sendBtn.disabled = busy || state.sessionReadOnly;
  if (input) {
    input.disabled = (busy && getSessionRun(state.sessionId)?.status === 'warming') || state.sessionReadOnly;
    const warming = getSessionRun(state.sessionId)?.status === 'warming';
    input.placeholder = warming
      ? '会话预热中，请稍候…'
      : '输入问题，Enter 发送';
  }
}

export function getApiBase() {
  return window.location.origin;
}

export function getWsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws`;
}

export function loadConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved.userId) state.userId = String(saved.userId);
    if (saved.userToken !== undefined) state.userToken = saved.userToken;
    if (saved.compareUserId) state.compareUserId = String(saved.compareUserId);
    if (saved.sessionId != null && saved.sessionId !== '') {
      state.sessionId = String(saved.sessionId);
    }
    if (saved.transport) state.transport = saved.transport;
    if (Array.isArray(saved.selectedMcp)) state.selectedMcp = saved.selectedMcp;
    if (saved.mcpSelectionTouched) state.mcpSelectionTouched = true;
    if (Array.isArray(saved.selectedSkills)) state.selectedSkills = saved.selectedSkills;
    if (saved.agentKind) state.agentKind = saved.agentKind;
    if (saved.subscriptionAgentId) state.subscriptionAgentId = saved.subscriptionAgentId;
    if (saved.contextEpoch) state.contextEpoch = saved.contextEpoch;
  } catch (_) {
    /* ignore */
  }
}

export function saveConfig() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      userId: state.userId,
      userToken: state.userToken,
      compareUserId: state.compareUserId,
      sessionId: state.sessionId,
      transport: state.transport,
      selectedMcp: state.selectedMcp,
      mcpSelectionTouched: state.mcpSelectionTouched,
      selectedSkills: state.selectedSkills,
      agentKind: state.agentKind,
      subscriptionAgentId: state.subscriptionAgentId,
      contextEpoch: state.contextEpoch,
    }),
  );
}

export function getSelectedSkillIds() {
  return state.selectedSkills.map((s) => String(s.market_skill_id || s.id || '')).filter(Boolean);
}

export function getIdentity() {
  return {
    user_id: state.userId,
    user_token: state.userToken || '',
    session_id: state.sessionId,
  };
}

export function getSelectedMcpList() {
  return state.selectedMcp.map((item) => ({
    id: item.id,
    name: item.name,
    type: item.type || 'http',
    url: item.url || '',
    headers: item.headers || {},
  }));
}

export function setSubscriptionSession(id, title, agentId, opts = {}) {
  state.sessionId = id;
  state.sessionTitle = title || '';
  state.agentKind = 'subscription';
  state.subscriptionAgentId = agentId || null;
  state.agentId = agentId || null;
  state.contextEpoch = opts.contextEpoch ?? 1;
  state.sessionReadOnly = Boolean(opts.readOnly);
  saveConfig();
  updateChatInputState();
  document.dispatchEvent(new CustomEvent('session-changed', {
    detail: { id, title, agentId, agentKind: 'subscription' },
  }));
}

export function setDefaultChatSession(id, title) {
  state.sessionId = id;
  state.sessionTitle = title || '';
  state.agentKind = 'default';
  state.subscriptionAgentId = null;
  state.agentId = null;
  state.sessionReadOnly = false;
  saveConfig();
  updateChatInputState();
  document.dispatchEvent(new CustomEvent('session-changed', {
    detail: { id, title, agentKind: 'default' },
  }));
}

export function setSession(id, title, agentId, agentKind = 'default') {
  if (agentKind === 'subscription') {
    setSubscriptionSession(id, title, agentId);
    return;
  }
  setDefaultChatSession(id, title);
}

export function updateStatusBar() {
  const wsEl = document.getElementById('status-ws');
  const sessionEl = document.getElementById('status-session');
  const mcpEl = document.getElementById('status-mcp-count');
  const skillEl = document.getElementById('status-skill-count');
  if (wsEl) {
    const askMode = state.transport === 'sse' ? 'SSE' : 'WS';
    wsEl.textContent = `WS: ${state.wsStatus} | 提问: ${askMode}`;
    wsEl.className = `badge ws-${state.wsStatus}`;
    wsEl.title =
      state.transport === 'sse'
        ? 'WebSocket 用于心跳/连接状态；当前提问走 SSE'
        : 'WebSocket 已用于提问与心跳';
  }
  if (sessionEl) {
    sessionEl.textContent = state.sessionId
      ? `Session: ${state.sessionId.slice(0, 8)}…`
      : 'Session: (new)';
  }
  if (mcpEl) mcpEl.textContent = `MCP: ${state.selectedMcp.length}`;
  if (skillEl) skillEl.textContent = `Skills: ${state.selectedSkills.length}`;
}
