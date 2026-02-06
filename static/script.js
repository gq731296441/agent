// 兼容文件：脚本已模块化到 /static/main.js
console.log('静态脚本入口已模块化，请使用 /static/main.js');
// ================= 全局状态与会话管理 =================
let ws;
let isNew = false;
let currentSessionId = null;  // 当前连接的 sessionId
let currentTaskId = null;
let isGenerating = false;
let toolCallCounter = 0;
let isStreaming = false;  // 全局标记是否在流式输出
let rawTextBuffer = ''; // 缓冲区
let currentStreamingDiv = null;  // 当前正在流式输出的消息容器
const activeToolCalls = {};

// 会话列表管理
const sessions = {
    list: [],  // [{id, title, timestamp, messages}, ...]
    currentId: null,  // 当前活跃会话 ID
    
    load: function() {
        const saved = localStorage.getItem("sessions_list");
        this.list = saved ? JSON.parse(saved) : [];
        this.currentId = localStorage.getItem("current_session_id");
    },
    
    save: function() {
        localStorage.setItem("sessions_list", JSON.stringify(this.list));
        localStorage.setItem("current_session_id", this.currentId);
    },
    
    add: function(sessionId, title = null) {
        const newSession = {
            id: sessionId,
            title: title || `会话 ${this.list.length + 1}`,
            timestamp: new Date().toLocaleString()
        };
        this.list.unshift(newSession);  // 新会话放在最前面
        this.currentId = sessionId;
        this.save();
        return newSession;
    },
    
    remove: function(sessionId) {
        this.list = this.list.filter(s => s.id !== sessionId);
        if (this.currentId === sessionId && this.list.length > 0) {
            this.currentId = this.list[0].id;
        } else if (this.list.length === 0) {
            this.currentId = null;
        }
        this.save();
        initSession();
    },
    
    renameSession: function(sessionId, newTitle) {
        const session = this.list.find(s => s.id === sessionId);
        if (session) {
            session.title = newTitle;
            this.save();
        }
    },
    
    getSession: function(sessionId) {
        return this.list.find(s => s.id === sessionId);
    }
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    sessions.load();
    initSession();
    
    // 建立 WebSocket 连接（无需握手消息）
    connectToSession();
    
    // 发送消息按钮
    document.getElementById('input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            send();
        }
    });
});

// 初始化会话列表 UI
function initSession() {
    let container = document.getElementById('sessions-panel');
    // 添加新建会话按钮的点击事件
    container.querySelector('#create-new-session').addEventListener('click', createNewSession);
    const sessionsList = container.querySelector('#sessions-list');
    
    if (sessions.list.length === 0) {
        sessionsList.innerHTML = '<div style="padding: 20px; text-align: center; color: #999; font-size: 14px;">暂无会话，点击下方新建</div>';
    } else {
        sessionsList.innerHTML = '';
        sessions.list.forEach(session => {
            const item = document.createElement('div');
            item.className = `session-item ${session.id === sessions.currentId ? 'active' : ''}`;
            item.id = `session-${session.id}`;
            const info = document.createElement('div');
            info.className = 'session-item-info';
            const titleDiv = document.createElement('div');
            titleDiv.className = 'session-item-title';
            titleDiv.textContent = session.title;
            
            const timeDiv = document.createElement('div');
            timeDiv.className = 'session-item-time';
            timeDiv.textContent = session.timestamp;
            
            info.appendChild(titleDiv);
            info.appendChild(timeDiv);
            
            info.onclick = () => {
                switchSession(session.id);
            };
            
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'session-item-delete';
            deleteBtn.textContent = '删除';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm(`删除会话"${session.title}"？`)) {
                    deleteSession(session.id);
                }
            };
            
            item.appendChild(info);
            item.appendChild(deleteBtn);
            sessionsList.appendChild(item);
        });
        switchSession(sessions.list[0].id); // 默认切换到第一个会话
    }
}

// 加载历史消息
async function loadHistoryMessages(sessionId) {
    if (!sessionId) {
        throw new Error('sessionId 是必填参数');
    }

    // 构建查询参数
    const params = new URLSearchParams();
    params.append('session_id', sessionId);

    const userId = JSON.parse(localStorage.getItem('user_info') || '{}').id;
    params.append('user_id', userId);
    // 完整的 URL
    const url = `/api/sessions/history?${params.toString()}`;

    try {
        const response = await fetch(url, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        }
        });

        if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (data.status && data.messages.length !== 0) {
            const container = document.getElementById('messages');
            data.messages.forEach(msg => {
                const div = document.createElement('div');
                const isAi = msg.role === 'ai';
                
                div.className = `message ${isAi ? 'ai' : (msg.role === 'human' ? 'user' : 'system')}`;
                
                let contentHtml = "";
                if (isAi) {
                    // 使用 marked 解析 Markdown
                    contentHtml = marked.parse(msg.content);
                } else {
                    contentHtml = msg.content.replace(/\n/g, '<br>');
                }
                
                div.innerHTML = `<div>${contentHtml}</div>`;
                container.appendChild(div);
            })
        }else {
            console.log("该会话无历史消息");
        }
        
    } catch (error) {
        if (error.name === 'AbortError') {
        console.warn('获取历史记录请求被取消');
        } else {
        console.error('获取会话历史失败:', error);
        }
        throw error; // 向上抛出，便于调用方处理
    }
}

function toggleSessionPanel() {
    const panel = document.getElementById('sessions-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

function createNewSession() {
    // 后端会生成 sessionId，先使用临时 ID 连接
    currentSessionId = null;
    sessions.currentId = null;
    console.log("开启新的会话:");
    isNew = true;
    document.getElementById('messages').innerHTML = ''; // 清空消息区域
    document.getElementById('statusUpdates').innerHTML = ''; // 清空状态更新区域
}

function connectToSession(isNew = false) {
    // 如果已有连接，先关闭
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
    }

    const protocol = location.protocol === "https:" ? "wss://" : "ws://";
    // 无 session_id 参数的 URL
    const url = `${protocol}${location.host}/ws`;
    ws = new WebSocket(url);
    const statusDot = document.getElementById('connection-status');

    ws.onopen = () => {
        statusDot.className = "status-indicator connected";
        statusDot.title = "已连接";
        console.log("✓ WebSocket 连接已建立");
        // 连接建立后不发送握手消息，等待第一条工作消息时一并发送 user_id 和 session_id
    };

    ws.onclose = () => {
        statusDot.className = "status-indicator disconnected";
        statusDot.title = "连接断开";
        console.log("✗ WebSocket 连接已断开");
    };

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        handleMessage(msg);
    };
    
    ws.onerror = (e) => {
        console.error("WebSocket error:", e);
        statusDot.className = "status-indicator disconnected";
    };
}

// 切换会话
function switchSession(sessionId) {
    sessions.currentId = sessionId;
    currentSessionId = sessionId;
    const sessionsList = document.querySelector('#sessions-list');
    const allSessions = sessionsList.querySelectorAll('.session-item');
    allSessions.forEach(item => {
        item.classList.remove('active');
    });

    const currentItem = document.getElementById(`session-${sessionId}`);
    if (currentItem) {
        currentItem.classList.add('active');
    }
    console.log("切换到会话: " + sessionId);

    document.getElementById('messages').innerHTML = ''; // 清空消息区域
    document.getElementById('statusUpdates').innerHTML = ''; // 清空状态更新区域
    loadHistoryMessages(currentSessionId)
    sessions.save();
    
    // connectToSession(sessionId);
}

// 删除会话
function deleteSession(sessionId) {
    if (currentSessionId === sessionId) {
        // 如果删除的是当前会话，切换到其他会话或创建新的
        if (sessions.list.length > 1) {
            switchSession(sessions.list.find(s => s.id !== sessionId).id);
        }else {
            currentSessionId = null;
            isNew = true;
            sessions.currentId = null;
        }
    }
    sessions.remove(sessionId);
}

// ================= 核心消息处理 =================
function handleMessage(msg) {
    switch (msg.type) {
        // 后端分配新的 session_id
        case "session_allocated":
            const newSessionId = msg.session_id;
            currentSessionId = newSessionId;
            console.log("更新sessionId：" + newSessionId);
            console.log("当前sessionId：" + currentSessionId);
            // 将新会话添加到列表
            const sessionTitle = msg.session_title || `会话 ${sessions.list.length + 1}`;
            sessions.add(newSessionId, sessionTitle);
            initSession();
            break;

        case "user":
            renderChatMessage({ type: "user", content: msg.content });
            break;

        // 流式输出（支持 token-by-token）
        case "assistant_message":
            if (!currentStreamingDiv) {
                createStreamingMessage();
                rawTextBuffer = ''; // 重置缓冲区
            }
            // 1. 将收到的新文本添加到缓冲区
            rawTextBuffer += msg.content;
            appendToStreamingMessage(rawTextBuffer);
            break;

        // HITL 人工审批
        case "interrupt":
            showHitlApproval(msg);
            break;

        case "status":
            addLogItem(msg.content);
            break;

        case "tool_call":
            renderToolCall(msg.content);
            break;

        case "tool_result":
            updateToolResult(msg.tool_name, msg.content, msg["tool_call_id"]);
            break;

        case "file_resource":
            addFileToList(msg.file);
            break;
        case "end":
            finishStreaming();
            rawTextBuffer = ''; // 清理缓冲区
            break;
    }
}

// ================= 流式输出 =================
function createStreamingMessage() {
    const messages = document.getElementById('messages');
    currentStreamingDiv = document.createElement('div');
    currentStreamingDiv.className = 'message ai streaming';
    // currentStreamingDiv.innerHTML = '<span class="cursor">▍</span>';
    currentStreamingDiv.innerHTML = '';
    messages.appendChild(currentStreamingDiv);
    messages.scrollTop = messages.scrollHeight;
    rawTextBuffer = ''; // 确保创建时清空
}

function appendToStreamingMessage(fullRawText) {
    if (!fullRawText) return
    if (!currentStreamingDiv) return;

    const content = currentStreamingDiv.textContent || '';
    // const cleanText = ;
    currentStreamingDiv.innerHTML = marked.parse(fullRawText);
    
    document.getElementById('messages').scrollTop = 
        document.getElementById('messages').scrollHeight;
}

// 结束流式
function finishStreaming() {
    if (currentStreamingDiv) {
        // 清理流式状态
        currentStreamingDiv.className = 'message ai';

        const content = currentStreamingDiv.innerHTML;
        currentStreamingDiv.innerHTML = marked.parse(content);
        currentStreamingDiv.classList.remove('streaming');
        currentStreamingDiv = null;
        isGenerating = false;
        toggleButtons();
    }
}

// ================= HITL 审批 窗口=================
function showHitlApproval(msg) {
    const { request_id, actions } = msg;
    let currentIndex = 0;
    const decisions = new Array(actions.length).fill(null);

    const container = document.createElement('div');
    container.className = 'message hitl-container';
    container.innerHTML = `
        <div class="hitl-track"></div>
        <div class="hitl-footer">
            <button class="nav-btn prev-btn">❮</button>
            <div class="progress-text" style="font-size:12px; color:#999; font-weight:bold;">1 / ${actions.length}</div>
            <button class="nav-btn next-btn">❯</button>
        </div>
    `;

    const track = container.querySelector('.hitl-track');
    const footer = container.querySelector('.hitl-footer');
    if (actions.length === 1) {
        footer.style.display = 'none';
    }

    // 渲染卡片
    actions.forEach((action, idx) => {
        const card = document.createElement('div');
        card.className = 'hitl-card';
        card.innerHTML = `
            <div class="card-content">
                <div class="hitl-card-header" >Agent 请求执行工具</div>
                <h3 style="color:#333; margin:0; padding: 10px 25px 0 25px;">${action.tool_name}</h3>
                <div class="content-stack" style="flex:1; position:relative; padding: 0 20px">
                    <div class="view-panel">
                        <pre style="background:#f4f7f9; padding:15px; border-radius:12px; font-size:12px;">${JSON.stringify(action.args, null, 2)}</pre>
                        <div class="btn-container-view" style="display:flex; gap:10px; justify-content:center; margin-top:20px;">
                            ${action.allowed_decisions.includes('approve') ? `<button class="hitl-btn approve">同意</button>` : ''}
                            ${action.allowed_decisions.includes('edit') ? `<button class="hitl-btn edit">修改参数</button>` : ''}
                            ${action.allowed_decisions.includes('reject') ? `<button class="hitl-btn reject">拒绝</button>` : ''}
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
        
        // --- 交互事件 ---
        const viewPanel = card.querySelector('.view-panel');
        const editPanel = card.querySelector('.edit-panel');
        
        card.querySelector('.approve').onclick = () => handleAction(idx, {type: 'approve'});
        card.querySelector('.reject').onclick = () => handleAction(idx, {type: 'reject'});
        
        card.querySelector('.edit')?.addEventListener('click', () => {
            const form = card.querySelector('.form-scroll');
            renderForm(action.args, form);
            viewPanel.style.display = 'none';
            editPanel.style.display = 'block';
        });

        card.querySelector('.cancel-edit')?.addEventListener('click', () => {
            viewPanel.style.display = 'block';
            editPanel.style.display = 'none';
        });

        card.querySelector('.save-edit').onclick = () => {
            viewPanel.style.display = 'block';
            editPanel.style.display = 'none';
            const newArgs = collectForm(card.querySelector('.form-scroll'));
            handleAction(idx, {type: 'edit', edited_action: {name: action.tool_name, args: newArgs }});
        };

        track.appendChild(card);
    });

    // 同步高度
    function syncHeights() {
        let maxHeight = 0;
        const cards = track.querySelectorAll('.hitl-card');
        
        // 临时显示所有面板以测量真实高度
        cards.forEach(card => {
            const vH = card.querySelector('.view-panel').scrollHeight;
            const eH = card.querySelector('.edit-panel').scrollHeight;
            // 考虑头部高度 + 内边距
            const totalH = Math.max(vH, eH) + 120; 
            if (totalH > maxHeight) maxHeight = totalH;
        });

        track.style.height = maxHeight + 'px';
        container.style.height = (maxHeight + 20) + 'px';
        if (actions.length === 1) {
            container.style.height = (maxHeight + 20) + 'px';
        }else {
            container.style.height = (maxHeight + 80) + 'px';
        }
    }

    // 更新卡片层叠状态
    function updateLayout() {
        const cards = track.querySelectorAll('.hitl-card');
        // 更新页数
        container.querySelector('.progress-text').textContent = `${currentIndex + 1} / ${actions.length}`;
        
        cards.forEach((card, i) => {
            card.className = 'hitl-card';
            card.style.zIndex = 1;
            card.style.opacity = 0;
            card.style.transform = 'translateZ(-300px) scale(0.8)'; // 确保它在后面且缩小

            if (i === currentIndex) {
                card.classList.add('active');
                card.style.zIndex = 10;
                card.style.opacity = 1;
                card.style.transform = 'translateZ(0) translateY(0)';
            } else if (i === currentIndex + 1) {
                card.classList.add('next');
                card.style.zIndex = 5;
                card.style.opacity = 0.7;
                card.style.transform = 'translateZ(-80px) translateY(15px) scale(0.95)';
            } else if (i < currentIndex) {
                card.classList.add('prev');
                card.style.zIndex = 1; // 已处理的卡片
                card.style.opacity = 0;
                card.style.transform = 'translateX(-120%) rotateZ(-10deg)'; // 移出左侧
            }
        });
    }

    function handleAction(idx, decision) {
        decisions[idx] = decision;
        // 添加成功提示
        const card = track.querySelectorAll('.hitl-card')[idx];
        const contentStack = card.querySelector('.content-stack');

        // 隐藏所有操作按钮
        const vPanel = card.querySelector('.btn-container-view');
        const ePanel = card.querySelector('.btn-container-edit');
        vPanel.style.display = 'none';
        ePanel.style.display = 'none';
        
        // 添加人工操作状态
        const statusBadge = document.createElement('div');
        statusBadge.className = `status-badge ${decision.type}`;
        statusBadge.textContent = decision.type === 'approve' || decision.type === 'edit' ? '已同意' : '已拒绝';
        
        // 如果是编辑后同意，需要更新显示参数
        if (decision.type === 'edit' && decision.args) {
            card.querySelector('.view-panel pre').textContent = JSON.stringify(decision.args, null, 2);
        }

        contentStack.appendChild(statusBadge);
        // 视觉反馈
        // card.style.border = decision.type === 'approve' || decision.type === 'edit' ? '2px solid #42d392' : '2px solid #f85032';
        card.style.boxShadow = `0 8px 8px ${decision.type === 'approve' || decision.type === 'edit' ? 'rgba(46,204,113,0.2)' : 'rgba(231,76,60,0.2)'}`;
        
        // 延迟切换到下一张卡片或提交
        setTimeout(() => {
            if (decisions.every(d => d !== null)) {
                submit();
            } else if (currentIndex < actions.length - 1) {
                currentIndex++;
                updateLayout();
            }
        }, 400);
    }

    function renderForm(args, container) {
        container.innerHTML = '';
        Object.entries(args).forEach(([k, v]) => {
            const inputGroup = document.createElement('div');
            inputGroup.style.marginBottom = '12px';
            inputGroup.innerHTML = `
                <label style="display:block; font-size:11px; color:#888; margin-bottom:4px;">${k}</label>
                <input name="${k}" value="${v}" style="width:100%; padding:10px; border:1px solid #e0e0e0; border-radius:8px; font-size:13px; outline:none; transition:border 0.2s;">
            `;
            const input = inputGroup.querySelector('input');
            input.onfocus = () => input.style.border = '1px solid #2981ff';
            input.onblur = () => input.style.border = '1px solid #e0e0e0';
            container.appendChild(inputGroup);
        });
    }

    function collectForm(container) {
        const obj = {};
        container.querySelectorAll('input').forEach(input => obj[input.name] = input.value);
        return obj;
    }

    function submit() {
        const userId = JSON.parse(localStorage.getItem('user_info') || '{}').id || 'guest';
        const userToken = JSON.parse(localStorage.getItem('user_info') || '{}').token || '';
        ws.send(JSON.stringify(
            {
                action: "resume_hitl",
                request_id, decisions,
                user_id: userId,
                user_token: userToken,
                session_id: currentSessionId,  // 新建会话时为 null，继续会话时为真实 ID
            }
        ));
        // console.log(JSON.stringify({ action: "resume_hitl", request_id, decisions }));
    }

    container.querySelector('.next-btn').onclick = () => { if(currentIndex < actions.length-1) { currentIndex++; updateLayout(); } };
    container.querySelector('.prev-btn').onclick = () => { if(currentIndex > 0) { currentIndex--; updateLayout(); } };

    document.getElementById('messages').appendChild(container);
    syncHeights();
    updateLayout();
    document.getElementById('messages').scrollTop = 
        document.getElementById('messages').scrollHeight;
}


// MCP工具相关
function toggleMcpPanel() {
    const panel = document.getElementById('mcp-panel');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
    } else {
        panel.style.display = 'none';
    }
}

// ================= 接口 =================

async function initMcpTools() {
    try {
        const response = await fetch('/api/mcp/tools');
        const data = await response.json();
        if (data.status === 'success') {
            updateMcpUI('connected', data.tools, data.servers);
        }
    } catch (e) {
        console.error("Failed to init MCP", e);
    }
}

function updateMcpUI(status, tools, servers) {
    const indicator = document.getElementById('mcp-status');
    const list = document.getElementById('mcp-tool-list');
    const serverList = document.getElementById('mcp-server-list'); // 新增
    
    // 更新指示器样式
    indicator.className = `status-indicator ${status}`; 
    indicator.title = (status === 'connected') ? `已连接 ${tools.length} 个工具` : `连接失败或未初始化`;
    
    // 更新工具列表
    list.innerHTML = '';
    
    if (tools && tools.length > 0) {
        tools.forEach(t => {
            list.innerHTML += `<li>🛠️ ${t}</li>`;
        });
    } else {
        list.innerHTML = '<li>无可用工具</li>';
    }

    // 渲染已连接服务列表 (mcp-server-list)
    serverList.innerHTML = '';
    const serverKeys = Object.keys(servers);
    if (serverKeys.length > 0) {
        serverKeys.forEach(id => {
            const url = servers[id].url;
            const li = document.createElement('li');
            // 绑定 deleteMcpServer 函数到删除按钮
            li.innerHTML = `
                <span class="server-id">${id}</span> 
                <span class="server-url" title="${url}">${url.split('://')[1].split('/mcp')[0]}</span>
                <button class="delete-btn" onclick="deleteMcpServer('${id}')">删除</button>`;
            serverList.appendChild(li);
        });
    } else {
         serverList.innerHTML = '<li>未配置任何 MCP 服务。</li>';
    }
}

async function addMcpServer() {
    const id = document.getElementById('new-mcp-id').value.trim();
    const port = document.getElementById('new-mcp-port').value.trim();
    
    if(!id || !port) {
        alert("ID 和端口号不能为空");
        return;
    }

    try {
        const response = await fetch('/api/mcp/server', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id, port })
        });
        
        const data = await response.json();
        if (response.ok && data.status === 'success') {
            updateMcpUI('connected', data.tools, data.servers);
            alert(`服务 ${id} 添加成功并重新加载了 Agent！`);
        } else {
            alert(`添加失败: ${data.detail || '未知错误'}`);
        }
    } catch (e) {
        alert("网络请求失败，请检查后端服务是否运行。");
    }
}

async function deleteMcpServer(serverId) {
    if (!confirm(`确定要删除服务 ID: ${serverId} 吗？`)) {
        return;
    }

    try {
        const response = await fetch('/api/mcp/server', {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: serverId }) // 使用 DELETE 接口
        });
        
        const data = await response.json();
        
        if (response.ok && data.status === 'success') {
            // 删除成功后，使用返回的最新工具和服务器列表刷新 UI
            updateMcpUI('connected', data.tools, data.servers);
            alert(`服务 ${serverId} 删除成功并重新加载了 Agent！`);
        } else {
            alert(`删除失败: ${data.detail || '未知错误'}`);
        }
    } catch (e) {
        alert("网络请求失败，请检查后端服务是否运行。");
        console.error("Delete failed:", e);
    }
}

// ================= 对话 =================
function renderChatMessage(msg) {
    const container = document.getElementById('messages');
    const div = document.createElement('div');
    const isAi = msg.type === 'assistant';
    
    div.className = `message ${isAi ? 'ai' : (msg.type === 'user' ? 'user' : 'system')}`;
    
    let contentHtml = "";
    if (isAi) {
        // 使用 marked 解析 Markdown
        contentHtml = marked.parse(msg.content);
    } else {
        contentHtml = msg.content.replace(/\n/g, '<br>');
    }
    
    div.innerHTML = `<div>${contentHtml}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// 渲染历史记录
function renderHistoryMessage(msg) {
    const container = document.getElementById('messages');
    const div = document.createElement('div');
    const isAi = msg.role === 'assistant';
    
    div.className = `message ${isAi ? 'ai' : (msg.role === 'user' ? 'user' : 'system')}`;
    
    let contentHtml = "";
    if (isAi) {
        // 使用 marked 解析 Markdown
        contentHtml = marked.parse(msg.content);
    } else {
        contentHtml = msg.content.replace(/\n/g, '<br>');
    }
    
    div.innerHTML = `<div>${contentHtml}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ================= 文件列表 =================
function addFileToList(fileData) {
    const list = document.getElementById('file-list');
    // 移除空状态
    if (list.querySelector('.empty-state')) {
        list.querySelector('.empty-state').remove();
    }

    const item = document.createElement('div');
    item.className = 'file-item';
    item.innerHTML = `<span class="file-icon">📄</span> ${fileData.name}`;
    
    // 点击事件：打开新 Tab
    item.onclick = () => {
        openFileTab(fileData);
    };
    
    list.prepend(item);
}

// ================= 文件预览 & agent执行状态 =================

// 切换 Tab
window.switchTab = function(tabId) {
    // Header 样式切换
    document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
    const activeHeader = document.querySelector(`.tab-item[data-target="${tabId}"]`) || 
                         (tabId === 'tool-logs-tab' ? document.querySelector('.tab-item:first-child') : null);
    if(activeHeader) activeHeader.classList.add('active');

    // 切换
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
}

// 打开文件 Tab
function openFileTab(fileData) {
    const tabId = `file-${fileData.name.replace(/[^a-zA-Z0-9]/g, '-')}`;
    
    // 如果已存在，直接切换
    if (document.getElementById(tabId)) {
        switchTab(tabId);
        return;
    }

    // 1. 创建 Tab Header
    const headerContainer = document.getElementById('tabs-header');
    const header = document.createElement('div');
    header.className = 'tab-item active';
    header.dataset.target = tabId;
    header.innerHTML = `
        ${fileData.name} 
        <span class="tab-close" onclick="closeTab(event, '${tabId}')">×</span>
    `;
    header.onclick = () => switchTab(tabId);
    
    // 移除其他激活状态
    document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
    headerContainer.appendChild(header);

    // 2. 创建 Tab Content
    const contentContainer = document.getElementById('tabs-content');
    const pane = document.createElement('div');
    pane.id = tabId;
    pane.className = 'tab-pane active';
    
    // 移除其他激活状态
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    
    // 3. 渲染文件内容
    renderFileContent(pane, fileData);
    
    contentContainer.appendChild(pane);
}

// 关闭 Tab
window.closeTab = function(e, tabId) {
    e.stopPropagation(); // 阻止触发切换
    const header = document.querySelector(`.tab-item[data-target="${tabId}"]`);
    const pane = document.getElementById(tabId);
    
    if (header) header.remove();
    if (pane) pane.remove();
    
    // 切换回默认 Tab
    switchTab('tool-logs-tab');
}

// 渲染文件逻辑 (Word, Excel, HTML)
function renderFileContent(container, fileData) {
    const ext = fileData.type.toLowerCase();
    const url = fileData.url;

    container.innerHTML = '<div style="padding:20px;">正在加载文件...</div>';

    if (ext === 'html') {
        container.innerHTML = `<iframe src="${url}" class="file-preview-frame"></iframe>`;
    } 
    else if (ext === 'docx' || ext === 'doc') {
        fetch(url)
            .then(resp => resp.arrayBuffer())
            .then(buffer => mammoth.convertToHtml({arrayBuffer: buffer}))
            .then(result => {
                container.innerHTML = `<div style="padding:20px; background:#fff;">${result.value}</div>`;
            })
            .catch(err => container.innerHTML = `无法预览 Word 文件: ${err}`);
    } 
    else if (ext === 'xlsx' || ext === 'xls') {
        fetch(url)
            .then(resp => resp.arrayBuffer())
            .then(buffer => {
                const wb = XLSX.read(buffer, {type: 'array'});
                const ws = wb.Sheets[wb.SheetNames[0]];
                const html = XLSX.utils.sheet_to_html(ws);
                container.innerHTML = `<div class="excel-preview">${html}</div>`;
            })
            .catch(err => container.innerHTML = `无法预览 Excel 文件: ${err}`);
    } 
    else if (ext === 'txt') {
        fetch(url)
            .then(resp => resp.text()) // 使用 resp.text() 获取纯文本内容
            .then(text => {
                // 将文本内容放入 <pre> 标签以保留格式和换行
                container.innerHTML = `<div style="padding:20px; background:#fff; white-space: pre-wrap;">
                                         <pre style="margin:0; font-family: inherit;">${text}</pre>
                                       </div>`;
            })
            .catch(err => container.innerHTML = `无法预览 TXT 文件: ${err}`);
    }
    else {
        container.innerHTML = `<div style="padding:20px;">不支持预览的文件类型: ${ext} <br> <a href="${url}" target="_blank">点击下载</a></div>`;
    }
}

// ================= 工具日志逻辑 =================
function renderToolCall(tool) {
    const container = document.getElementById('statusUpdates');
    // const id = `tool-${Date.now()}`;
    let id = "";
    if (tool["tool_call_id"]) {
        id = tool["tool_call_id"]
    }else {
        return
    }
    activeToolCalls[tool.tool_name] = id; // 简单映射

    const div = document.createElement('div');
    div.className = 'tool-item';
    div.id = id;
    
    const argsStr = typeof tool.args === 'string' ? tool.args : JSON.stringify(tool.args, null, 2);

    // <div class="tool-label">输入参数:</div>
    // <div class="tool-args">${argsStr}</div>
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



function updateToolResult(toolName, result, id) {
    // 简单查找最后一个匹配的工具
    // 实际项目中应该通过 task_id 或 tool_call_id 精确匹配
    const items = document.querySelectorAll(`#${id}`);
    if (items.length > 0) {
        const lastItem = items[items.length - 1];
        const resDiv = lastItem.querySelector('.tool-result');
        const headerStatus = lastItem.querySelector('.status-indicator');
        
        if (resDiv) {
            resDiv.textContent = Array.isArray(result) ? result[0].text : result;
            if(headerStatus) {
                headerStatus.textContent = "执行完成";
                headerStatus.style.color = "#999";
            }
        }
    }
    scrollToBottomLogs();
}

function addLogItem(text) {
    const container = document.getElementById('statusUpdates');
    const div = document.createElement('div');
    div.className = 'status-item';
    div.textContent = `[System] ${text}`;
    container.appendChild(div);
    scrollToBottomLogs();
}

function scrollToBottomLogs() {
    const el = document.getElementById('tool-logs-tab');
    el.scrollTop = el.scrollHeight;
}

// ================= 交互控制 =================
function send() {
    const input = document.getElementById('input');
    const val = input.value.trim();
    if(!val || isGenerating) return;

    isGenerating = true;
    toggleButtons();
    
    // 每条消息都携带 user_id 和 session_id（若为新建会话则为 null）
    const userId = JSON.parse(localStorage.getItem('user_info') || '{}').id || 'guest';
    
    ws.send(JSON.stringify({
        action: "ask",
        question: val,
        user_id: userId,
        session_id: currentSessionId,  // 新建会话时为 null，继续会话时为真实 ID
        task_id: "task_" + Date.now()
    }));
    
    input.value = "";
}

function stop() {
    // 每条消息都携带 user_id 和 session_id（若为新建会话则为 null）
    const userId = JSON.parse(localStorage.getItem('user_info') || '{}').id || 'guest';
    
    ws.send(JSON.stringify({
        action: "stop",
        user_id: userId,
        session_id: currentSessionId,  // 新建会话时为 null，继续会话时为真实 ID
        task_id: "task_" + Date.now()
    }));
    isGenerating = false;
    toggleButtons();
}

function toggleButtons() {
    document.getElementById('sendBtn').style.display = isGenerating ? 'none' : 'inline-block';
    document.getElementById('stopBtn').style.display = isGenerating ? 'inline-block' : 'none';
}
