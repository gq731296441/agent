// HITL (人工在环) 审批模块
export function showHitlApproval(msg, wsSendParam) {
  const { request_id, actions } = msg.content;
  let currentIndex = 0;
  const decisions = new Array(actions.length).fill(null);
  const container = document.createElement("div");
  container.className = "message hitl-container";
  container.innerHTML = `
        <div class="hitl-track"></div>
        <div class="hitl-footer">
            <button class="nav-btn prev-btn">❮</button>
            <div class="progress-text" style="font-size:12px; color:#999; font-weight:bold;">1 / ${actions.length}</div>
            <button class="nav-btn next-btn">❯</button>
        </div>
    `;
  const track = container.querySelector(".hitl-track");
  if (actions.length === 1)
    container.querySelector(".hitl-footer").style.display = "none";

  actions.forEach((action, idx) => {
    const card = document.createElement("div");
    card.className = "hitl-card";
    card.innerHTML = `
            <div class="card-content">
                <div class="hitl-card-header">Agent 请求执行工具</div>
                <h3 style="color:#333; margin:0; padding: 10px 25px 0 25px;">${
                  action.tool_name
                }</h3>
                <div class="content-stack" style="flex:1; position:relative; padding: 0 20px">
                    <div class="view-panel">
                        <pre style="background:#f4f7f9; padding:15px; border-radius:12px; font-size:12px;">${JSON.stringify(
                          action.args,
                          null,
                          2
                        )}</pre>
                        <div class="btn-container-view" style="display:flex; gap:10px; justify-content:center; margin-top:20px;">
                            ${
                              action.allowed_decisions.includes("approve")
                                ? `<button class="hitl-btn approve">同意</button>`
                                : ""
                            }
                            ${
                              action.allowed_decisions.includes("edit")
                                ? `<button class="hitl-btn edit">修改参数</button>`
                                : ""
                            }
                            ${
                              action.allowed_decisions.includes("reject")
                                ? `<button class="hitl-btn reject">拒绝</button>`
                                : ""
                            }
                        </div>
                    </div>
                    <div class="edit-panel" style="display:none">
                        <div class="form-scroll" style="max-height:200px; overflow-y:auto; margin-bottom:15px;"></div>
                        <div class="btn-container-edit" style="display:flex; gap:10px;">
                            <button class="hitl-btn approve save-edit" style="flex:1">完成并同意</button>
                            <button class="hitl-btn cancel-edit" style="background:#ddd; color:#666">取消</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    const viewPanel = card.querySelector(".view-panel");
    const editPanel = card.querySelector(".edit-panel");
    card
      .querySelector(".approve")
      ?.addEventListener("click", () => handleAction(idx, { type: "approve" }));
    card
      .querySelector(".reject")
      ?.addEventListener("click", () => handleAction(idx, { type: "reject" }));
    card.querySelector(".edit")?.addEventListener("click", () => {
      renderForm(action.args, card.querySelector(".form-scroll"));
      viewPanel.style.display = "none";
      editPanel.style.display = "block";
    });
    card.querySelector(".cancel-edit")?.addEventListener("click", () => {
      viewPanel.style.display = "block";
      editPanel.style.display = "none";
    });
    card.querySelector(".save-edit")?.addEventListener("click", () => {
      viewPanel.style.display = "block";
      editPanel.style.display = "none";
      const newArgs = collectForm(card.querySelector(".form-scroll"));
      handleAction(idx, {
        type: "edit",
        edited_action: { name: action.tool_name, args: newArgs },
      });
    });
    track.appendChild(card);
  });

  function renderForm(args, container) {
    container.innerHTML = "";
    Object.entries(args).forEach(([k, v]) => {
      const g = document.createElement("div");
      g.style.marginBottom = "12px";
      g.innerHTML = `<label style="display:block; font-size:11px; color:#888; margin-bottom:4px;">${k}</label><input name="${k}" value="${v}" style="width:100%; padding:10px; border:1px solid #e0e0e0; border-radius:8px; font-size:13px; outline:none;">`;
      g.querySelector("input").onfocus = () =>
        (g.querySelector("input").style.border = "1px solid #2981ff");
      g.querySelector("input").onblur = () =>
        (g.querySelector("input").style.border = "1px solid #e0e0e0");
      container.appendChild(g);
    });
  }
  function collectForm(container) {
    const o = {};
    container.querySelectorAll("input").forEach((i) => (o[i.name] = i.value));
    return o;
  }

  function handleAction(idx, decision) {
    decisions[idx] = decision;
    const card = track.querySelectorAll(".hitl-card")[idx];
    const contentStack = card.querySelector(".content-stack");
    card.querySelector(".btn-container-view").style.display = "none";
    card.querySelector(".btn-container-edit").style.display = "none";
    const statusBadge = document.createElement("div");
    statusBadge.className = `status-badge ${decision.type}`;
    statusBadge.textContent =
      decision.type === "approve" || decision.type === "edit"
        ? "已同意"
        : "已拒绝";
    contentStack.appendChild(statusBadge);
    card.style.boxShadow = `0 8px 8px ${
      decision.type === "approve" || decision.type === "edit"
        ? "rgba(46,204,113,0.2)"
        : "rgba(231,76,60,0.2)"
    }`;
    setTimeout(() => {
      if (decisions.every((d) => d !== null)) {
        if (typeof wsSendParam === 'function') wsSendParam({ action: 'resume_hitl', request_id, decisions });
        else if (typeof window.ws !== 'undefined' && window.ws && window.ws.readyState === WebSocket.OPEN) window.ws.send(JSON.stringify({ action: 'resume_hitl', request_id, decisions }));
      } else {
        currentIndex = Math.min(currentIndex + 1, actions.length - 1);
        updateLayout();
      }
    }, 400);
  }

  function updateLayout() {
    const cards = track.querySelectorAll(".hitl-card");
    container.querySelector(".progress-text").textContent = `${
      currentIndex + 1
    } / ${actions.length}`;
    cards.forEach((card, i) => {
      card.style.opacity = i === currentIndex ? 1 : 0;
      card.style.transform =
        i === currentIndex
          ? "translateZ(0) scale(1)"
          : "translateZ(-300px) scale(0.8)";
      card.style.zIndex = i === currentIndex ? 2 : 1;
    });
  }

  // 不再使用内部 wsSend，使用外部传入的 wsSendParam 或全局 window.ws

  container.querySelector(".next-btn").onclick = () => {
    if (currentIndex < actions.length - 1) {
      currentIndex++;
      updateLayout();
    }
  };
  container.querySelector(".prev-btn").onclick = () => {
    if (currentIndex > 0) {
      currentIndex--;
      updateLayout();
    }
  };
  document.getElementById("messages").appendChild(container);
  updateLayout();
}
