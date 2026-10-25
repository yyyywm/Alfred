/* Alfred Web UI — 前端逻辑 v2 */

// ── 状态管理 ──

const state = {
  sessionId: null,
  ws: null,
  connected: false,
  turnRunning: false,
  currentModel: '',
  sessions: [],
  models: [],
  pendingConfirmId: null,
  streamingMessage: null,
  currentAssistantBubble: null,
  toolCalls: {},
  renameSessionId: null,
};

// ── DOM 元素 ──

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const el = {
  messages: $('#messages'),
  input: $('#user-input'),
  btnSend: $('#btn-send'),
  sessionList: $('#session-list'),
  sessionSearch: $('#session-search-input'),
  sessionTitle: $('#session-title'),
  btnNewSession: $('#btn-new-session'),
  btnDeleteSession: $('#btn-delete-session'),
  btnRenameSession: $('#btn-rename-session'),
  modelSelect: $('#model-select'),
  connectionStatus: $('#connection-status'),
  turnStatus: $('#turn-status'),
  confirmModal: $('#confirm-modal'),
  confirmMessage: $('#confirm-message'),
  confirmToolName: $('#confirm-tool-name'),
  btnConfirmApprove: $('#btn-confirm-approve'),
  btnConfirmDeny: $('#btn-confirm-deny'),
  renameModal: $('#rename-modal'),
  renameInput: $('#rename-input'),
  btnRenameCancel: $('#btn-rename-cancel'),
  btnRenameConfirm: $('#btn-rename-confirm'),
  toast: $('#toast'),
  statusInfo: $('#status-info'),
  skillsList: $('#skills-list'),
  whoamiInfo: $('#whoami-info'),
};

// ── 工具函数 ──

function showToast(msg, type = 'info') {
  el.toast.textContent = msg;
  el.toast.className = `toast ${type}`;
  el.toast.style.display = 'block';
  clearTimeout(state._toastTimer);
  state._toastTimer = setTimeout(() => { el.toast.style.display = 'none'; }, 4000);
}

function setConnectionStatus(status) {
  el.connectionStatus.className = `status-dot status-${status}`;
  el.connectionStatus.textContent = {
    connected: '已连接',
    disconnected: '已断开',
    thinking: '思考中…',
  }[status] || status;
}

function setTurnStatus(text) { el.turnStatus.textContent = text; }

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  if (diff < 604800) return Math.floor(diff / 86400) + ' 天前';
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function renderMarkdown(text) {
  if (!text) return '';
  try {
    const html = marked.parse(text);
    return highlightCodeBlocks(html);
  } catch {
    return escapeHtml(text);
  }
}

function highlightCodeBlocks(html) {
  const temp = document.createElement('div');
  temp.innerHTML = html;
  temp.querySelectorAll('pre code').forEach(block => {
    const parent = block.parentElement;
    if (!parent.classList.contains('hljs')) parent.classList.add('hljs');
    if (window.hljs && hljs.highlight) {
      try { block.innerHTML = hljs.highlight(block.textContent, block.className).value; } catch {}
    }
  });
  return temp.innerHTML;
}

function truncate(text, max) {
  if (!text) return '';
  return text.length > max ? text.slice(0, max) + '…' : text;
}

// ── 侧边栏标签页 ──

$$('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.tab').forEach(t => t.classList.remove('active'));
    $$('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    $(`#tab-${tab.dataset.tab}`).classList.add('active');
    if (tab.dataset.tab === 'memory') loadMemoryBlocks();
    if (tab.dataset.tab === 'settings') loadSettings();
  });
});

// ── 会话管理 ──

async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    state.sessions = await res.json();
    renderSessionList();
  } catch (e) {
    console.error('加载会话列表失败:', e);
  }
}

function renderSessionList(filter = '') {
  el.sessionList.innerHTML = '';
  const f = filter.toLowerCase();
  for (const s of state.sessions) {
    const title = s.title || s.preview || '(空会话)';
    if (f && !title.toLowerCase().includes(f)) continue;

    const item = document.createElement('div');
    item.className = `session-item ${s.id === state.sessionId ? 'active' : ''}`;
    item.innerHTML = `
      <div class="session-item-content">
        <div class="session-item-title">${escapeHtml(truncate(title, 30))}</div>
        <div class="session-item-meta">
          <span>${formatDate(s.mtime)}</span>
          <span>${s.msg_count || 0} 条</span>
        </div>
      </div>
      <div class="session-item-actions">
        <button class="session-item-btn" data-action="rename" title="重命名">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button class="session-item-btn btn-danger" data-action="delete" title="删除">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
          </svg>
        </button>
      </div>
    `;
    item.addEventListener('click', (e) => {
      const actionBtn = e.target.closest('[data-action]');
      if (actionBtn) {
        if (actionBtn.dataset.action === 'delete') {
          deleteSession(s.id);
        } else if (actionBtn.dataset.action === 'rename') {
          openRenameModal(s.id, title);
        }
        return;
      }
      loadSession(s.id);
    });
    el.sessionList.appendChild(item);
  }
}

// 搜索过滤
el.sessionSearch.addEventListener('input', () => {
  renderSessionList(el.sessionSearch.value);
});

async function createSession() {
  try {
    const res = await fetch('/api/sessions', { method: 'POST' });
    const data = await res.json();
    await loadSession(data.id);
  } catch (e) {
    showToast('创建会话失败: ' + e.message, 'error');
  }
}

async function deleteSession(id) {
  if (!confirm('确定删除该会话？此操作不可恢复。')) return;
  try {
    await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
    if (state.sessionId === id) {
      disconnect();
      state.sessionId = null;
      el.sessionTitle.textContent = '新会话';
      el.btnDeleteSession.style.display = 'none';
      el.btnRenameSession.style.display = 'none';
      clearMessages();
    }
    await loadSessions();
    showToast('会话已删除', 'success');
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error');
  }
}

// ── 会话重命名 ──

function openRenameModal(sessionId, currentTitle) {
  state.renameSessionId = sessionId;
  el.renameInput.value = currentTitle;
  el.renameModal.style.display = 'flex';
  setTimeout(() => el.renameInput.focus(), 50);
}

async function saveRename() {
  if (!state.renameSessionId) return;
  const title = el.renameInput.value.trim();
  if (!title) {
    showToast('标题不能为空', 'error');
    return;
  }
  try {
    const res = await fetch(`/api/sessions/${state.renameSessionId}/title`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (res.ok) {
      // 更新本地状态
      const s = state.sessions.find(x => x.id === state.renameSessionId);
      if (s) s.title = title;
      if (state.sessionId === state.renameSessionId) {
        el.sessionTitle.textContent = title;
      }
      await loadSessions();
      showToast('标题已更新', 'success');
    } else {
      showToast('保存失败', 'error');
    }
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
  el.renameModal.style.display = 'none';
  state.renameSessionId = null;
}

function closeRenameModal() {
  el.renameModal.style.display = 'none';
  state.renameSessionId = null;
}

// 弹窗事件
el.btnRenameConfirm.addEventListener('click', saveRename);
el.btnRenameCancel.addEventListener('click', closeRenameModal);
el.renameInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') saveRename();
  if (e.key === 'Escape') closeRenameModal();
});
el.renameModal.addEventListener('click', (e) => {
  if (e.target === el.renameModal) closeRenameModal();
});

// ── 加载会话 ──

async function loadSession(id) {
  disconnect();
  state.sessionId = id;
  el.btnDeleteSession.style.display = 'inline-flex';
  el.btnRenameSession.style.display = 'inline-flex';

  const session = state.sessions.find(s => s.id === id);
  el.sessionTitle.textContent = session?.title || session?.preview || `会话 ${id.slice(0, 8)}`;

  connectWebSocket(id);

  try {
    const res = await fetch(`/api/sessions/${id}/messages`);
    const messages = await res.json();
    clearMessages();
    for (const msg of messages) {
      renderHistoricalMessage(msg);
    }
    scrollToBottom();
    renderSessionList(el.sessionSearch.value);
  } catch (e) {
    console.error('加载消息失败:', e);
  }
}

// ── WebSocket 连接 ──

function connectWebSocket(sessionId) {
  if (state.ws) { state.ws.close(); state.ws = null; }

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${location.host}/ws/chat/${sessionId}`;
  state.ws = new WebSocket(wsUrl);

  state.ws.onopen = () => { state.connected = true; setConnectionStatus('connected'); };

  state.ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleServerEvent(data);
  };

  state.ws.onclose = () => {
    state.connected = false;
    setConnectionStatus('disconnected');
    if (state.turnRunning) { state.turnRunning = false; setTurnStatus('已断开'); }
  };

  state.ws.onerror = () => { setConnectionStatus('disconnected'); };
}

function disconnect() {
  if (state.ws) { state.ws.close(); state.ws = null; }
  state.connected = false;
  setConnectionStatus('disconnected');
}

function sendMessage(text) {
  if (!state.ws || !state.connected) { showToast('未连接到服务器', 'error'); return; }
  if (state.turnRunning) { showToast('当前对话仍在进行中', 'error'); return; }

  state.ws.send(JSON.stringify({ type: 'message', text }));
  addUserMessage(text);
  state.turnRunning = true;
  setTurnStatus('等待回复…');
  setConnectionStatus('thinking');
  el.input.value = '';
  autoResizeInput();
  scrollToBottom();
}

// ── 服务器事件处理 ──

function handleServerEvent(data) {
  switch (data.type) {
    case 'session_info':
      state.currentModel = data.model;
      updateModelSelect(data.model);
      break;
    case 'turn_start': showTypingIndicator(); break;
    case 'assistant_chunk': handleAssistantChunk(data.delta); break;
    case 'tool_call_start': handleToolCallStart(data); break;
    case 'tool_call_end': handleToolCallEnd(data); break;
    case 'tool_denied': handleToolDenied(data); break;
    case 'turn_end': handleTurnEnd(data); break;
    case 'turn_error': showError(data.error); handleTurnEnd(data); break;
    case 'turn_retrying': showRetryNotice(data); break;
    case 'context_compacted': showCompactionNotice(data); break;
    case 'confirm_request': showConfirmDialog(data); break;
    case 'turn_complete': handleTurnEnd(data); break;
    case 'model_changed':
      state.currentModel = data.model;
      updateModelSelect(data.model);
      showToast(`已切换模型: ${data.model}`, 'success');
      break;
    case 'pong': break;
    case 'error': showToast(data.error, 'error'); break;
  }
}

// ── 消息渲染 ──

function clearMessages() {
  el.messages.innerHTML = `
    <div class="welcome-message">
      <div class="welcome-icon">🤖</div>
      <h2>Alfred</h2>
      <p>你的私人管家 AI — 长期记忆 + 笔记知识库 + 自我进化</p>
      <div class="welcome-features">
        <span class="welcome-tag">长期记忆</span>
        <span class="welcome-tag">笔记 RAG</span>
        <span class="welcome-tag">自我进化</span>
        <span class="welcome-tag">多模型切换</span>
      </div>
      <p class="welcome-hint">输入消息开始对话，或点击左侧「+」新建会话</p>
    </div>
  `;
  state.streamingMessage = null;
  state.currentAssistantBubble = null;
  state.toolCalls = {};
}

function showTypingIndicator() {
  const welcome = el.messages.querySelector('.welcome-message');
  if (welcome) welcome.remove();
  const existing = el.messages.querySelector('.typing-indicator');
  if (existing) existing.remove();
  const indicator = document.createElement('div');
  indicator.className = 'message assistant';
  indicator.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div class="typing-indicator"><span></span><span></span><span></span></div>
  `;
  el.messages.appendChild(indicator);
  scrollToBottom();
}

function handleAssistantChunk(delta) {
  const indicator = el.messages.querySelector('.typing-indicator');
  if (indicator) {
    const msg = indicator.parentElement;
    if (msg) msg.remove();
  }
  if (!state.currentAssistantBubble) {
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.innerHTML = `<div class="message-avatar">🤖</div><div class="message-content"><div class="message-bubble"></div></div>`;
    el.messages.appendChild(msg);
    state.currentAssistantBubble = msg.querySelector('.message-bubble');
  }
  state.streamingMessage = (state.streamingMessage || '') + delta;
  state.currentAssistantBubble.innerHTML = renderMarkdown(state.streamingMessage);
  setTurnStatus('回复中…');
  scrollToBottom();
}

function finalizeAssistantMessage() {
  if (state.currentAssistantBubble) {
    state.currentAssistantBubble.innerHTML = renderMarkdown(state.streamingMessage || '');
    const time = document.createElement('div');
    time.className = 'message-time';
    time.textContent = formatTime(Date.now() / 1000);
    state.currentAssistantBubble.parentElement.appendChild(time);
  }
  state.streamingMessage = null;
  state.currentAssistantBubble = null;
}

function addUserMessage(text) {
  const welcome = el.messages.querySelector('.welcome-message');
  if (welcome) welcome.remove();
  const msg = document.createElement('div');
  msg.className = 'message user';
  msg.innerHTML = `
    <div class="message-avatar">🧑</div>
    <div class="message-content">
      <div class="message-bubble">${escapeHtml(text)}</div>
      <div class="message-time">${formatTime(Date.now() / 1000)}</div>
    </div>
  `;
  el.messages.appendChild(msg);
  scrollToBottom();
}

function renderHistoricalMessage(msg) {
  const welcome = el.messages.querySelector('.welcome-message');
  if (welcome) welcome.remove();
  if (msg.role === 'user') {
    addUserMessage(msg.content);
  } else if (msg.role === 'assistant') {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
      <div class="message-avatar">🤖</div>
      <div class="message-content">
        <div class="message-bubble">${renderMarkdown(msg.content)}</div>
        <div class="message-time">${formatTime(msg.ts)}</div>
      </div>
    `;
    el.messages.appendChild(div);
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      for (const tc of msg.tool_calls) renderToolCall(tc, true);
    }
  }
}

// ── 工具调用渲染 ──

function handleToolCallStart(data) {
  finalizeAssistantMessage();
  const toolCall = createToolCallElement(data.tool_name, data.args, data.tool_call_id);
  state.toolCalls[data.tool_call_id] = toolCall;
  el.messages.appendChild(toolCall);
  scrollToBottom();
}

function handleToolCallEnd(data) {
  const el = state.toolCalls[data.tool_call_id];
  if (el) {
    updateToolCallStatus(el, data.is_error ? 'error' : 'done');
    const body = el.querySelector('.tool-call-body');
    if (body) body.innerHTML = `<div class="tool-call-section-title">结果:</div>\n${escapeHtml(truncate(data.result, 1500))}`;
    scrollToBottom();
  }
}

function handleToolDenied(data) {
  const el = state.toolCalls[data.tool_call_id];
  if (el) {
    updateToolCallStatus(el, 'denied');
    const body = el.querySelector('.tool-call-body');
    if (body) body.innerHTML = `<div class="tool-call-section-title">原因:</div>\n${escapeHtml(data.reason)}`;
  }
  showToast(`工具调用已拒绝: ${data.tool_name}`, 'error');
}

function createToolCallElement(toolName, args, toolCallId) {
  const icon = getToolIcon(toolName);
  const div = document.createElement('div');
  div.className = 'message assistant';
  div.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div class="tool-call" data-tool-call-id="${toolCallId}">
      <div class="tool-call-header">
        <span class="tool-call-icon">${icon}</span>
        <span class="tool-call-name">${escapeHtml(toolName)}</span>
        <span class="tool-call-status running">运行中…</span>
      </div>
      <div class="tool-call-body"><div class="tool-call-section-title">参数:</div>${escapeHtml(JSON.stringify(args, null, 2))}</div>
    </div>
  `;
  div.querySelector('.tool-call-header').addEventListener('click', () => {
    div.querySelector('.tool-call').classList.toggle('expanded');
  });
  return div;
}

function getToolIcon(toolName) {
  const icons = {
    'memory_search': '🔍', 'memory_update_block': '🧠', 'notes_search': '📝',
    'episodes_search': '💡', 'save_episode': '💾', 'file_read': '📄',
    'create_goal': '🎯', 'update_goal': '🎯', 'get_goal': '🎯',
    'session_search': '🔎', 'frameworks_search': '📚',
    'schedule_create': '⏰', 'schedule_delete': '⏰', 'schedule_list': '⏰',
    'web_search': '🌐', 'web_fetch': '📡', 'shell': '💻',
    'run_python': '🐍', 'code_patch': '🔧',
  };
  return icons[toolName] || '🔧';
}

function updateToolCallStatus(toolCallEl, status) {
  const statusEl = toolCallEl.querySelector('.tool-call-status');
  if (statusEl) {
    statusEl.className = `tool-call-status ${status}`;
    statusEl.textContent = {
      'running': '运行中…', 'done': '✓ 完成', 'error': '✗ 错误', 'denied': '✗ 已拒绝',
    }[status] || status;
  }
}

function renderToolCall(tc, historical = false) {
  const toolCall = createToolCallElement(tc.tool_name, tc.args, tc.tool_call_id);
  el.messages.appendChild(toolCall);
  if (historical && tc.result !== undefined) {
    updateToolCallStatus(toolCall, tc.is_error ? 'error' : 'done');
    const body = toolCall.querySelector('.tool-call-body');
    if (body) body.innerHTML = `<div class="tool-call-section-title">参数:</div>\n${escapeHtml(JSON.stringify(tc.args, null, 2))}\n\n<div class="tool-call-section-title">结果:</div>\n${escapeHtml(truncate(tc.result, 1500))}`;
  }
}

function showRetryNotice(data) {
  const div = document.createElement('div');
  div.className = 'retry-notice';
  div.textContent = `⚡ 网络重试 (${data.attempt}/${data.max_retries})，等待 ${data.wait_seconds}s…`;
  el.messages.appendChild(div);
  scrollToBottom();
}

function showCompactionNotice(data) {
  const div = document.createElement('div');
  div.className = 'compaction-notice';
  div.textContent = `📦 上下文已压缩 (${data.retained_message_count} 条消息保留)`;
  el.messages.appendChild(div);
  scrollToBottom();
}

function showError(error) {
  const div = document.createElement('div');
  div.className = 'error-notice';
  div.textContent = `❌ ${error}`;
  el.messages.appendChild(div);
  scrollToBottom();
}

function handleTurnEnd(data) {
  finalizeAssistantMessage();
  state.turnRunning = false;
  setTurnStatus('就绪');
  setConnectionStatus('connected');
  loadSessions();
}

// ── 确认弹窗 ──

function showConfirmDialog(data) {
  state.pendingConfirmId = data.id;
  el.confirmMessage.textContent = data.message;
  el.confirmToolName.textContent = data.tool_name || '';
  el.confirmModal.style.display = 'flex';
}

function respondConfirm(approved) {
  if (!state.ws || !state.connected || !state.pendingConfirmId) return;
  state.ws.send(JSON.stringify({ type: 'confirm', id: state.pendingConfirmId, approved }));
  el.confirmModal.style.display = 'none';
  state.pendingConfirmId = null;
}

el.btnConfirmApprove.addEventListener('click', () => respondConfirm(true));
el.btnConfirmDeny.addEventListener('click', () => respondConfirm(false));
el.confirmModal.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') respondConfirm(true);
  if (e.key === 'Escape') respondConfirm(false);
});

// ── 模型切换 ──

async function loadModels() {
  try {
    const res = await fetch('/api/models');
    state.models = await res.json();
    const select = el.modelSelect;
    select.innerHTML = '<option value="">选择模型…</option>';
    for (const m of state.models) {
      const opt = document.createElement('option');
      opt.value = m.ref;
      opt.textContent = `${m.provider}:${m.name}`;
      select.appendChild(opt);
    }
    if (state.currentModel) select.value = state.currentModel;
  } catch (e) {
    console.error('加载模型列表失败:', e);
  }
}

function updateModelSelect(model) { if (el.modelSelect) el.modelSelect.value = model; }

el.modelSelect.addEventListener('change', (e) => {
  const model = e.target.value;
  if (!model || !state.ws || !state.connected) return;
  state.ws.send(JSON.stringify({ type: 'model', model }));
});

// ── 设置面板 ──

async function loadSettings() { await Promise.all([loadStatus(), loadSkills(), loadWhoami()]); }

async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    el.statusInfo.innerHTML = `
      <div class="status-row"><span>闲聊模型</span><span>${escapeHtml(data.chat_model)}</span></div>
      <div class="status-row"><span>记忆模型</span><span>${escapeHtml(data.memory_write_model)}</span></div>
      <div class="status-row"><span>Embedding</span><span>${escapeHtml(data.embed_model)}</span></div>
      <div class="status-row"><span>记忆层</span><span class="${data.memory_layer === 'ok' ? 'status-ok' : 'status-error'}">${escapeHtml(data.memory_layer)}</span></div>
      <div class="status-row"><span>记忆 Provider</span><span>${escapeHtml(data.memory_provider)}</span></div>
    `;
  } catch (e) { el.statusInfo.textContent = '加载失败'; }
}

async function loadSkills() {
  try {
    const res = await fetch('/api/skills');
    const skills = await res.json();
    if (skills.length === 0) { el.skillsList.textContent = '暂无已安装技能'; return; }
    el.skillsList.innerHTML = skills.map(s => `
      <div class="skill-item">
        <span class="skill-name">${escapeHtml(s.name)}</span>
        <span style="color:var(--text-muted);font-size:11px;margin-left:4px">[${escapeHtml(s.source)}]</span>
        <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(truncate(s.description, 60))}</div>
      </div>
    `).join('');
  } catch (e) { el.skillsList.textContent = '加载失败'; }
}

async function loadWhoami() {
  try {
    const res = await fetch('/api/whoami');
    const data = await res.json();
    const blocks = data.memory_blocks || {};
    let html = `<div class="status-row"><span>闲聊模型</span><span>${escapeHtml(data.chat_model)}</span></div>`;
    for (const [name, info] of Object.entries(blocks)) {
      const pct = Math.round((info.char_count / info.char_limit) * 100);
      html += `<div class="status-row"><span>${escapeHtml(name)}</span><span>${info.char_count}/${info.char_limit} (${pct}%)</span></div>`;
    }
    html += `<div class="status-row"><span>教训条目</span><span>${data.lessons_count || 0}</span></div>`;
    el.whoamiInfo.innerHTML = html;
  } catch (e) { el.whoamiInfo.textContent = '加载失败'; }
}

// ── 记忆块编辑 ──

async function loadMemoryBlocks() {
  for (const block of ['human', 'persona', 'lessons']) {
    try {
      let url;
      if (block === 'lessons') url = '/api/memory/lessons';
      else url = `/api/memory/${block}`;
      const res = await fetch(url);
      const data = await res.json();
      const editor = document.querySelector(`.memory-block[data-block="${block}"] .memory-editor`);
      const count = document.querySelector(`.memory-block[data-block="${block}"] .block-count`);
      if (editor) editor.value = data.content || '';
      if (count) count.textContent = `${data.char_count}/${data.char_limit} 字符`;
    } catch (e) { console.error(`加载 ${block} 失败:`, e); }
  }
}

$$('.memory-block').forEach(blockEl => {
  const blockName = blockEl.dataset.block;
  const editor = blockEl.querySelector('.memory-editor');
  const saveBtn = blockEl.querySelector('button');
  saveBtn.addEventListener('click', async () => {
    const content = editor.value;
    try {
      let url;
      if (blockName === 'lessons') url = '/api/memory/lessons';
      else url = `/api/memory/${blockName}`;
      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, reason: 'Web UI 手动编辑' }),
      });
      if (res.ok) { showToast(`${blockName} 已保存`, 'success'); loadMemoryBlocks(); }
      else { const err = await res.json(); showToast(`保存失败: ${err.error || '未知错误'}`, 'error'); }
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
  });
});

// ── 输入处理 ──

function autoResizeInput() {
  const input = el.input;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
}

el.input.addEventListener('input', autoResizeInput);

el.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = el.input.value.trim();
    if (text) sendMessage(text);
  }
});

el.btnSend.addEventListener('click', () => {
  const text = el.input.value.trim();
  if (text) sendMessage(text);
});

// ── 会话操作按钮 ──

el.btnNewSession.addEventListener('click', async () => { await createSession(); });

el.btnDeleteSession.addEventListener('click', () => {
  if (state.sessionId) deleteSession(state.sessionId);
});

el.btnRenameSession.addEventListener('click', () => {
  if (state.sessionId) {
    const session = state.sessions.find(s => s.id === state.sessionId);
    const title = session?.title || session?.preview || '';
    openRenameModal(state.sessionId, title);
  }
});

// ── 滚动到底部 ──

function scrollToBottom() {
  requestAnimationFrame(() => { el.messages.scrollTop = el.messages.scrollHeight; });
}

// ── 初始化 ──

async function init() {
  await loadModels();
  await loadSessions();
  loadStatus();
  if (state.sessions.length > 0) {
    await loadSession(state.sessions[0].id);
  } else {
    await createSession();
  }
  setInterval(() => {
    if (state.ws && state.connected && !state.turnRunning) {
      state.ws.send(JSON.stringify({ type: 'ping' }));
    }
  }, 30000);
}

init();
