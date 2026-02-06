// MCP 管理模块
export async function initMcpTools() {
  try {
    const response = await fetch("/api/mcp/tools");
    const data = await response.json();
    if (data.status === "success")
      updateMcpUI("connected", data.tools, data.servers);
  } catch (e) {
    console.error("Failed to init MCP", e);
  }
}

export function updateMcpUI(status, tools = [], servers = {}) {
  const indicator = document.getElementById("mcp-status");
  const list = document.getElementById("mcp-tool-list");
  const serverList = document.getElementById("mcp-server-list");
  if (indicator) indicator.className = `status-indicator ${status}`;
  if (indicator)
    indicator.title =
      status === "connected"
        ? `已连接 ${tools.length} 个工具`
        : `连接失败或未初始化`;
  if (list) {
    list.innerHTML = "";
    if (tools && tools.length)
      tools.forEach((t) => (list.innerHTML += `<li>🛠️ ${t}</li>`));
    else list.innerHTML = "<li>无可用工具</li>";
  }
  if (serverList) {
    serverList.innerHTML = "";
    const keys = Object.keys(servers || {});
    if (keys.length)
      keys.forEach((id) => {
        const url = servers[id].url;
        const li = document.createElement("li");
        li.innerHTML = `<span class="server-id">${id}</span> <span class="server-url" title="${url}">${
          url.split("://")[1].split("/mcp")[0]
        }</span> <button class="delete-btn" onclick="deleteMcpServer('${id}')">删除</button>`;
        serverList.appendChild(li);
      });
    else serverList.innerHTML = "<li>未配置任何 MCP 服务。</li>";
  }
}

export async function addMcpServer(id, port) {
  if (!id || !port) {
    alert("ID 和端口号不能为空");
    return;
  }
  try {
    const response = await fetch("/api/mcp/server", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, port }),
    });
    const data = await response.json();
    if (response.ok && data.status === "success") {
      updateMcpUI("connected", data.tools, data.servers);
      alert(`服务 ${id} 添加成功并重新加载了 Agent！`);
    } else alert(`添加失败: ${data.detail || "未知错误"}`);
  } catch (e) {
    alert("网络请求失败，请检查后端服务是否运行。");
  }
}

export async function deleteMcpServer(serverId) {
  if (!confirm(`确定要删除服务 ID: ${serverId} 吗？`)) return;
  try {
    const response = await fetch("/api/mcp/server", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: serverId }),
    });
    const data = await response.json();
    if (response.ok && data.status === "success") {
      updateMcpUI("connected", data.tools, data.servers);
      alert(`服务 ${serverId} 删除成功并重新加载了 Agent！`);
    } else alert(`删除失败: ${data.detail || "未知错误"}`);
  } catch (e) {
    alert("网络请求失败，请检查后端服务是否运行。");
    console.error("Delete failed:", e);
  }
}

export function toggleMcpPanel() {
    const panel = document.getElementById('mcp-panel');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
    } else {
        panel.style.display = 'none';
    }
}
