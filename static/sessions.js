// 会话管理模块
export const sessions = {
  list: [],
  currentId: null,
  load() {
    const saved = localStorage.getItem("sessions_list");
    this.list = saved ? JSON.parse(saved) : [];
    this.currentId = localStorage.getItem("current_session_id");
  },
  save() {
    localStorage.setItem("sessions_list", JSON.stringify(this.list));
    localStorage.setItem("current_session_id", this.currentId);
  },
  add(sessionId, title = null) {
    const newSession = {
      id: sessionId,
      title: title || `会话 ${this.list.length + 1}`,
      timestamp: new Date().toLocaleString(),
    };
    this.list.unshift(newSession);
    this.currentId = sessionId;
    this.save();
    return newSession;
  },
  remove(sessionId) {
    this.list = this.list.filter((s) => s.id !== sessionId);
    if (this.currentId === sessionId && this.list.length > 0)
      this.currentId = this.list[0].id;
    else if (this.list.length === 0) this.currentId = null;
    this.save();
  },
  renameSession(sessionId, newTitle) {
    const s = this.list.find((x) => x.id === sessionId);
    if (s) {
      s.title = newTitle;
      this.save();
    }
  },
  getSession(sessionId) {
    return this.list.find((s) => s.id === sessionId);
  },
  // 设置会话为激活状态（仅更新 DOM 的选中样式，不重新渲染整个列表）
  setActiveSession(sessionId) {
    this.currentId = sessionId;
    const list = document.querySelectorAll('.session-item');
    list.forEach((el) => {
        if (!el.id) return;
        const id = el.id.replace('session-', '');
        if (sessionId && id === String(sessionId)) el.classList.add('active');
        else el.classList.remove('active');
    });
   }
};

export function initSessionUI(onSwitch) {
  const container = document.getElementById("sessions-panel");

  // 使用 onclick 覆盖旧的处理器，避免重复绑定

  const sessionsList = container.querySelector("#sessions-list");

  if (!sessions.list.length) {
    sessionsList.innerHTML =
      '<div style="padding: 20px; text-align: center; color: #999; font-size: 14px;">暂无会话，点击下方新建</div>';
    return;
  }
  sessionsList.innerHTML = "";
  sessions.list.forEach((session) => {
    const item = document.createElement("div");
    item.className = `session-item ${
      session.id === sessions.currentId ? "active" : ""
    }`;
    item.id = `session-${session.id}`;
    const info = document.createElement("div");
    info.className = "session-item-info";
    const titleDiv = document.createElement("div");
    titleDiv.className = "session-item-title";
    titleDiv.textContent = session.title;
    const timeDiv = document.createElement("div");
    timeDiv.className = "session-item-time";
    timeDiv.textContent = session.timestamp;
    info.appendChild(titleDiv);
    info.appendChild(timeDiv);
    info.onclick = () => onSwitch(session.id, false);
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "session-item-delete";
    deleteBtn.textContent = "删除";
    deleteBtn.onclick = (e) => {
      e.stopPropagation();
      if (confirm(`删除会话"${session.title}"？`))
        onSwitch(session.id, "delete");
    };
    item.appendChild(info);
    item.appendChild(deleteBtn);
    sessionsList.appendChild(item);
  });
}
