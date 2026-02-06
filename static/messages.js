// 消息处理与渲染模块
import { showHitlApproval } from "./hitl.js";
import { renderToolCall } from "./toolLogs.js";
import { addFileToList } from "./filePreview.js";

export let currentAgentContainer = null;
export let currentTextContainer = null;
export let rawTextBuffer = "";
export let toolResultMap = new Map();

// 新增：用于存储思考内容和正式回答的容器引用
let thinkingContainer = null;  // 折叠框容器 <details>
let thinkingContentArea = null; // 折叠框内容区域
let currentThinkingBlock = null; // 当前正在进行的思考块
let currentReasoningBuffer = ""; // 当前思考内容的累加缓冲区
let answerBox = null;
let completedThinkingCount = 0; // 已完成的思考块计数

// 分页加载相关状态
let historyCurrentPage = 1;       // 当前页码
let historyHasMore = true;        // 是否还有更多历史消息
let historyIsLoading = false;     // 是否正在加载
let historySessionId = null;      // 当前加载的会话ID
const HISTORY_PAGE_SIZE = 50;     // 每页消息数量

// 滚动到底部
function scrollToBottom() {
    const messages = document.getElementById("messages");
    messages.scrollTop = messages.scrollHeight;
}

// 创建工具调用卡片 DOM
function createToolCardDOM(toolData) {
    const toolDiv = document.createElement("div");
    toolDiv.className = "tool-call-card";
    toolDiv.innerHTML = `
        <span style="font-size: 12px;">
          <svg t="1768468598949" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="5477" width="16" height="16"><path d="M609.382 202.144c38.468-14.749 76.107-22.971 110.164-22.08 34.287 0.898 66.834 11.195 91.094 35.455 24.259 24.259 34.557 56.807 35.455 91.093 0.891 34.057-7.332 71.696-22.08 110.164-11.885 30.999-28.37 63.47-48.931 96.317 20.757 33.076 37.384 65.781 49.351 96.992 14.748 38.468 22.971 76.107 22.08 110.164-0.898 34.287-11.195 66.834-35.454 91.094-24.26 24.259-56.807 34.556-91.094 35.454-34.057 0.892-71.696-7.33-110.164-22.079-31.238-11.977-63.974-28.623-97.079-49.406-32.72 20.453-65.063 36.859-95.945 48.699-38.468 14.748-76.107 22.971-110.164 22.08-34.286-0.898-66.834-11.196-91.094-35.455-24.258-24.26-34.556-56.806-35.454-91.093-0.891-34.057 7.332-71.696 22.08-110.164 11.882-30.989 28.358-63.45 48.91-96.285-20.356-32.606-36.69-64.835-48.49-95.612-14.748-38.468-22.97-76.107-22.079-110.164 0.898-34.286 11.195-66.834 35.454-91.093 24.26-24.26 56.807-34.557 91.094-35.454 34.057-0.892 71.696 7.331 110.164 22.08 30.75 11.789 62.948 28.105 95.525 48.435 32.962-20.66 65.551-37.216 96.657-49.142z m125.616 368.652c-22.647 29.635-48.355 59.054-76.75 87.449-28.502 28.502-58.038 54.296-87.787 77.006 21.504 12.15 42.405 22.098 62.254 29.708 33.459 12.828 62.489 18.464 85.577 17.859 22.858-0.598 37.935-7.151 47.514-16.73 9.578-9.579 16.133-24.656 16.731-47.514 0.605-23.088-5.032-52.118-17.86-85.578-7.604-19.832-17.542-40.715-29.679-62.2z m-443.872 0.105c-11.929 21.2-21.714 41.808-29.222 61.39-12.828 33.459-18.464 62.489-17.859 85.577 0.599 22.858 7.153 37.934 16.731 47.513 9.579 9.578 24.656 16.133 47.514 16.731 23.088 0.605 52.118-5.031 85.577-17.859 19.52-7.484 40.056-17.231 61.184-29.108-29.389-22.512-58.555-48.033-86.717-76.195-28.586-28.586-54.45-58.21-77.208-88.049z m221.67-242.628c-33.243 23.776-66.958 52.222-99.628 84.892-32.782 32.783-61.312 66.616-85.137 99.971 23.91 33.545 52.582 67.585 85.558 100.56 32.532 32.533 66.1 60.876 99.207 84.59 33.427-23.857 67.341-52.44 100.197-85.296 32.744-32.744 61.244-66.538 85.052-99.854-23.723-33.126-52.081-66.714-84.632-99.265-32.994-32.994-67.054-61.68-100.617-85.598z m0.345 98.047c47.949 0 86.82 38.871 86.82 86.821 0 47.949-38.871 86.82-86.82 86.82-47.95 0-86.821-38.871-86.821-86.82 0-47.95 38.871-86.821 86.821-86.821z m0 64c-12.603 0-22.821 10.218-22.821 22.821 0 12.603 10.217 22.82 22.821 22.82 12.603 0 22.82-10.217 22.82-22.82 0-12.604-10.217-22.821-22.82-22.821z m204.73-246.278c-23.088-0.604-52.119 5.032-85.578 17.86-19.699 7.553-40.436 17.409-61.77 29.435 29.871 22.776 59.528 48.662 88.145 77.279 28.192 28.192 53.737 57.392 76.266 86.813 11.974-21.262 21.793-41.928 29.322-61.565 12.828-33.459 18.464-62.49 17.859-85.578-0.598-22.858-7.152-37.935-16.73-47.514-9.579-9.578-24.656-16.131-47.514-16.73z m-323.583 18.565c-33.459-12.828-62.489-18.463-85.577-17.859-22.858 0.599-37.935 7.153-47.514 16.731-9.578 9.579-16.132 24.656-16.73 47.514-0.605 23.088 5.031 52.118 17.859 85.577 7.433 19.386 17.098 39.776 28.864 60.751 22.641-29.622 48.34-59.028 76.723-87.411 28.276-28.275 57.565-53.888 87.074-76.466-20.957-11.754-41.329-21.41-60.699-28.837z" fill="#000000" p-id="5478"></path></svg>
        </span>
        <span style="font-weight: 500;">正在使用: ${toolData.tool_name}</span>
        <span style="margin-left:auto; color: #888; font-size: 12px;">查看结果 ›</span>
    `;
    
    // Hover effect
    toolDiv.onmouseover = () => { toolDiv.style.background = "#e4e7ed"; toolDiv.style.borderColor = "#cdd2d9"; };
    toolDiv.onmouseout = () => { toolDiv.style.background = "#fff"; toolDiv.style.borderColor = "#fff"; };
    
    toolDiv.onclick = (e) => {
        e.stopPropagation();
        // 高亮当前选中的工具
        document.querySelectorAll('.tool-call-card').forEach(el => el.style.borderLeft = "1px solid #dde1e6");
        toolDiv.style.borderLeft = "4px solid #007acc";
        showToolResult(toolData.tool_call_id);
    };
    return toolDiv;
}

// 确保当前有 Agent 容器
export function ensureAgentContainer() {
  const messages = document.getElementById("messages");
  if (!currentAgentContainer) {
    currentAgentContainer = document.createElement("div");
    currentAgentContainer.className = "message ai streaming";
    messages.appendChild(currentAgentContainer);
    scrollToBottom();
  }
  return currentAgentContainer;
}

// 追加流式文本（正式回答）
export function appendTextChunk(textChunk) {
  const container = ensureAgentContainer();
  rawTextBuffer += textChunk;

  if (!currentTextContainer) {
    currentTextContainer = document.createElement("div");
    currentTextContainer.className = "ai-response-wrapper";
    container.appendChild(currentTextContainer);
  }

  // 直接渲染正式回答（思考内容由 reasoning 类型消息处理）
  renderAnswer(rawTextBuffer);
  scrollToBottom();
}

/**
 * 追加思考内容（从 reasoning 类型消息）
 */
export function appendReasoningChunk(reasoningText) {
  const container = ensureAgentContainer();

  if (!currentTextContainer) {
    currentTextContainer = document.createElement("div");
    currentTextContainer.className = "ai-response-wrapper";
    container.appendChild(currentTextContainer);
  }

  // 确保思考折叠框存在
  ensureThinkingContainer();
  
  // 累加思考内容到缓冲区
  currentReasoningBuffer += reasoningText;
  
  // 更新当前正在进行的思考块
  if (!currentThinkingBlock) {
    currentThinkingBlock = document.createElement("div");
    currentThinkingBlock.className = "thinking-block in-progress";
    currentThinkingBlock.innerHTML = `<div class="thinking-block-header">思考中...</div><div class="thinking-block-content"></div>`;
    thinkingContentArea.appendChild(currentThinkingBlock);
  }
  currentThinkingBlock.querySelector(".thinking-block-content").textContent = currentReasoningBuffer;
  
  // 更新折叠框标题
  updateThinkingTitle(true);
  scrollToBottom();
}

/**
 * 完成当前思考块（reasoning 消息结束时调用）
 */
export function finishReasoningBlock() {
  if (currentThinkingBlock && thinkingContentArea) {
    // 将正在进行的思考块转为已完成状态
    currentThinkingBlock.classList.remove("in-progress");
    currentThinkingBlock.classList.add("completed");
    completedThinkingCount++;
    currentThinkingBlock.querySelector(".thinking-block-header").textContent = `思考 #${completedThinkingCount}`;
    currentThinkingBlock = null;
    currentReasoningBuffer = ""; // 重置累加缓冲区
    
    // 更新折叠框标题
    updateThinkingTitle(false);
  }
}

/**
 * 确保思考折叠框存在
 */
function ensureThinkingContainer() {
  if (!thinkingContainer) {
    thinkingContainer = document.createElement("details");
    thinkingContainer.className = "thinking-process";
    thinkingContainer.open = true; // 默认展开，方便查看进度
    
    const summary = document.createElement("summary");
    summary.className = "thinking-title";
    summary.textContent = "思考过程";
    thinkingContainer.appendChild(summary);
    
    thinkingContentArea = document.createElement("div");
    thinkingContentArea.className = "thinking-content-area";
    thinkingContainer.appendChild(thinkingContentArea);
    
    currentTextContainer.appendChild(thinkingContainer);
  }
}

/**
 * 更新思考折叠框标题
 */
function updateThinkingTitle(isInProgress) {
  if (thinkingContainer) {
    const summary = thinkingContainer.querySelector("summary");
    const totalBlocks = completedThinkingCount + (isInProgress ? 1 : 0);
    summary.textContent = isInProgress 
      ? `思考中...`
      : `思考过程`;
  }
}

/**
 * 渲染正式回答
 */
function renderAnswer(answer) {
  if (answer) {
    if (!answerBox) {
      answerBox = document.createElement("div");
      answerBox.className = "markdown-body";
      currentTextContainer.appendChild(answerBox);
      // 如果答案开始了，可以把思考框折叠
      if (thinkingContainer) {
        thinkingContainer.open = false;
      }
    }
    answerBox.innerHTML = marked.parse(answer);
  }
}

/**
 * HTML 转义函数
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * 渲染完整消息（用于历史消息渲染，不再解析<think>标签）
 * 思考内容由 reasoning 类型消息单独处理
 */
export function renderFullMessage(content) {
  const wrapper = document.createElement("div");
  wrapper.className = "ai-response-wrapper";

  // 直接渲染为正式回答（思考内容由 reasoning 类型消息处理）
  if (content) {
    const answerBox = document.createElement("div");
    answerBox.className = "markdown-body";
    answerBox.innerHTML = marked.parse(content);
    wrapper.appendChild(answerBox);
  }

  return wrapper;
}

// 追加工具调用卡片
export function appendToolCallCard(toolData) {
  const container = ensureAgentContainer();
  
  if (!toolData.tool_call_id) {
    return;
  }
  
  // 确保有 currentTextContainer
  if (!currentTextContainer) {
    currentTextContainer = document.createElement("div");
    currentTextContainer.className = "ai-response-wrapper";
    container.appendChild(currentTextContainer);
  }
  
  const card = createToolCardDOM(toolData);
  
  // 如果存在思考折叠框，将工具调用卡片添加到折叠框内
  if (thinkingContentArea) {
    // 创建一个工具调用包装器，便于区分
    const toolWrapper = document.createElement("div");
    toolWrapper.className = "thinking-tool-wrapper";
    toolWrapper.appendChild(card);
    thinkingContentArea.appendChild(toolWrapper);
  } else {
    // 如果没有思考框，创建一个并添加工具到其中
    if (!thinkingContainer) {
      thinkingContainer = document.createElement("details");
      thinkingContainer.className = "thinking-process";
      thinkingContainer.open = true;
      
      const summary = document.createElement("summary");
      summary.className = "thinking-title";
      summary.textContent = "思考过程";
      thinkingContainer.appendChild(summary);
      
      thinkingContentArea = document.createElement("div");
      thinkingContentArea.className = "thinking-content-area";
      thinkingContainer.appendChild(thinkingContentArea);
      
      currentTextContainer.appendChild(thinkingContainer);
    }
    
    const toolWrapper = document.createElement("div");
    toolWrapper.className = "thinking-tool-wrapper";
    toolWrapper.appendChild(card);
    thinkingContentArea.appendChild(toolWrapper);
  }
  
  // 注意：不要清空 rawTextBuffer，保持思考块计数的一致性
  // 如果当前有正在进行的思考块，移除它（因为工具调用会打断思考）
  if (currentThinkingBlock && currentThinkingBlock.parentNode) {
    currentThinkingBlock.remove();
    currentThinkingBlock = null;
  }
  
  scrollToBottom();
}

// 结束流式输出
export function finishStreaming() {
  // 先完成任何正在进行的思考块
  if (currentThinkingBlock && thinkingContentArea) {
    currentThinkingBlock.classList.remove("in-progress");
    currentThinkingBlock.classList.add("completed");
    completedThinkingCount++;
    currentThinkingBlock.querySelector(".thinking-block-header").textContent = `思考 #${completedThinkingCount}`;
    // 更新折叠框标题为结束状态
    if (thinkingContainer) {
      const summary = thinkingContainer.querySelector("summary");
      summary.textContent = `思考过程)`;
    }
  }
  
  if (currentAgentContainer) {
    currentAgentContainer.classList.remove("streaming");
  }
  currentAgentContainer = null;
  currentTextContainer = null;
  rawTextBuffer = "";
  thinkingContainer = null;
  thinkingContentArea = null;
  currentThinkingBlock = null;
  currentReasoningBuffer = ""; // 重置累加缓冲区
  answerBox = null;
  completedThinkingCount = 0;
  isGenerating = false;
  toggleButtons();
}

// 展示工具结果到 Workspace Panel
export function showToolResult(toolCallId) {
  const result = toolResultMap.get(toolCallId);
  const panel = document.getElementById("statusUpdates");
  if (!panel) return;

  panel.innerHTML = ""; // 清空面板

  if (result) {
    const header = document.createElement("div");
    header.style =
      "font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #eee; padding-bottom: 5px; color: #333;";
    header.textContent = `${result.tool_name} 执行结果:`;

    const body = document.createElement("div");
    body.style =
      "white-space: pre-wrap; font-family: 'Menlo', 'Monaco', 'Courier New', monospace; background: #f8f9fa; padding: 12px; border-radius: 6px; font-size: 13px; color: #24292e; border: 1px solid #e1e4e8; overflow-x: auto;";
    
    let resText = result.tool_result[0].text;
    body.textContent = resText;

    panel.appendChild(header);
    panel.appendChild(body);

    // 尝试切换Tab
    if (window.switchTab) {
        window.switchTab('tool-logs-tab');
    } else {
        // Fallback implementation if global not available
        const headerTab = document.querySelector(`.tab-item[onclick*='tool-logs-tab']`);
        const contentTab = document.getElementById('tool-logs-tab');
        if(headerTab && contentTab) {
             document.querySelectorAll('.tab-item').forEach(t => t.classList.remove('active'));
             document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
             headerTab.classList.add('active');
             contentTab.classList.add('active');
        }
    }
  } else {
    panel.innerHTML =
      '<div style="color:#999; padding:10px;">未找到该工具的执行结果数据</div>';
  }
}

/**
 * 渲染历史消息（分组逻辑）- 保持与流式输出一致的样式
 * @param {Array} messages - 消息数组
 * @param {boolean} prepend - 是否在顶部插入（用于加载更多历史）
 */
function renderHistory(messages, prepend = false) {
    const container = document.getElementById("messages");
    
    // 如果不是追加模式，清空容器和工具结果映射
    if (!prepend) {
        container.innerHTML = "";
        toolResultMap.clear();
    }
    
    // 创建一个临时容器来存放新渲染的消息
    const tempContainer = document.createElement("div");
    
    let currentAgentDiv = null;
    let currentWrapper = null;      // ai-response-wrapper
    let currentThinkingDetails = null;  // 思考折叠框
    let currentThinkingArea = null;     // 思考内容区域
    let thinkingBlockIndex = 0;         // 思考块索引

    // 辅助函数：确保有思考折叠框
    function ensureHistoryThinkingContainer() {
        if (!currentThinkingDetails) {
            currentThinkingDetails = document.createElement("details");
            currentThinkingDetails.className = "thinking-process";
            
            const summary = document.createElement("summary");
            summary.className = "thinking-title";
            summary.textContent = "思考过程";
            currentThinkingDetails.appendChild(summary);
            
            currentThinkingArea = document.createElement("div");
            currentThinkingArea.className = "thinking-content-area";
            currentThinkingDetails.appendChild(currentThinkingArea);
            
            currentWrapper.appendChild(currentThinkingDetails);
        }
    }

    // 辅助函数：更新思考折叠框标题
    function updateHistoryThinkingTitle() {
        if (currentThinkingDetails) {
            const summary = currentThinkingDetails.querySelector("summary");
            summary.textContent = `思考过程`;
        }
    }

    // 第一遍：收集所有工具结果
    messages.forEach(msg => {
        if (msg.type === 'tool_result') {
            const c = msg.content;
            if (c && c.tool_call_id) {
                toolResultMap.set(c.tool_call_id, c);
            }
        }
    });

    // 第二遍：渲染消息
    messages.forEach(msg => {
        // 跳过工具结果（已在第一遍收集）
        if (msg.type === 'tool_result') {
            return;
        }

        // 1. 提取内容 (处理 JSONB 存储格式)
        let content = msg.content;
        if (typeof content === 'object' && content !== null && content.text) {
             content = content.text;
        }

        // 2. 用户消息：渲染并断开当前的 Agent 块
        if (msg.sender === 'user') {
            // 重置所有 Agent 相关状态
            currentAgentDiv = null;
            currentWrapper = null;
            currentThinkingDetails = null;
            currentThinkingArea = null;
            thinkingBlockIndex = 0;
            
            const div = document.createElement("div");
            div.className = "message user";
            const textHTML = typeof content === 'string' ? content.replace(/\n/g, "<br>") : JSON.stringify(content);
            div.innerHTML = `<div>${textHTML}</div>`;
            tempContainer.appendChild(div);
            return;
        }

        // 3. End 消息：断开 Agent 块
        if (msg.type === 'end') {
            currentAgentDiv = null;
            currentWrapper = null;
            currentThinkingDetails = null;
            currentThinkingArea = null;
            thinkingBlockIndex = 0;
            return;
        }

        // 4. 确保有 Agent 容器和 wrapper（用于 reasoning、reasoning_final、chat、tool_call）
        if (msg.type === 'reasoning' || msg.type === 'reasoning_final' || msg.sender === 'assistant' || msg.type === 'tool_call') {
            if (!currentAgentDiv) {
                currentAgentDiv = document.createElement("div");
                currentAgentDiv.className = "message ai";
                tempContainer.appendChild(currentAgentDiv);
            }
            
            if (!currentWrapper) {
                currentWrapper = document.createElement("div");
                currentWrapper.className = "ai-response-wrapper";
                currentAgentDiv.appendChild(currentWrapper);
            }
        }

        // 5. 处理 reasoning 和 reasoning_final 类型消息（思考过程）
        if (msg.type === 'reasoning' || msg.type === 'reasoning_final') {
            ensureHistoryThinkingContainer();
            
            thinkingBlockIndex++;
            const blockDiv = document.createElement("div");
            blockDiv.className = "thinking-block completed";
            // 兼容两种格式：直接字符串 或 { text: "..." } 对象
            const reasoningContent = typeof content === 'string' ? content : (msg.content?.text || '');
            blockDiv.innerHTML = `<div class="thinking-block-header">思考 #${thinkingBlockIndex}</div><div class="thinking-block-content">${escapeHtml(reasoningContent)}</div>`;
            currentThinkingArea.appendChild(blockDiv);
            
            updateHistoryThinkingTitle();
            return;
        }

        // 6. 处理 chat 类型（assistant 正式回答）
        if ((msg.type === 'chat' || msg.sender === 'assistant') && msg.sender !== 'user') {
            const textContent = msg.content?.text || (typeof content === 'string' ? content : '');
            
            if (textContent) {
                const answerBox = document.createElement("div");
                answerBox.className = "markdown-body";
                answerBox.innerHTML = marked.parse(textContent);
                currentWrapper.appendChild(answerBox);
            }
            return;
        }
        
        // 7. 处理 tool_call 类型
        if (msg.type === 'tool_call') {
            const toolData = msg.content;
            if (!toolData.tool_call_id) {
                return;
            }
            
            ensureHistoryThinkingContainer();
            
            const toolWrapper = document.createElement("div");
            toolWrapper.className = "thinking-tool-wrapper";
            const toolDiv = createToolCardDOM(toolData);
            toolWrapper.appendChild(toolDiv);
            currentThinkingArea.appendChild(toolWrapper);
        }
    });
    
    // 根据模式插入到容器
    if (prepend) {
        // 追加模式：在顶部插入，保持滚动位置
        const oldScrollHeight = container.scrollHeight;
        
        // 将临时容器中的所有子元素插入到容器开头
        while (tempContainer.firstChild) {
            container.insertBefore(tempContainer.lastChild, container.firstChild);
        }
        
        // 调整滚动位置，使用户看到的内容不变
        const newScrollHeight = container.scrollHeight;
        container.scrollTop = newScrollHeight - oldScrollHeight;
    } else {
        // 初始加载模式：直接添加并滚动到底部
        while (tempContainer.firstChild) {
            container.appendChild(tempContainer.firstChild);
        }
        container.scrollTop = container.scrollHeight;
    }
}

// 渲染用户消息
export function renderChatMessage(msg) {
  const container = document.getElementById("messages");
  const div = document.createElement("div");

  const isAi = msg.sender === "assistant";
  div.className = `message ${
    isAi ? "ai" : msg.sender === "user" ? "user" : "system"
  }`;

  let contentHtml = "";
  if (isAi) {
    contentHtml = marked.parse(msg.content);
  }else {
    if (typeof msg.content === 'string') {
      contentHtml = msg.content.replace(/\n/g, "<br>")
    }else {
      return
    }
  }

  div.innerHTML = `<div>${contentHtml}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

// 根据ws返回的消息类型进行处理
export function handleMessage(msg, ctx) {
  switch (msg.type) {
    case "session_allocated": // 新会话id分配,更新会话列表
      ctx.currentSessionId = msg.content.session_id;
      window.currentSessionId = msg.content.session_id;
      console.log(`分配sessionId到当前新对话：${ctx.currentSessionId}`);
      ctx.sessions.add(msg.content.session_id, msg.content.session_title);
      ctx.initSessionUI(ctx.onSwitch);
      break;
    case "reasoning": // 思考过程消息
      appendReasoningChunk(msg.content);
      break;
    case "reasoning_end": // 思考过程结束
      finishReasoningBlock();
      break;
    case "chat":
      if (msg.sender === "user") {
        // 如果之前有正在流式的 Agent 消息，强制结束
        finishStreaming();
        renderChatMessage(msg);
        break;
      }
      if (msg.sender === "assistant") {
        // 正式回答内容
        appendTextChunk(msg.content);
        break;
      }
      break;
    case "interrupt": // 中断消息,弹出确认框
      finishStreaming(); // 中断时也应结束之前的流式
      showHitlApproval(msg, ctx.wsSend);
      break;
    case "status": // 系统状态消息,添加到日志中
      ctx.addLogItem?.(msg.content);
      break;
    case "tool_call": // 工具调用消息
      // 如果有正在进行的思考块，先完成它
      finishReasoningBlock();
      // 1. 侧边栏工作台渲染
      renderToolCall(msg.content);
      // 2. 聊天窗口渲染卡片
      appendToolCallCard(msg.content); 
      break;
    case "tool_result": // 工具结果消息
      // 1. 更新侧边栏状态
      ctx.updateToolResult?.(msg.content);
      // 2. 记录到 Map 供点击查看
      if (msg.content && msg.content.tool_call_id) {
         toolResultMap.set(msg.content.tool_call_id, msg.content);
      }
      break;
    case "file_resource": // 文件资源消息,添加到文件列表中
      addFileToList(msg.content);
      break;
    case "end": // 结束消息,清理缓冲区
      finishStreaming();
      break;
    default:
      console.warn("未知消息类型", msg.type);
  }
}

/**
 * 重置分页状态（切换会话时调用）
 */
function resetHistoryPagination() {
    historyCurrentPage = 1;
    historyHasMore = true;
    historyIsLoading = false;
    historySessionId = null;
}

/**
 * 加载历史消息（支持分页）
 * @param {string} sessionId - 会话ID
 * @param {number} page - 页码，默认为1
 * @param {boolean} prepend - 是否在顶部插入（加载更多时为true）
 */
async function fetchHistoryPage(sessionId, page = 1, prepend = false) {
    if (historyIsLoading) return;
    
    historyIsLoading = true;
    
    // 显示加载指示器（仅在加载更多时）
    const container = document.getElementById("messages");
    let loadingIndicator = null;
    if (prepend) {
        loadingIndicator = document.createElement("div");
        loadingIndicator.className = "history-loading-indicator";
        loadingIndicator.innerHTML = '<div style="text-align:center;padding:10px;color:#888;">加载中...</div>';
        container.insertBefore(loadingIndicator, container.firstChild);
    }
    
    try {
        const params = new URLSearchParams();
        params.append("session_id", sessionId);
        params.append("limit", HISTORY_PAGE_SIZE);
        params.append("page", page);
        
        const url = `/api/sessions/history?${params.toString()}`;
        const response = await fetch(url, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        // 移除加载指示器
        if (loadingIndicator && loadingIndicator.parentNode) {
            loadingIndicator.remove();
        }
        
        if (data.status && data.data.length) {
            // 数据库返回的是按时间倒序（最新在前），需要反转为正序（最旧在前）以正确显示
            const messagesInOrder = [...data.data].reverse();
            renderHistory(messagesInOrder, prepend);
            
            // 更新分页状态
            historyCurrentPage = page;
            historyHasMore = data.data.length >= HISTORY_PAGE_SIZE;
            
            // 如果没有更多了，显示提示
            if (!historyHasMore && prepend) {
                showNoMoreHistoryIndicator();
            }
        } else {
            if (!prepend) {
                console.log("该会话无历史消息");
            } else {
                // 加载更多时没有数据了，显示提示
                showNoMoreHistoryIndicator();
            }
            historyHasMore = false;
        }
    } catch (e) {
        console.error("获取会话历史失败:", e);
        // 移除加载指示器
        if (loadingIndicator && loadingIndicator.parentNode) {
            loadingIndicator.remove();
        }
    } finally {
        historyIsLoading = false;
    }
}

/**
 * 显示"没有更多历史消息"提示
 */
function showNoMoreHistoryIndicator() {
    const container = document.getElementById("messages");
    // 检查是否已存在提示
    if (container.querySelector(".history-end-indicator")) return;
    
    const indicator = document.createElement("div");
    indicator.className = "history-end-indicator";
    indicator.textContent = "— 已加载全部历史消息 —";
    container.insertBefore(indicator, container.firstChild);
}

/**
 * 加载更多历史消息（向上滚动触发）
 */
async function loadMoreHistory() {
    if (!historySessionId || !historyHasMore || historyIsLoading) return;
    
    const nextPage = historyCurrentPage + 1;
    await fetchHistoryPage(historySessionId, nextPage, true);
}

/**
 * 滚动事件处理器 - 检测是否滚动到顶部
 */
function handleHistoryScroll(event) {
    const container = event.target;
    
    // 当滚动到顶部附近（距离顶部50px以内）时加载更多
    if (container.scrollTop <= 50 && historyHasMore && !historyIsLoading) {
        loadMoreHistory();
    }
}

/**
 * 初始化滚动监听器
 */
function initHistoryScrollListener() {
    const container = document.getElementById("messages");
    if (container) {
        // 移除旧的监听器（防止重复绑定）
        container.removeEventListener("scroll", handleHistoryScroll);
        // 添加新的监听器
        container.addEventListener("scroll", handleHistoryScroll);
    }
}

// api接口————加载指定会话的历史消息（入口函数）
export async function loadHistoryMessages(sessionId) {
    if (!sessionId) throw new Error("sessionId 是必填参数");
    
    // 重置分页状态
    resetHistoryPagination();
    historySessionId = sessionId;
    
    // 初始化滚动监听
    initHistoryScrollListener();
    
    // 加载第一页
    await fetchHistoryPage(sessionId, 1, false);
}
