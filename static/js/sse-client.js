/** SSE 流式客户端 */

import { getApiBase, state, getIdentity, getSelectedMcpList, getSelectedSkillIds } from './config.js';
import { logEvent } from './event-log.js';
import { handleAgentMessage, resetChatStream } from './message-renderer.js';
import { setSession } from './config.js';

let abortController = null;

export async function sendAskSse(question) {
  abortSse();
  resetChatStream();
  state.isStreaming = true;
  abortController = new AbortController();

  const id = getIdentity();
  const payload = {
    action: 'ask',
    user_id: id.user_id,
    user_token: id.user_token,
    session_id: id.session_id,
    question,
    stream: true,
    capabilities: {
      mcp: state.agentKind === 'default' ? getSelectedMcpList() : [],
      skills: state.agentKind === 'default' ? getSelectedSkillIds() : [],
    },
  };
  if (state.agentKind === 'subscription' && state.subscriptionAgentId) {
    payload.agent_id = state.subscriptionAgentId;
  }

  logEvent('sse', payload, 'out');

  const res = await fetch(`${getApiBase()}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal: abortController.signal,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || `SSE HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data:')) continue;
      const jsonStr = line.slice(5).trim();
      if (!jsonStr) continue;
      try {
        const msg = JSON.parse(jsonStr);
        logEvent('sse', msg, 'in');
        if (msg.type === 'session_allocated') {
          const c = msg.content || {};
          setSession(c.session_id || msg.session_id, c.session_title);
        }
        handleAgentMessage(msg);
      } catch (e) {
        logEvent('sse', { parseError: e.message, raw: jsonStr }, 'in');
      }
    }
  }
  state.isStreaming = false;
}

export async function sendStopSse() {
  const id = getIdentity();
  await fetch(`${getApiBase()}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: 'stop',
      user_id: id.user_id,
      user_token: id.user_token,
      session_id: id.session_id,
      question: ' ',
    }),
  });
  abortSse();
  state.isStreaming = false;
}

export async function sendResumeHitlSse(requestId, decisions) {
  abortSse();
  state.isStreaming = true;
  abortController = new AbortController();

  const id = getIdentity();
  const payload = {
    action: 'resume_hitl',
    user_id: id.user_id,
    user_token: id.user_token,
    session_id: id.session_id,
    request_id: requestId,
    decisions,
    question: ' ',
    stream: true,
    capabilities: {
      mcp: state.agentKind === 'default' ? getSelectedMcpList() : [],
      skills: state.agentKind === 'default' ? getSelectedSkillIds() : [],
    },
  };

  logEvent('sse', payload, 'out');

  const res = await fetch(`${getApiBase()}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal: abortController.signal,
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data:')) continue;
      const msg = JSON.parse(line.slice(5).trim());
      logEvent('sse', msg, 'in');
      handleAgentMessage(msg);
    }
  }
  state.isStreaming = false;
}

export function abortSse() {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
}
