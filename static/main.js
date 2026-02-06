// 前端模块化入口
import { connectToSession, sendRaw, ws as wsVar } from "./ws.js";
import { sessions, initSessionUI } from "./sessions.js";
import * as msg from "./messages.js";
import * as mcp from "./mcp.js";
import * as logs from "./toolLogs.js";
import * as filePreview from "./filePreview.js";
import { showHitlApproval } from "./hitl.js";

// 共享全局状态
window.isNew = false;
window.currentSessionId = null;
window.currentTaskId = null;
window.isGenerating = false;
window.toolCallCounter = 0;
window.isStreaming = false;

// 将部分模块方法注入到上下文供 messages 模块 回调 使用
const ctx = {
  currentSessionId: window.currentSessionId, // 当前会话ID
  sessions, // 会话管理对象
  initSessionUI: (cb) => initSessionUI(handleSessionSwitch), // 初始化会话UI
  onSwitch: handleSessionSwitch, // 会话切换回调
  wsSend: (o) => {
    // WebSocket发送消息（使用模块化 sendRaw）
    try {
      sendRaw(o);
    } catch (e) {
      console.error("wsSend failed", e);
    }
  },
  addLogItem: logs.addLogItem, // 添加工具日志项
  updateToolResult: logs.updateToolResult, // 更新工具结果
};

// 会话切换处理函数
function handleSessionSwitch(sessionId, action) {
  // 处理删除会话请求：删除后重新渲染会话列表
  if (action === "delete") {
    sessions.remove(sessionId);
    // 更新会话列表的选中样式
    sessions.setActiveSession("");
    initSessionUI(handleSessionSwitch);
    return;
  }

  // 处理新建会话请求: 仅重置本地状态，不触发会话列表重渲染（避免嵌套调用）
  if (action === true || sessionId === null) {
    window.currentSessionId = null;
    window.isNew = true;
    console.log("创建新会话");
    const msgs = document.getElementById("messages");
    const status = document.getElementById("statusUpdates");
    if (msgs) msgs.innerHTML = "";
    if (status) status.innerHTML = "";
    sessions.currentId = null;
    // 更新会话列表的选中样式
    sessions.setActiveSession("");
    return;
  }

  // 切换到指定会话（不在此处重新渲染会话列表以避免重复绑定）
  window.currentSessionId = sessionId;
  sessions.currentId = sessionId;
  // 更新会话列表的选中样式
  sessions.setActiveSession(sessionId);
  console.log(`切换到会话：${window.currentSessionId}`);
  const msgs = document.getElementById("messages");
  const status = document.getElementById("statusUpdates");
  if (msgs) msgs.innerHTML = "";
  if (status) status.innerHTML = "";
  msg.loadHistoryMessages(sessionId);
  sessions.save();
}

function handleMessage(m) {
  msg.handleMessage(m, ctx);
}

// 连接到 WebSocket 服务器并处理消息
document.addEventListener("DOMContentLoaded", () => {
  sessions.load();
  initSessionUI(handleSessionSwitch); // 初始化会话列表
  // 如果存在会话则默认加载第一个，会话列表为空时跳过
  if (sessions.list && sessions.list.length > 0) {
    handleSessionSwitch(sessions.list[0].id, null);
  }
  connectToSession(handleMessage); // 连接ws，发送/接收后端消息
  // 绑定输入快捷键
  const inputEl = document.getElementById("input");
  if (inputEl)
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
  inputEl.addEventListener("input", () => {
    const textarea = document.getElementById("input");

    // 先将高度设为 auto，让 scrollHeight 能正确计算内容所需高度
    textarea.style.height = "auto";

    // 设置为内容实际所需高度（加上内边距）
    textarea.style.height = textarea.scrollHeight + "px";

    // 当超过 max-height 时，恢复滚动条
    if (textarea.scrollHeight > 200) {
      // 200 与 CSS max-height 一致
      textarea.style.overflowY = "auto";
    } else {
      textarea.style.overflowY = "hidden";
    }
  });
  // 初始化 MCP UI
  //   mcp.initMcpTools();
});

// 兼容旧全局函数
window.send = function () {
  const input = document.getElementById("input");
  if (!input) return;
  const val = input.value.trim();
  if (!val || window.isGenerating) return;
  window.isGenerating = true;
  toggleButtons();
  // 从本地存储中获取用户ID
  const userId = (JSON.parse(localStorage.getItem("user_info") || "{}").id || "guest");
  const userToken =
    JSON.parse(localStorage.getItem("user_info") || "{}").token || "";
  try {
    sendRaw({
      action: "ask",
      question: val,
      user_id: userId,
      user_token: userToken,
      session_id: window.currentSessionId,
      capabilities: {
        mcp: [      
             {
                "name": "mrm",
                "type": "http",
                "url": "http://127.0.0.1:6002/mcp",
                "id": "1f0ec672-eb05-6b92-bfff-18be25e16466",
            },
            {
                "name": "AI空间搜索",
                "type": "http",
                "url": "http://10.57.83.61:6002/mcp",
                "id": "1f0ec672-eb05-6b92-bfff-950953b90a31",
            }
        ]
      }
    });
  } catch (e) {
    console.error("send failed", e);
  }
  input.value = "";

  // 先将高度设为 auto，让 scrollHeight 能正确计算内容所需高度
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
  input.style.overflowY = "hidden";
};

window.stop = function () {
  const userId = (JSON.parse(localStorage.getItem("user_info") || "{}").id || "guest");
  const userToken =
    JSON.parse(localStorage.getItem("user_info") || "{}").token || "";
  try {
    sendRaw({
      action: "stop",
      user_id: userId,
      user_token: userToken,
      session_id: window.currentSessionId,
      task_id: "task_" + Date.now(),
      capabilities: {
        mcp: [      
             {
                "name": "mrm",
                "type": "http",
                "url": "http://127.0.0.1:6002/mcp",
                "id": "1f0ec672-eb05-6b92-bfff-18be25e16466",
            },
            {
                "name": "AI空间搜索",
                "type": "http",
                "url": "http://10.57.83.61:6002/mcp",
                "id": "1f0ec672-eb05-6b92-bfff-950953b90a31",
            }
        ]
      }
    });
  } catch (e) {
    console.error("stop failed", e);
  }
  window.isGenerating = false;
  toggleButtons();
};

window.addNewSessionButton = function () {
  handleSessionSwitch(null, true);
};

// 切换发送/停止按钮显示状态
window.toggleButtons = function () {
  const s = window.isGenerating;
  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  if (sendBtn) sendBtn.style.display = s ? "none" : "inline-block";
  if (stopBtn) stopBtn.style.display = s ? "inline-block" : "none";
};

// 切换会话面板显示状态
window.toggleSessionPanel = function () {
  const panel = document.getElementById("sessions-panel");
  panel.style.display = panel.style.display === "none" ? "block" : "none";
  const seesionContainer = document.getElementById("session-container");
  seesionContainer.style.width =
    panel.style.display === "none" ? "50px" : "250px";
  const text = document.getElementById("add-new-session-text");
  text.style.display = panel.style.display === "none" ? "none" : "inline";
  const container = document.getElementById("create-new-session");
  container.style.width = panel.style.display === "none" ? "32px" : "120px";
};

// 暴露部分工具与 UI 兼容函数
window.addMcpServer = function () {
  const id = document.getElementById("new-mcp-id").value.trim();
  const port = document.getElementById("new-mcp-port").value.trim();
  mcp.addMcpServer(id, port);
};
window.deleteMcpServer = function (id) {
  mcp.deleteMcpServer(id);
};
window.toggleMcpPanel = function () {
  mcp.toggleMcpPanel();
};
window.switchTab = filePreview.switchTab;
window.closeTab = filePreview.closeTab;
window.openFileTab = filePreview.openFileTab;

console.log("模块化前端已加载 (main.js)");
