/** 应用入口：导航、连接、对话、REST 调试 */

import {
  loadConfig,
  saveConfig,
  state,
  updateStatusBar,
  getSelectedSkillIds,
} from './config.js';
import { initEventLog } from './event-log.js';
import { checkHealth, agentChat, agentConfigApi } from './api.js';
import {
  connectWs,
  disconnectWs,
  sendAsk,
  sendStop,
  ensureConnected,
} from './ws-client.js';
import { sendAskSse, sendStopSse, abortSse } from './sse-client.js';
import { appendUserMessage, clearChatMessages, showToast } from './message-renderer.js';
import { initHitl } from './hitl.js';
import { initSessions, refreshSessionList, loadHistoryIntoChat } from './sessions.js';
import { initMcpPanel, loadMcpTools } from './mcp-panel.js';
import { initSkillPanel, loadInstalledSkills } from './skill-panel.js';
import { initSkillMarketPanel, refreshSkillMarketPanel } from './skill-market-panel.js';
import { initStatsPanel } from './stats-panel.js';
import { initMultiUser } from './multi-user.js';
import { initWorkspacePanel, refreshWorkspacePanel } from './workspace-panel.js';
import { initSubscriptionAgentsPanel, ensureLlmConfigured, openLlmConfigDialog } from './subscription-agents-panel.js';
import { injectSubagentStart, injectSubagentEnd, injectSubagentFlow } from './message-fixtures.js';

function switchTab(tabId) {
  document.querySelectorAll('.nav-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.tab === tabId);
  });
  document.querySelectorAll('.panel').forEach((el) => {
    el.classList.toggle('active', el.id === `panel-${tabId}`);
  });
  document.querySelector('.main-content')?.classList.toggle('chat-mode', tabId === 'chat');
}

function bindIdentityForm() {
  const userIdEl = document.getElementById('input-user-id');
  const tokenEl = document.getElementById('input-user-token');
  const compareEl = document.getElementById('input-compare-user-id');

  if (userIdEl) userIdEl.value = state.userId;
  if (tokenEl) tokenEl.value = state.userToken;
  if (compareEl) compareEl.value = state.compareUserId;

  document.getElementById('btn-save-config')?.addEventListener('click', () => {
    state.userId = userIdEl?.value.trim() || 'guest';
    state.userToken = tokenEl?.value || '';
    state.compareUserId = compareEl?.value.trim() || 'test_user_2';
    saveConfig();
    updateStatusBar();
    loadInstalledSkills();
    import('./ws-client.js').then(({ requestSandboxStatusWhenReady }) =>
      requestSandboxStatusWhenReady(),
    );
    showToast('配置已保存', 'info');
  });

  document.getElementById('btn-check-health')?.addEventListener('click', async () => {
    const el = document.getElementById('health-result');
    try {
      const body = await checkHealth();
      if (el) el.textContent = JSON.stringify(body, null, 2);
      const badge = document.getElementById('status-health');
      if (badge) {
        badge.textContent = `Health: ${body.status}`;
        badge.className = `badge ${body.status === 'ok' ? 'badge-ok' : 'badge-fail'}`;
      }
    } catch (e) {
      if (el) el.textContent = e.message;
    }
  });

  document.getElementById('btn-ws-connect')?.addEventListener('click', () => connectWs());
  document.getElementById('btn-ws-disconnect')?.addEventListener('click', () => disconnectWs());
}

function bindChat() {
  const transportWs = document.getElementById('transport-ws');
  const transportSse = document.getElementById('transport-sse');
  if (state.transport === 'sse') {
    transportSse?.setAttribute('checked', 'checked');
  } else {
    transportWs?.setAttribute('checked', 'checked');
  }

  document.querySelectorAll('input[name="transport"]').forEach((r) => {
    r.addEventListener('change', () => {
      state.transport = r.value;
      saveConfig();
      if (state.transport === 'sse') {
        abortSse();
      } else {
        abortSse();
        connectWs();
      }
      updateStatusBar();
    });
  });

  document.getElementById('btn-send')?.addEventListener('click', () => submitAsk());
  document.getElementById('input-question')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitAsk();
    }
  });

  document.getElementById('btn-stop')?.addEventListener('click', async () => {
    try {
      if (state.transport === 'sse') await sendStopSse();
      else sendStop();
      showToast('已发送 stop', 'info');
    } catch (e) {
      showToast(e.message, 'error');
    }
  });

  document.getElementById('btn-clear-chat')?.addEventListener('click', clearChatMessages);

  document.getElementById('btn-test-subagent-start')?.addEventListener('click', injectSubagentStart);
  document.getElementById('btn-test-subagent-end')?.addEventListener('click', injectSubagentEnd);
  document.getElementById('btn-test-subagent-flow')?.addEventListener('click', injectSubagentFlow);
}

async function submitAsk() {
  const input = document.getElementById('input-question');
  const q = input?.value?.trim();
  if (!q) {
    showToast('请输入问题', 'error');
    return;
  }
  if (!state.sessionReadOnly && !(await ensureLlmConfigured())) {
    showToast('请先配置模型 Key', 'warning');
    await openLlmConfigDialog();
    return;
  }
  if (state.sessionWarming) {
    showToast('会话预热中，消息将排队处理', 'info');
  }
  appendUserMessage(q);
  if (input) input.value = '';
  try {
    if (state.transport === 'sse') {
      await sendAskSse(q);
    } else {
      await ensureConnected();
      sendAsk(q);
    }
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function bindRestDebug() {
  document.getElementById('btn-rest-chat')?.addEventListener('click', async () => {
    const out = document.getElementById('rest-output');
    const q = document.getElementById('rest-question')?.value || '你好';
    if (out) out.textContent = '请求中…';
    try {
      const { body } = await agentChat({
        action: 'ask',
        user_id: state.userId,
        user_token: state.userToken,
        session_id: state.sessionId,
        question: q,
        stream: false,
        capabilities: { mcp: state.selectedMcp, skills: getSelectedSkillIds() },
      });
      if (out) out.textContent = JSON.stringify(body, null, 2);
    } catch (e) {
      if (out) out.textContent = e.message;
    }
  });

  document.getElementById('btn-rest-get-config')?.addEventListener('click', async () => {
    const out = document.getElementById('rest-output');
    const sid = document.getElementById('rest-session-id')?.value || state.sessionId;
    if (!sid) {
      showToast('需要 session_id', 'error');
      return;
    }
    const { body } = await agentConfigApi.get(sid);
    if (out) out.textContent = JSON.stringify(body, null, 2);
  });

  document.getElementById('btn-rest-list-config')?.addEventListener('click', async () => {
    const out = document.getElementById('rest-output');
    const { body } = await agentConfigApi.list(state.userId);
    if (out) out.textContent = JSON.stringify(body, null, 2);
  });
}

function bindNavigation() {
  document.querySelectorAll('.nav-item').forEach((el) => {
    el.addEventListener('click', () => {
      switchTab(el.dataset.tab);
      if (el.dataset.tab === 'workspace') refreshWorkspacePanel();
      if (el.dataset.tab === 'skill-market') refreshSkillMarketPanel();
    });
  });
}

function boot() {
  loadConfig();
  bindNavigation();
  bindIdentityForm();
  bindChat();
  bindRestDebug();
  try {
    initEventLog();
    initHitl();
    initSessions();
    initMcpPanel();
    initSkillPanel();
    initSkillMarketPanel();
    initStatsPanel();
    initMultiUser();
    initWorkspacePanel();
  } catch (e) {
    console.error('[app] 初始化失败', e);
    showToast(`初始化失败: ${e.message}`, 'error');
  }
  updateStatusBar();
  initWorkspacePanel();
  initSubscriptionAgentsPanel();

  switchTab('chat');

  connectWs();

  refreshSessionList();

  if (state.sessionId) {
    loadHistoryIntoChat(state.sessionId, state.sessionTitle);
  }

  loadMcpTools();
  loadInstalledSkills();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
