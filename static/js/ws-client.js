/** WebSocket 客户端 */

import { getWsUrl, state, getIdentity, getSelectedMcpList, getSelectedSkillIds } from './config.js';
import { logEvent } from './event-log.js';
import { handleAgentMessage, resetChatStream } from './message-renderer.js';

let ws = null;
let reconnectTimer = null;
let onMessageHandler = handleAgentMessage;

export function setMessageHandler(fn) {
  onMessageHandler = fn || handleAgentMessage;
}

export function connectWs() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return ws;
  }
  disconnectWs(false);
  state.wsStatus = 'connecting';
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());

  ws = new WebSocket(getWsUrl());

  ws.onopen = () => {
    state.wsStatus = 'connected';
    import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
    logEvent('ws', { event: 'open' }, 'in');
    requestSandboxStatusWhenReady();
  };

  ws.onclose = (ev) => {
    state.wsStatus = 'disconnected';
    import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
    logEvent('ws', { event: 'close', code: ev.code }, 'in');
    scheduleReconnect();
  };

  ws.onerror = () => {
    state.wsStatus = 'error';
    import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
  };

  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      logEvent('ws', ev.data, 'in');
      return;
    }
    logEvent('ws', msg, 'in');

    if (msg.type === 'ping') {
      state.pingCount += 1;
      sendRaw({ type: 'pong', user_id: state.userId });
      state.pongCount += 1;
      updateHeartbeatUI();
      if (msg.sandbox_status) {
        import('./sessions.js').then(({ applySandboxStatus }) => {
          applySandboxStatus(msg.sandbox_status);
        });
      }
      return;
    }

    if (msg.type === 'sandbox_status') {
      import('./sessions.js').then(({ applySandboxStatus }) => {
        applySandboxStatus(msg.content || msg.sandbox_status);
      });
      return;
    }

    onMessageHandler(msg);
  };

  return ws;
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    if (state.transport === 'ws') connectWs();
  }, 3000);
}

export function disconnectWs(clearTimer = true) {
  if (clearTimer && reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  state.wsStatus = 'disconnected';
  import('./config.js').then(({ updateStatusBar }) => updateStatusBar());
}

export function requestSandboxStatus() {
  const id = getIdentity();
  if (!id.user_id || !ws || ws.readyState !== WebSocket.OPEN) {
    return false;
  }
  sendRaw({ type: 'get_sandbox_status', user_id: id.user_id });
  return true;
}

/** WS 就绪后请求沙箱状态（避免连接未完成时误走 HTTP fallback） */
export function requestSandboxStatusWhenReady(timeoutMs = 5000) {
  return new Promise((resolve) => {
    if (requestSandboxStatus()) {
      resolve(true);
      return;
    }
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      connectWs();
    }
    if (!ws) {
      resolve(false);
      return;
    }
    if (ws.readyState === WebSocket.OPEN) {
      resolve(requestSandboxStatus());
      return;
    }
    const timer = setTimeout(() => resolve(false), timeoutMs);
    ws.addEventListener(
      'open',
      () => {
        clearTimeout(timer);
        resolve(requestSandboxStatus());
      },
      { once: true },
    );
  });
}

export function sendRaw(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectWs();
    throw new Error('WebSocket 未连接，正在重连…');
  }
  logEvent('ws', payload, 'out');
  ws.send(JSON.stringify(payload));
}

function buildPayload(action, extra = {}) {
  const id = getIdentity();
  return {
    action,
    user_id: id.user_id,
    user_token: id.user_token,
    session_id: id.session_id,
    question: extra.question ?? ' ',
    ...extra,
  };
}

export function sendAsk(question) {
  if (state.sessionWarming) {
    import('./message-renderer.js').then(({ showToast }) =>
      showToast('会话预热中，消息将自动排队', 'info'),
    );
  }
  resetChatStream();
  state.isStreaming = true;
  if (state.sessionId) {
    import('./config.js').then(({ patchSessionRun }) =>
      patchSessionRun(state.sessionId, { status: 'streaming' }),
    );
  }
  import('./config.js').then(({ updateChatInputState }) => updateChatInputState());
  const payload = buildPayload('ask', {
    question,
    capabilities: {
      mcp: state.agentKind === 'default' ? getSelectedMcpList() : [],
      skills: state.agentKind === 'default' ? getSelectedSkillIds() : [],
    },
  });
  if (state.agentKind === 'subscription' && state.subscriptionAgentId) {
    payload.agent_id = state.subscriptionAgentId;
  }
  sendRaw(payload);
}

export function sendStop() {
  sendRaw(buildPayload('stop', { question: ' ' }));
  state.isStreaming = false;
}

export function sendResumeHitl(requestId, decisions) {
  sendRaw(
    buildPayload('resume_hitl', {
      request_id: requestId,
      decisions,
      question: ' ',
      capabilities: {
        mcp: state.agentKind === 'default' ? getSelectedMcpList() : [],
        skills: state.agentKind === 'default' ? getSelectedSkillIds() : [],
      },
    }),
  );
  state.isStreaming = true;
}

function updateHeartbeatUI() {
  const el = document.getElementById('status-heartbeat');
  if (el) el.textContent = `ping ${state.pingCount} / pong ${state.pongCount}`;
}

export function getWs() {
  return ws;
}

export function ensureConnected() {
  connectWs();
  if (!ws) {
    return Promise.reject(new Error('WebSocket 初始化失败'));
  }
  if (ws.readyState === WebSocket.OPEN) {
    return Promise.resolve(ws);
  }
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('WS 连接超时')), 5000);
    ws.addEventListener(
      'open',
      () => {
        clearTimeout(t);
        resolve(ws);
      },
      { once: true },
    );
    ws.addEventListener(
      'error',
      () => {
        clearTimeout(t);
        reject(new Error('WS 连接失败'));
      },
      { once: true },
    );
  });
}
