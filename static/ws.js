// WebSocket 管理模块
export let ws = null;

export function connectToSession(handleMessage) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.close();
  const protocol = location.protocol === "https:" ? "wss://" : "ws://";
  const url = `${protocol}${location.host}/ws`;
  ws = new WebSocket(url);
  // 将模块内 ws 同步到全局，供旧代码使用 window.ws
  try { window.ws = ws; } catch(e) {}
  const statusDot = document.getElementById("connection-status");

  ws.onopen = () => {
    statusDot.className = "status-indicator connected";
    statusDot.title = "已连接";
    console.log("✓ WebSocket 连接已建立");
  };

  ws.onclose = () => {
    statusDot.className = "status-indicator disconnected";
    statusDot.title = "连接断开";
    console.log("✗ WebSocket 连接已断开");
    try { window.ws = null; } catch(e) {}
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
        return;
      }
      handleMessage(msg);
    } catch (e) {
      console.error(e);
    }
  };

  ws.onerror = (e) => {
    console.error("WebSocket error:", e);
    statusDot.className = "status-indicator disconnected";
  };
}

export function sendRaw(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(obj));
}
