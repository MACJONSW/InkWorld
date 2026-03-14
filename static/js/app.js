/**
 * 墨境 · AI 长篇小说写作平台 - 主应用逻辑
 */
const App = {
    authToken: localStorage.getItem('auth_token') || '',
    currentUser: null,
    authDialogResolve: null,
    currentBookId: null,
    currentNodeId: null,
    autoSaveTimer: null,
    isStreaming: false,
    selectionPopup: null,
    agentOutputText: '',
    diffOldText: '',
    diffNewText: '',
    // 自动补全相关
    autocompleteTimer: null,
    autocompleteEnabled: true,
    ghostPrediction: '',
    autocompleteAbort: null,
    characterReminderTimer: null,
    // 冲突设计缓存
    lastConflictData: null,
    draggingNodeId: null,
    focusMode: localStorage.getItem('focus_mode') === '1',
    versionCache: [],
    guardHardBlocked: false,

    // ==================== 初始化 ====================
    async init() {
        const ok = await this.ensureAuth();
        if (!ok) return;
        this.applyFocusMode(this.focusMode, false);
        this.updateUserPill();
        await this.loadBooks();
        this.setupEditorEvents();
        this.setupSelectionPopup();
        console.log('墨境 · AI 长篇小说写作平台 已启动');
    },

    authHeaders(extra = {}) {
        const headers = { ...extra };
        if (this.authToken) {
            headers['Authorization'] = `Bearer ${this.authToken}`;
        }
        return headers;
    },

    async ensureAuth() {
        if (this.authToken) {
            const me = await this.fetchMe();
            if (me) {
                this.currentUser = me;
                this.updateUserPill();
                return true;
            }
            this.authToken = '';
            localStorage.removeItem('auth_token');
        }

        while (true) {
            const input = await this.showAuthDialog();
            if (!input) {
                this.toast('未登录，无法使用平台 API', 'error');
                return false;
            }

            const endpoint = input.mode === 'register' ? '/api/auth/register' : '/api/auth/login';
            const res = await this.api(endpoint, 'POST', {
                email: input.email,
                password: input.password
            }, false);
            if (!res || res.error || !res.token) {
                const err = res?.error || 'unknown_error';
                this.toast(`认证失败: ${err}`, 'error');
                continue;
            }
            this.authToken = res.token;
            localStorage.setItem('auth_token', this.authToken);
            this.currentUser = res.user || null;
            this.updateUserPill();
            this.toast(`已登录: ${input.email}`, 'success');
            return true;
        }
    },

    async fetchMe() {
        const res = await this.api('/api/auth/me', 'GET');
        if (res && !res.error) return res;
        return null;
    },

    updateUserPill() {
        const pill = document.getElementById('userPill');
        if (!pill) return;
        pill.textContent = this.currentUser?.email || '未登录';
    },

    showAuthDialog() {
        const modal = document.getElementById('authModal');
        modal.style.display = 'flex';
        const email = document.getElementById('authEmail');
        const password = document.getElementById('authPassword');
        if (email) email.focus();
        return new Promise((resolve) => {
            this.authDialogResolve = resolve;
        });
    },

    submitAuth(mode) {
        const email = (document.getElementById('authEmail').value || '').trim().toLowerCase();
        const password = document.getElementById('authPassword').value || '';
        if (!email || !email.includes('@')) {
            this.toast('请输入合法邮箱', 'warning');
            return;
        }
        if (password.length < 6) {
            this.toast('密码至少 6 位', 'warning');
            return;
        }
        document.getElementById('authModal').style.display = 'none';
        if (this.authDialogResolve) {
            this.authDialogResolve({ mode, email, password });
            this.authDialogResolve = null;
        }
    },

    cancelAuthDialog() {
        document.getElementById('authModal').style.display = 'none';
        if (this.authDialogResolve) {
            this.authDialogResolve(null);
            this.authDialogResolve = null;
        }
    },

    logout() {
        this.authToken = '';
        this.currentUser = null;
        localStorage.removeItem('auth_token');
        this.updateUserPill();
        this.toast('已退出登录，请刷新后重新登录', 'info');
    },

    toggleFocusMode() {
        this.applyFocusMode(!this.focusMode, true);
    },

    applyFocusMode(enabled, shouldToast = true) {
        this.focusMode = !!enabled;
        document.body.classList.toggle('focus-mode', this.focusMode);
        localStorage.setItem('focus_mode', this.focusMode ? '1' : '0');
        const btn = document.getElementById('focusModeBtn');
        if (btn) {
            btn.title = this.focusMode ? '退出无干扰纯净模式 (Ctrl+\\)' : '无干扰纯净模式 (Ctrl+\\)';
            btn.innerHTML = this.focusMode
                ? '<i class="fas fa-compress"></i>'
                : '<i class="fas fa-expand"></i>';
        }
        if (shouldToast) {
            this.toast(this.focusMode ? '已进入纯净模式' : '已退出纯净模式', 'info');
        }
    },

    // ==================== API 工具 ====================
    async api(url, method = 'GET', body = null, withAuth = true) {
        try {
            const baseHeaders = { 'Content-Type': 'application/json' };
            const opts = {
                method,
                headers: withAuth ? this.authHeaders(baseHeaders) : baseHeaders
            };
            if (body) opts.body = JSON.stringify(body);
            const res = await fetch(url, opts);
            if (res.status === 401 && withAuth) {
                this.authToken = '';
                localStorage.removeItem('auth_token');
                this.toast('登录已过期，请刷新后重新登录', 'warning');
                return { error: 'unauthorized' };
            }
            return await res.json();
        } catch (e) {
            console.error('API Error:', e);
            this.toast('网络请求失败: ' + e.message, 'error');
            return null;
        }
    },

    toast(msg, type = 'info') {
        const container = document.getElementById('toastContainer');
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        const icons = { success: 'check-circle', error: 'circle-xmark', info: 'circle-info', warning: 'triangle-exclamation' };
        t.innerHTML = `<i class="fas fa-${icons[type] || 'circle-info'}"></i> ${msg}`;
        container.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3500);
    },

    // ==================== 书籍管理 ====================
    async loadBooks() {
        const books = await this.api('/api/books');
        const sel = document.getElementById('currentBook');
        sel.innerHTML = '<option value="">— 选择书籍 —</option>';
        if (books) {
            books.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.id;
                opt.textContent = b.title;
                if (b.id === this.currentBookId) opt.selected = true;
                sel.appendChild(opt);
            });
        }
    },

    showNewBookDialog() {
        document.getElementById('newBookModal').style.display = 'flex';
        document.getElementById('newBookTitle').focus();
    },

    closeNewBookDialog() {
        document.getElementById('newBookModal').style.display = 'none';
    },

    async createBook() {
        const title = document.getElementById('newBookTitle').value.trim();
        if (!title) { this.toast('请输入书名', 'warning'); return; }
        const data = {
            title,
            description: document.getElementById('newBookDesc').value,
            author: document.getElementById('newBookAuthor').value,
            genre: document.getElementById('newBookGenre').value
        };
        const res = await this.api('/api/books', 'POST', data);
        if (res && res.id) {
            this.currentBookId = res.id;
            await this.loadBooks();
            document.getElementById('currentBook').value = res.id;
            this.closeNewBookDialog();
            this.loadDocTree();
            this.loadLorebook();
            this.toast('书籍创建成功', 'success');
        }
    },

    async switchBook(bookId) {
        this.currentBookId = bookId;
        this.currentNodeId = null;
        this.versionCache = [];
        document.getElementById('editorArea').innerText = '';
        document.getElementById('editorPath').textContent = '未选择章节';
        if (bookId) {
            this.loadDocTree();
            this.loadLorebook();
            this.loadEntityGraph();
            this.loadSummaries();
            this.loadCharacterReminders();
            this.loadWorldState();
            this.loadTensionDiagnostics();
        } else {
            document.getElementById('docTree').innerHTML = '<div class="empty-state"><i class="fas fa-book-open"></i><p>请选择或创建一本书籍</p></div>';
            document.getElementById('tensionSummary').textContent = '尚未诊断';
            document.getElementById('tensionChart').innerHTML = '';
            document.getElementById('tensionWarnings').innerHTML = '';
            document.getElementById('characterReminderList').innerHTML = '<div class="empty-state"><p>请选择书籍后查看人物提醒</p></div>';
        }
    },

    // ==================== 文档树 ====================
    async loadDocTree() {
        if (!this.currentBookId) return;
        const tree = await this.api(`/api/books/${this.currentBookId}/tree`);
        const container = document.getElementById('docTree');
        if (!tree || tree.length === 0) {
            container.innerHTML = '<div class="empty-state"><i class="fas fa-folder-open"></i><p>文档树为空<br>点击上方按钮创建卷/章</p></div>';
            return;
        }
        container.innerHTML = this.renderTree(tree);
    },

    renderTree(nodes, depth = 0) {
        return nodes.map(n => {
            const typeIcons = { volume: 'fa-book', chapter: 'fa-file-lines', scene: 'fa-film' };
            const icon = typeIcons[n.type] || 'fa-file';
            const hasChildren = n.children && n.children.length > 0;
            const isActive = n.id === this.currentNodeId;
            const wc = n.word_count ? `${n.word_count}字` : '';
            return `
                <div class="tree-node" data-id="${n.id}">
                    <div class="tree-node-header ${isActive ? 'active' : ''}"
                         draggable="true"
                         onclick="App.selectNode('${n.id}')"
                         ondragstart="App.onTreeDragStart(event, '${n.id}')"
                         ondragend="App.onTreeDragEnd()"
                         ondragover="App.onTreeDragOver(event)"
                         ondragleave="App.onTreeDragLeave(event)"
                         ondrop="App.onTreeDrop(event, '${n.id}')">
                        <span class="tree-node-icon"><i class="fas ${icon}"></i></span>
                        <span class="tree-node-label">${this.escHtml(n.title)}</span>
                        ${wc ? `<span class="tree-node-count">${wc}</span>` : ''}
                        <span class="tree-node-status ${n.status}">${n.status === 'final' ? '定稿' : '草稿'}</span>
                        <span class="tree-node-actions">
                            <button onclick="event.stopPropagation(); App.editNodeTitle('${n.id}', '${this.escHtml(n.title)}')" title="重命名"><i class="fas fa-pen"></i></button>
                            <button onclick="event.stopPropagation(); App.deleteNode('${n.id}')" title="删除"><i class="fas fa-trash"></i></button>
                        </span>
                    </div>
                    ${hasChildren ? `<div class="tree-node-children">${this.renderTree(n.children, depth + 1)}</div>` : ''}
                </div>`;
        }).join('');
    },

    onTreeDragStart(event, nodeId) {
        this.draggingNodeId = nodeId;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', nodeId);
    },

    onTreeDragOver(event) {
        event.preventDefault();
        event.currentTarget.classList.add('drag-over');
    },

    onTreeDragLeave(event) {
        event.currentTarget.classList.remove('drag-over');
    },

    onTreeDragEnd() {
        this.draggingNodeId = null;
        document.querySelectorAll('.tree-node-header.drag-over').forEach((el) => el.classList.remove('drag-over'));
    },

    async onTreeDrop(event, targetNodeId) {
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.classList.remove('drag-over');

        const draggedNodeId = event.dataTransfer.getData('text/plain') || this.draggingNodeId;
        if (!draggedNodeId || draggedNodeId === targetNodeId) return;
        if (this.isDescendantNode(targetNodeId, draggedNodeId)) {
            this.toast('不能拖到自己的子节点下', 'warning');
            return;
        }

        const dragged = document.querySelector(`.tree-node[data-id="${draggedNodeId}"]`);
        const target = document.querySelector(`.tree-node[data-id="${targetNodeId}"]`);
        if (!dragged || !target) return;

        let children = Array.from(target.children).find((x) => x.classList?.contains('tree-node-children'));
        if (!children) {
            children = document.createElement('div');
            children.className = 'tree-node-children';
            target.appendChild(children);
        }
        children.appendChild(dragged);
        await this.persistTreeOrder();
        this.onTreeDragEnd();
    },

    async onTreeDropToRoot(event) {
        event.preventDefault();
        const draggedNodeId = event.dataTransfer.getData('text/plain') || this.draggingNodeId;
        if (!draggedNodeId) return;
        const dragged = document.querySelector(`.tree-node[data-id="${draggedNodeId}"]`);
        const root = document.getElementById('docTree');
        if (!dragged || !root) return;
        root.appendChild(dragged);
        await this.persistTreeOrder();
        this.onTreeDragEnd();
    },

    isDescendantNode(targetNodeId, sourceNodeId) {
        const sourceNode = document.querySelector(`.tree-node[data-id="${sourceNodeId}"]`);
        if (!sourceNode) return false;
        return !!sourceNode.querySelector(`.tree-node[data-id="${targetNodeId}"]`);
    },

    async persistTreeOrder() {
        const root = document.getElementById('docTree');
        const items = [];

        const walk = (container, parentId) => {
            const children = Array.from(container.children).filter((el) => el.classList?.contains('tree-node'));
            children.forEach((nodeEl, idx) => {
                const id = nodeEl.dataset.id;
                items.push({
                    id,
                    parent_id: parentId || null,
                    sort_order: idx
                });
                const childContainer = Array.from(nodeEl.children).find((x) => x.classList?.contains('tree-node-children'));
                if (childContainer) {
                    walk(childContainer, id);
                }
            });
        };
        walk(root, null);

        const res = await this.api('/api/nodes/reorder', 'POST', { items });
        if (res && !res.error) {
            this.toast('目录顺序已更新', 'success');
            await this.loadDocTree();
        }
    },

    async selectNode(nodeId) {
        // 先保存当前内容
        if (this.currentNodeId) await this.saveContent();

        this.currentNodeId = nodeId;
        this.versionCache = [];
        const node = await this.api(`/api/nodes/${nodeId}`);
        const content = await this.api(`/api/nodes/${nodeId}/content`);

        if (node) {
            document.getElementById('editorPath').textContent = node.title;
            document.getElementById('statusBadge').textContent = node.status === 'final' ? '定稿' : '草稿';
            document.getElementById('statusBadge').className = `status-badge ${node.status}`;
            document.getElementById('nodeStatus').value = node.status;
        }
        if (content) {
            document.getElementById('editorArea').innerText = content.content || '';
            this.updateWordCount();
        }

        // 刷新高亮
        this.loadDocTree();
        this.loadVersions();
        this.loadMemoryStatus();
        this.loadCharacterReminders();
        this.dismissGhostText();
    },

    async addNode(type) {
        if (!this.currentBookId) { this.toast('请先选择或创建书籍', 'warning'); return; }
        const title = prompt(`请输入${type === 'volume' ? '卷' : type === 'chapter' ? '章' : '场景'}名称：`);
        if (!title) return;

        const parentId = type === 'chapter' ? this.findParentVolume() :
                         type === 'scene' ? this.currentNodeId : null;
        const data = {
            book_id: this.currentBookId,
            parent_id: parentId,
            type,
            title
        };
        const res = await this.api('/api/nodes', 'POST', data);
        if (res && res.id) {
            this.loadDocTree();
            this.toast(`${title} 已创建`, 'success');
        }
    },

    findParentVolume() {
        // 如果当前选中的是章或场景，找到其父卷
        // 简单实现：如果有选中node就当做parent
        return this.currentNodeId || null;
    },

    async editNodeTitle(nodeId, oldTitle) {
        const newTitle = prompt('重命名：', oldTitle);
        if (newTitle && newTitle !== oldTitle) {
            await this.api(`/api/nodes/${nodeId}`, 'PUT', { title: newTitle });
            this.loadDocTree();
            if (nodeId === this.currentNodeId) {
                document.getElementById('editorPath').textContent = newTitle;
            }
        }
    },

    async deleteNode(nodeId) {
        if (!confirm('确定删除此节点及其所有子节点？')) return;
        await this.api(`/api/nodes/${nodeId}`, 'DELETE');
        if (nodeId === this.currentNodeId) {
            this.currentNodeId = null;
            document.getElementById('editorArea').innerText = '';
            document.getElementById('editorPath').textContent = '未选择章节';
        }
        this.loadDocTree();
        this.toast('已删除', 'info');
    },

    async updateNodeStatus() {
        if (!this.currentNodeId) return;
        const status = document.getElementById('nodeStatus').value;
        await this.api(`/api/nodes/${this.currentNodeId}`, 'PUT', { status });
        document.getElementById('statusBadge').textContent = status === 'final' ? '定稿' : '草稿';
        document.getElementById('statusBadge').className = `status-badge ${status}`;
        this.loadDocTree();
    },

    // ==================== 编辑器 ====================
    setupEditorEvents() {
        const editor = document.getElementById('editorArea');

        // 自动保存 (防抖)
        editor.addEventListener('input', () => {
            clearTimeout(this.autoSaveTimer);
            document.getElementById('autoSaveStatus').textContent = '编辑中...';
            this.autoSaveTimer = setTimeout(() => this.saveContent(), 2000);
            this.updateWordCount();

            // 触发自动补全 (防抖 800ms)
            this.dismissGhostText();
            this.scheduleAutocomplete();
            this.scheduleCharacterReminderRefresh();
        });

        // 关闭 slash 菜单
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.slash-menu')) {
                document.getElementById('slashMenu').style.display = 'none';
            }
        });

        // 快捷键：Ctrl+\ 切换纯净模式
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === '\\') {
                e.preventDefault();
                this.toggleFocusMode();
            }
        });
    },

    onEditorInput() {
        // 已在 setupEditorEvents 中处理
    },

    onEditorKeydown(e) {
        // Tab 接受 ghost text
        if (e.key === 'Tab' && this.ghostPrediction) {
            e.preventDefault();
            this.acceptGhostText();
            return;
        }

        // 任意按键（非Tab）时清除 ghost text
        if (this.ghostPrediction && e.key !== 'Shift' && e.key !== 'Control' && e.key !== 'Alt' && e.key !== 'Meta') {
            if (e.key !== 'Tab') {
                this.dismissGhostText();
            }
        }

        // 斜杠命令
        if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
            const sel = window.getSelection();
            if (sel.rangeCount > 0) {
                const range = sel.getRangeAt(0);
                const rect = range.getBoundingClientRect();
                const menu = document.getElementById('slashMenu');
                menu.style.display = 'block';
                menu.style.left = rect.left + 'px';
                menu.style.top = (rect.bottom + 8) + 'px';
            }
        }

        // Esc 关闭菜单/停止生成/消除ghost text
        if (e.key === 'Escape') {
            document.getElementById('slashMenu').style.display = 'none';
            this.dismissGhostText();
            if (this.isStreaming) this.stopGeneration();
        }

        // Ctrl+S 保存
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            this.saveContent();
        }
    },

    async saveContent() {
        if (!this.currentNodeId) return;
        const content = document.getElementById('editorArea').innerText;
        await this.api(`/api/nodes/${this.currentNodeId}/content`, 'PUT', { content });
        document.getElementById('autoSaveStatus').textContent = '已保存';
        this.updateWordCount();
    },

    updateWordCount() {
        const text = document.getElementById('editorArea').innerText;
        const count = text.replace(/\s/g, '').length;
        document.getElementById('editorWordCount').textContent = `${count} 字`;
        document.getElementById('wordCountDisplay').textContent = `${count} 字`;
    },

    // 划词选中弹出菜单
    setupSelectionPopup() {
        document.addEventListener('mouseup', (e) => {
            this.removeSelectionPopup();
            const sel = window.getSelection();
            const text = sel.toString().trim();
            if (text && text.length > 1 && e.target.closest('#editorArea')) {
                const range = sel.getRangeAt(0);
                const rect = range.getBoundingClientRect();
                this.showSelectionPopup(rect, text);
            }
        });
    },

    showSelectionPopup(rect, text) {
        this.removeSelectionPopup();
        const popup = document.createElement('div');
        popup.className = 'selection-popup';
        popup.innerHTML = `
            <button onclick="App.lookupSelected('${this.escJs(text)}')"><i class="fas fa-search"></i> 查询</button>
            <button onclick="App.queryStateSelected('${this.escJs(text)}')"><i class="fas fa-globe"></i> 状态</button>
            <button onclick="App.polishSelected()"><i class="fas fa-gem"></i> 润色</button>
            <button onclick="App.rewriteSelected()"><i class="fas fa-rotate"></i> 改写</button>
            <button onclick="App.expandSelected()"><i class="fas fa-expand"></i> 扩写</button>
        `;
        popup.style.left = rect.left + 'px';
        popup.style.top = (rect.top - 44) + 'px';
        document.body.appendChild(popup);
        this.selectionPopup = popup;
    },

    removeSelectionPopup() {
        if (this.selectionPopup) {
            this.selectionPopup.remove();
            this.selectionPopup = null;
        }
    },

    async lookupSelected(text) {
        this.removeSelectionPopup();
        if (!this.currentBookId) return;
        const res = await this.api('/api/lookup', 'POST', { text, book_id: this.currentBookId });
        if (res) {
            this.switchRightTab('memory');
            const container = document.getElementById('entityResults');
            let html = '';
            if (res.entries && res.entries.length > 0) {
                res.entries.forEach(e => {
                    html += `<div class="entity-result-item">
                        <div class="entity-result-name">${this.escHtml(e.name)} <small>(${e.category})</small></div>
                        <div class="entity-result-content">${this.escHtml(e.content || e.description)}</div>
                    </div>`;
                });
            }
            if (res.relations && res.relations.length > 0) {
                res.relations.forEach(r => {
                    html += `<div class="entity-result-item">
                        <div class="entity-result-name">${r.source_entity} → ${r.target_entity}</div>
                        <div class="entity-result-content">${r.relation_type}: ${r.relation_value}</div>
                    </div>`;
                });
            }
            if (res.world_states && res.world_states.length > 0) {
                res.world_states.forEach(s => {
                    html += `<div class="entity-result-item">
                        <div class="entity-result-name">${this.escHtml(s.entity_name)} <small>(状态)</small></div>
                        <div class="entity-result-content">${this.escHtml(s.state_type)}: ${this.escHtml(s.state_value)}</div>
                    </div>`;
                });
            }
            if (!html) html = '<div class="empty-state"><p>未找到相关设定</p></div>';
            container.innerHTML = html;
        }
    },

    async queryStateSelected(text) {
        this.removeSelectionPopup();
        if (!this.currentBookId) return;
        const res = await this.api('/api/lookup', 'POST', { text, book_id: this.currentBookId });
        this.switchRightTab('agent');
        this.selectAgent('worldstate');
        document.getElementById('worldstateText').value = text;

        const states = res?.world_states || [];
        const container = document.getElementById('worldStateList');
        if (!states.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-globe"></i> 未命中该实体的状态记录</p>';
            return;
        }
        container.innerHTML = states.map((s) => `
            <div class="world-state-item">
                <div>
                    <span class="world-state-entity">${this.escHtml(s.entity_name)}</span>
                    <span class="world-state-type">${this.escHtml(s.state_type)}</span>
                </div>
                <div class="world-state-value">${this.escHtml(s.state_value)}</div>
            </div>
        `).join('');
        this.toast(`找到 ${states.length} 条状态记录`, 'success');
    },

    async polishSelected() {
        this.removeSelectionPopup();
        const text = window.getSelection().toString();
        if (!text) return;
        this.selectAgent('polisher');
        this.runPolisher(text);
    },

    async rewriteSelected() {
        this.removeSelectionPopup();
        const text = window.getSelection().toString();
        if (!text) return;
        this.streamInlineCommand('rewrite', text);
    },

    async expandSelected() {
        this.removeSelectionPopup();
        const text = window.getSelection().toString();
        if (!text) return;
        this.streamInlineCommand('expand_env', text);
    },

    // ==================== 行内指令 (Slash) ====================
    execSlash(command) {
        document.getElementById('slashMenu').style.display = 'none';
        // 删除刚输入的 /
        const editor = document.getElementById('editorArea');
        const text = editor.innerText;
        if (text.endsWith('/')) {
            editor.innerText = text.slice(0, -1);
        }

        // 智能续写走专用流程
        if (command === 'smart_continue') {
            this.switchRightTab('agent');
            this.selectAgent('continuation');
            this.runContinuation();
            return;
        }

        const selectedText = window.getSelection().toString().trim();
        const contextText = selectedText || text.slice(-2000);

        this.streamInlineCommand(command, contextText);
    },

    async streamInlineCommand(command, text) {
        this.isStreaming = true;
        document.getElementById('streamingControls').style.display = 'flex';
        const output = document.getElementById('agentOutput');
        output.innerHTML = '';
        this.agentOutputText = '';

        try {
            const response = await fetch('/api/inline-command', {
                method: 'POST',
                headers: this.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    command,
                    text,
                    book_id: this.currentBookId,
                    node_id: this.currentNodeId
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') break;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.text) {
                                this.agentOutputText += parsed.text;
                                output.textContent = this.agentOutputText;
                                output.scrollTop = output.scrollHeight;
                            }
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {
            this.toast('生成出错: ' + e.message, 'error');
        }

        this.isStreaming = false;
        document.getElementById('streamingControls').style.display = 'none';
    },

    async stopGeneration() {
        await this.api('/api/agent/stop', 'POST');
        this.isStreaming = false;
        document.getElementById('streamingControls').style.display = 'none';
        this.toast('已停止生成', 'info');
    },

    // ==================== 版本管理 ====================
    toggleVersions() {
        const bar = document.getElementById('versionBar');
        bar.style.display = bar.style.display === 'none' ? 'flex' : 'none';
        if (bar.style.display === 'flex') this.loadVersions();
    },

    async loadVersions() {
        if (!this.currentNodeId) return;
        const versions = await this.api(`/api/nodes/${this.currentNodeId}/versions`);
        this.versionCache = versions || [];
        const list = document.getElementById('versionList');
        if (!versions || versions.length === 0) {
            list.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">暂无版本分支</span>';
            this.fillVersionCompareOptions([]);
            return;
        }
        list.innerHTML = versions.map(v =>
            `<span class="version-chip ${v.is_active ? 'active' : ''}"
                  onclick="App.activateVersion('${v.id}')">${v.label}</span>`
        ).join('');
        this.fillVersionCompareOptions(versions);
    },

    fillVersionCompareOptions(versions) {
        const from = document.getElementById('versionDiffFrom');
        const to = document.getElementById('versionDiffTo');
        if (!from || !to) return;
        if (!versions || versions.length === 0) {
            from.innerHTML = '';
            to.innerHTML = '';
            return;
        }
        const options = versions.map((v) => `<option value="${v.id}">${this.escHtml(v.label || v.id)}</option>`).join('');
        from.innerHTML = options;
        to.innerHTML = options;
        const active = versions.find((v) => v.is_active) || versions[0];
        from.value = active.id;
        const other = versions.find((v) => v.id !== active.id) || active;
        to.value = other.id;
    },

    async createVersion() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }
        const label = prompt('版本标签 (如 A, B, C)：', String.fromCharCode(65 + Math.floor(Math.random() * 26)));
        if (!label) return;
        const content = document.getElementById('editorArea').innerText;
        await this.api(`/api/nodes/${this.currentNodeId}/versions`, 'POST', {
            label,
            content,
            is_active: 0
        });
        this.loadVersions();
        this.toast(`版本 ${label} 已创建`, 'success');
    },

    async createABCVersions() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }
        const existing = this.versionCache.length ? this.versionCache : (await this.api(`/api/nodes/${this.currentNodeId}/versions`)) || [];
        const labels = new Set(existing.map((v) => String(v.label || '').trim().toUpperCase()));
        const missing = ['A', 'B', 'C'].filter((x) => !labels.has(x));
        if (!missing.length) {
            this.toast('A/B/C 分支已存在', 'info');
            return;
        }
        const content = document.getElementById('editorArea').innerText;
        for (const label of missing) {
            await this.api(`/api/nodes/${this.currentNodeId}/versions`, 'POST', {
                label,
                content,
                is_active: 0
            });
        }
        await this.loadVersions();
        this.toast(`已补齐分支: ${missing.join('/')}`, 'success');
    },

    async activateVersion(verId) {
        if (!this.currentNodeId) return;
        await this.api(`/api/nodes/${this.currentNodeId}/versions/${verId}/activate`, 'POST');
        // Reload content
        const content = await this.api(`/api/nodes/${this.currentNodeId}/content`);
        if (content) {
            document.getElementById('editorArea').innerText = content.content || '';
            this.updateWordCount();
        }
        this.loadVersions();
        this.toast('已切换版本', 'success');
    },

    // ==================== Diff 对比 ====================
    async showDiffView() {
        if (!this.agentOutputText) {
            this.toast('无 Agent 输出，已切换为分支对比模式', 'info');
            await this.compareVersions();
            return;
        }
        this.diffOldText = document.getElementById('editorArea').innerText;
        this.diffNewText = this.agentOutputText;

        const res = await this.api('/api/diff', 'POST', {
            old_text: this.diffOldText,
            new_text: this.diffNewText
        });

        if (res && res.lines) {
            const view = document.getElementById('diffView');
            view.innerHTML = res.lines.map(l =>
                `<div class="diff-line diff-${l.type}">${l.type === 'insert' ? '+ ' : l.type === 'delete' ? '- ' : '  '}${this.escHtml(l.text)}</div>`
            ).join('');
            document.getElementById('diffModal').style.display = 'flex';
        }
    },

    async compareVersions() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }
        const versions = this.versionCache.length ? this.versionCache : (await this.api(`/api/nodes/${this.currentNodeId}/versions`)) || [];
        if (!versions || versions.length < 2) {
            this.toast('至少需要两个分支才能对比', 'warning');
            return;
        }
        const fromId = document.getElementById('versionDiffFrom')?.value;
        const toId = document.getElementById('versionDiffTo')?.value;
        if (!fromId || !toId || fromId === toId) {
            this.toast('请选择两个不同分支', 'warning');
            return;
        }
        const fromVer = versions.find((v) => v.id === fromId);
        const toVer = versions.find((v) => v.id === toId);
        if (!fromVer || !toVer) {
            this.toast('分支信息已过期，请刷新后重试', 'warning');
            await this.loadVersions();
            return;
        }
        this.diffOldText = fromVer.content || '';
        this.diffNewText = toVer.content || '';

        const res = await this.api('/api/diff', 'POST', {
            old_text: this.diffOldText,
            new_text: this.diffNewText
        });
        if (res && res.lines) {
            const view = document.getElementById('diffView');
            const header = `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">分支对比：${this.escHtml(fromVer.label)} → ${this.escHtml(toVer.label)}</div>`;
            view.innerHTML = header + res.lines.map(l =>
                `<div class="diff-line diff-${l.type}">${l.type === 'insert' ? '+ ' : l.type === 'delete' ? '- ' : '  '}${this.escHtml(l.text)}</div>`
            ).join('');
            document.getElementById('diffModal').style.display = 'flex';
        }
    },

    acceptDiff() {
        document.getElementById('editorArea').innerText = this.diffNewText;
        this.saveContent();
        this.closeDiff();
        this.toast('已应用新版本', 'success');
    },

    closeDiff() {
        document.getElementById('diffModal').style.display = 'none';
    },

    // ==================== 左侧面板标签切换 ====================
    switchLeftTab(tab) {
        document.querySelectorAll('#panelLeft .panel-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`#panelLeft .panel-tab[data-tab="${tab}"]`).classList.add('active');
        document.querySelectorAll('#panelLeft .tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
    },

    switchRightTab(tab) {
        document.querySelectorAll('#panelRight .panel-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`#panelRight .panel-tab[data-tab="${tab}"]`).classList.add('active');
        document.querySelectorAll('#panelRight .tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
        if (tab === 'memory') {
            this.loadCharacterReminders();
        }
    },

    // ==================== Lorebook 设定集 ====================
    async loadLorebook() {
        if (!this.currentBookId) return;
        const entries = await this.api(`/api/lorebook/${this.currentBookId}`);
        const list = document.getElementById('lorebookList');
        if (!entries || entries.length === 0) {
            list.innerHTML = '<div class="empty-state"><i class="fas fa-book-atlas"></i><p>暂无设定条目<br>点击新条目添加角色、地点等</p></div>';
            return;
        }
        list.innerHTML = entries.map(e => `
            <div class="lore-entry ${e.category}" onclick="App.editLorebookEntry('${e.id}')">
                <div class="lore-entry-name">
                    ${this.escHtml(e.name)}
                    <span class="lore-entry-category">${this.categoryLabel(e.category)}</span>
                </div>
                <div class="lore-entry-desc">${this.escHtml(e.description || e.content?.substring(0, 80) || '')}</div>
                <div class="lore-entry-actions">
                    <button class="btn btn-xs btn-ghost" onclick="event.stopPropagation(); App.deleteLorebookEntry('${e.id}')">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
    },

    categoryLabel(cat) {
        const labels = { character: '角色', location: '地点', item: '物品', faction: '派系', law: '法则' };
        return labels[cat] || cat;
    },

    addLorebookEntry() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        document.getElementById('loreEntryId').value = '';
        document.getElementById('loreName').value = '';
        document.getElementById('loreCategory').value = 'character';
        document.getElementById('loreDesc').value = '';
        document.getElementById('loreKeywords').value = '';
        document.getElementById('loreContent').value = '';
        document.getElementById('lorebookModalTitle').innerHTML = '<i class="fas fa-book-atlas"></i> 新建设定';
        document.getElementById('lorebookModal').style.display = 'flex';
    },

    async editLorebookEntry(entryId) {
        const entries = await this.api(`/api/lorebook/${this.currentBookId}`);
        const entry = entries?.find(e => e.id === entryId);
        if (!entry) return;

        document.getElementById('loreEntryId').value = entry.id;
        document.getElementById('loreName').value = entry.name;
        document.getElementById('loreCategory').value = entry.category;
        document.getElementById('loreDesc').value = entry.description || '';
        document.getElementById('loreKeywords').value = entry.keywords || '';
        document.getElementById('loreContent').value = entry.content || '';
        document.getElementById('lorebookModalTitle').innerHTML = '<i class="fas fa-book-atlas"></i> 编辑设定';
        document.getElementById('lorebookModal').style.display = 'flex';
    },

    async saveLorebookEntry() {
        const entryId = document.getElementById('loreEntryId').value;
        const data = {
            name: document.getElementById('loreName').value,
            category: document.getElementById('loreCategory').value,
            description: document.getElementById('loreDesc').value,
            keywords: document.getElementById('loreKeywords').value,
            content: document.getElementById('loreContent').value,
        };

        if (!data.name) { this.toast('请输入名称', 'warning'); return; }

        if (entryId) {
            await this.api(`/api/lorebook/${this.currentBookId}/${entryId}`, 'PUT', data);
        } else {
            await this.api(`/api/lorebook/${this.currentBookId}`, 'POST', data);
        }
        this.closeLorebookModal();
        this.loadLorebook();
        this.toast('设定已保存', 'success');
    },

    async deleteLorebookEntry(entryId) {
        if (!confirm('确定删除此设定条目？')) return;
        await this.api(`/api/lorebook/${this.currentBookId}/${entryId}`, 'DELETE');
        this.loadLorebook();
    },

    closeLorebookModal() {
        document.getElementById('lorebookModal').style.display = 'none';
    },

    filterLorebook() {
        // reload and filter client-side
        this.loadLorebook();
    },

    // ==================== 实体图谱 ====================
    async loadEntityGraph() {
        if (!this.currentBookId) return;
        const graph = await this.api(`/api/entity-graph/${this.currentBookId}`);
        const container = document.getElementById('entityGraph');
        if (!graph || graph.length === 0) {
            container.innerHTML = '<div class="empty-state"><i class="fas fa-diagram-project"></i><p>暂无实体关系<br>点击添加角色间的关系</p></div>';
            return;
        }
        container.innerHTML = graph.map(r => `
            <div class="graph-relation">
                <span class="graph-entity">${this.escHtml(r.source_entity)}</span>
                <span class="graph-arrow">→</span>
                <span class="graph-entity">${this.escHtml(r.target_entity)}</span>
                <span class="graph-rel-type">${this.escHtml(r.relation_type)}</span>
                <span class="graph-rel-value">${this.escHtml(r.relation_value)}</span>
            </div>
        `).join('');
    },

    addRelation() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        document.getElementById('relSource').value = '';
        document.getElementById('relTarget').value = '';
        document.getElementById('relType').value = '好感度';
        document.getElementById('relValue').value = '';
        document.getElementById('relationModal').style.display = 'flex';
    },

    async saveRelation() {
        const source = document.getElementById('relSource').value.trim();
        const target = document.getElementById('relTarget').value.trim();
        const type = document.getElementById('relType').value;
        const value = document.getElementById('relValue').value;

        if (!source || !target) { this.toast('请填写实体名称', 'warning'); return; }

        // Fetch existing, add new, save all
        const existing = await this.api(`/api/entity-graph/${this.currentBookId}`) || [];
        existing.push({ source_entity: source, target_entity: target, relation_type: type, relation_value: value });

        await this.api(`/api/entity-graph/${this.currentBookId}`, 'POST', {
            relations: existing.map(r => ({
                source: r.source_entity || r.source,
                target: r.target_entity || r.target,
                type: r.relation_type || r.type,
                value: r.relation_value || r.value
            }))
        });

        this.closeRelationModal();
        this.loadEntityGraph();
        this.toast('关系已保存', 'success');
    },

    closeRelationModal() {
        document.getElementById('relationModal').style.display = 'none';
    },

    // ==================== Agent 面板 ====================
    selectAgent(agent) {
        document.querySelectorAll('.agent-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`.agent-btn[data-agent="${agent}"]`)?.classList.add('active');

        document.querySelectorAll('.agent-panel').forEach(p => p.classList.remove('active'));
        const panels = {
            planner: 'agentPlanner', beats: 'agentBeats', drafter: 'agentDrafter',
            validator: 'agentValidator', polisher: 'agentPolisher',
            continuation: 'agentContinuation', conflict: 'agentConflict', brainstorm: 'agentBrainstorm',
            foreshadow: 'agentForeshadow', subtext: 'agentSubtext', psychology: 'agentPsychology',
            worldstate: 'agentWorldstate', plansolve: 'agentPlansolve', hallcheck: 'agentHallcheck'
        };
        document.getElementById(panels[agent])?.classList.add('active');
    },

    async runPlanner() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const inspiration = document.getElementById('plannerInspiration').value;
        if (!inspiration) { this.toast('请输入灵感或概述', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 架构师正在规划大纲...';

        const res = await this.api('/api/agent/plan', 'POST', {
            inspiration,
            book_id: this.currentBookId,
            genre: document.getElementById('plannerGenre').value,
            volume_count: parseInt(document.getElementById('plannerVolumes').value),
            chapters_per_volume: parseInt(document.getElementById('plannerChapters').value)
        });

        if (res && res.outline) {
            this.agentOutputText = res.outline;
            output.textContent = res.outline;
            this.toast('大纲生成完成', 'success');
        }
    },

    async runBeats() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const outline = document.getElementById('beatsOutline').value;
        if (!outline) { this.toast('请输入章节大纲', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 节拍器正在拆解场景...';

        const res = await this.api('/api/agent/beats', 'POST', {
            chapter_outline: outline,
            book_id: this.currentBookId
        });

        if (res && res.beats) {
            this.agentOutputText = res.beats;
            output.textContent = res.beats;
            this.toast('场景节拍生成完成', 'success');
        }
    },

    async runDrafter() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }

        // 检查是否启用幻觉防护
        const guardEnabled = document.getElementById('drafterGuardToggle')?.checked ||
                             document.getElementById('hallucinationGuardToggle')?.checked;
        const endpoint = guardEnabled ? '/api/agent/draft-guarded' : '/api/agent/draft';

        this.isStreaming = true;
        document.getElementById('streamingControls').style.display = 'flex';
        const output = document.getElementById('agentOutput');
        output.innerHTML = '';
        this.agentOutputText = '';
        this._lastHallucinationConflicts = null;
        this.guardHardBlocked = false;

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: this.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    beat: document.getElementById('drafterBeat').value,
                    style: document.getElementById('drafterStyle').value,
                    book_id: this.currentBookId,
                    node_id: this.currentNodeId,
                    previous_text: document.getElementById('editorArea').innerText
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') break;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.text) {
                                // Parse guard markers
                                const txt = parsed.text;
                                if (txt.startsWith('[GUARD:')) {
                                    this._handleGuardMarker(txt);
                                } else if (txt.startsWith('[HALLUCINATION_ALERT]')) {
                                    const conflictJson = txt.replace('[HALLUCINATION_ALERT]', '');
                                    try {
                                        this._lastHallucinationConflicts = JSON.parse(conflictJson);
                                    } catch(e2) {}
                                    this._showHallucinationAlert(this._lastHallucinationConflicts);
                                } else {
                                    this.agentOutputText += txt;
                                    output.textContent = this.agentOutputText;
                                    output.scrollTop = output.scrollHeight;
                                }
                            }
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {
            this.toast('生成出错: ' + e.message, 'error');
        }

        this.isStreaming = false;
        document.getElementById('streamingControls').style.display = 'none';
        if (this.guardHardBlocked) {
            this.toast('幻觉防护拦截：请重采样后再上屏', 'warning');
        } else {
            this.toast('创作完成', 'success');
        }
    },

    async runValidator() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('编辑器内容为空', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 验证者正在审查文本...';

        const res = await this.api('/api/agent/validate', 'POST', {
            text,
            book_id: this.currentBookId
        });

        if (res && res.validation) {
            this.agentOutputText = res.validation;
            output.textContent = res.validation;
            this.toast('校验完成', 'success');
        }
    },

    async runPolisher(selectedText = null) {
        const text = selectedText || window.getSelection().toString() || document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请选中文本或确保编辑器有内容', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 润色 Agent 处理中...';

        const res = await this.api('/api/agent/polish', 'POST', {
            text,
            style: document.getElementById('polisherStyle').value,
            instruction: document.getElementById('polisherInstruction').value
        });

        if (res && res.polished) {
            this.agentOutputText = res.polished;
            this.diffOldText = res.original;
            this.diffNewText = res.polished;
            output.textContent = res.polished;
            this.toast('润色完成，可使用 Diff 对比查看修改', 'success');
        }
    },

    applyAgentOutput() {
        if (!this.agentOutputText) { this.toast('无可用输出', 'warning'); return; }
        if (!this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }
        if (this.guardHardBlocked) {
            this.toast('当前文本触发幻觉拦截，请先重采样或手工修正', 'error');
            this.switchRightTab('agent');
            this.selectAgent('hallcheck');
            return;
        }

        const editor = document.getElementById('editorArea');
        const currentText = editor.innerText;
        // 追加到编辑器末尾
        editor.innerText = currentText + (currentText ? '\n\n' : '') + this.agentOutputText;
        this.saveContent();
        this.updateWordCount();
        this.toast('已应用到编辑器', 'success');
    },

    clearAgentOutput() {
        document.getElementById('agentOutput').innerHTML = '';
        this.agentOutputText = '';
        this.guardHardBlocked = false;
    },

    // ==================== 记忆面板 ====================
    async loadSummaries() {
        if (!this.currentBookId) return;
        const summaries = await this.api(`/api/memory/summary/${this.currentBookId}`);
        const list = document.getElementById('summaryList');
        if (!summaries || summaries.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>暂无摘要记录</p></div>';
            return;
        }
        list.innerHTML = summaries.map(s => `
            <div class="summary-item">
                <div class="summary-item-title">${this.escHtml(s.chapter_title)}</div>
                <div class="summary-item-text">${this.escHtml(s.summary.substring(0, 200))}</div>
            </div>
        `).join('');
    },

    scheduleCharacterReminderRefresh() {
        if (!this.currentBookId || !this.currentNodeId || this.isStreaming) return;
        clearTimeout(this.characterReminderTimer);
        this.characterReminderTimer = setTimeout(() => this.loadCharacterReminders(), 900);
    },

    async loadCharacterReminders(text = null) {
        const container = document.getElementById('characterReminderList');
        if (!container) return;
        if (!this.currentBookId) {
            container.innerHTML = '<div class="empty-state"><p>请选择书籍后查看人物提醒</p></div>';
            return;
        }

        const payload = {
            book_id: this.currentBookId,
            node_id: this.currentNodeId,
            text: text !== null ? text : (document.getElementById('editorArea')?.innerText || '')
        };
        const res = await this.api('/api/character-reminders', 'POST', payload);
        this.renderCharacterReminders(res?.characters || []);
    },

    renderCharacterReminders(characters) {
        const container = document.getElementById('characterReminderList');
        if (!container) return;
        if (!characters || characters.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>当前章节尚未识别到人物，或尚无可用历史记录</p></div>';
            return;
        }

        container.innerHTML = characters.map((character) => {
            const personality = character.personality ?
                `<div class="character-reminder-personality">${this.escHtml(character.personality)}</div>` : '';
            const lastSeen = character.last_seen_chapter ?
                `<span class="character-reminder-chip"><i class="fas fa-bookmark"></i> 最近出场：${this.escHtml(character.last_seen_chapter)}</span>` : '';
            const matched = character.matched_terms?.length ?
                `<span class="character-reminder-chip"><i class="fas fa-crosshairs"></i> 命中：${this.escHtml(character.matched_terms.join(' / '))}</span>` : '';
            const history = (character.recent_history || []).map((item) => `
                <div class="character-history-item ${item.is_manual ? 'manual' : ''}">
                    <div class="character-history-main">
                        <span class="character-history-type">${this.escHtml(item.entry_type || 'event')}</span>
                        <span class="character-history-text">${this.escHtml(item.summary || '')}</span>
                    </div>
                    ${item.chapter_title ? `<div class="character-history-meta">章节：${this.escHtml(item.chapter_title)}</div>` : ''}
                    ${item.source_excerpt ? `<div class="character-history-excerpt">${this.escHtml(item.source_excerpt)}</div>` : ''}
                    ${item.is_manual ? `<div class="character-history-actions">
                        <button class="btn btn-xs btn-ghost" onclick="App.editCharacterHistory('${item.id}', '${this.escJs(item.summary || '')}', '${this.escJs(item.details || '')}', '${this.escJs(character.name || '')}', '${this.escJs(item.entry_type || 'note')}')"><i class="fas fa-pen"></i></button>
                        <button class="btn btn-xs btn-ghost" onclick="App.deleteCharacterHistory('${item.id}')"><i class="fas fa-trash"></i></button>
                    </div>` : ''}
                </div>
            `).join('');
            const foreshadowing = (character.foreshadowing || []).map((item) => `
                <div class="character-reminder-subitem">
                    <strong>${this.escHtml(item.label || '伏笔')}</strong>
                    <span>${this.escHtml(item.description || item.text || '')}</span>
                </div>
            `).join('');
            const states = (character.world_state || []).map((item) => `
                <div class="character-reminder-subitem compact">
                    <strong>${this.escHtml(item.state_type || '状态')}</strong>
                    <span>${this.escHtml(item.state_value || '')}</span>
                </div>
            `).join('');

            return `
                <div class="character-reminder-card">
                    <div class="character-reminder-header">
                        <div>
                            <div class="character-reminder-name">${this.escHtml(character.name || '')}</div>
                            <div class="character-reminder-meta">${lastSeen}${matched}</div>
                        </div>
                        <div class="character-reminder-actions">
                            <button class="btn btn-xs btn-ghost" onclick="App.addCharacterHistory('${this.escJs(character.name || '')}')"><i class="fas fa-plus"></i></button>
                        </div>
                    </div>
                    ${personality}
                    ${history ? `<div class="character-reminder-block"><div class="character-reminder-block-title">历史</div>${history}</div>` : ''}
                    ${foreshadowing ? `<div class="character-reminder-block"><div class="character-reminder-block-title">未回收伏笔</div>${foreshadowing}</div>` : ''}
                    ${states ? `<div class="character-reminder-block"><div class="character-reminder-block-title">当前状态</div>${states}</div>` : ''}
                </div>
            `;
        }).join('');
    },

    async refreshCharacterHistory(mode = 'node') {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        if (mode === 'node' && !this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }

        const payload = mode === 'node' ? {
            node_id: this.currentNodeId,
            chapter_title: document.getElementById('editorPath')?.textContent || '',
            text: document.getElementById('editorArea')?.innerText || ''
        } : {};

        const res = await this.api(`/api/character-history/${this.currentBookId}/refresh`, 'POST', payload);
        if (res && !res.error) {
            this.loadCharacterReminders();
            this.toast(mode === 'node'
                ? `当前章节人物历史已刷新，新增 ${res.created_entries || 0} 条`
                : `全书回填完成：${res.refreshed_nodes || 0} 个章节，新增 ${res.created_entries || 0} 条`, 'success');
        }
    },

    async addCharacterHistory(characterName = '') {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const name = (characterName || prompt('角色名：', '') || '').trim();
        if (!name) return;
        const entryType = (prompt('记录类型（event/note/personality/foreshadow）：', 'note') || 'note').trim() || 'note';
        const summary = (prompt('简述这条人物记录：', '') || '').trim();
        if (!summary) { this.toast('记录摘要不能为空', 'warning'); return; }
        const details = prompt('详细说明（可选）：', '') || '';

        const res = await this.api(`/api/character-history/${this.currentBookId}`, 'POST', {
            character_name: name,
            entry_type: entryType,
            summary,
            details,
            source_node_id: this.currentNodeId,
            chapter_title: document.getElementById('editorPath')?.textContent || '',
            is_manual: true
        });
        if (res && res.id) {
            this.loadCharacterReminders();
            this.toast('人物记录已添加', 'success');
        }
    },

    async editCharacterHistory(historyId, currentSummary = '', currentDetails = '', characterName = '', entryType = 'note') {
        if (!this.currentBookId || !historyId) return;
        const nextSummary = prompt(`编辑 ${characterName || '角色'} 的记录摘要：`, currentSummary || '');
        if (nextSummary === null) return;
        const nextDetails = prompt('编辑详细说明：', currentDetails || '');
        if (nextDetails === null) return;
        const nextType = prompt('记录类型（event/note/personality/foreshadow）：', entryType || 'note');
        if (nextType === null) return;

        const res = await this.api(`/api/character-history/${this.currentBookId}/${historyId}`, 'PUT', {
            summary: nextSummary,
            details: nextDetails,
            entry_type: nextType,
            is_manual: true
        });
        if (res && !res.error) {
            this.loadCharacterReminders();
            this.toast('人物记录已更新', 'success');
        }
    },

    async deleteCharacterHistory(historyId) {
        if (!this.currentBookId || !historyId) return;
        if (!confirm('确定删除这条人物记录？')) return;
        const res = await this.api(`/api/character-history/${this.currentBookId}/${historyId}`, 'DELETE');
        if (res && !res.error) {
            this.loadCharacterReminders();
            this.toast('人物记录已删除', 'success');
        }
    },

    async loadTensionDiagnostics() {
        if (!this.currentBookId) return;
        const summary = document.getElementById('tensionSummary');
        const chart = document.getElementById('tensionChart');
        const warnings = document.getElementById('tensionWarnings');
        summary.textContent = '诊断中...';
        chart.innerHTML = '<div class="loading-spinner"></div>';
        warnings.innerHTML = '';

        const res = await this.api(`/api/diagnostics/tension/${this.currentBookId}`);
        if (!res || res.error) {
            summary.textContent = '诊断失败';
            chart.innerHTML = '<div class="empty-state"><p>无法读取张力曲线</p></div>';
            return;
        }
        this.renderTensionDiagnostics(res);
    },

    renderTensionDiagnostics(data) {
        const summary = document.getElementById('tensionSummary');
        const chart = document.getElementById('tensionChart');
        const warnings = document.getElementById('tensionWarnings');
        const chapters = data?.chapters || [];
        const avg = data?.average_tension ?? 0;

        if (!chapters.length) {
            summary.textContent = '暂无章节内容可诊断';
            chart.innerHTML = '<div class="empty-state"><p>请先写入章节内容</p></div>';
            warnings.innerHTML = '';
            return;
        }

        summary.textContent = `平均张力 ${avg}/100 · 已分析 ${chapters.length} 个章节`;

        const width = 580;
        const height = 150;
        const padX = 26;
        const padY = 14;
        const plotW = width - padX * 2;
        const plotH = height - padY * 2;
        const pointX = (idx) => padX + (chapters.length <= 1 ? plotW / 2 : (plotW * idx) / (chapters.length - 1));
        const pointY = (score) => {
            const s = Math.max(0, Math.min(100, Number(score) || 0));
            return height - padY - (s / 100) * plotH;
        };

        const points = chapters.map((c, i) => `${pointX(i)},${pointY(c.tension_score)}`).join(' ');
        const labelStep = Math.max(1, Math.ceil(chapters.length / 6));
        const gridLines = [25, 50, 75].map((v) => {
            const y = pointY(v);
            return `<line class="tension-grid-line" x1="${padX}" y1="${y}" x2="${width - padX}" y2="${y}"></line>`;
        }).join('');

        let pointsSvg = '';
        chapters.forEach((c, i) => {
            const x = pointX(i);
            const y = pointY(c.tension_score);
            pointsSvg += `<circle class="tension-dot" cx="${x}" cy="${y}" r="3.5"></circle>`;
            pointsSvg += `<text class="tension-value" x="${x}" y="${Math.max(10, y - 6)}" text-anchor="middle">${c.tension_score}</text>`;
            if (i % labelStep === 0 || i === chapters.length - 1) {
                const shortTitle = this.escHtml((c.title || `章节${i + 1}`).slice(0, 6));
                pointsSvg += `<text class="tension-label" x="${x}" y="${height - 2}" text-anchor="middle">${shortTitle}</text>`;
            }
        });

        chart.innerHTML = `
            <svg class="tension-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
                ${gridLines}
                <polyline class="tension-line" points="${points}"></polyline>
                ${pointsSvg}
            </svg>
        `;

        const warns = data?.warnings || [];
        if (!warns.length) {
            warnings.innerHTML = '';
            return;
        }
        warnings.innerHTML = warns.map((w) =>
            `<div class="tension-warning-item">[${this.escHtml(w.type || '提醒')}] ${this.escHtml(w.message || '')}</div>`
        ).join('');
    },

    async generateSummary() {
        if (!this.currentNodeId || !this.currentBookId) { this.toast('请先选择章节', 'warning'); return; }
        const text = document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('当前章节无内容', 'warning'); return; }

        const node = await this.api(`/api/nodes/${this.currentNodeId}`);
        this.toast('正在生成摘要...', 'info');

        const res = await this.api('/api/agent/summarize', 'POST', {
            text,
            chapter_title: node?.title || '',
            book_id: this.currentBookId,
            node_id: this.currentNodeId
        });

        if (res) {
            this.loadSummaries();
            this.loadCharacterReminders();
            this.toast('摘要已生成', 'success');
        }
    },

    async lookupEntity() {
        const text = document.getElementById('entitySearch').value.trim();
        if (!text || !this.currentBookId) return;

        const res = await this.api('/api/lookup', 'POST', { text, book_id: this.currentBookId });
        const container = document.getElementById('entityResults');
        let html = '';

        if (res?.entries?.length > 0) {
            res.entries.forEach(e => {
                html += `<div class="entity-result-item">
                    <div class="entity-result-name">${this.escHtml(e.name)} <small>(${this.categoryLabel(e.category)})</small></div>
                    <div class="entity-result-content">${this.escHtml(e.content || e.description || '')}</div>
                </div>`;
            });
        }
        if (res?.relations?.length > 0) {
            res.relations.forEach(r => {
                html += `<div class="entity-result-item">
                    <div class="entity-result-name">${r.source_entity} → ${r.target_entity}</div>
                    <div class="entity-result-content">${r.relation_type}: ${r.relation_value}</div>
                </div>`;
            });
        }
        if (res?.world_states?.length > 0) {
            res.world_states.forEach(s => {
                html += `<div class="entity-result-item">
                    <div class="entity-result-name">${this.escHtml(s.entity_name)} <small>(状态)</small></div>
                    <div class="entity-result-content">${this.escHtml(s.state_type)}: ${this.escHtml(s.state_value)}</div>
                </div>`;
            });
        }
        if (!html) html = '<div class="empty-state"><p>未找到相关设定</p></div>';
        container.innerHTML = html;
    },

    // ==================== 设置面板 ====================
    async openSettings() {
        document.getElementById('settingsModal').style.display = 'flex';
        this.loadModels();
        this.loadGenParams();
        this.loadTokenStats();
    },

    closeSettings() {
        document.getElementById('settingsModal').style.display = 'none';
    },

    switchSettingsTab(tab) {
        document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        event.target.classList.add('active');
        document.querySelectorAll('.settings-content').forEach(c => c.classList.remove('active'));
        const map = { models: 'settingsModels', routing: 'settingsRouting', params: 'settingsParams', tokens: 'settingsTokens' };
        document.getElementById(map[tab]).classList.add('active');

        if (tab === 'routing') this.loadRoutingGrid();
        if (tab === 'tokens') this.loadTokenStats();
    },

    // 模型管理
    async loadModels() {
        const models = await this.api('/api/models');
        const list = document.getElementById('modelList');
        if (!models || models.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>未添加模型配置<br>请点击"添加模型"</p></div>';
            return;
        }
        list.innerHTML = models.map(m => `
            <div class="model-card">
                <div class="model-card-info">
                    <div class="model-card-name">${this.escHtml(m.name)}</div>
                    <div class="model-card-detail">${m.provider} | ${m.model_id} | Key: ${m.api_key_display} | Max: ${m.max_context}</div>
                </div>
                <div class="model-card-actions">
                    <button class="btn btn-xs btn-ghost" onclick="App.editModel('${m.id}')"><i class="fas fa-pen"></i></button>
                    <button class="btn btn-xs btn-ghost" onclick="App.removeModel('${m.id}')"><i class="fas fa-trash"></i></button>
                </div>
            </div>
        `).join('');
    },

    addModel() {
        document.getElementById('modelFormId').value = '';
        document.getElementById('modelName').value = '';
        document.getElementById('modelProvider').value = 'openai';
        document.getElementById('modelBaseUrl').value = '';
        document.getElementById('modelApiKey').value = '';
        document.getElementById('modelModelId').value = '';
        document.getElementById('modelMaxCtx').value = '8192';
        document.getElementById('modelFormTitle').textContent = '添加模型';
        document.getElementById('modelForm').style.display = 'block';
    },

    async editModel(id) {
        const models = await this.api('/api/models');
        const m = models?.find(x => x.id === id);
        if (!m) return;

        document.getElementById('modelFormId').value = m.id;
        document.getElementById('modelName').value = m.name;
        document.getElementById('modelProvider').value = m.provider;
        document.getElementById('modelBaseUrl').value = m.base_url;
        document.getElementById('modelApiKey').value = m.api_key;
        document.getElementById('modelModelId').value = m.model_id;
        document.getElementById('modelMaxCtx').value = m.max_context;
        document.getElementById('modelFormTitle').textContent = '编辑模型';
        document.getElementById('modelForm').style.display = 'block';
    },

    async saveModel() {
        const id = document.getElementById('modelFormId').value;
        const data = {
            name: document.getElementById('modelName').value,
            provider: document.getElementById('modelProvider').value,
            base_url: document.getElementById('modelBaseUrl').value,
            api_key: document.getElementById('modelApiKey').value,
            model_id: document.getElementById('modelModelId').value,
            max_context: parseInt(document.getElementById('modelMaxCtx').value)
        };

        if (!data.name || !data.base_url) { this.toast('请填写名称和 Base URL', 'warning'); return; }

        if (id) {
            await this.api(`/api/models/${id}`, 'PUT', data);
        } else {
            await this.api('/api/models', 'POST', data);
        }
        this.cancelModelForm();
        this.loadModels();
        this.toast('模型已保存', 'success');
    },

    cancelModelForm() {
        document.getElementById('modelForm').style.display = 'none';
    },

    async removeModel(id) {
        if (!confirm('确定删除此模型配置？')) return;
        await this.api(`/api/models/${id}`, 'DELETE');
        this.loadModels();
    },

    // 路由配置
    async loadRoutingGrid() {
        const models = await this.api('/api/models');
        const routing = await this.api('/api/routing');
        const roles = [
            { id: 'planner', name: '架构师', icon: 'fa-sitemap' },
            { id: 'beat_generator', name: '节拍器', icon: 'fa-music' },
            { id: 'drafter', name: '执笔者', icon: 'fa-pen-nib' },
            { id: 'validator', name: '验证者', icon: 'fa-check-double' },
            { id: 'polisher', name: '润色', icon: 'fa-gem' },
            { id: 'summarizer', name: '摘要', icon: 'fa-scroll' },
            { id: 'autocomplete', name: '自动补全', icon: 'fa-magic' },
            { id: 'association', name: '联想', icon: 'fa-lightbulb' },
            { id: 'plan_and_solve', name: 'Plan模式', icon: 'fa-layer-group' },
            { id: 'hallucination', name: '幻觉检测', icon: 'fa-shield-halved' },
        ];

        const grid = document.getElementById('routingGrid');
        grid.innerHTML = roles.map(r => {
            const opts = models?.map(m =>
                `<option value="${m.id}" ${routing?.[r.id] === m.id ? 'selected' : ''}>${m.name}</option>`
            ).join('') || '';
            return `
                <div class="routing-row">
                    <div class="routing-role"><i class="fas ${r.icon}"></i> ${r.name}</div>
                    <select class="routing-select" data-role="${r.id}">
                        <option value="">— 使用默认 —</option>
                        ${opts}
                    </select>
                </div>`;
        }).join('');
    },

    async saveRouting() {
        const data = {};
        document.querySelectorAll('.routing-select').forEach(sel => {
            if (sel.value) data[sel.dataset.role] = sel.value;
        });
        await this.api('/api/routing', 'POST', data);
        this.toast('路由配置已保存', 'success');
    },

    // 生成参数
    async loadGenParams() {
        const params = await this.api('/api/generation-params');
        if (params) {
            document.getElementById('paramTemp').value = params.temperature ?? 0.7;
            document.getElementById('tempValue').textContent = (params.temperature ?? 0.7).toFixed(2);
            document.getElementById('paramTopP').value = params.top_p ?? 0.9;
            document.getElementById('topPValue').textContent = (params.top_p ?? 0.9).toFixed(2);
            document.getElementById('paramPP').value = params.presence_penalty ?? 0;
            document.getElementById('ppValue').textContent = (params.presence_penalty ?? 0).toFixed(2);
            document.getElementById('paramFP').value = params.frequency_penalty ?? 0;
            document.getElementById('fpValue').textContent = (params.frequency_penalty ?? 0).toFixed(2);
            document.getElementById('paramMT').value = params.max_tokens ?? 2000;
            document.getElementById('mtValue').textContent = params.max_tokens ?? 2000;
        }
    },

    async saveGenParams() {
        const data = {
            temperature: parseFloat(document.getElementById('paramTemp').value),
            top_p: parseFloat(document.getElementById('paramTopP').value),
            presence_penalty: parseFloat(document.getElementById('paramPP').value),
            frequency_penalty: parseFloat(document.getElementById('paramFP').value),
            max_tokens: parseInt(document.getElementById('paramMT').value),
        };
        await this.api('/api/generation-params', 'POST', data);
        this.toast('生成参数已保存', 'success');
    },

    async loadTokenStats() {
        const stats = await this.api('/api/token-stats');
        const container = document.getElementById('tokenStats');
        if (!stats || stats.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>暂无 Token 消耗记录</p></div>';
            return;
        }
        let totalTokens = 0;
        const html = stats.map(s => {
            totalTokens += s.grand_total || 0;
            return `<div class="token-stat-card">
                <div>
                    <div class="token-stat-label">${s.role || '未知'}</div>
                    <div style="font-size:11px;color:var(--text-muted)">Prompt: ${s.total_prompt?.toLocaleString() || 0} | Completion: ${s.total_completion?.toLocaleString() || 0}</div>
                </div>
                <div class="token-stat-value">${(s.grand_total || 0).toLocaleString()}</div>
            </div>`;
        }).join('');
        container.innerHTML = `<div class="token-stat-card" style="border-left:3px solid var(--accent);">
            <div class="token-stat-label">总计</div>
            <div class="token-stat-value">${totalTokens.toLocaleString()}</div>
        </div>` + html;

        document.getElementById('tokenDisplay').textContent = `Token: ${totalTokens.toLocaleString()}`;
    },

    // ==================== 导出/导入 ====================
    showExportMenu() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        document.getElementById('exportModal').style.display = 'flex';
    },

    closeExportMenu() {
        document.getElementById('exportModal').style.display = 'none';
    },

    async doExport(format) {
        if (!this.currentBookId) return;
        try {
            const res = await fetch(`/api/export/${this.currentBookId}/${format}`, {
                method: 'GET',
                headers: this.authHeaders()
            });
            if (!res.ok) {
                throw new Error(`导出失败(${res.status})`);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const contentDisposition = res.headers.get('Content-Disposition') || '';
            const filenameMatch = contentDisposition.match(/filename=([^;]+)/i);
            const filename = filenameMatch ? filenameMatch[1].replace(/"/g, '') : `export.${format}`;
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            this.toast(`已导出 ${format.toUpperCase()}`, 'success');
        } catch (e) {
            this.toast(`导出失败: ${e.message}`, 'error');
        }
        this.closeExportMenu();
    },

    showImportDialog() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch('/api/import', {
                    method: 'POST',
                    headers: this.authHeaders(),
                    body: formData
                });
                const data = await res.json();
                if (data.book_id) {
                    this.currentBookId = data.book_id;
                    await this.loadBooks();
                    document.getElementById('currentBook').value = data.book_id;
                    this.switchBook(data.book_id);
                    this.toast('工作空间导入成功', 'success');
                }
            } catch (e) {
                this.toast('导入失败: ' + e.message, 'error');
            }
        };
        input.click();
    },

    // ==================== 工具函数 ====================

    // ==================== 自动补全 (Ghost Text) ====================
    scheduleAutocomplete() {
        if (!this.autocompleteEnabled || this.isStreaming) return;
        clearTimeout(this.autocompleteTimer);
        // 取消正在进行的请求
        if (this.autocompleteAbort) {
            this.autocompleteAbort.abort();
            this.autocompleteAbort = null;
        }
        this.autocompleteTimer = setTimeout(() => this.requestAutocomplete(), 1200);
    },

    async requestAutocomplete() {
        if (!this.currentBookId || !this.currentNodeId || this.isStreaming) return;
        const editor = document.getElementById('editorArea');
        const text = editor.innerText;
        if (!text || text.trim().length < 20) return;
        this.loadCharacterReminders(text);

        // 显示思考中指示器
        const indicator = document.getElementById('autocompleteIndicator');
        indicator.style.display = 'flex';
        document.getElementById('autocompleteStatus').textContent = '预测中...';

        try {
            this.autocompleteAbort = new AbortController();
            const res = await fetch('/api/agent/autocomplete', {
                method: 'POST',
                headers: this.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    text: text,
                    book_id: this.currentBookId
                }),
                signal: this.autocompleteAbort.signal
            });
            const data = await res.json();

            if (data.prediction && data.prediction.trim()) {
                this.showGhostText(data.prediction.trim());
            }
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.log('Autocomplete error:', e);
            }
        } finally {
            indicator.style.display = 'none';
            this.autocompleteAbort = null;
        }
    },

    showGhostText(prediction) {
        this.ghostPrediction = prediction;
        const overlay = document.getElementById('ghostTextOverlay');
        const ghostEl = document.getElementById('ghostText');

        // 计算ghost text位置：在光标所在行的末尾
        const sel = window.getSelection();
        if (!sel.rangeCount) return;
        const range = sel.getRangeAt(0);
        const rect = range.getBoundingClientRect();
        const editorRect = document.getElementById('editorArea').getBoundingClientRect();

        ghostEl.textContent = prediction;
        overlay.style.display = 'block';
        overlay.style.left = (rect.right - editorRect.left) + 'px';
        overlay.style.top = (rect.top - editorRect.top) + 'px';
    },

    acceptGhostText() {
        if (!this.ghostPrediction) return;
        const editor = document.getElementById('editorArea');

        // 在光标位置插入预测文本
        const sel = window.getSelection();
        if (sel.rangeCount > 0) {
            const range = sel.getRangeAt(0);
            range.collapse(false);
            const textNode = document.createTextNode(this.ghostPrediction);
            range.insertNode(textNode);
            // 移动光标到插入文本末尾
            range.setStartAfter(textNode);
            range.collapse(true);
            sel.removeAllRanges();
            sel.addRange(range);
        }

        this.dismissGhostText();
        this.saveContent();
        this.updateWordCount();
        this.toast('已接受补全', 'success');
    },

    dismissGhostText() {
        this.ghostPrediction = '';
        document.getElementById('ghostTextOverlay').style.display = 'none';
        document.getElementById('ghostText').textContent = '';
        // 取消正在进行的请求
        if (this.autocompleteAbort) {
            this.autocompleteAbort.abort();
            this.autocompleteAbort = null;
        }
        clearTimeout(this.autocompleteTimer);
    },

    _buildPlanModeBeat(goal, previousText) {
        const trimmedGoal = (goal || '').trim();
        if (trimmedGoal) return trimmedGoal;

        const contextTail = (previousText || '').trim().slice(-400);
        if (contextTail) {
            return `请基于以下前文自然续写，先规划当前场景的核心冲突、人物动机与推进节奏，再输出下一段正文：\n${contextTail}`;
        }

        return '请先规划当前场景的核心冲突、人物动机和推进节奏，再输出自然衔接的下一段正文。';
    },

    // ==================== 智能续写 ====================
    async runContinuation() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }

        const mode = document.getElementById('continuationMode').value;
        const goal = document.getElementById('continuationGoal').value;
        const style = document.getElementById('continuationStyle').value;
        const previousText = document.getElementById('editorArea').innerText;

        if (mode === 'plan') {
            document.getElementById('plansolveBeat').value = this._buildPlanModeBeat(goal, previousText);
            document.getElementById('plansolveStyle').value = style;
            this.switchRightTab('agent');
            this.selectAgent('plansolve');
            await this.runPlanAndSolve();
            return;
        }

        this.isStreaming = true;
        document.getElementById('streamingControls').style.display = 'flex';
        const output = document.getElementById('agentOutput');
        output.innerHTML = '';
        this.agentOutputText = '';

        const endpoint = mode === 'critique' ? '/api/agent/continue' : '/api/agent/continue-fast';

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: this.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    book_id: this.currentBookId,
                    node_id: this.currentNodeId,
                    previous_text: previousText,
                    goal: goal,
                    style: style,
                    max_retries: 2
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') break;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.text) {
                                this.agentOutputText += parsed.text;
                                output.textContent = this.agentOutputText;
                                output.scrollTop = output.scrollHeight;
                            }
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {
            this.toast('续写出错: ' + e.message, 'error');
        }

        this.isStreaming = false;
        document.getElementById('streamingControls').style.display = 'none';
        this.toast('续写完成', 'success');
    },

    // ==================== 冲突设计 ====================
    triggerConflictDesign() {
        document.getElementById('slashMenu').style.display = 'none';
        this.switchRightTab('agent');
        this.selectAgent('conflict');
        // 自动填充上下文
        const editor = document.getElementById('editorArea');
        const text = editor.innerText;
        if (text && text.length > 50) {
            document.getElementById('conflictContext').value = text.slice(-500);
        }
    },

    async runConflictDesign() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 冲突设计Agent正在分析角色关系...';

        const res = await this.api('/api/agent/conflict', 'POST', {
            book_id: this.currentBookId,
            context: document.getElementById('conflictContext').value,
            characters: document.getElementById('conflictCharacters').value,
            conflict_type: document.getElementById('conflictType').value
        });

        if (res && res.conflicts) {
            this.lastConflictData = res.conflicts;
            this.agentOutputText = res.conflicts;
            this.renderConflictCards(res.conflicts);
            this.toast('冲突方案已生成', 'success');
        }
    },

    renderConflictCards(rawText) {
        const output = document.getElementById('agentOutput');
        // 尝试解析JSON
        try {
            let data;
            const jsonMatch = rawText.match(/\{[\s\S]*\}/);
            if (jsonMatch) {
                data = JSON.parse(jsonMatch[0]);
            } else {
                output.textContent = rawText;
                return;
            }

            let html = '';

            // 对抗矩阵
            if (data.antagonist_matrix) {
                const m = data.antagonist_matrix;
                html += `<div class="antagonist-matrix">
                    <h5><i class="fas fa-chess"></i> 对抗矩阵</h5>
                    <div class="matrix-grid">
                        <div class="matrix-item"><div class="matrix-item-label">主角</div><div class="matrix-item-value">${this.escHtml(m.protagonist || '')}</div></div>
                        <div class="matrix-item"><div class="matrix-item-label">对手</div><div class="matrix-item-value">${this.escHtml(m.antagonist || '')}</div></div>
                        <div class="matrix-item"><div class="matrix-item-label">赌注</div><div class="matrix-item-value">${this.escHtml(m.stakes || '')}</div></div>
                        <div class="matrix-item"><div class="matrix-item-label">力量对比</div><div class="matrix-item-value">${this.escHtml(m.power_dynamic || '')}</div></div>
                        <div class="matrix-item" style="grid-column:span 2"><div class="matrix-item-label">情感内核</div><div class="matrix-item-value">${this.escHtml(m.emotional_core || '')}</div></div>
                    </div>
                </div>`;
            }

            // 冲突方案卡片
            if (data.conflicts && Array.isArray(data.conflicts)) {
                html += '<div class="conflict-cards">';
                data.conflicts.forEach(c => {
                    const severity = c.severity || 'medium';
                    const tensionScore = c.tension_score || 50;
                    html += `<div class="conflict-card" onclick="App.selectConflict('${this.escJs(c.id || '')}')">
                        <div class="conflict-card-header">
                            <div class="conflict-card-id ${c.id || 'A'}">${c.id || '?'}</div>
                            <div class="conflict-card-title">${this.escHtml(c.title || '')}</div>
                            <span class="conflict-severity ${severity}">${severity === 'high' ? '高危' : severity === 'medium' ? '中等' : '低'}</span>
                        </div>
                        <div class="conflict-card-desc">${this.escHtml(c.description || '')}</div>
                        <div class="conflict-card-meta">
                            <span class="conflict-meta-item"><i class="fas fa-bolt"></i> ${this.escHtml(c.trigger || '')}</span>
                            <span class="conflict-meta-item"><i class="fas fa-arrow-up"></i> ${this.escHtml(c.escalation || '')}</span>
                        </div>
                        ${c.affected_chars ? `<div class="conflict-card-meta" style="margin-top:4px">${
                            c.affected_chars.map(ch => `<span class="conflict-meta-item"><i class="fas fa-user"></i> ${this.escHtml(ch)}</span>`).join('')
                        }</div>` : ''}
                        <div class="conflict-tension-bar">
                            <div class="conflict-tension-fill ${severity}" style="width:${tensionScore}%"></div>
                        </div>
                    </div>`;
                });
                html += '</div>';
            }

            output.innerHTML = html || rawText;
        } catch (e) {
            output.textContent = rawText;
        }
    },

    selectConflict(conflictId) {
        this.toast(`已选择冲突方案 ${conflictId}`, 'info');
    },

    // ==================== 联想/头脑风暴 ====================
    triggerBrainstorm() {
        document.getElementById('slashMenu').style.display = 'none';
        this.switchRightTab('agent');
        this.selectAgent('brainstorm');
        const editor = document.getElementById('editorArea');
        const text = editor.innerText;
        if (text && text.length > 30) {
            document.getElementById('brainstormSeed').value = text.slice(-300);
        }
    },

    async runBrainstorm() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }

        const seedText = document.getElementById('brainstormSeed').value ||
                         document.getElementById('editorArea').innerText.slice(-500);
        if (!seedText.trim()) { this.toast('请输入种子文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 联想Agent正在发散思维...';

        const res = await this.api('/api/agent/associate', 'POST', {
            book_id: this.currentBookId,
            seed_text: seedText,
            dimension: document.getElementById('brainstormDimension').value
        });

        if (res && res.cards) {
            this.renderBrainstormCards(res.cards);
            this.agentOutputText = JSON.stringify(res.cards, null, 2);
            this.toast(`生成了 ${res.cards.length} 张创意卡片`, 'success');
        }
    },

    renderBrainstormCards(cards) {
        const container = document.getElementById('brainstormCards');
        const output = document.getElementById('agentOutput');

        if (!cards || cards.length === 0) {
            container.style.display = 'none';
            output.textContent = '未生成创意卡片';
            return;
        }

        let html = '';
        cards.forEach((card, i) => {
            const type = card.type || card.probe_type || 'causal';
            const typeLabel = { causal: '因果', reverse: '反转', detail: '细节' }[type] || type;
            html += `<div class="brainstorm-card" onclick="App.useBrainstormCard(${i})">
                <div class="brainstorm-card-header">
                    <span class="brainstorm-card-title">${this.escHtml(card.title || '')}</span>
                    <span class="brainstorm-card-type ${type}">${typeLabel}</span>
                </div>
                <div class="brainstorm-card-content">${this.escHtml(card.content || '')}</div>
                ${card.hook ? `<div class="brainstorm-card-hook">"${this.escHtml(card.hook)}"</div>` : ''}
                <div class="brainstorm-card-footer">
                    <div class="brainstorm-card-tags">
                        ${(card.tags || []).map(t => `<span class="brainstorm-tag">${this.escHtml(t)}</span>`).join('')}
                    </div>
                    ${card.usability ? `<span class="brainstorm-card-score"><i class="fas fa-star"></i> ${card.usability}</span>` : ''}
                </div>
                <div class="brainstorm-card-actions">
                    <button onclick="event.stopPropagation(); App.useBrainstormCard(${i})"><i class="fas fa-paste"></i> 采纳</button>
                    <button onclick="event.stopPropagation(); App.expandBrainstormCard(${i})"><i class="fas fa-expand"></i> 展开</button>
                </div>
            </div>`;
        });

        container.innerHTML = html;
        container.style.display = 'grid';
        output.innerHTML = `<div style="padding:8px;font-size:12px;color:var(--text-secondary)">
            <i class="fas fa-lightbulb" style="color:var(--warning)"></i> 
            生成了 ${cards.length} 张创意卡片，点击卡片可采纳到编辑器
        </div>`;

        // 存储cards数据供后续使用
        this._brainstormCards = cards;
    },

    useBrainstormCard(index) {
        const cards = this._brainstormCards;
        if (!cards || !cards[index]) return;
        const card = cards[index];
        const text = `${card.title}：${card.content}`;
        this.agentOutputText = text;
        document.getElementById('agentOutput').textContent = text;
        this.toast('已选择创意卡片，可点击"应用"添加到编辑器', 'info');
    },

    expandBrainstormCard(index) {
        const cards = this._brainstormCards;
        if (!cards || !cards[index]) return;
        const card = cards[index];
        // 将卡片内容作为种子进行续写
        document.getElementById('brainstormSeed').value = card.content;
        this.toast('已将卡片内容填入种子文本，可再次发散', 'info');
    },

    // ==================== 三层记忆管理 ====================
    async loadMemoryStatus() {
        if (!this.currentBookId || !this.currentNodeId) return;
        try {
            const res = await this.api(`/api/memory/status/${this.currentBookId}/${this.currentNodeId}`);
            if (res) {
                document.getElementById('tier1Value').textContent =
                    res.tier1_working?.active ? `${res.tier1_working.chars} 字` : '空';
                document.getElementById('tier2Value').textContent =
                    res.tier2_rolling?.active ? `${res.tier2_rolling.summary_count} 条` : '空';
                document.getElementById('tier3Value').textContent =
                    res.tier3_vector?.indexed ?
                        `${res.tier3_vector.chunk_count} 块${res.tier3_vector.has_faiss ? ' (FAISS)' : ''}` : '未索引';
            }
        } catch (e) {
            // silently fail
        }
    },

    async buildVectorIndex() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        this.toast('正在构建向量索引...', 'info');
        const res = await this.api(`/api/memory/vectorize/${this.currentBookId}`, 'POST');
        if (res) {
            this.toast(`向量索引已构建：${res.chunk_count} 个文本块${res.has_faiss ? ' (FAISS加速)' : ''}`, 'success');
            this.loadMemoryStatus();
        }
    },

    async vectorSearch() {
        const query = document.getElementById('vectorSearch').value.trim();
        if (!query || !this.currentBookId) return;

        const res = await this.api('/api/memory/retrieve', 'POST', {
            book_id: this.currentBookId,
            query: query,
            top_k: 5
        });

        const container = document.getElementById('vectorResults');
        if (res?.results?.length > 0) {
            container.innerHTML = res.results.map(r => `
                <div class="entity-result-item">
                    <div class="entity-result-name">
                        ${this.escHtml(r.name || '')}
                        <small>(${r.source} | 相关度: ${r.score})</small>
                    </div>
                    <div class="entity-result-content">${this.escHtml(r.text || '')}</div>
                </div>
            `).join('');
        } else {
            container.innerHTML = '<div class="empty-state"><p>未找到相关内容，请先构建向量索引</p></div>';
        }
    },

    // ==================== 伏笔追踪 ====================
    async runForeshadowDetect() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('foreshadowText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在检测伏笔元素...';

        const res = await this.api('/api/agent/foreshadow-detect', 'POST', {
            text,
            book_id: this.currentBookId,
            node_id: this.currentNodeId
        });

        if (res && res.foreshadowing) {
            this.agentOutputText = res.foreshadowing;
            output.textContent = res.foreshadowing;

            // 解析并显示伏笔列表
            try {
                const arrMatch = res.foreshadowing.match(/\[[\s\S]*\]/);
                if (arrMatch) {
                    const items = JSON.parse(arrMatch[0]);
                    this.renderForeshadowList(items);
                }
            } catch (e) {}
            this.toast('伏笔检测完成', 'success');
        }
    },

    async runForeshadowScan() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('foreshadowText').value ||
                     document.getElementById('editorArea').innerText;

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在扫描待填坑伏笔...';

        const node = this.currentNodeId ? await this.api(`/api/nodes/${this.currentNodeId}`) : null;
        const res = await this.api('/api/agent/foreshadow-scan', 'POST', {
            text,
            book_id: this.currentBookId,
            chapter_title: node?.title || ''
        });

        if (res && res.suggestions) {
            this.agentOutputText = res.suggestions;
            output.textContent = res.suggestions;
            this.toast('伏笔扫描完成', 'success');
        }
    },

    renderForeshadowList(items) {
        const container = document.getElementById('foreshadowList');
        if (!items || items.length === 0) {
            container.innerHTML = '<p class="agent-hint">未检测到伏笔元素</p>';
            return;
        }
        container.innerHTML = items.map(item => `
            <div class="foreshadow-item" onclick="App.saveForeshadowing('${this.escJs(item.label || '')}', '${this.escJs(item.text || '')}', '${this.escJs(item.description || '')}')">
                <div class="foreshadow-item-label">
                    <span class="status-tag unresolved">未填</span>
                    ${this.escHtml(item.label || '')}
                </div>
                <div class="foreshadow-item-text">${this.escHtml(item.text || '')}</div>
            </div>
        `).join('');
    },

    async saveForeshadowing(label, text, description) {
        if (!this.currentBookId) return;
        const node = this.currentNodeId ? await this.api(`/api/nodes/${this.currentNodeId}`) : null;
        await this.api(`/api/foreshadowing/${this.currentBookId}`, 'POST', {
            node_id: this.currentNodeId || '',
            text, label, description,
            created_chapter: node?.title || ''
        });
        this.toast(`伏笔「${label}」已保存到追踪池`, 'success');
    },

    async loadForeshadowing() {
        if (!this.currentBookId) return;
        const items = await this.api(`/api/foreshadowing/${this.currentBookId}`);
        const container = document.getElementById('foreshadowList');
        if (!items || items.length === 0) {
            container.innerHTML = '<p class="agent-hint">暂无追踪的伏笔</p>';
            return;
        }
        container.innerHTML = items.map(item => `
            <div class="foreshadow-item">
                <div class="foreshadow-item-label">
                    <span class="status-tag ${item.status || 'unresolved'}">${item.status === 'resolved' ? '已填' : '未填'}</span>
                    ${this.escHtml(item.label || '')}
                </div>
                <div class="foreshadow-item-text">${this.escHtml(item.text || '')}</div>
            </div>
        `).join('');
    },

    // ==================== 潜台词分析 ====================
    async runSubtextAnalysis() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('subtextText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在分析潜台词...';

        const res = await this.api('/api/agent/subtext', 'POST', {
            text,
            book_id: this.currentBookId,
            characters: document.getElementById('subtextCharacters').value
        });

        if (res && res.analysis) {
            this.agentOutputText = res.analysis;
            output.textContent = res.analysis;
            this.toast('潜台词分析完成', 'success');
        }
    },

    // ==================== 心理透视 ====================
    async runPsychologyLens() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('psychologyText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在进行深层心理分析...';

        const res = await this.api('/api/agent/psychology', 'POST', {
            text,
            book_id: this.currentBookId,
            character: document.getElementById('psychologyCharacter').value
        });

        if (res && res.psychology) {
            this.agentOutputText = res.psychology;
            output.textContent = res.psychology;
            this.toast('心理分析完成', 'success');
        }
    },

    // ==================== 世界状态 ====================
    async runWorldStateExtract() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('worldstateText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在提取世界状态...';

        const res = await this.api('/api/agent/world-state-extract', 'POST', {
            text,
            book_id: this.currentBookId,
            node_id: this.currentNodeId
        });

        if (res && res.world_state) {
            this.agentOutputText = res.world_state;
            output.textContent = res.world_state;
            this.loadWorldState();
            this.toast('世界状态提取完成', 'success');
        }
    },

    async runWorldStateValidate() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('worldstateText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 正在验证一致性...';

        const res = await this.api('/api/agent/world-state-validate', 'POST', {
            text,
            book_id: this.currentBookId
        });

        if (res && res.validation) {
            this.agentOutputText = res.validation;
            output.textContent = res.validation;
            this.toast('一致性验证完成', 'success');
        }
    },

    async loadWorldState() {
        if (!this.currentBookId) return;
        const states = await this.api(`/api/world-state/${this.currentBookId}`);
        const container = document.getElementById('worldStateList');
        if (!states || states.length === 0) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-globe"></i> 暂无世界状态记录</p>';
            return;
        }
        container.innerHTML = states.slice(0, 20).map(s => `
            <div class="world-state-item">
                <div>
                    <span class="world-state-entity">${this.escHtml(s.entity_name)}</span>
                    <span class="world-state-type">${this.escHtml(s.state_type)}</span>
                </div>
                <div class="world-state-value">${this.escHtml(s.state_value)}</div>
            </div>
        `).join('');
    },

    // ==================== Module 11: Plan 模式 / Plan-and-Solve ====================
    triggerPlanAndSolve() {
        document.getElementById('slashMenu').style.display = 'none';
        this.switchRightTab('agent');
        this.selectAgent('plansolve');
        const text = document.getElementById('editorArea').innerText;
        if (text && text.length > 50) {
            // 不填充beat，让用户自己写
        }
    },

    async runPlanAndSolve() {
        if (!this.currentNodeId) { this.toast('请先选择一个章节', 'warning'); return; }
        const beat = document.getElementById('plansolveBeat').value;
        if (!beat.trim()) { this.toast('请输入场景节拍', 'warning'); return; }

        this.isStreaming = true;
        document.getElementById('streamingControls').style.display = 'flex';
        const output = document.getElementById('agentOutput');
        output.innerHTML = '';
        this.agentOutputText = '';

        // 显示阶段进度
        const progress = document.getElementById('phaseProgress');
        progress.style.display = 'flex';
        this._resetPhaseProgress();

        try {
            const response = await fetch('/api/agent/plan-and-solve', {
                method: 'POST',
                headers: this.authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    beat,
                    style: document.getElementById('plansolveStyle').value,
                    book_id: this.currentBookId,
                    node_id: this.currentNodeId,
                    previous_text: document.getElementById('editorArea').innerText
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') break;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.text) {
                                const txt = parsed.text;
                                if (txt.startsWith('[PHASE:')) {
                                    const phase = txt.match(/\[PHASE:(.+?)\]/)?.[1];
                                    if (phase) this._updatePhaseProgress(phase);
                                } else {
                                    this.agentOutputText += txt;
                                    output.textContent = this.agentOutputText;
                                    output.scrollTop = output.scrollHeight;
                                }
                            }
                        } catch (e) {}
                    }
                }
            }
        } catch (e) {
            this.toast('Plan模式出错: ' + e.message, 'error');
        }

        // 标记所有阶段完成
        document.querySelectorAll('#phaseProgress .phase-step').forEach(s => {
            s.classList.remove('active');
            s.classList.add('completed');
        });

        this.isStreaming = false;
        document.getElementById('streamingControls').style.display = 'none';
        this.toast('Plan模式完成', 'success');
    },

    _resetPhaseProgress() {
        document.querySelectorAll('#phaseProgress .phase-step').forEach(s => {
            s.classList.remove('active', 'completed');
        });
    },

    _updatePhaseProgress(phase) {
        const phaseOrder = ['1', '2A', '2B', '2C', '3'];
        const idx = phaseOrder.indexOf(phase);
        const steps = document.querySelectorAll('#phaseProgress .phase-step');

        steps.forEach((step, i) => {
            step.classList.remove('active', 'completed');
            if (i < idx) step.classList.add('completed');
            else if (i === idx) step.classList.add('active');
        });
    },

    // ==================== Module 12: 幻觉检测 ====================
    triggerHallucinationCheck() {
        document.getElementById('slashMenu').style.display = 'none';
        this.switchRightTab('agent');
        this.selectAgent('hallcheck');
    },

    async runHallucinationCheck() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const text = document.getElementById('hallcheckText').value ||
                     document.getElementById('editorArea').innerText;
        if (!text.trim()) { this.toast('请输入待检测文本', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 多维幻觉检测中...';
        const guardStatus = document.getElementById('guardStatus');
        guardStatus.style.display = 'flex';
        document.getElementById('guardStatusText').textContent = '检测中...';

        const res = await this.api('/api/agent/hallucination-check', 'POST', {
            text,
            book_id: this.currentBookId,
            node_id: this.currentNodeId
        });

        guardStatus.style.display = 'none';

        if (res) {
            this.renderHallcheckResults(res);
            this.agentOutputText = JSON.stringify(res, null, 2);
            output.innerHTML = '';  // 结果已在专用区域显示
            this.toast(res.has_contradiction ? '发现矛盾！' : '未发现矛盾', res.has_contradiction ? 'warning' : 'success');
        }
    },

    renderHallcheckResults(res) {
        const container = document.getElementById('hallcheckResults');
        let html = '';

        // Verdict badge
        const verdictClass = res.nli_verdict === 'Contradiction' ? 'fail' :
                             res.nli_verdict === 'Entailment' ? 'pass' : 'neutral';
        const verdictIcon = res.has_contradiction ? 'fa-triangle-exclamation' : 'fa-check-circle';
        html += `<div class="hallcheck-verdict ${verdictClass}">
            <i class="fas ${verdictIcon}"></i>
            <span>NLI: ${res.nli_verdict || 'N/A'} (${Math.round((res.nli_confidence || 0) * 100)}%)</span>
            <span style="margin-left:auto;">${res.world_state_consistent ? '世界态✓' : '世界态✗'}</span>
        </div>`;

        // Overall
        html += `<div style="padding:6px 10px;font-size:11px;color:var(--text-secondary);margin-bottom:8px;">
            ${this.escHtml(res.overall_verdict || '')}
        </div>`;

        // Conflicts
        if (res.conflicts && res.conflicts.length > 0) {
            html += '<div style="font-size:11px;font-weight:600;color:var(--text-accent);margin-bottom:4px;">冲突列表：</div>';
            res.conflicts.forEach(c => {
                html += `<div class="hallcheck-conflict ${c.severity || 'warning'}">
                    <div class="hallcheck-conflict-type">${this.escHtml(c.type || '')} [${c.severity || ''}]</div>
                    <div class="hallcheck-conflict-desc">${this.escHtml(c.description || '')}</div>
                </div>`;
            });
        }

        // Reasoning
        if (res.nli_reasoning) {
            html += `<div style="padding:6px 10px;font-size:11px;color:var(--text-muted);margin-top:8px;border-top:1px solid var(--border);">
                <strong>推理：</strong>${this.escHtml(res.nli_reasoning)}
            </div>`;
        }

        container.innerHTML = html;
    },

    _handleGuardMarker(txt) {
        const guardStatus = document.getElementById('guardStatus');
        guardStatus.style.display = 'flex';

        if (txt === '[GUARD:generating]') {
            document.getElementById('guardStatusText').innerHTML =
                '<span class="guard-badge checking"><i class="fas fa-pen-nib"></i> 生成中</span>';
        } else if (txt === '[GUARD:checking]') {
            document.getElementById('guardStatusText').innerHTML =
                '<span class="guard-badge checking"><i class="fas fa-shield-halved"></i> 幻觉检测中</span>';
        } else if (txt.startsWith('[GUARD:retry:')) {
            const n = txt.match(/\d+/)?.[0] || '?';
            document.getElementById('guardStatusText').innerHTML =
                `<span class="guard-badge retry"><i class="fas fa-rotate"></i> 第${n}次重试</span>`;
        } else if (txt === '[GUARD:passed]') {
            document.getElementById('guardStatusText').innerHTML =
                '<span class="guard-badge passed"><i class="fas fa-check"></i> 通过</span>';
            setTimeout(() => { guardStatus.style.display = 'none'; }, 3000);
        } else if (txt === '[GUARD:failed]') {
            document.getElementById('guardStatusText').innerHTML =
                '<span class="guard-badge failed"><i class="fas fa-xmark"></i> 未通过</span>';
            this.guardHardBlocked = true;
        }
    },

    _showHallucinationAlert(conflicts) {
        const bar = document.getElementById('hallucinationAlertBar');
        const details = document.getElementById('hallucinationAlertDetails');

        let detailsHtml = '';
        if (conflicts && conflicts.length > 0) {
            detailsHtml = conflicts.slice(0, 3).map(c =>
                `<div>• [${c.type || '?'}] ${this.escHtml(c.description || '')}</div>`
            ).join('');
        } else {
            detailsHtml = '检测到文本与设定/上下文存在矛盾。';
        }
        details.innerHTML = detailsHtml;
        bar.style.display = 'flex';
    },

    dismissHallucinationAlert() {
        document.getElementById('hallucinationAlertBar').style.display = 'none';
    },

    resampleDrafter() {
        this.dismissHallucinationAlert();
        this.switchRightTab('agent');
        this.selectAgent('drafter');
        this.runDrafter();
    },

    showHallucinationDetails() {
        this.switchRightTab('agent');
        this.selectAgent('hallcheck');
        if (this._lastHallucinationConflicts) {
            this.renderHallcheckResults({
                has_contradiction: true,
                nli_verdict: 'Contradiction',
                nli_confidence: 0.9,
                world_state_consistent: false,
                conflicts: this._lastHallucinationConflicts,
                overall_verdict: '幻觉防护检测到矛盾',
                nli_reasoning: ''
            });
        }
        this.dismissHallucinationAlert();
    },

    escHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    escJs(str) {
        if (!str) return '';
        return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
    },

    // ==================== 新手引导 & 项目模板 ====================

    showNewBookDialog() {
        document.getElementById('newBookWithTemplateModal').style.display = 'flex';
        this.loadTemplates();
    },

    closeNewBookWithTemplateDialog() {
        document.getElementById('newBookWithTemplateModal').style.display = 'none';
    },

    async loadTemplates() {
        const resp = await fetch('/api/templates', { headers: this.authHeaders() });
        if (!resp.ok) return;
        const templates = await resp.json();
        const grid = document.getElementById('templateGrid');
        grid.innerHTML = templates.map(t => `
            <div class="template-card" onclick="App.selectTemplate('${this.escJs(t.id)}', '${this.escJs(t.name)}', '${this.escJs(t.genre)}')">
                <div class="template-card-icon">${this._templateIcon(t.id)}</div>
                <div class="template-card-name">${this.escHtml(t.name)}</div>
                <div class="template-card-desc">${this.escHtml(t.description)}</div>
            </div>
        `).join('');
    },

    _templateIcon(id) {
        const icons = { fantasy: '⚔️', romance: '💕', mystery: '🔍', scifi: '🚀', blank: '📝' };
        return icons[id] || '📖';
    },

    _selectedTemplateId: null,

    selectTemplate(templateId, templateName, defaultGenre) {
        this._selectedTemplateId = templateId;
        document.getElementById('templateGrid').style.display = 'none';
        document.getElementById('templateSelectedForm').style.display = 'block';
        document.getElementById('templateSelectedName').textContent = `模板：${templateName}`;
        if (defaultGenre) document.getElementById('templateBookGenre').value = defaultGenre;
        document.getElementById('templateBookTitle').focus();
    },

    resetTemplateSelection() {
        this._selectedTemplateId = null;
        document.getElementById('templateGrid').style.display = 'grid';
        document.getElementById('templateSelectedForm').style.display = 'none';
    },

    async createBookFromTemplate() {
        const title = document.getElementById('templateBookTitle').value.trim() || '未命名小说';
        const author = document.getElementById('templateBookAuthor').value.trim();
        const genre = document.getElementById('templateBookGenre').value.trim();
        const description = document.getElementById('templateBookDesc').value.trim();
        const templateId = this._selectedTemplateId || 'blank';

        const resp = await fetch('/api/books/from-template', {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ template_id: templateId, title, author, genre, description })
        });
        if (!resp.ok) { this.showToast('创建失败', 'error'); return; }
        const data = await resp.json();
        this.closeNewBookWithTemplateDialog();
        await this.loadBooks();
        this.switchBook(data.id);
        this.showToast(`📚 书籍已创建，模板：${templateId}`, 'success');
    },

    // ==================== 全局搜索与替换 ====================

    showGlobalSearch() {
        if (!this.currentBookId) { this.showToast('请先选择书籍', 'warning'); return; }
        document.getElementById('globalSearchModal').style.display = 'flex';
        document.getElementById('globalSearchQuery').focus();
        document.getElementById('globalSearchResults').innerHTML = '';
        document.getElementById('globalReplaceBtn').disabled = true;
    },

    closeGlobalSearch() {
        document.getElementById('globalSearchModal').style.display = 'none';
    },

    async doGlobalSearch() {
        const query = document.getElementById('globalSearchQuery').value.trim();
        if (!query) return;
        const caseSensitive = document.getElementById('searchCaseSensitive').checked;
        const resp = await fetch(`/api/search/${this.currentBookId}`, {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ query, case_sensitive: caseSensitive })
        });
        if (!resp.ok) { this.showToast('搜索失败', 'error'); return; }
        const data = await resp.json();
        this._searchResults = data.results;
        this._renderSearchResults(data.results, data.total);
        const hasReplace = document.getElementById('globalReplaceText').value.trim().length > 0;
        document.getElementById('globalReplaceBtn').disabled = !data.total || !hasReplace;
    },

    _searchResults: [],

    _renderSearchResults(results, total) {
        const container = document.getElementById('globalSearchResults');
        if (!results.length) {
            container.innerHTML = '<p class="agent-hint">未找到匹配结果</p>';
            return;
        }
        container.innerHTML = `<p class="settings-hint">共找到 <strong>${total}</strong> 处匹配，涉及 <strong>${results.length}</strong> 个章节</p>` +
            results.map(r => `
                <div class="search-result-item" onclick="App.jumpToNode('${this.escJs(r.node_id)}')">
                    <div class="search-result-title">
                        <i class="fas fa-file-lines"></i> ${this.escHtml(r.title)}
                        <span class="badge badge-info">${r.match_count} 处</span>
                    </div>
                    ${r.matches.slice(0, 3).map(m => `
                        <div class="search-result-snippet">...${this.escHtml(m.snippet)}...</div>
                    `).join('')}
                </div>
            `).join('');
    },

    async jumpToNode(nodeId) {
        await this.selectNode(nodeId);
        this.closeGlobalSearch();
    },

    async doGlobalReplace() {
        const query = document.getElementById('globalSearchQuery').value.trim();
        const replacement = document.getElementById('globalReplaceText').value;
        const caseSensitive = document.getElementById('searchCaseSensitive').checked;
        if (!query) return;
        if (!confirm(`确定要将所有"${query}"替换为"${replacement}"？此操作不可直接撤销（会自动创建快照）。`)) return;
        const resp = await fetch(`/api/search/${this.currentBookId}/replace`, {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ query, replacement, case_sensitive: caseSensitive })
        });
        if (!resp.ok) { this.showToast('替换失败', 'error'); return; }
        const data = await resp.json();
        this.showToast(`✅ 替换完成：${data.affected_nodes} 个章节，已创建自动快照`, 'success');
        this.closeGlobalSearch();
        if (this.currentNodeId) this.selectNode(this.currentNodeId);
    },

    // ==================== 回收站 ====================

    showRecycleBin() {
        if (!this.currentBookId) { this.showToast('请先选择书籍', 'warning'); return; }
        document.getElementById('recycleBinModal').style.display = 'flex';
        this.loadRecycleBin();
    },

    closeRecycleBin() {
        document.getElementById('recycleBinModal').style.display = 'none';
    },

    async loadRecycleBin() {
        const resp = await fetch(`/api/recycle-bin/${this.currentBookId}`, {
            headers: this.authHeaders()
        });
        if (!resp.ok) return;
        const items = await resp.json();
        const container = document.getElementById('recycleBinList');
        if (!items.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-trash"></i> 回收站为空</p>';
            return;
        }
        container.innerHTML = items.map(item => `
            <div class="recycle-bin-item">
                <div class="recycle-bin-info">
                    <i class="fas fa-file-lines"></i>
                    <strong>${this.escHtml(item.title)}</strong>
                    <span class="badge">${item.node_type}</span>
                    <span class="text-muted" style="font-size:12px;">${item.deleted_at ? item.deleted_at.substring(0,16) : ''}</span>
                </div>
                <div class="recycle-bin-actions">
                    <button class="btn btn-xs btn-primary" onclick="App.restoreDeletedNode('${this.escJs(item.id)}')">
                        <i class="fas fa-trash-restore"></i> 恢复
                    </button>
                    <button class="btn btn-xs btn-danger" onclick="App.purgeDeletedNode('${this.escJs(item.id)}')">
                        <i class="fas fa-times"></i> 彻底删除
                    </button>
                </div>
            </div>
        `).join('');
    },

    async restoreDeletedNode(deletedId) {
        const resp = await fetch(`/api/recycle-bin/${this.currentBookId}/${deletedId}/restore`, {
            method: 'POST',
            headers: this.authHeaders()
        });
        if (!resp.ok) { this.showToast('恢复失败', 'error'); return; }
        this.showToast('✅ 章节已恢复', 'success');
        this.loadRecycleBin();
        this.loadTree();
    },

    async purgeDeletedNode(deletedId) {
        if (!confirm('确定要永久删除？此操作不可恢复。')) return;
        const resp = await fetch(`/api/recycle-bin/${this.currentBookId}/${deletedId}`, {
            method: 'DELETE',
            headers: this.authHeaders()
        });
        if (!resp.ok) { this.showToast('删除失败', 'error'); return; }
        this.showToast('已永久删除', 'info');
        this.loadRecycleBin();
    },

    // ==================== 时间线与事件账本 ====================

    async loadTimeline() {
        if (!this.currentBookId) return;
        const eventType = document.getElementById('timelineFilter')?.value || '';
        const entity = document.getElementById('timelineEntityFilter')?.value.trim() || '';
        let url = `/api/timeline/${this.currentBookId}`;
        const params = new URLSearchParams();
        if (eventType) params.append('event_type', eventType);
        if (entity) params.append('entity', entity);
        if (params.toString()) url += '?' + params.toString();
        const resp = await fetch(url, { headers: this.authHeaders() });
        if (!resp.ok) return;
        const events = await resp.json();
        this._renderTimeline(events);
    },

    _renderTimeline(events) {
        const container = document.getElementById('timelineList');
        if (!events.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-info-circle"></i> 暂无时间线事件</p>';
            return;
        }
        const typeIcons = {
            injury: '🩸', recovery: '💊', location: '📍', relationship: '💬',
            item: '📦', death: '💀', revelation: '💡', event: '⚡', other: '📌'
        };
        container.innerHTML = events.map(ev => `
            <div class="timeline-event-item">
                <div class="timeline-event-type">${typeIcons[ev.event_type] || '📌'}</div>
                <div class="timeline-event-body">
                    <div class="timeline-event-meta">
                        <strong>${this.escHtml(ev.entity_name || '?')}</strong>
                        ${ev.chapter_title ? `<span class="badge">${this.escHtml(ev.chapter_title)}</span>` : ''}
                    </div>
                    <div class="timeline-event-desc">${this.escHtml(ev.description)}</div>
                    ${ev.state_before || ev.state_after ? `
                        <div class="timeline-event-state">
                            ${ev.state_before ? `<span class="state-before">${this.escHtml(ev.state_before)}</span>` : ''}
                            ${ev.state_before && ev.state_after ? ' → ' : ''}
                            ${ev.state_after ? `<span class="state-after">${this.escHtml(ev.state_after)}</span>` : ''}
                        </div>
                    ` : ''}
                </div>
                <div class="timeline-event-actions">
                    <button class="btn btn-xs btn-ghost" onclick="App.editTimelineEvent('${this.escJs(ev.id)}', ${this.escJs(JSON.stringify(ev)).replace(/'/g, "\\'")})">
                        <i class="fas fa-pen"></i>
                    </button>
                    <button class="btn btn-xs btn-ghost btn-danger" onclick="App.deleteTimelineEvent('${this.escJs(ev.id)}')">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
    },

    addTimelineEvent() {
        document.getElementById('timelineEventId').value = '';
        document.getElementById('timelineEntityName').value = '';
        document.getElementById('timelineEventType').value = 'event';
        document.getElementById('timelineChapterTitle').value = '';
        document.getElementById('timelineChapterNumber').value = '0';
        document.getElementById('timelineDescription').value = '';
        document.getElementById('timelineStateBefore').value = '';
        document.getElementById('timelineStateAfter').value = '';
        document.getElementById('timelineEventModalTitle').innerHTML = '<i class="fas fa-timeline"></i> 添加时间线事件';
        document.getElementById('timelineEventModal').style.display = 'flex';
    },

    editTimelineEvent(eventId, eventData) {
        document.getElementById('timelineEventId').value = eventId;
        document.getElementById('timelineEntityName').value = eventData.entity_name || '';
        document.getElementById('timelineEventType').value = eventData.event_type || 'event';
        document.getElementById('timelineChapterTitle').value = eventData.chapter_title || '';
        document.getElementById('timelineChapterNumber').value = eventData.chapter_number || 0;
        document.getElementById('timelineDescription').value = eventData.description || '';
        document.getElementById('timelineStateBefore').value = eventData.state_before || '';
        document.getElementById('timelineStateAfter').value = eventData.state_after || '';
        document.getElementById('timelineEventModalTitle').innerHTML = '<i class="fas fa-timeline"></i> 编辑时间线事件';
        document.getElementById('timelineEventModal').style.display = 'flex';
    },

    closeTimelineEventModal() {
        document.getElementById('timelineEventModal').style.display = 'none';
    },

    async saveTimelineEvent() {
        const eventId = document.getElementById('timelineEventId').value;
        const data = {
            entity_name: document.getElementById('timelineEntityName').value.trim(),
            event_type: document.getElementById('timelineEventType').value,
            chapter_title: document.getElementById('timelineChapterTitle').value.trim(),
            chapter_number: parseInt(document.getElementById('timelineChapterNumber').value) || 0,
            description: document.getElementById('timelineDescription').value.trim(),
            state_before: document.getElementById('timelineStateBefore').value.trim(),
            state_after: document.getElementById('timelineStateAfter').value.trim(),
            node_id: this.currentNodeId || '',
        };
        if (!data.description && !data.entity_name) {
            this.showToast('请填写事件描述或涉及角色', 'warning'); return;
        }
        const url = eventId
            ? `/api/timeline/${this.currentBookId}/${eventId}`
            : `/api/timeline/${this.currentBookId}`;
        const method = eventId ? 'PUT' : 'POST';
        const resp = await fetch(url, {
            method,
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(data)
        });
        if (!resp.ok) { this.showToast('保存失败', 'error'); return; }
        this.closeTimelineEventModal();
        this.loadTimeline();
        this.showToast('✅ 时间线事件已保存', 'success');
    },

    async deleteTimelineEvent(eventId) {
        if (!confirm('确定要删除这条时间线事件？')) return;
        await fetch(`/api/timeline/${this.currentBookId}/${eventId}`, {
            method: 'DELETE', headers: this.authHeaders()
        });
        this.loadTimeline();
    },

    async extractTimelineFromChapter() {
        if (!this.currentNodeId) { this.showToast('请先选择章节', 'warning'); return; }
        const content = document.getElementById('editorArea')?.innerText || '';
        if (!content.trim()) { this.showToast('当前章节无内容', 'warning'); return; }
        this.showToast('正在提取时间线...', 'info');
        const resp = await fetch('/api/agent/timeline-extract', {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                text: content,
                book_id: this.currentBookId,
                node_id: this.currentNodeId,
                chapter_title: document.getElementById('editorPath')?.textContent || '',
                chapter_number: 0,
            })
        });
        if (!resp.ok) { this.showToast('提取失败', 'error'); return; }
        const data = await resp.json();
        const count = data.events?.length || 0;
        this.showToast(`✅ 提取完成：新增 ${count} 条时间线事件`, 'success');
        this.loadTimeline();
    },

    // ==================== 创作规则中心 ====================

    async loadWritingRules() {
        if (!this.currentBookId) return;
        const ruleType = document.getElementById('rulesTypeFilter')?.value || '';
        let url = `/api/writing-rules/${this.currentBookId}`;
        if (ruleType) url += `?type=${encodeURIComponent(ruleType)}`;
        const resp = await fetch(url, { headers: this.authHeaders() });
        if (!resp.ok) return;
        const rules = await resp.json();
        this._renderWritingRules(rules);
    },

    _renderWritingRules(rules) {
        const container = document.getElementById('writingRulesList');
        if (!rules.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-info-circle"></i> 暂无创作规则，添加规则后AI写作时会自动遵守</p>';
            return;
        }
        const typeLabels = {
            style: '文风', pov: '视角', forbidden: '禁用词',
            character_voice: '角色语气', format: '格式规范', other: '其他'
        };
        container.innerHTML = rules.map(r => `
            <div class="writing-rule-item ${r.is_active ? '' : 'rule-disabled'}">
                <div class="rule-toggle">
                    <input type="checkbox" ${r.is_active ? 'checked' : ''}
                           onchange="App.toggleWritingRule('${this.escJs(r.id)}', this.checked)">
                </div>
                <div class="rule-body">
                    <div class="rule-header">
                        <span class="rule-type-badge">${typeLabels[r.rule_type] || r.rule_type}</span>
                        <strong>${this.escHtml(r.title)}</strong>
                    </div>
                    <div class="rule-content">${this.escHtml(r.content.substring(0, 120))}${r.content.length > 120 ? '...' : ''}</div>
                </div>
                <div class="rule-actions">
                    <button class="btn btn-xs btn-ghost" onclick="App.editWritingRule('${this.escJs(r.id)}', ${JSON.stringify(r).replace(/'/g, "\\'")})">
                        <i class="fas fa-pen"></i>
                    </button>
                    <button class="btn btn-xs btn-ghost btn-danger" onclick="App.deleteWritingRule('${this.escJs(r.id)}')">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
    },

    addWritingRule() {
        document.getElementById('writingRuleId').value = '';
        document.getElementById('writingRuleTitle').value = '';
        document.getElementById('writingRuleType').value = 'style';
        document.getElementById('writingRuleContent').value = '';
        document.getElementById('writingRuleModalTitle').innerHTML = '<i class="fas fa-scroll"></i> 添加创作规则';
        document.getElementById('writingRuleModal').style.display = 'flex';
    },

    editWritingRule(ruleId, ruleData) {
        document.getElementById('writingRuleId').value = ruleId;
        document.getElementById('writingRuleTitle').value = ruleData.title || '';
        document.getElementById('writingRuleType').value = ruleData.rule_type || 'style';
        document.getElementById('writingRuleContent').value = ruleData.content || '';
        document.getElementById('writingRuleModalTitle').innerHTML = '<i class="fas fa-scroll"></i> 编辑创作规则';
        document.getElementById('writingRuleModal').style.display = 'flex';
    },

    closeWritingRuleModal() {
        document.getElementById('writingRuleModal').style.display = 'none';
    },

    async saveWritingRule() {
        const ruleId = document.getElementById('writingRuleId').value;
        const data = {
            title: document.getElementById('writingRuleTitle').value.trim(),
            rule_type: document.getElementById('writingRuleType').value,
            content: document.getElementById('writingRuleContent').value.trim(),
            is_active: true,
        };
        if (!data.title) { this.showToast('请填写规则标题', 'warning'); return; }
        const url = ruleId
            ? `/api/writing-rules/${this.currentBookId}/${ruleId}`
            : `/api/writing-rules/${this.currentBookId}`;
        const method = ruleId ? 'PUT' : 'POST';
        const resp = await fetch(url, {
            method,
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(data)
        });
        if (!resp.ok) { this.showToast('保存失败', 'error'); return; }
        this.closeWritingRuleModal();
        this.loadWritingRules();
        this.showToast('✅ 创作规则已保存', 'success');
    },

    async toggleWritingRule(ruleId, isActive) {
        await fetch(`/api/writing-rules/${this.currentBookId}/${ruleId}`, {
            method: 'PUT',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ is_active: isActive })
        });
        this.loadWritingRules();
    },

    async deleteWritingRule(ruleId) {
        if (!confirm('确定要删除这条规则？')) return;
        await fetch(`/api/writing-rules/${this.currentBookId}/${ruleId}`, {
            method: 'DELETE', headers: this.authHeaders()
        });
        this.loadWritingRules();
    },

    // ==================== 快照管理 ====================

    async loadSnapshots() {
        if (!this.currentNodeId) return;
        const resp = await fetch(`/api/snapshots/${this.currentNodeId}`, {
            headers: this.authHeaders()
        });
        if (!resp.ok) return;
        const snapshots = await resp.json();
        this._renderSnapshots(snapshots);
    },

    _renderSnapshots(snapshots) {
        const container = document.getElementById('snapshotsList');
        if (!snapshots.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-info-circle"></i> 暂无快照</p>';
            return;
        }
        container.innerHTML = snapshots.map(s => `
            <div class="snapshot-item">
                <div class="snapshot-info">
                    <div class="snapshot-label">${this.escHtml(s.label || '自动快照')}</div>
                    <div class="snapshot-meta">
                        <span class="badge ${s.trigger_type === 'manual' ? 'badge-primary' : 'badge-secondary'}">${s.trigger_type === 'manual' ? '手动' : '自动'}</span>
                        ${s.word_count ? `${s.word_count} 字` : ''}
                        · ${s.created_at ? s.created_at.substring(0, 16) : ''}
                    </div>
                </div>
                <button class="btn btn-xs btn-primary" onclick="App.restoreSnapshot('${this.escJs(s.id)}')">
                    <i class="fas fa-rotate-left"></i> 恢复
                </button>
            </div>
        `).join('');
    },

    async createManualSnapshot() {
        if (!this.currentNodeId) { this.showToast('请先选择章节', 'warning'); return; }
        const content = document.getElementById('editorArea')?.innerText || '';
        const resp = await fetch(`/api/snapshots/${this.currentNodeId}`, {
            method: 'POST',
            headers: this.authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                content,
                label: `手动快照 ${new Date().toLocaleString('zh-CN', { hour12: false }).substring(0, 16)}`,
                trigger_type: 'manual'
            })
        });
        if (!resp.ok) { this.showToast('快照失败', 'error'); return; }
        this.showToast('✅ 快照已创建', 'success');
        this.loadSnapshots();
    },

    async restoreSnapshot(snapId) {
        if (!confirm('确定要恢复到此快照？当前内容将被备份后替换。')) return;
        const resp = await fetch(`/api/snapshots/${this.currentNodeId}/${snapId}/restore`, {
            method: 'POST', headers: this.authHeaders()
        });
        if (!resp.ok) { this.showToast('恢复失败', 'error'); return; }
        this.showToast('✅ 已恢复到快照', 'success');
        this.selectNode(this.currentNodeId);
        this.loadSnapshots();
    },

    // ==================== 异步任务中心 ====================

    showTaskCenter() {
        document.getElementById('taskCenterModal').style.display = 'flex';
        this.loadTasks();
    },

    closeTaskCenter() {
        document.getElementById('taskCenterModal').style.display = 'none';
    },

    async loadTasks() {
        const params = new URLSearchParams();
        if (this.currentBookId) params.append('book_id', this.currentBookId);
        const resp = await fetch(`/api/tasks?${params}`, { headers: this.authHeaders() });
        if (!resp.ok) return;
        const tasks = await resp.json();
        this._renderTasks(tasks);
    },

    _renderTasks(tasks) {
        const container = document.getElementById('taskList');
        if (!tasks.length) {
            container.innerHTML = '<p class="agent-hint"><i class="fas fa-tasks"></i> 暂无后台任务</p>';
            return;
        }
        const statusIcons = {
            pending: '⏳', running: '🔄', completed: '✅', failed: '❌', cancelled: '🚫'
        };
        const typeLabels = {
            vectorize: '构建向量索引',
            refresh_character_history: '回填人物历史',
        };
        container.innerHTML = tasks.map(t => `
            <div class="task-item task-${t.status}">
                <div class="task-info">
                    <span class="task-status-icon">${statusIcons[t.status] || '?'}</span>
                    <div class="task-details">
                        <div class="task-type">${typeLabels[t.task_type] || t.task_type}</div>
                        <div class="task-meta">
                            <span class="badge badge-${t.status}">${t.status}</span>
                            ${t.progress && t.total ? `${t.progress}/${t.total}` : ''}
                            · ${t.updated_at ? t.updated_at.substring(0, 16) : ''}
                        </div>
                        ${t.error ? `<div class="task-error text-danger" style="font-size:12px;">${this.escHtml(t.error.substring(0, 100))}</div>` : ''}
                        ${t.result && t.status === 'completed' ? `<div class="task-result" style="font-size:12px;color:var(--text-muted);">${this.escHtml(t.result.substring(0, 100))}</div>` : ''}
                    </div>
                </div>
                ${['pending', 'running'].includes(t.status) ? `
                    <button class="btn btn-xs btn-ghost" onclick="App.cancelTask('${this.escJs(t.id)}')">
                        <i class="fas fa-times"></i> 取消
                    </button>
                ` : ''}
            </div>
        `).join('');
    },

    async cancelTask(taskId) {
        await fetch(`/api/tasks/${taskId}/cancel`, {
            method: 'POST', headers: this.authHeaders()
        });
        this.loadTasks();
    },

    async buildVectorIndexAsync() {
        if (!this.currentBookId) { this.showToast('请先选择书籍', 'warning'); return; }
        const resp = await fetch(`/api/memory/vectorize-async/${this.currentBookId}`, {
            method: 'POST', headers: this.authHeaders()
        });
        if (!resp.ok) { this.showToast('启动失败', 'error'); return; }
        this.showToast('✅ 向量构建任务已提交到后台', 'success');
        this.loadTasks();
    },

    async refreshCharacterHistoryAsync() {
        if (!this.currentBookId) { this.showToast('请先选择书籍', 'warning'); return; }
        const resp = await fetch(`/api/character-history/${this.currentBookId}/refresh-async`, {
            method: 'POST', headers: this.authHeaders()
        });
        if (!resp.ok) { this.showToast('启动失败', 'error'); return; }
        this.showToast('✅ 人物历史回填任务已提交到后台', 'success');
        this.loadTasks();
    },

    // Override switchRightTab to load new tab data
    _origSwitchRightTab: null,
};

// Patch switchRightTab to load new tab data
const _origSwitchRightTab = App.switchRightTab.bind(App);
App.switchRightTab = function(tab) {
    _origSwitchRightTab(tab);
    if (tab === 'timeline') App.loadTimeline();
    if (tab === 'rules') App.loadWritingRules();
    if (tab === 'snapshots') App.loadSnapshots();
};

// ==================== CSS for new features ====================
(function injectStyles() {
    const style = document.createElement('style');
    style.textContent = `
        /* Template grid */
        .template-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; padding: 4px 0; }
        .template-card { border: 2px solid var(--border); border-radius: 10px; padding: 16px 12px; text-align: center; cursor: pointer; transition: all 0.2s; }
        .template-card:hover { border-color: var(--primary); background: var(--bg-hover); }
        .template-card-icon { font-size: 32px; margin-bottom: 8px; }
        .template-card-name { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
        .template-card-desc { font-size: 12px; color: var(--text-muted); }

        /* Search results */
        .search-results-list { max-height: 400px; overflow-y: auto; }
        .search-result-item { border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; cursor: pointer; transition: background 0.2s; }
        .search-result-item:hover { background: var(--bg-hover); }
        .search-result-title { font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
        .search-result-snippet { font-size: 12px; color: var(--text-muted); padding: 4px 8px; background: var(--bg-subtle); border-radius: 4px; margin-top: 4px; font-family: monospace; white-space: pre-wrap; word-break: break-all; }

        /* Recycle bin */
        .recycle-bin-list { max-height: 400px; overflow-y: auto; }
        .recycle-bin-item { display: flex; align-items: center; justify-content: space-between; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }
        .recycle-bin-info { display: flex; align-items: center; gap: 8px; flex: 1; }
        .recycle-bin-actions { display: flex; gap: 6px; }

        /* Timeline */
        .timeline-entity-filter { padding: 6px 0; }
        .timeline-entity-filter input { width: 100%; padding: 5px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: var(--bg); color: var(--text); }
        .timeline-list { margin-top: 8px; }
        .timeline-event-item { display: flex; gap: 10px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; align-items: flex-start; }
        .timeline-event-type { font-size: 20px; flex-shrink: 0; }
        .timeline-event-body { flex: 1; min-width: 0; }
        .timeline-event-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
        .timeline-event-desc { font-size: 13px; color: var(--text); }
        .timeline-event-state { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .state-before { color: var(--danger); text-decoration: line-through; }
        .state-after { color: var(--success); }
        .timeline-event-actions { display: flex; gap: 4px; flex-shrink: 0; }

        /* Writing rules */
        .rules-list { margin-top: 8px; }
        .writing-rule-item { display: flex; gap: 10px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; align-items: flex-start; }
        .writing-rule-item.rule-disabled { opacity: 0.5; }
        .rule-toggle { flex-shrink: 0; padding-top: 2px; }
        .rule-body { flex: 1; min-width: 0; }
        .rule-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
        .rule-type-badge { font-size: 11px; background: var(--primary-light, #e0f0ff); color: var(--primary); padding: 2px 6px; border-radius: 4px; }
        .rule-content { font-size: 12px; color: var(--text-muted); }
        .rule-actions { display: flex; gap: 4px; flex-shrink: 0; }

        /* Snapshots */
        .snapshots-list { margin-top: 8px; }
        .snapshot-item { display: flex; align-items: center; justify-content: space-between; border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin-bottom: 6px; }
        .snapshot-info { flex: 1; }
        .snapshot-label { font-weight: 500; font-size: 13px; }
        .snapshot-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

        /* Tasks */
        .task-list { max-height: 400px; overflow-y: auto; }
        .task-item { display: flex; align-items: flex-start; justify-content: space-between; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }
        .task-info { display: flex; gap: 10px; flex: 1; }
        .task-status-icon { font-size: 18px; flex-shrink: 0; }
        .task-type { font-weight: 500; font-size: 13px; }
        .task-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
        .task-completed { border-left: 3px solid var(--success); }
        .task-failed { border-left: 3px solid var(--danger); }
        .task-running { border-left: 3px solid var(--primary); }
        .badge-completed { background: var(--success-light, #d4edda); color: var(--success); }
        .badge-failed { background: var(--danger-light, #f8d7da); color: var(--danger); }
        .badge-running { background: var(--primary-light, #cce5ff); color: var(--primary); }
        .badge-pending { background: var(--warning-light, #fff3cd); color: #856404; }
        .badge-cancelled { background: var(--bg-subtle); color: var(--text-muted); }

        /* Misc */
        .text-muted { color: var(--text-muted); }
        .text-danger { color: var(--danger); }
        .btn-danger { background: var(--danger); color: #fff; border-color: var(--danger); }
        .btn-danger:hover { opacity: 0.85; }
        .badge-info { background: #cce5ff; color: #004085; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
        .badge-secondary { background: var(--bg-subtle); color: var(--text-muted); font-size: 11px; padding: 2px 6px; border-radius: 4px; }
        .badge-primary { background: var(--primary-light, #e0f0ff); color: var(--primary); font-size: 11px; padding: 2px 6px; border-radius: 4px; }
    `;
    document.head.appendChild(style);
})();

// 初始化
document.addEventListener('DOMContentLoaded', () => App.init());
