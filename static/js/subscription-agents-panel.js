/** 订阅智能体配置页 */

import { state, setSubscriptionSession, saveConfig, getApiBase } from './config.js';
import { subscriptionAgentsApi, llmConfigApi, mcpApi, skillsApi, sessionsApi } from './api.js';
import { showToast } from './message-renderer.js';
import { loadHistoryIntoChat, refreshSessionList } from './sessions.js';

let currentSubTab = 'all';

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export async function ensureLlmConfigured() {
  const { body } = await llmConfigApi.get(state.userId);
  return Boolean(body?.data?.configured);
}

export async function openLlmConfigDialog() {
  const dlg = document.getElementById('dialog-llm-config');
  const { body } = await llmConfigApi.get(state.userId);
  const cfg = body?.data || {};
  const fast = cfg.fast || {};
  document.getElementById('llm-model').value = cfg.model || '';
  document.getElementById('llm-base-url').value = cfg.base_url || '';
  document.getElementById('llm-fast-model').value = fast.model || '';
  document.getElementById('llm-fast-base-url').value = fast.base_url || '';
  const keyInput = document.getElementById('llm-api-key');
  const keyMasked = document.getElementById('llm-api-key-saved');
  if (keyInput) {
    keyInput.value = '';
    keyInput.required = !cfg.has_api_key;
  }
  if (keyMasked) {
    if (cfg.has_api_key && cfg.api_key_masked) {
      keyMasked.textContent = `已保存: ${cfg.api_key_masked}`;
      keyMasked.classList.remove('hidden');
    } else {
      keyMasked.textContent = '';
      keyMasked.classList.add('hidden');
    }
  }
  const fastKeyInput = document.getElementById('llm-fast-api-key');
  const fastKeyMasked = document.getElementById('llm-fast-api-key-saved');
  if (fastKeyInput) fastKeyInput.value = '';
  if (fastKeyMasked) {
    if (fast.configured && fast.api_key_masked) {
      fastKeyMasked.textContent = `已保存: ${fast.api_key_masked}`;
      fastKeyMasked.classList.remove('hidden');
    } else if (fast.has_api_key && fast.api_key_masked) {
      fastKeyMasked.textContent = `已保存: ${fast.api_key_masked}`;
      fastKeyMasked.classList.remove('hidden');
    } else {
      fastKeyMasked.textContent = fast.model || fast.base_url ? '未配置 Key（保存时需填写）' : '未配置时将使用主模型';
      fastKeyMasked.classList.toggle('hidden', !(fast.model || fast.base_url));
    }
  }
  document.getElementById('llm-test-result').textContent = '';
  dlg?.showModal();
}

async function saveLlmConfig(ev) {
  ev.preventDefault();
  const model = document.getElementById('llm-model').value.trim();
  const base_url = document.getElementById('llm-base-url').value.trim();
  const api_key = document.getElementById('llm-api-key').value.trim();
  const fast_model = document.getElementById('llm-fast-model').value.trim();
  const fast_base_url = document.getElementById('llm-fast-base-url').value.trim();
  const fast_api_key = document.getElementById('llm-fast-api-key').value.trim();
  const { body: getBody } = await llmConfigApi.get(state.userId);
  const existing = getBody?.data || {};
  const payload = { model, base_url, fast_model, fast_base_url };
  if (api_key) {
    payload.api_key = api_key;
  } else if (!existing.has_api_key) {
    showToast('首次配置必须填写主模型 API Key', 'error');
    return;
  }
  if (fast_api_key) payload.fast_api_key = fast_api_key;
  if (!fast_model && !fast_base_url && !fast_api_key && existing.fast?.configured) {
    payload.clear_fast = true;
  }
  const { body } = await llmConfigApi.save(state.userId, payload);
  if (body.code === 0) {
    showToast('模型配置已保存', 'info');
    document.getElementById('dialog-llm-config')?.close();
  } else {
    showToast(body.msg || '保存失败', 'error');
  }
}

async function testLlmConfig(target = 'main') {
  const modelEl = target === 'fast' ? 'llm-fast-model' : 'llm-model';
  const baseEl = target === 'fast' ? 'llm-fast-base-url' : 'llm-base-url';
  const model = document.getElementById(modelEl).value.trim();
  const base_url = document.getElementById(baseEl).value.trim();
  const el = document.getElementById('llm-test-result');
  el.textContent = '测试中…';
  const { body } = await llmConfigApi.test(state.userId, {
    target,
    model: model || undefined,
    base_url: base_url || undefined,
  });
  el.textContent = JSON.stringify(body, null, 2);
  if (body.code === 0) {
    showToast(target === 'fast' ? '快速模型连接成功' : '主模型连接成功', 'info');
  } else {
    showToast(body.msg || '连接测试失败', 'error');
  }
}

function renderAgentCard(agent) {
  const archived = agent.archived ? '<span class="badge badge-warn">已下架</span>' : '';
  const subBtn =
    agent.is_subscribed && currentSubTab !== 'mine'
      ? `<button type="button" class="btn sm btn-unsub" data-id="${agent.id}">取消订阅</button>`
      : currentSubTab !== 'mine'
        ? `<button type="button" class="btn sm btn-sub" data-id="${agent.id}">订阅</button>`
        : '';
  const chatBtn = `<button type="button" class="btn sm btn-chat-agent" data-id="${agent.id}">开始对话</button>`;
  const apiBtn = `<button type="button" class="btn sm btn-api-example" data-id="${agent.id}">API 示例</button>`;
  const editBtn =
    currentSubTab === 'mine'
      ? `<button type="button" class="btn sm btn-edit-agent" data-id="${agent.id}">编辑</button>
         <button type="button" class="btn sm btn-stats-agent" data-id="${agent.id}">详情</button>
         <button type="button" class="btn sm btn-del-agent" data-id="${agent.id}">下架</button>`
      : '';
  const stats =
    currentSubTab === 'mine'
      ? `<div class="sub-card-stats">Token: ${agent.total_tokens ?? 0} · 完成率: ${((agent.completion_rate || 0) * 100).toFixed(1)}% · 用户: ${agent.user_count ?? 0}</div>`
      : `<div class="sub-card-stats">使用人数: ${agent.used_user_count ?? 0} · 订阅: ${agent.subscriber_count ?? 0}</div>`;

  return `
    <div class="sub-agent-card" data-id="${agent.id}">
      <h4>${escapeHtml(agent.name)} ${archived}</h4>
      <p>${escapeHtml(agent.description || '无描述')}</p>
      <div class="sub-card-meta">创建者: ${escapeHtml(agent.creator_user_id || '')}</div>
      ${stats}
      <div class="sub-card-actions">${subBtn}${chatBtn}${apiBtn}${editBtn}</div>
    </div>`;
}

async function loadCatalog() {
  const q = document.getElementById('sub-agent-search')?.value?.trim() || '';
  const grid = document.getElementById('sub-agents-grid');
  if (!grid) return;
  grid.innerHTML = '<p>加载中…</p>';
  const { body } = await subscriptionAgentsApi.catalog(state.userId, currentSubTab, q);
  const items = body?.data || [];
  if (!items.length) {
    grid.innerHTML = '<p class="empty">暂无数据</p>';
    return;
  }
  grid.innerHTML = items.map(renderAgentCard).join('');
  bindCardActions(grid);
}

async function loadUsedAgents() {
  const list = document.getElementById('used-agents-list');
  if (!list) return;
  const { body } = await subscriptionAgentsApi.used(state.userId);
  const items = body?.data || [];
  if (!items.length) {
    list.innerHTML = '<li class="empty">暂无</li>';
    return;
  }
  list.innerHTML = items
    .map(
      (a) => `
    <li class="session-item" data-agent-id="${a.agent_id}" data-session-id="${a.session_id}" data-agent-name="${escapeHtml(a.agent_name || '')}" data-readonly="${a.read_only}">
      <div class="session-title">${escapeHtml(a.agent_name || a.agent_id)} ${a.archived ? '· 已下架' : ''}</div>
      <div class="session-meta-row">
        <span class="session-meta">${a.read_only ? '只读' : '可对话'}</span>
        <button type="button" class="btn sm btn-api-example-used" data-agent-id="${a.agent_id}" data-session-id="${a.session_id}">API</button>
      </div>
    </li>`,
    )
    .join('');
  list.querySelectorAll('.session-item').forEach((el) => {
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('.btn-api-example-used')) return;
      openSubscriptionChat(
        el.dataset.agentId,
        el.dataset.sessionId,
        el.dataset.readonly === 'true',
        el.dataset.agentName,
      );
    });
  });
  list.querySelectorAll('.btn-api-example-used').forEach((btn) => {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openSubscriptionApiExample(
        btn.dataset.agentId,
        btn.closest('.session-item')?.querySelector('.session-title')?.textContent?.replace(/· 已下架.*/, '').trim() || '',
        btn.dataset.sessionId,
      );
    });
  });
}

async function openSubscriptionChat(agentId, existingSessionId, readOnly = false, presetAgentName = '') {
  if (!readOnly && !(await ensureLlmConfigured())) {
    showToast('请先配置模型 Key', 'warning');
    await openLlmConfigDialog();
    return;
  }
  let sessionId = existingSessionId;
  let agentName = presetAgentName || '';
  let contextEpoch = 1;
  let frozen = readOnly;
  if (!sessionId) {
    const { body } = await sessionsApi.ensureSubscription(state.userId, agentId);
    if (body.code !== 0) {
      showToast(body.msg || '无法打开会话', 'error');
      return;
    }
    sessionId = body.data.session_id;
    agentName = body.data.agent_name;
    contextEpoch = body.data.context_epoch || 1;
    frozen = body.data.subscription_frozen;
  }
  setSubscriptionSession(sessionId, agentName || '订阅智能体', agentId, {
    contextEpoch,
    readOnly: frozen,
  });
  document.querySelector('.nav-item[data-tab="chat"]')?.click();
  await refreshSessionList('chat-session-list');
  await loadHistoryIntoChat(sessionId, agentName);
  showToast('已进入订阅智能体对话', 'info');
}

function resolveUserTokenForExample() {
  const t = String(state.userToken || '').trim();
  return t || '{YOUR_USER_TOKEN}';
}

function buildSubscriptionApiExamples({ apiBase, userId, agentId, sessionId }) {
  const userToken = resolveUserTokenForExample();
  const sid = sessionId || '{SESSION_ID}';
  const ensureUrl = `${apiBase}/sessions/subscription/${agentId}/ensure?user_id=${encodeURIComponent(userId)}`;

  const ensureCurl = `# 每个 user_id + agent_id 对应唯一 session_id，可缓存返回值\ncurl -X POST "${ensureUrl}"`;

  const chatBody = {
    action: 'ask',
    user_id: userId,
    user_token: userToken,
    session_id: sid,
    agent_id: agentId,
    question: '你好',
    stream: true,
  };

  const chatBodyJson = JSON.stringify(chatBody, null, 2);
  const chatBodyCompact = JSON.stringify(chatBody);
  const chatCurl = `# 响应为 text/event-stream，每行 data: {JSON}\ncurl -N -X POST "${apiBase}/agent/chat" \\\n  -H "Content-Type: application/json" \\\n  -H "Accept: text/event-stream" \\\n  -d '${chatBodyCompact.replace(/'/g, "'\\''")}'`;

  const chatFetch = `const res = await fetch("${apiBase}/agent/chat", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
  },
  body: JSON.stringify(${chatBodyJson}),
});

if (!res.ok) throw new Error(await res.text());

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const parts = buffer.split("\\n\\n");
  buffer = parts.pop() || "";
  for (const part of parts) {
    const line = part.trim();
    if (!line.startsWith("data:")) continue;
    const msg = JSON.parse(line.slice(5).trim());
    console.log(msg.type, msg);
  }
}`;

  return { ensureCurl, chatCurl, chatFetch, userToken, sessionId: sid };
}

async function copySnippet(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast('已复制到剪贴板', 'info');
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast('已复制到剪贴板', 'info');
  }
}

async function openSubscriptionApiExample(agentId, agentName = '', existingSessionId = '') {
  if (!agentId) return;

  const dlg = document.getElementById('dialog-sub-api-example');
  const titleEl = document.getElementById('sub-api-example-title');
  const hintEl = document.getElementById('sub-api-example-hint');
  if (!dlg) return;

  titleEl.textContent = agentName
    ? `「${agentName}」SSE 调用示例`
    : '订阅智能体 SSE 调用示例';
  hintEl.textContent = '加载中…';
  dlg.showModal();

  let sessionId = existingSessionId || '';
  let ensureOk = Boolean(sessionId);
  let ensureMsg = '';

  if (!sessionId) {
    const { body } = await sessionsApi.ensureSubscription(state.userId, agentId);
    if (body.code === 0 && body.data?.session_id) {
      sessionId = body.data.session_id;
      ensureOk = true;
      ensureMsg = body.data.is_new ? '已创建订阅会话。' : '已获取现有订阅会话。';
      loadUsedAgents();
    } else {
      ensureMsg = body.msg || '无法获取 session_id，请先订阅该智能体并配置模型 Key。';
    }
  } else {
    ensureMsg = '使用已有 session_id（来自「使用过的智能体」）。';
  }

  const examples = buildSubscriptionApiExamples({
    apiBase: getApiBase(),
    userId: state.userId,
    agentId,
    sessionId: ensureOk ? sessionId : '',
  });

  document.getElementById('sub-api-meta-agent-id').textContent = agentId;
  document.getElementById('sub-api-meta-session-id').textContent = examples.sessionId;
  document.getElementById('sub-api-meta-user-id').textContent = state.userId;
  document.getElementById('sub-api-ensure').textContent = examples.ensureCurl;
  document.getElementById('sub-api-curl').textContent = examples.chatCurl;
  document.getElementById('sub-api-fetch').textContent = examples.chatFetch;

  const tokenNote =
    examples.userToken === '{YOUR_USER_TOKEN}'
      ? '请将示例中的 {YOUR_USER_TOKEN} 替换为实际 user_token。'
      : '示例中 user_token 来自当前顶栏配置。';
  hintEl.textContent = [
    '调用前请确保已在「模型配置」中填写 LLM Key；订阅智能体无需传 capabilities（MCP/Skill 由创建者绑定）。',
    ensureMsg,
    tokenNote,
    '步骤 2 默认 stream: true（SSE 流式）；将 stream 设为 false 可改为非流式 JSON 响应。',
  ]
    .filter(Boolean)
    .join(' ');
}

function bindCardActions(grid) {
  grid.querySelectorAll('.btn-sub').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const { body } = await subscriptionAgentsApi.subscribe(btn.dataset.id, state.userId);
      showToast(body.msg || '已订阅', body.code === 0 ? 'info' : 'error');
      loadCatalog();
    });
  });
  grid.querySelectorAll('.btn-unsub').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      await subscriptionAgentsApi.unsubscribe(btn.dataset.id, state.userId);
      showToast('已取消订阅', 'info');
      loadCatalog();
      loadUsedAgents();
    });
  });
  grid.querySelectorAll('.btn-chat-agent').forEach((btn) => {
    btn.addEventListener('click', () => openSubscriptionChat(btn.dataset.id));
  });
  grid.querySelectorAll('.btn-api-example').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openSubscriptionApiExample(
        btn.dataset.id,
        btn.closest('.sub-agent-card')?.querySelector('h4')?.textContent?.replace(/已下架.*/, '').trim() || '',
      );
    });
  });
  grid.querySelectorAll('.btn-edit-agent').forEach((btn) => {
    btn.addEventListener('click', () => openAgentForm(btn.dataset.id));
  });
  grid.querySelectorAll('.btn-stats-agent').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const { body } = await subscriptionAgentsApi.stats(btn.dataset.id, state.userId);
      alert(JSON.stringify(body.data, null, 2));
    });
  });
  grid.querySelectorAll('.btn-del-agent').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('确认下架该智能体？')) return;
      await subscriptionAgentsApi.remove(btn.dataset.id, state.userId);
      loadCatalog();
    });
  });
}

async function openAgentForm(agentId = null) {
  const dlg = document.getElementById('dialog-sub-agent');
  document.getElementById('sub-agent-edit-id').value = agentId || '';
  document.getElementById('sub-agent-form-title').textContent = agentId
    ? '编辑订阅智能体'
    : '创建订阅智能体';

  const [mcpRes, skillRes] = await Promise.all([
    mcpApi.tools(state.userId),
    skillsApi.installed(state.userId),
  ]);
  const mcps = mcpRes.body?.data || [];
  const skills = skillRes.body?.data || [];
  document.getElementById('sub-agent-mcp-list').innerHTML = mcps
    .map(
      (m) =>
        `<label><input type="checkbox" name="mcp" value="${m.id}" /> ${escapeHtml(m.name)} (${m.id})</label>`,
    )
    .join('');
  document.getElementById('sub-agent-skill-list').innerHTML = skills
    .map(
      (s) =>
        `<label><input type="checkbox" name="skill" value="${s.id}" /> ${escapeHtml(s.skill_name || s.id)}</label>`,
    )
    .join('');

  if (agentId) {
    const { body } = await subscriptionAgentsApi.get(agentId, state.userId);
    const a = body.data || {};
    document.getElementById('sub-agent-name').value = a.name || '';
    document.getElementById('sub-agent-desc').value = a.description || '';
    document.getElementById('sub-agent-prompt').value = a.system_prompt || '';
    document.getElementById('sub-agent-visibility').value = a.visibility || 'public';
    (a.mcp_ids || []).forEach((id) => {
      const cb = document.querySelector(`#sub-agent-mcp-list input[value="${id}"]`);
      if (cb) cb.checked = true;
    });
    (a.skill_ids || []).forEach((id) => {
      const cb = document.querySelector(`#sub-agent-skill-list input[value="${id}"]`);
      if (cb) cb.checked = true;
    });
  } else {
    document.getElementById('form-sub-agent').reset();
    document.getElementById('sub-agent-visibility').value = 'public';
  }
  dlg?.showModal();
}

async function saveAgentForm(ev) {
  ev.preventDefault();
  const agentId = document.getElementById('sub-agent-edit-id').value;
  const mcp_ids = [...document.querySelectorAll('#sub-agent-mcp-list input:checked')].map((el) =>
    parseInt(el.value, 10),
  );
  const skill_ids = [...document.querySelectorAll('#sub-agent-skill-list input:checked')].map(
    (el) => el.value,
  );
  const payload = {
    user_id: state.userId,
    name: document.getElementById('sub-agent-name').value.trim(),
    description: document.getElementById('sub-agent-desc').value.trim(),
    system_prompt: document.getElementById('sub-agent-prompt').value.trim(),
    visibility: document.getElementById('sub-agent-visibility').value,
    mcp_ids,
    skill_ids,
  };
  let body;
  if (agentId) {
    ({ body } = await subscriptionAgentsApi.update(agentId, state.userId, payload));
  } else {
    ({ body } = await subscriptionAgentsApi.create(payload));
  }
  if (body.code === 0) {
    showToast('已保存', 'info');
    document.getElementById('dialog-sub-agent')?.close();
    loadCatalog();
  } else {
    showToast(body.msg || '保存失败', 'error');
  }
}

export function initSubscriptionAgentsPanel() {
  document.getElementById('btn-llm-config')?.addEventListener('click', openLlmConfigDialog);
  document.getElementById('form-llm-config')?.addEventListener('submit', saveLlmConfig);
  document.getElementById('btn-llm-cancel')?.addEventListener('click', () =>
    document.getElementById('dialog-llm-config')?.close(),
  );
  document.getElementById('btn-llm-test')?.addEventListener('click', () => testLlmConfig('main'));
  document.getElementById('btn-llm-test-fast')?.addEventListener('click', () => testLlmConfig('fast'));

  document.querySelectorAll('.sub-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentSubTab = btn.dataset.subTab;
      loadCatalog();
    });
  });
  document.getElementById('btn-sub-agent-create')?.addEventListener('click', () => openAgentForm());
  document.getElementById('btn-sub-agent-refresh')?.addEventListener('click', () => {
    loadCatalog();
    loadUsedAgents();
  });
  document.getElementById('sub-agent-search')?.addEventListener('input', () => loadCatalog());
  document.getElementById('form-sub-agent')?.addEventListener('submit', saveAgentForm);
  document.getElementById('btn-sub-agent-cancel')?.addEventListener('click', () =>
    document.getElementById('dialog-sub-agent')?.close(),
  );
  document.getElementById('btn-sub-new-context')?.addEventListener('click', handleNewContext);

  document.getElementById('dialog-sub-api-example')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.btn-copy-snippet');
    if (!btn) return;
    const pre = document.getElementById(btn.dataset.target);
    if (pre?.textContent) copySnippet(pre.textContent);
  });
  document.getElementById('btn-sub-api-close')?.addEventListener('click', () =>
    document.getElementById('dialog-sub-api-example')?.close(),
  );

  document.querySelector('.nav-item[data-tab="subscription-agents"]')?.addEventListener('click', () => {
    loadCatalog();
    loadUsedAgents();
  });
}

export async function handleNewContext() {
  if (state.agentKind !== 'subscription' || !state.sessionId) {
    showToast('请先在订阅智能体页选择或打开一个订阅对话', 'warning');
    return;
  }
  const { body } = await sessionsApi.newContext(
    state.userId,
    state.sessionId,
    state.contextEpoch,
  );
  if (body.code === 409) {
    showToast('会话状态已变化，正在重载…', 'warning');
    await loadHistoryIntoChat(state.sessionId, state.sessionTitle);
    return;
  }
  if (body.code !== 0) {
    showToast(body.msg || '失败', 'error');
    return;
  }
  state.contextEpoch = body.data.context_epoch;
  saveConfig();
  await loadHistoryIntoChat(state.sessionId, state.sessionTitle);
  showToast('已开始新会话（上下文已清空）', 'info');
  document.dispatchEvent(new CustomEvent('sessions-refresh'));
}
