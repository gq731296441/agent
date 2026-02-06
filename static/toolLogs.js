// 工具日志与状态模块
export function renderToolCall(tool) {
  const container = document.getElementById("statusUpdates");
  let id = tool["tool_call_id"];
  let args = tool["args"];
  if (!id) return;
  const div = document.createElement("div");
  div.className = "tool-item";
  div.id = id;
  div.innerHTML = `
        <div class="tool-header">
            <span>🚀 调用: ${tool.tool_name}</span>
            <span class="status-indicator connected">执行中</span>
        </div>
        <div class="tool-body">
            <div class="tool-label">执行结果:</div>
            <div class="tool-result" id="${id}-result">等待返回...</div>
        </div>
    `;
  container.appendChild(div);
  scrollToBottomLogs();
}

export function updateToolResult(toolResult) {
 const { toolName, result, id } = toolResult;
  const items = document.querySelectorAll(`#${id}`);
  if (items.length === 0) return;
  const lastItem = items[items.length - 1];
  const resDiv = lastItem.querySelector(".tool-result");
  const headerStatus = lastItem.querySelector(".status-indicator");
  if (resDiv) {
    resDiv.textContent = Array.isArray(result)
      ? result[0]?.text || ""
      : String(result);
    if (headerStatus) headerStatus.className = "status-indicator disconnected";
  }
  scrollToBottomLogs();
}

export function addLogItem(text) {
  const container = document.getElementById("statusUpdates");
  const div = document.createElement("div");
  div.className = "status-item";
  div.textContent = `[System] ${text}`;
  container.appendChild(div);
  scrollToBottomLogs();
}

export function scrollToBottomLogs() {
  const el = document.getElementById("tool-logs-tab");
  if (el) el.scrollTop = el.scrollHeight;
}
