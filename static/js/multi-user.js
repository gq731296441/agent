/** 多用户测试：双 WS 连接与同 session 互斥 */

import { getWsUrl, state } from './config.js';
import { logEvent } from './event-log.js';

let wsA = null;
let wsB = null;

function createSecondaryWs(userId, label) {
  const url = getWsUrl();
  const socket = new WebSocket(url);
  const logEl = document.getElementById('multi-user-log');
  const append = (text) => {
    if (logEl) {
      const p = document.createElement('div');
      p.className = 'multi-log-line';
      p.textContent = `[${label}] ${text}`;
      logEl.appendChild(p);
      logEl.scrollTop = logEl.scrollHeight;
    }
  };

  socket.onopen = () => append('已连接');
  socket.onclose = () => append('已断开');
  socket.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'ping') {
        socket.send(JSON.stringify({ type: 'pong', user_id: userId }));
        return;
      }
      append(`${msg.type}: ${JSON.stringify(msg.content).slice(0, 80)}`);
      logEvent(`ws-${label}`, msg, 'in');
    } catch {
      append(ev.data.slice(0, 80));
    }
  };
  return { socket, append, userId };
}

export function initMultiUser() {
  document.getElementById('btn-dual-connect')?.addEventListener('click', () => {
    if (wsA) wsA.socket.close();
    if (wsB) wsB.socket.close();
    wsA = createSecondaryWs(state.userId, 'UserA');
    wsB = createSecondaryWs(state.compareUserId, 'UserB');
  });

  document.getElementById('btn-dual-disconnect')?.addEventListener('click', () => {
    wsA?.socket.close();
    wsB?.socket.close();
    wsA = wsB = null;
  });

  document.getElementById('btn-dual-ask-a')?.addEventListener('click', () => {
    if (!wsA || wsA.socket.readyState !== WebSocket.OPEN) return;
    const q = document.getElementById('dual-question')?.value || '你好';
    const payload = {
      action: 'ask',
      user_id: state.userId,
      user_token: state.userToken || '',
      session_id: state.sessionId,
      question: q,
      capabilities: { mcp: [] },
    };
    wsA.socket.send(JSON.stringify(payload));
    logEvent('ws-UserA', payload, 'out');
    wsA.append(`→ ask: ${q.slice(0, 40)}`);
  });

  document.getElementById('btn-dual-ask-b')?.addEventListener('click', () => {
    if (!wsB || wsB.socket.readyState !== WebSocket.OPEN) return;
    const q = document.getElementById('dual-question')?.value || '你好';
    const payload = {
      action: 'ask',
      user_id: state.compareUserId,
      user_token: state.userToken || '',
      session_id: state.sessionId,
      question: q,
      capabilities: { mcp: [] },
    };
    wsB.socket.send(JSON.stringify(payload));
    logEvent('ws-UserB', payload, 'out');
    wsB.append(`→ ask (same session): ${q.slice(0, 40)}`);
  });

  document.getElementById('btn-clear-multi-log')?.addEventListener('click', () => {
    const logEl = document.getElementById('multi-user-log');
    if (logEl) logEl.innerHTML = '';
  });
}
