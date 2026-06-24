/** REST API 封装 */

import { getApiBase, state } from './config.js';

async function parseResponse(res) {
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!res.ok && body.code === undefined) {
    throw new Error(body.detail || body.msg || `HTTP ${res.status}`);
  }
  return { ok: res.ok, status: res.status, body };
}

export async function apiGet(path, params = {}) {
  const url = new URL(path, getApiBase());
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  const res = await fetch(url.toString());
  return parseResponse(res);
}

export async function apiPost(path, body, params = {}) {
  const url = new URL(path, getApiBase());
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const res = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseResponse(res);
}

export async function apiPut(path, body, params = {}) {
  const url = new URL(path, getApiBase());
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const res = await fetch(url.toString(), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseResponse(res);
}

export async function apiDelete(path, params = {}) {
  const url = new URL(path, getApiBase());
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const res = await fetch(url.toString(), { method: 'DELETE' });
  return parseResponse(res);
}

export async function checkHealth() {
  const res = await fetch(`${getApiBase()}/health`);
  const body = await res.json();
  state.healthStatus = body;
  return body;
}

// --- Sessions ---
export const sessionsApi = {
  list: (userId, agent_kind) => apiGet('/sessions/list', { userId, agent_kind }),
  ensureSubscription: (user_id, agent_id) =>
    apiPost(`/sessions/subscription/${agent_id}/ensure`, {}, { user_id }),
  newContext: (user_id, session_id, expected_epoch) =>
    apiPost(`/sessions/${session_id}/new-context`, {}, { user_id, expected_epoch }),
  history: (userId, session_id, page = 1, limit = 50) =>
    apiGet('/sessions/history', { userId, session_id, page, limit }),
  checkpointerHistory: (user_id, session_id) =>
    apiGet('/sessions/checkpointer/history', { user_id, session_id }),
  deleteSoft: (user_id, session_id) =>
    apiDelete('/sessions/delete', { user_id, session_id }),
  deletePermanent: (user_id, session_id) =>
    apiDelete('/sessions/delete/permanent', { user_id, session_id }),
};

// --- MCP ---
export const mcpApi = {
  tools: (user_id) => apiGet('/mcp/tools', { user_id }),
};

// --- Skills ---
export const skillsApi = {
  catalog: (params) => apiGet('/skills/catalog', params),
  subscribed: (user_id) => apiGet('/skills/subscribed', { user_id }),
  subscribe: (skill_id, user_id) =>
    apiPost(`/skills/subscribe/${skill_id}`, {}, { user_id }),
  unsubscribe: (skill_id, user_id) =>
    apiDelete(`/skills/unsubscribe/${skill_id}`, { user_id }),
  upgrade: (skill_id, user_id) =>
    apiPost(`/skills/subscribed/${skill_id}/upgrade`, {}, { user_id }),
  installed: (user_id) => apiGet('/skills/installed', { user_id }),
  market: () => apiGet('/skills/market'),
  install: (skill_id, user_id) =>
    apiPost(`/skills/market/install/${skill_id}`, {}, { user_id }),
};

// --- Agent Config ---
export const agentConfigApi = {
  get: (session_id) => apiGet(`/agent_config/${session_id}`),
  list: (user_id) => apiGet('/agent_config/', { user_id }),
  create: (session_id, config, model_name = 'nebulacoder-v8') =>
    apiPost(`/agent_config/${session_id}`, config, { model_name }),
  update: (session_id, config, model_name) =>
    apiPut(`/agent_config/${session_id}`, config, model_name ? { model_name } : {}),
  delete: (session_id) => apiDelete(`/agent_config/${session_id}`),
  getMcpIds: (session_id) => apiGet(`/agent_config/${session_id}/mcp-ids`),
  setMcpIds: (session_id, mcp_ids) => {
    const url = new URL(`/agent_config/${session_id}/mcp-ids`, getApiBase());
    mcp_ids.forEach((id) => url.searchParams.append('mcp_ids', id));
    return fetch(url.toString(), { method: 'PUT' }).then((r) => parseResponse(r));
  },
};

// --- Agent chat (non-stream) ---
export async function agentChat(payload) {
  return apiPost('/agent/chat', {
    ...payload,
    question: payload.question || ' ',
    user_id: payload.user_id || state.userId,
    user_token: payload.user_token ?? state.userToken,
  });
}

// --- Sandbox ---
export const sandboxApi = {
  status: (user_id) => apiGet('/sandbox/status', { user_id }),
  stop: (user_id, session_id) => apiPost('/sandbox/stop', {}, { user_id, session_id }),
  stopAll: (user_id) => apiPost('/sandbox/stop-all', {}, { user_id }),
};

// --- Workspace ---
export const workspaceApi = {
  quota: (user_id) => apiGet('/workspace/quota', { user_id }),
  list: (user_id, { session_id, scope = 'all' } = {}) =>
    apiGet('/workspace/files', { user_id, session_id, scope }),
  deleteFile: (user_id, path) => apiDelete('/workspace/files', { user_id, path }),
  reconcile: (user_id) => apiPost('/workspace/reconcile', {}, { user_id }),
  downloadUrl: (path, user_id) => {
    const url = new URL('/workspace/files/download', getApiBase());
    url.searchParams.set('user_id', user_id);
    url.searchParams.set('path', path);
    return url.toString();
  },
};

// --- Stats ---
export const statsApi = {
  overview: (start_date, end_date) =>
    apiGet('/stats/overview', { start_date, end_date }),
  users: (start_date, end_date) => apiGet('/stats/users', { start_date, end_date }),
  sessions: (start_date, end_date) =>
    apiGet('/stats/sessions', { start_date, end_date }),
  messages: (start_date, end_date) =>
    apiGet('/stats/messages', { start_date, end_date }),
  toolCalls: (start_date, end_date, top_n = 20) =>
    apiGet('/stats/tool-calls', { start_date, end_date, top_n }),
  tasks: (start_date, end_date) => apiGet('/stats/tasks', { start_date, end_date }),
};

export const llmConfigApi = {
  get: (user_id) => apiGet('/users/me/llm-config', { user_id }),
  save: (user_id, body) => apiPut('/users/me/llm-config', body, { user_id }),
  test: (user_id, body = {}) => apiPost('/users/me/llm-config/test', body, { user_id }),
};

export const subscriptionAgentsApi = {
  catalog: (user_id, tab = 'all', q = '') =>
    apiGet('/subscription-agents/catalog', { user_id, tab, q }),
  used: (user_id) => apiGet('/subscription-agents/used', { user_id }),
  get: (agent_id, user_id) => apiGet(`/subscription-agents/${agent_id}`, { user_id }),
  create: (body) => apiPost('/subscription-agents', body),
  update: (agent_id, user_id, body) =>
    apiPut(`/subscription-agents/${agent_id}`, body, { user_id }),
  remove: (agent_id, user_id) => apiDelete(`/subscription-agents/${agent_id}`, { user_id }),
  subscribe: (agent_id, user_id) =>
    apiPost(`/subscription-agents/${agent_id}/subscribe`, {}, { user_id }),
  unsubscribe: (agent_id, user_id) =>
    apiDelete(`/subscription-agents/${agent_id}/subscribe`, { user_id }),
  stats: (agent_id, user_id) => apiGet(`/subscription-agents/${agent_id}/stats`, { user_id }),
  runs: (agent_id, user_id, limit = 20) =>
    apiGet(`/subscription-agents/${agent_id}/runs`, { user_id, limit }),
};
