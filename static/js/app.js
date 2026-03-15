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
    themeMode: localStorage.getItem('ui_theme_mode') || 'light',
    versionCache: [],
    guardHardBlocked: false,
    leftTab: localStorage.getItem('ui_left_tab') || 'outline',
    rightTab: localStorage.getItem('ui_right_tab') || 'agent',
    activeAgent: localStorage.getItem('ui_active_agent') || 'planner',
    activeAgentGroup: localStorage.getItem('ui_agent_group') || 'planning',
    sectionFolds: (() => {
        try {
            return JSON.parse(localStorage.getItem('ui_section_folds') || '{}');
        } catch (e) {
            return {};
        }
    })(),
    agentOutputObserver: null,

    // ==================== 初始化 ====================
    async init() {
        this.applyTheme(this.themeMode, false);
        this.applyFocusMode(this.focusMode, false);
        this.setupEditorEvents();
        this.setupSelectionPopup();
        this.setupAgentOutputObserver();
        this.initFoldSections();
        this.switchLeftTab(this.leftTab);
        this.switchRightTab(this.rightTab);
        this.selectAgent(this.activeAgent);
        this.syncAgentOutputVisibility();
        this.updateUserPill();
        const ok = await this.ensureAuth();
        if (!ok) {
            console.log('墨境 · UI 已初始化，等待登录');
            return;
        }
        await this.loadBooks();
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

    toggleTheme() {
        this.applyTheme(this.themeMode === 'dark' ? 'light' : 'dark', true);
    },

    applyTheme(mode, shouldToast = true) {
        const nextMode = mode === 'dark' ? 'dark' : 'light';
        this.themeMode = nextMode;
        const root = document.documentElement;
        root.classList.toggle('theme-dark', nextMode === 'dark');
        root.classList.toggle('theme-light', nextMode !== 'dark');
        document.body.classList.toggle('theme-dark', nextMode === 'dark');
        document.body.classList.toggle('theme-light', nextMode !== 'dark');
        document.documentElement.style.colorScheme = nextMode;
        localStorage.setItem('ui_theme_mode', nextMode);

        const btn = document.getElementById('themeToggleBtn');
        const icon = document.getElementById('themeToggleIcon');
        const label = document.getElementById('themeToggleLabel');
        const targetModeLabel = nextMode === 'dark' ? '浅色模式' : '深色模式';

        if (btn) {
            btn.title = `切换到${targetModeLabel}`;
            btn.setAttribute('aria-label', `切换到${targetModeLabel}`);
        }
        if (icon) {
            icon.className = nextMode === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
        }
        if (label) {
            label.textContent = nextMode === 'dark' ? '深色' : '浅色';
        }
        if (shouldToast) {
            this.toast(nextMode === 'dark' ? '已切换为深色模式' : '已切换为浅色模式', 'info');
        }
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
        const icon = document.createElement('i');
        icon.className = `fas fa-${icons[type] || 'circle-info'}`;
        t.textContent = '';
        t.appendChild(icon);
        t.appendChild(document.createTextNode(' ' + msg));
        container.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3500);
    },

    persistUiValue(key, value) {
        localStorage.setItem(key, value);
    },

    persistSectionFolds() {
        localStorage.setItem('ui_section_folds', JSON.stringify(this.sectionFolds));
    },

    initFoldSections() {
        document.querySelectorAll('.fold-section[data-fold-key]').forEach((section) => {
            const key = section.dataset.foldKey;
            const isOpen = Object.prototype.hasOwnProperty.call(this.sectionFolds, key)
                ? !!this.sectionFolds[key]
                : section.classList.contains('is-open');
            this.applySectionFoldState(key, isOpen, false);
        });
    },

    applySectionFoldState(key, isOpen, persist = true) {
        const section = document.querySelector(`.fold-section[data-fold-key="${key}"]`);
        if (!section) return;
        section.classList.toggle('is-open', !!isOpen);
        const header = section.querySelector('.fold-section-header');
        const body = section.querySelector('.fold-section-body');
        if (header) header.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        if (body) body.hidden = !isOpen;
        if (persist) {
            this.sectionFolds[key] = !!isOpen;
            this.persistSectionFolds();
        }
    },

    toggleSectionFold(key, forceState = null) {
        const section = document.querySelector(`.fold-section[data-fold-key="${key}"]`);
        if (!section) return;
        const nextState = forceState === null ? !section.classList.contains('is-open') : !!forceState;
        this.applySectionFoldState(key, nextState, true);
    },

    setupAgentOutputObserver() {
        const output = document.getElementById('agentOutput');
        if (!output) return;
        if (this.agentOutputObserver) {
            this.agentOutputObserver.disconnect();
        }
        this.agentOutputObserver = new MutationObserver(() => this.syncAgentOutputVisibility());
        this.agentOutputObserver.observe(output, {
            childList: true,
            subtree: true,
            characterData: true
        });
    },

    syncAgentOutputVisibility() {
        const output = document.getElementById('agentOutput');
        const meta = document.getElementById('agentOutputMeta');
        const section = document.querySelector('.fold-section[data-fold-key="agent-output"]');
        if (!output || !meta || !section) return;

        const hasLoading = !!output.querySelector('.loading-spinner');
        const hasEmptyState = !!output.querySelector('.empty-state');
        const plainText = (output.textContent || '').trim();
        const hasRichContent = output.children.length > 0 && !hasEmptyState;
        const hasContent = hasLoading || (!!plainText && !hasEmptyState) || hasRichContent;

        section.classList.toggle('is-empty', !hasContent && !hasLoading);
        meta.textContent = hasLoading ? '生成中' : hasContent ? '可上屏' : '待生成';

        if (hasContent || hasLoading) {
            this.applySectionFoldState('agent-output', true, false);
        } else {
            this.applySectionFoldState('agent-output', false, false);
        }
    },

    resetAgentOutputPanel() {
        const output = document.getElementById('agentOutput');
        if (!output) return;
        output.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-scroll"></i>
                <p>AI 生成结果会显示在这里</p>
            </div>
        `;
        this.agentOutputText = '';
        this.guardHardBlocked = false;
        this.syncAgentOutputVisibility();
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
        this.resetAgentOutputPanel();
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
            document.getElementById('memoryTensionChart').innerHTML = '';
            document.getElementById('tensionWarnings').innerHTML = '';
            document.getElementById('characterReminderList').innerHTML = '<div class="empty-state"><p>请选择书籍后查看人物提醒</p></div>';
            ['analysisTensionChart', 'emotionChart', 'characterArcChart', 'foreshadowDistChart', 'pacingDiagnosis', 'arcCompleteness'].forEach((id) => {
                const el = document.getElementById(id);
                if (el) el.innerHTML = '';
            });
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
                        <div class="entity-result-name">${this.escHtml(e.name)} <small>(${this.escHtml(e.category)})</small></div>
                        <div class="entity-result-content">${this.escHtml(e.content || e.description)}</div>
                    </div>`;
                });
            }
            if (res.relations && res.relations.length > 0) {
                res.relations.forEach(r => {
                    html += `<div class="entity-result-item">
                        <div class="entity-result-name">${this.escHtml(r.source_entity)} → ${this.escHtml(r.target_entity)}</div>
                        <div class="entity-result-content">${this.escHtml(r.relation_type)}: ${this.escHtml(r.relation_value)}</div>
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
        document.querySelector(`#panelLeft .panel-tab[data-tab="${tab}"]`)?.classList.add('active');
        document.querySelectorAll('#panelLeft .tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1))?.classList.add('active');
        this.leftTab = tab;
        this.persistUiValue('ui_left_tab', tab);
    },

    switchRightTab(tab) {
        document.querySelectorAll('#panelRight .panel-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`#panelRight .panel-tab[data-tab="${tab}"]`)?.classList.add('active');
        document.querySelectorAll('#panelRight .tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1))?.classList.add('active');
        this.rightTab = tab;
        this.persistUiValue('ui_right_tab', tab);
        if (tab === 'memory') {
            this.loadCharacterReminders();
        }
        if (tab === 'analysis') {
            this.loadNarrativeAnalysis();
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
        if (!document.querySelector(`.agent-btn[data-agent="${agent}"]`)) {
            agent = 'planner';
        }
        const groupMap = {
            planner: 'planning',
            beats: 'planning',
            conflict: 'planning',
            brainstorm: 'planning',
            drafter: 'writing',
            continuation: 'writing',
            polisher: 'writing',
            plansolve: 'writing',
            validator: 'validation',
            hallcheck: 'validation',
            worldstate: 'validation',
            foreshadow: 'validation',
            subtext: 'analysis',
            psychology: 'analysis'
        };
        const targetGroup = groupMap[agent];
        if (targetGroup) {
            this.switchAgentGroup(targetGroup, null, false);
        }
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
        this.activeAgent = agent;
        this.persistUiValue('ui_active_agent', agent);
        this.applySectionFoldState('agent-workbench', true, false);
    },

    async runPlanner() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const inspiration = document.getElementById('plannerInspiration').value;
        if (!inspiration) { this.toast('请输入灵感或概述', 'warning'); return; }

        const output = document.getElementById('agentOutput');
        output.innerHTML = '<div class="loading-spinner"></div> 架构师正在规划大纲...';
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.resetAgentOutputPanel();
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
        const chart = document.getElementById('memoryTensionChart');
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
        const chart = document.getElementById('memoryTensionChart');
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

    switchSettingsTab(tab, evt = null) {
        document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        evt?.target?.classList.add('active');
        document.querySelectorAll('.settings-content').forEach(c => c.classList.remove('active'));
        const map = { models: 'settingsModels', routing: 'settingsRouting', params: 'settingsParams', rules: 'settingsRules', tokens: 'settingsTokens', stats: 'settingsStats' };
        document.getElementById(map[tab]).classList.add('active');

        if (tab === 'routing') this.loadRoutingGrid();
        if (tab === 'tokens') this.loadTokenStats();
        if (tab === 'rules') this.loadRuleSets();
        if (tab === 'stats') this.loadStatsDashboard();
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
                    <div class="model-card-detail">${this.escHtml(m.provider)} | ${this.escHtml(m.model_id)} | Key: ${this.escHtml(m.api_key_display)} | Max: ${m.max_context}</div>
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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';

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
        this.agentOutputText = '';
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

    // ==================== Agent Group Switching ====================
    switchAgentGroup(group, evt = null, persist = true) {
        document.querySelectorAll('.agent-group-tab').forEach(t => t.classList.remove('active'));
        const activeBtn = evt?.target?.closest('.agent-group-tab')
            || document.querySelector(`.agent-group-tab[data-group="${group}"]`);
        activeBtn?.classList.add('active');
        document.querySelectorAll('.agent-group[data-group]').forEach(g => g.classList.remove('active'));
        const target = document.querySelector(`.agent-group[data-group="${group}"]`);
        if (target) target.classList.add('active');
        this.activeAgentGroup = group;
        if (persist) {
            this.persistUiValue('ui_agent_group', group);
        }
    },

    // ==================== Global Search ====================
    openGlobalSearch() {
        document.getElementById('searchModal').style.display = 'flex';
        document.getElementById('globalSearchInput').focus();
    },
    closeGlobalSearch() {
        document.getElementById('searchModal').style.display = 'none';
    },
    async doGlobalSearch() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const query = document.getElementById('globalSearchInput').value.trim();
        if (!query) return;
        const scope = document.getElementById('globalSearchScope').value || null;
        const res = await this.api(`/api/search/${this.currentBookId}`, 'POST', { query, scope });
        if (!res) return;
        const container = document.getElementById('globalSearchResults');
        let html = '';
        const groups = { content: '正文', summary: '摘要', lorebook: '设定', character_history: '角色历史', world_state: '世界状态' };
        for (const [key, label] of Object.entries(groups)) {
            const items = res[key] || [];
            if (items.length === 0) continue;
            html += `<div class="search-result-group"><h4>${label} (${items.length})</h4>`;
            items.forEach(item => {
                const title = item.title || item.name || item.chapter_title || item.entity_name || '';
                const excerpt = item.excerpts ? item.excerpts[0] : (item.excerpt || item.state_value || '');
                html += `<div class="search-result-item"><div class="result-title">${this.escHtml(title)}</div><div class="result-excerpt">${this.escHtml(excerpt)}</div></div>`;
            });
            html += '</div>';
        }
        container.innerHTML = html || '<p class="settings-hint">无结果</p>';
    },
    showReplacePanel() {
        const panel = document.getElementById('replacePanel');
        panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    },
    async previewReplace() {
        if (!this.currentBookId) return;
        const search = document.getElementById('replaceSearchText').value;
        const replace = document.getElementById('replaceNewText').value;
        if (!search) return;
        const res = await this.api(`/api/search/${this.currentBookId}/replace`, 'POST', { search_text: search, replace_text: replace, preview_only: true });
        if (res) {
            this.toast(`将影响 ${res.affected_chapters} 个章节，共 ${res.total_replacements} 处`, 'info');
        }
    },
    async executeReplace() {
        if (!this.currentBookId) return;
        const search = document.getElementById('replaceSearchText').value;
        const replace = document.getElementById('replaceNewText').value;
        if (!search || !confirm(`确认将全书中的"${search}"替换为"${replace}"？此操作不可撤销。`)) return;
        const res = await this.api(`/api/search/${this.currentBookId}/replace`, 'POST', { search_text: search, replace_text: replace, preview_only: false });
        if (res) {
            this.toast(`已替换 ${res.total_replacements} 处`, 'success');
            if (this.currentNodeId) this.loadNodeContent(this.currentNodeId);
        }
    },

    // ==================== Timeline ====================
    async loadTimeline() {
        if (!this.currentBookId) return;
        const entity = document.getElementById('timelineEntityFilter').value || undefined;
        const type = document.getElementById('timelineTypeFilter').value || undefined;
        let url = `/api/timeline/${this.currentBookId}?`;
        if (entity) url += `entity_name=${encodeURIComponent(entity)}&`;
        if (type) url += `event_type=${encodeURIComponent(type)}&`;
        const events = await this.api(url, 'GET');
        const container = document.getElementById('timelineList');
        if (!events || events.length === 0) {
            container.innerHTML = '<p class="agent-hint">暂无时间线事件</p>';
            return;
        }
        container.innerHTML = events.map(ev => `
            <div class="timeline-event">
                <div class="event-entity">${this.escHtml(ev.entity_name)}</div>
                <div class="event-desc">${this.escHtml(ev.description)}</div>
                <div class="event-meta">
                    <span class="event-location">${ev.location ? '@' + this.escHtml(ev.location) : ''}</span>
                    <span>${ev.event_type || ''}</span>
                </div>
            </div>
        `).join('');
    },
    async addTimelineEvent() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const entity = prompt('实体名称（角色/物品等）:');
        if (!entity) return;
        const desc = prompt('事件描述:');
        if (!desc) return;
        const location = prompt('地点（可选）:') || '';
        await this.api(`/api/timeline/${this.currentBookId}/events`, 'POST', {
            entity_name: entity, description: desc, location, event_type: 'action'
        });
        this.toast('事件已添加', 'success');
        this.loadTimeline();
    },
    async extractTimelineEvents() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请选择章节', 'warning'); return; }
        this.toast('正在提取事件...', 'info');
        const res = await this.api(`/api/timeline/${this.currentBookId}/extract`, 'POST', { node_id: this.currentNodeId });
        if (res) {
            this.toast(`提取了 ${res.created_count || 0} 个事件`, 'success');
            this.loadTimeline();
        }
    },
    async detectTimelineConflicts() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const res = await this.api(`/api/timeline/${this.currentBookId}/detect-conflicts`, 'POST');
        if (res && res.conflicts) {
            if (res.conflicts.length === 0) {
                this.toast('未发现时间线冲突', 'success');
            } else {
                this.toast(`发现 ${res.conflicts.length} 个冲突`, 'warning');
            }
        }
    },

    // ==================== Consistency Report ====================
    openConsistencyPanel() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        document.getElementById('consistencyModal').style.display = 'flex';
        this.loadConsistencyReports();
    },
    closeConsistencyPanel() {
        document.getElementById('consistencyModal').style.display = 'none';
    },
    async runConsistencyScan() {
        if (!this.currentBookId) return;
        this.toast('正在发起全书扫描...', 'info');
        document.getElementById('consistencyStatus').textContent = '扫描中...';
        const res = await this.api(`/api/consistency/${this.currentBookId}/scan`, 'POST');
        if (res && res.report_id) {
            this.toast('扫描完成', 'success');
            this.loadConsistencyReport(res.report_id);
        }
    },
    async loadConsistencyReports() {
        const res = await this.api(`/api/consistency/${this.currentBookId}/reports`, 'GET');
        if (res && res.length > 0) {
            this.loadConsistencyReport(res[0].id);
        }
    },
    async loadConsistencyReport(reportId) {
        const res = await this.api(`/api/consistency/${this.currentBookId}/reports/${reportId}`, 'GET');
        if (!res) return;
        document.getElementById('consistencyStatus').textContent = res.status === 'completed' ? '已完成' : res.status;
        document.getElementById('consistencySummary').style.display = 'block';
        document.getElementById('consistencyHigh').textContent = res.high_count || 0;
        document.getElementById('consistencyMedium').textContent = res.medium_count || 0;
        document.getElementById('consistencyLow').textContent = res.low_count || 0;
        const issues = res.issues || [];
        document.getElementById('consistencyIssues').innerHTML = issues.map(issue => `
            <div class="consistency-issue ${issue.severity}">
                <div class="issue-title">${this.escHtml(issue.title)}</div>
                <div class="issue-desc">${this.escHtml(issue.description)}</div>
                <div class="issue-actions">
                    <button class="btn btn-xs btn-ghost" onclick="App.resolveIssue('${issue.id}', 'ignored')">忽略</button>
                    <button class="btn btn-xs btn-primary" onclick="App.resolveIssue('${issue.id}', 'fixed')">已修复</button>
                    <button class="btn btn-xs btn-ghost" onclick="App.resolveIssue('${issue.id}', 'exception')">设为例外</button>
                </div>
            </div>
        `).join('');
    },
    async resolveIssue(issueId, resolution) {
        await this.api(`/api/consistency/issues/${issueId}`, 'PUT', { resolution });
        this.toast('已更新', 'success');
    },

    // ==================== Job Center ====================
    openJobCenter() {
        document.getElementById('jobModal').style.display = 'flex';
        this.loadJobs();
    },
    closeJobCenter() {
        document.getElementById('jobModal').style.display = 'none';
    },
    async loadJobs() {
        const res = await this.api('/api/jobs', 'GET');
        const container = document.getElementById('jobList');
        if (!res || res.length === 0) {
            container.innerHTML = '<p class="settings-hint">暂无任务</p>';
            document.getElementById('jobBadge').style.display = 'none';
            return;
        }
        const running = res.filter(j => j.status === 'running');
        const badge = document.getElementById('jobBadge');
        if (running.length > 0) {
            badge.style.display = 'inline';
            badge.textContent = running.length;
        } else {
            badge.style.display = 'none';
        }
        container.innerHTML = res.map(job => `
            <div class="job-item">
                <div class="job-header">
                    <span class="job-type">${this.escHtml(job.job_type)}</span>
                    <span class="job-status ${job.status}">${job.status}</span>
                </div>
                ${job.status === 'running' ? `<div class="job-progress"><div class="job-progress-bar"><div class="job-progress-fill" style="width:${job.progress || 0}%"></div></div></div>` : ''}
                ${job.error_message ? `<div style="font-size:11px;color:var(--danger);margin-top:4px;">${this.escHtml(job.error_message)}</div>` : ''}
                <div style="margin-top:6px;display:flex;gap:4px;">
                    ${job.status === 'running' ? `<button class="btn btn-xs btn-danger" onclick="App.cancelJob('${job.id}')">取消</button>` : ''}
                    ${job.status === 'failed' ? `<button class="btn btn-xs btn-primary" onclick="App.retryJob('${job.id}')">重试</button>` : ''}
                </div>
            </div>
        `).join('');
    },
    async cancelJob(jobId) {
        await this.api(`/api/jobs/${jobId}/cancel`, 'POST');
        this.toast('已取消', 'info');
        this.loadJobs();
    },
    async retryJob(jobId) {
        await this.api(`/api/jobs/${jobId}/retry`, 'POST');
        this.toast('已重试', 'info');
        this.loadJobs();
    },

    // ==================== Workflow ====================
    _currentWorkflowRunId: null,
    _currentWorkflowStep: 0,
    async startWorkflow() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请选择章节', 'warning'); return; }
        document.getElementById('workflowModal').style.display = 'flex';
        const res = await this.api('/api/workflow/run', 'POST', {
            book_id: this.currentBookId, node_id: this.currentNodeId,
            goals: document.getElementById('workflowGoals').value || ''
        });
        if (res && res.run_id) {
            this._currentWorkflowRunId = res.run_id;
            this._currentWorkflowStep = 0;
            this.loadWorkflowStatus();
        }
    },
    closeWorkflow() {
        document.getElementById('workflowModal').style.display = 'none';
    },
    async loadWorkflowStatus() {
        if (!this._currentWorkflowRunId) return;
        const res = await this.api(`/api/workflow/run/${this._currentWorkflowRunId}`, 'GET');
        if (!res) return;
        const steps = res.step_results || [];
        const container = document.getElementById('workflowSteps');
        container.innerHTML = steps.map((s, i) => `
            <div class="workflow-step ${s.status}">
                <div class="step-num">${s.status === 'completed' ? '<i class="fas fa-check"></i>' : i + 1}</div>
                <div class="step-name">${this.escHtml(s.name)}</div>
                <div class="step-status">${s.status}</div>
            </div>
        `).join('');
        this._currentWorkflowStep = res.current_step || 0;
    },
    async runWorkflowStep() {
        if (!this._currentWorkflowRunId) return;
        this.toast('正在执行步骤...', 'info');
        const res = await this.api(`/api/workflow/run/${this._currentWorkflowRunId}/step/${this._currentWorkflowStep}`, 'POST', {
            user_id: this.currentUser?.id
        });
        if (res) {
            this.toast('步骤完成', 'success');
            this._currentWorkflowStep++;
            this.loadWorkflowStatus();
        }
    },

    // ==================== Snapshots & Recycle Bin ====================
    openSnapshots() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        document.getElementById('snapshotModal').style.display = 'flex';
        this.loadSnapshots();
    },
    closeSnapshots() {
        document.getElementById('snapshotModal').style.display = 'none';
    },
    switchSnapshotTab(tab, evt = null) {
        document.querySelectorAll('.snapshot-tab').forEach(t => t.classList.remove('active'));
        evt?.target?.classList.add('active');
        document.getElementById('snapshotList').style.display = tab === 'snapshots' ? 'block' : 'none';
        document.getElementById('recycleList').style.display = tab === 'recycle' ? 'block' : 'none';
        if (tab === 'recycle') this.loadRecycleBin();
    },
    async loadSnapshots() {
        const res = await this.api(`/api/snapshots/${this.currentBookId}`, 'GET');
        const container = document.getElementById('snapshotList');
        if (!res || res.length === 0) {
            container.innerHTML = '<p class="settings-hint">暂无快照</p>';
            return;
        }
        container.innerHTML = res.map(s => `
            <div class="snapshot-item">
                <div class="snap-info">
                    <div class="snap-label">${this.escHtml(s.label || s.snapshot_type)}</div>
                    <div class="snap-meta">${s.created_at || ''}</div>
                </div>
                <button class="btn btn-xs btn-primary" onclick="App.restoreSnapshot('${s.id}')">恢复</button>
            </div>
        `).join('');
    },
    async restoreSnapshot(snapshotId) {
        if (!confirm('确认恢复此快照？当前内容将被覆盖。')) return;
        await this.api(`/api/snapshots/${snapshotId}/restore`, 'POST');
        this.toast('已恢复', 'success');
        if (this.currentNodeId) this.loadNodeContent(this.currentNodeId);
    },
    async loadRecycleBin() {
        const res = await this.api(`/api/recycle-bin/${this.currentBookId}`, 'GET');
        const container = document.getElementById('recycleList');
        if (!res || res.length === 0) {
            container.innerHTML = '<p class="settings-hint">回收站为空</p>';
            return;
        }
        container.innerHTML = res.map(item => `
            <div class="recycle-item">
                <div class="recycle-info">
                    <div class="recycle-type">${this.escHtml(item.item_type)}</div>
                    <div class="recycle-name">${item.deleted_at || ''}</div>
                </div>
                <div>
                    <button class="btn btn-xs btn-primary" onclick="App.restoreRecycleItem('${item.id}')">恢复</button>
                    <button class="btn btn-xs btn-danger" onclick="App.deleteRecycleItem('${item.id}')">永久删除</button>
                </div>
            </div>
        `).join('');
    },
    async restoreRecycleItem(itemId) {
        await this.api(`/api/recycle-bin/${itemId}/restore`, 'POST');
        this.toast('已恢复', 'success');
        this.loadRecycleBin();
        this.loadDocTree();
    },
    async deleteRecycleItem(itemId) {
        if (!confirm('永久删除？此操作不可撤销。')) return;
        await this.api(`/api/recycle-bin/${itemId}`, 'DELETE');
        this.toast('已删除', 'info');
        this.loadRecycleBin();
    },

    // ==================== Writing Rules ====================
    async loadRuleSets() {
        if (!this.currentBookId) return;
        const res = await this.api(`/api/rules/${this.currentBookId}/sets`, 'GET');
        const container = document.getElementById('ruleSetsList');
        if (!res || res.length === 0) {
            container.innerHTML = '<p class="settings-hint">暂无规则集。点击"新建规则集"开始。</p>';
            return;
        }
        let html = '';
        for (const set of res) {
            const rules = await this.api(`/api/rules/${this.currentBookId}/rules?rule_set_id=${set.id}`, 'GET');
            html += `<div class="rule-set-card">
                <div class="rule-set-header">
                    <span class="rule-set-name">${this.escHtml(set.name)}</span>
                    <div>
                        <button class="btn btn-xs btn-primary" onclick="App.addRule('${set.id}')"><i class="fas fa-plus"></i></button>
                        <button class="btn btn-xs btn-danger" onclick="App.deleteRuleSet('${set.id}')"><i class="fas fa-trash"></i></button>
                    </div>
                </div>
                <div class="rules-in-set">`;
            if (rules && rules.length > 0) {
                rules.forEach(r => {
                    html += `<div class="rule-item">
                        <span class="rule-cat">${r.category}</span>
                        <span class="rule-name">${this.escHtml(r.title)}</span>
                        <div>
                            <button class="btn btn-xs btn-ghost" onclick="App.editRule('${r.id}', '${set.id}')"><i class="fas fa-edit"></i></button>
                            <button class="btn btn-xs btn-ghost" onclick="App.deleteRule('${r.id}')"><i class="fas fa-trash"></i></button>
                        </div>
                    </div>`;
                });
            }
            html += '</div></div>';
        }
        container.innerHTML = html;
    },
    async addRuleSet() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const name = prompt('规则集名称:');
        if (!name) return;
        await this.api(`/api/rules/${this.currentBookId}/sets`, 'POST', { name });
        this.toast('已创建', 'success');
        this.loadRuleSets();
    },
    async deleteRuleSet(setId) {
        if (!confirm('删除此规则集及所有规则？')) return;
        await this.api(`/api/rules/${this.currentBookId}/sets/${setId}`, 'DELETE');
        this.loadRuleSets();
    },
    addRule(setId) {
        document.getElementById('ruleEditId').value = '';
        document.getElementById('ruleEditSetId').value = setId;
        document.getElementById('ruleTitle').value = '';
        document.getElementById('ruleContent').value = '';
        document.getElementById('ruleEditModal').style.display = 'flex';
    },
    async editRule(ruleId, setId) {
        const rules = await this.api(`/api/rules/${this.currentBookId}/rules?rule_set_id=${setId}`, 'GET');
        const rule = (rules || []).find(r => r.id === ruleId);
        if (!rule) return;
        document.getElementById('ruleEditId').value = ruleId;
        document.getElementById('ruleEditSetId').value = setId;
        document.getElementById('ruleTitle').value = rule.title || '';
        document.getElementById('ruleCategory').value = rule.category || 'style';
        document.getElementById('ruleScope').value = rule.scope_type || 'book';
        document.getElementById('ruleContent').value = rule.content || '';
        document.getElementById('ruleEditModal').style.display = 'flex';
    },
    closeRuleEditModal() {
        document.getElementById('ruleEditModal').style.display = 'none';
    },
    async saveRule() {
        const id = document.getElementById('ruleEditId').value;
        const setId = document.getElementById('ruleEditSetId').value;
        const data = {
            rule_set_id: setId,
            title: document.getElementById('ruleTitle').value,
            category: document.getElementById('ruleCategory').value,
            scope_type: document.getElementById('ruleScope').value,
            content: document.getElementById('ruleContent').value
        };
        if (id) {
            await this.api(`/api/rules/${this.currentBookId}/rules/${id}`, 'PUT', data);
        } else {
            await this.api(`/api/rules/${this.currentBookId}/rules`, 'POST', data);
        }
        this.toast('规则已保存', 'success');
        this.closeRuleEditModal();
        this.loadRuleSets();
    },
    async deleteRule(ruleId) {
        if (!confirm('删除此规则？')) return;
        await this.api(`/api/rules/${this.currentBookId}/rules/${ruleId}`, 'DELETE');
        this.loadRuleSets();
    },
    async checkRuleConflicts() {
        if (!this.currentBookId) return;
        const res = await this.api(`/api/rules/${this.currentBookId}/conflicts`, 'GET');
        if (res && res.length > 0) {
            this.toast(`发现 ${res.length} 个规则冲突`, 'warning');
        } else {
            this.toast('未发现规则冲突', 'success');
        }
    },

    // ==================== Statistics Dashboard ====================
    async loadStatsDashboard() {
        if (!this.currentBookId) return;
        const res = await this.api(`/api/stats/${this.currentBookId}/enhanced`, 'GET');
        if (!res) return;
        const container = document.getElementById('statsDashboard');
        const totals = res.totals || {};
        const agents = res.agents || [];
        let html = `<div class="stats-grid">
            <div class="stat-card"><div class="stat-value">${totals.total_calls || 0}</div><div class="stat-label">总调用次数</div></div>
            <div class="stat-card"><div class="stat-value">${totals.success_rate || 0}%</div><div class="stat-label">成功率</div></div>
            <div class="stat-card"><div class="stat-value">${totals.adoption_rate || 0}%</div><div class="stat-label">采纳率</div></div>
            <div class="stat-card"><div class="stat-value">${totals.total_tokens || 0}</div><div class="stat-label">总Token数</div></div>
        </div>`;
        if (agents.length > 0) {
            html += `<table class="stats-table"><thead><tr>
                <th>角色</th><th>调用</th><th>成功率</th><th>采纳率</th><th>平均延迟</th><th>Token</th>
            </tr></thead><tbody>`;
            agents.forEach(a => {
                html += `<tr><td>${this.escHtml(a.role)}</td><td>${a.call_count}</td><td>${a.success_rate}%</td><td>${a.adoption_rate}%</td><td>${a.avg_first_token_ms}ms</td><td>${a.total_tokens}</td></tr>`;
            });
            html += '</tbody></table>';
        }
        container.innerHTML = html;
    },

    // ==================== Import Dialog ====================
    _importFileData: null,
    _importFileType: null,
    showImportDialog() {
        document.getElementById('importModal').style.display = 'flex';
        document.getElementById('importPreview').style.display = 'none';
    },
    closeImportDialog() {
        document.getElementById('importModal').style.display = 'none';
        this._importFileData = null;
    },
    async handleImportFile(event) {
        const file = event.target.files[0];
        if (!file) return;
        const ext = file.name.split('.').pop().toLowerCase();
        this._importFileType = ext;
        document.getElementById('importBookTitle').value = file.name.replace(/\.[^.]+$/, '');

        if (ext === 'json') {
            const text = await file.text();
            this._importFileData = text;
            document.getElementById('importPreviewContent').textContent = `JSON 工作空间文件 (${(text.length / 1024).toFixed(1)} KB)`;
        } else if (ext === 'md' || ext === 'txt') {
            const text = await file.text();
            this._importFileData = text;
            const lines = text.split('\n').length;
            document.getElementById('importPreviewContent').textContent = `${ext.toUpperCase()} 文件: ${lines} 行，${text.length} 字符`;
        } else if (ext === 'docx') {
            this._importFileData = file;
            document.getElementById('importPreviewContent').textContent = `DOCX 文件: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        } else {
            this.toast('不支持的文件格式', 'error');
            return;
        }
        document.getElementById('importPreview').style.display = 'block';
    },
    async confirmImport() {
        const title = document.getElementById('importBookTitle').value || '导入的书籍';
        const type = this._importFileType;
        if (!this._importFileData) { this.toast('无文件数据', 'error'); return; }

        if (type === 'json') {
            try {
                const data = JSON.parse(this._importFileData);
                const res = await this.api('/api/import', 'POST', data);
                if (res && res.book_id) {
                    this.toast('导入成功', 'success');
                    await this.loadBooks();
                    this.switchBook(res.book_id);
                }
            } catch (e) {
                this.toast('JSON 解析失败: ' + e.message, 'error');
            }
        } else if (type === 'md' || type === 'txt') {
            const res = await this.api('/api/import/file', 'POST', {
                format: type === 'md' ? 'markdown' : 'txt',
                content: this._importFileData,
                title
            });
            if (res && res.book_id) {
                this.toast('导入成功', 'success');
                await this.loadBooks();
                this.switchBook(res.book_id);
            }
        } else if (type === 'docx') {
            const formData = new FormData();
            formData.append('file', this._importFileData);
            formData.append('title', title);
            formData.append('format', 'docx');
            const res = await fetch('/api/import/file', {
                method: 'POST',
                headers: this.authHeaders(),
                body: formData
            });
            const data = await res.json();
            if (data && data.book_id) {
                this.toast('导入成功', 'success');
                await this.loadBooks();
                this.switchBook(data.book_id);
            }
        }
        this.closeImportDialog();
    },

    // ==================== Memory Injection Log ====================
    async openInjectionLog() {
        if (!this.currentBookId) return;
        document.getElementById('injectionLogModal').style.display = 'flex';
        const params = this.currentNodeId ? `?node_id=${this.currentNodeId}` : '';
        const res = await this.api(`/api/memory/injection-log/${this.currentBookId}${params}`, 'GET');
        const container = document.getElementById('injectionLogContent');
        if (!res || res.length === 0) {
            container.innerHTML = '<p class="settings-hint">暂无注入记录</p>';
            return;
        }
        const log = res[0];
        const items = typeof log.injected_items === 'string' ? JSON.parse(log.injected_items) : (log.injected_items || []);
        container.innerHTML = items.map(item => `
            <div class="injection-item">
                <span class="inject-tier t${item.tier || 0}">T${item.tier || 0}</span>
                <div class="inject-content">
                    <div class="inject-source">${this.escHtml(item.source || '')} (${item.type || ''})</div>
                    <div class="inject-preview">${this.escHtml((item.content_preview || '').substring(0, 150))}</div>
                    <div class="inject-reason">${this.escHtml(item.reason || '')}</div>
                </div>
            </div>
        `).join('');
    },
    closeInjectionLog() {
        document.getElementById('injectionLogModal').style.display = 'none';
    },

    // ==================== Embedding 索引 ====================
    async buildEmbeddingIndex() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        const status = document.getElementById('embeddingStatus');
        status.textContent = '正在构建 Embedding 索引...';
        const res = await this.api(`/api/embedding/${this.currentBookId}/build`, 'POST');
        if (res) {
            status.textContent = `Embedding 索引已构建: ${res.chunk_count || 0} chunks, 维度 ${res.dim || 0}`;
            this.toast('Embedding 索引构建完成', 'success');
        }
    },
    async loadEmbeddingStatus() {
        if (!this.currentBookId) return;
        const res = await this.api(`/api/embedding/${this.currentBookId}/status`);
        const status = document.getElementById('embeddingStatus');
        if (res && res.has_index && res.meta) {
            status.textContent = `Embedding: ${res.meta.model_id} | ${res.meta.chunk_count} chunks | ${res.meta.last_built_at?.substring(0, 10) || ''}`;
        } else {
            status.textContent = 'Embedding 索引未构建';
        }
    },

    // ==================== NER 实体识别 ====================
    async runNER() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }
        const text = document.getElementById('editorArea').innerText;
        this.toast('正在识别实体...', 'info');
        const res = await this.api(`/api/ner/${this.currentBookId}/extract`, 'POST', {
            node_id: this.currentNodeId, text
        });
        if (res && res.entities) {
            this.renderNERResults(res.entities);
            this.toast(`识别到 ${res.entities.length} 个实体`, 'success');
        }
    },
    async runNERAll() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        this.toast('正在全书识别...', 'info');
        const res = await this.api(`/api/ner/${this.currentBookId}/extract-all`, 'POST');
        if (res) {
            this.toast(`全书识别完成: ${res.total} 个实体`, 'success');
            this.loadNERResults();
        }
    },
    async loadNERResults() {
        if (!this.currentBookId) return;
        const nodeId = this.currentNodeId || '';
        const entities = await this.api(`/api/ner/${this.currentBookId}/entities?node_id=${nodeId}`);
        if (entities) this.renderNERResults(entities);
    },
    renderNERResults(entities) {
        const container = document.getElementById('nerEntityList');
        if (!entities || entities.length === 0) {
            container.innerHTML = '<p class="memory-helper-text">暂无识别结果</p>';
            return;
        }
        const groups = {};
        entities.forEach(e => {
            const type = e.entity_type || 'other';
            if (!groups[type]) groups[type] = [];
            groups[type].push(e);
        });
        const typeLabels = { character: '人物', location: '地点', faction: '组织', item: '物品', concept: '概念', event: '事件' };
        let html = '';
        for (const [type, items] of Object.entries(groups)) {
            html += `<div class="ner-entity-group"><h5><span class="entity-type-tag ${type}">${typeLabels[type] || type}</span> (${items.length})</h5>`;
            items.slice(0, 20).forEach(e => {
                const linked = e.linked_lorebook_id ? '<i class="fas fa-link" style="color:var(--success);font-size:10px;" title="已关联"></i>' : '';
                const statusIcon = e.status === 'confirmed' ? '✓' : (e.status === 'dismissed' ? '✗' : '');
                html += `<div class="ner-entity-item">
                    <span class="entity-name">${this.escHtml(e.entity_text)} ${linked} ${statusIcon}</span>
                    <span class="entity-conf">${Math.round((e.confidence || 0) * 100)}%</span>
                    <span class="entity-actions">
                        ${e.status !== 'confirmed' ? `<button class="btn btn-xs btn-ghost" onclick="App.confirmEntity('${e.id}')" title="确认"><i class="fas fa-check"></i></button>` : ''}
                        ${e.status !== 'dismissed' ? `<button class="btn btn-xs btn-ghost" onclick="App.dismissEntity('${e.id}')" title="忽略"><i class="fas fa-times"></i></button>` : ''}
                    </span>
                </div>`;
            });
            html += '</div>';
        }
        container.innerHTML = html;
    },
    async confirmEntity(entityId) {
        await this.api(`/api/ner/${this.currentBookId}/entities/${entityId}/confirm`, 'POST');
        this.loadNERResults();
    },
    async dismissEntity(entityId) {
        await this.api(`/api/ner/${this.currentBookId}/entities/${entityId}/dismiss`, 'POST');
        this.loadNERResults();
    },

    // ==================== Knowledge Graph (vis-network) ====================
    _visNetwork: null,
    async extractKnowledge() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }
        const text = document.getElementById('editorArea').innerText;
        this.toast('正在抽取图谱...', 'info');
        const res = await this.api(`/api/knowledge/${this.currentBookId}/extract`, 'POST', {
            node_id: this.currentNodeId, text
        });
        if (res) {
            this.toast(`抽取完成: ${(res.new_edges || []).length} 关系, ${(res.new_events || []).length} 事件`, 'success');
            this.loadKnowledgeGraph();
        }
    },
    async extractKnowledgeAll() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        this.toast('正在全书抽取图谱...', 'info');
        const res = await this.api(`/api/knowledge/${this.currentBookId}/extract-all`, 'POST');
        if (res) {
            this.toast(`全书抽取完成: ${res.total?.relations || 0} 关系, ${res.total?.events || 0} 事件`, 'success');
            this.loadKnowledgeGraph();
        }
    },
    async loadKnowledgeGraph(center) {
        if (!this.currentBookId) return;
        const url = center
            ? `/api/knowledge/${this.currentBookId}/graph?center=${encodeURIComponent(center)}&depth=2`
            : `/api/knowledge/${this.currentBookId}/graph`;
        const data = await this.api(url);
        if (data && data.nodes) {
            this.renderGraphVisualization(data);
        }
    },
    renderGraphVisualization(data) {
        const container = document.getElementById('knowledgeGraphVis');
        const oldGraph = document.getElementById('entityGraph');
        if (typeof vis !== 'undefined' && vis.Network) {
            oldGraph.style.display = 'none';
            container.style.display = 'block';
            const typeColors = {
                character: '#a78bfa', location: '#34d399', faction: '#fbbf24',
                item: '#60a5fa', concept: '#f472b6', event: '#f87171'
            };
            const nodes = new vis.DataSet(data.nodes.map(n => ({
                id: n.id, label: n.entity_name || n.label,
                color: { background: typeColors[n.entity_type] || '#888', border: '#444' },
                font: { color: '#e0e0e0', size: 12 },
                shape: 'dot', size: Math.min(30, 10 + (n.mention_count || 1) * 2)
            })));
            const edges = new vis.DataSet(data.edges.map(e => ({
                from: e.source_node_id || e.from, to: e.target_node_id || e.to,
                label: e.relation_type || '', font: { color: '#888', size: 10 },
                color: { color: (e.confidence || 0.5) < 0.5 ? '#666' : '#999' },
                dashes: (e.status === 'auto' && (e.confidence || 0.5) < 0.5),
                arrows: 'to'
            })));
            if (this._visNetwork) this._visNetwork.destroy();
            this._visNetwork = new vis.Network(container, { nodes, edges }, {
                physics: { stabilization: { iterations: 100 }, barnesHut: { gravitationalConstant: -3000 } },
                interaction: { hover: true, tooltipDelay: 200 },
                layout: { improvedLayout: true }
            });
            this._visNetwork.on('click', params => {
                if (params.nodes.length > 0) {
                    const nodeId = params.nodes[0];
                    const node = data.nodes.find(n => n.id === nodeId);
                    if (node) this.showGraphEntityDetail(node);
                }
            });
        } else {
            // Fallback: text rendering
            oldGraph.innerHTML = data.nodes.map(n =>
                `<div style="padding:4px;font-size:12px;">${n.entity_name} (${n.entity_type})</div>`
            ).join('');
        }
    },
    showGraphEntityDetail(node) {
        const detail = document.getElementById('graphEntityDetail');
        detail.style.display = 'block';
        detail.innerHTML = `<h5>${this.escHtml(node.entity_name)} <span class="entity-type-tag ${node.entity_type}">${node.entity_type}</span></h5>
            <div class="detail-row"><span class="detail-label">描述:</span>${this.escHtml(node.description || '无')}</div>
            <div class="detail-row"><span class="detail-label">出现次数:</span>${node.mention_count || 0}</div>
            <div class="detail-row"><span class="detail-label">首次出现:</span>${node.first_seen_node || '-'}</div>`;
    },
    switchGraphView(mode) {
        const visContainer = document.getElementById('knowledgeGraphVis');
        const tableContainer = document.getElementById('graphTableView');
        const oldGraph = document.getElementById('entityGraph');
        if (mode === 'visual') {
            visContainer.style.display = 'block';
            tableContainer.style.display = 'none';
            oldGraph.style.display = 'none';
            this.loadKnowledgeGraph();
        } else {
            visContainer.style.display = 'none';
            tableContainer.style.display = 'block';
            oldGraph.style.display = 'none';
            this.loadGraphTable();
        }
    },
    async loadGraphTable() {
        if (!this.currentBookId) return;
        const edges = await this.api(`/api/knowledge/${this.currentBookId}/graph`);
        const container = document.getElementById('graphTableView');
        if (!edges || !edges.edges || edges.edges.length === 0) {
            container.innerHTML = '<p class="memory-helper-text">暂无关系数据</p>';
            return;
        }
        let html = '<table><tr><th>源实体</th><th>关系</th><th>目标实体</th><th>置信度</th></tr>';
        const nodeMap = {};
        (edges.nodes || []).forEach(n => nodeMap[n.id] = n.entity_name || n.label);
        edges.edges.forEach(e => {
            html += `<tr>
                <td>${this.escHtml(nodeMap[e.source_node_id || e.from] || '')}</td>
                <td>${this.escHtml(e.relation_type || '')}</td>
                <td>${this.escHtml(nodeMap[e.target_node_id || e.to] || '')}</td>
                <td>${Math.round((e.confidence || 0) * 100)}%</td>
            </tr>`;
        });
        container.innerHTML = html + '</table>';
    },
    filterGraph(query) {
        if (!query.trim()) { this.loadKnowledgeGraph(); return; }
        this.loadKnowledgeGraph(query.trim());
    },

    // ==================== Foreshadow Payoff ====================
    async scanPayoffs() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }
        const text = document.getElementById('editorArea').innerText;
        this.toast('正在扫描伏笔回收...', 'info');
        const res = await this.api(`/api/foreshadow/${this.currentBookId}/scan-payoffs`, 'POST', {
            node_id: this.currentNodeId, text
        });
        if (res && res.payoffs && res.payoffs.length > 0) {
            this.showPayoffResults(res.payoffs);
            this.toast(`检测到 ${res.payoffs.length} 个可能的伏笔回收`, 'success');
        } else {
            this.toast('未检测到伏笔回收', 'info');
        }
    },
    showPayoffResults(payoffs) {
        const section = document.getElementById('payoffResultsSection');
        const container = document.getElementById('payoffResults');
        section.style.display = 'block';
        container.innerHTML = payoffs.map(p => `
            <div class="payoff-card">
                <div class="payoff-label">${this.escHtml(p.label || p.foreshadow_label || '')}</div>
                <span class="payoff-type ${p.payoff_type || ''}">${p.payoff_type === 'resolved' ? '完全回收' :
                    (p.payoff_type === 'partially_resolved' ? '部分回收' : (p.payoff_type === 'strengthened' ? '强化' : p.payoff_type || ''))}</span>
                <span class="entity-conf" style="margin-left:8px;">${Math.round((p.confidence || 0) * 100)}%</span>
                <div class="payoff-evidence">"${this.escHtml(p.evidence || p.evidence_quote || '')}"</div>
                <div class="payoff-actions">
                    <button class="btn btn-xs btn-primary" onclick="App.applyPayoff('${p.foreshadow_id || p.id}', '${p.payoff_type || 'resolved'}', '${this.escHtml(p.evidence || p.evidence_quote || '')}')">
                        <i class="fas fa-check"></i> 确认
                    </button>
                    <button class="btn btn-xs btn-ghost" onclick="this.parentElement.parentElement.remove()">
                        <i class="fas fa-times"></i> 忽略
                    </button>
                </div>
            </div>
        `).join('');
    },
    async applyPayoff(foreshadowId, payoffType, evidence) {
        await this.api(`/api/foreshadow/${this.currentBookId}/apply-payoff`, 'POST', {
            foreshadow_id: foreshadowId,
            node_id: this.currentNodeId,
            payoff_type: payoffType,
            evidence: evidence
        });
        this.toast('伏笔回填已应用', 'success');
        this.loadNarrativeAnalysis();
    },

    // ==================== Narrative Analysis ====================
    async analyzeChapter() {
        if (!this.currentBookId || !this.currentNodeId) { this.toast('请先选择章节', 'warning'); return; }
        this.toast('正在分析章节叙事...', 'info');
        const res = await this.api(`/api/narrative/${this.currentBookId}/analyze`, 'POST', { node_id: this.currentNodeId });
        if (res && res.tension !== undefined) {
            this.toast('章节分析完成', 'success');
            this.loadNarrativeAnalysis();
        }
    },
    async analyzeBook() {
        if (!this.currentBookId) { this.toast('请先选择书籍', 'warning'); return; }
        this.toast('正在全书分析...可能需要较长时间', 'info');
        const res = await this.api(`/api/narrative/${this.currentBookId}/analyze-all`, 'POST');
        if (res) {
            this.toast(`全书分析完成: ${res.count} 章`, 'success');
            this.loadNarrativeAnalysis();
        }
    },
    async loadNarrativeAnalysis() {
        if (!this.currentBookId) return;
        // Load tension curve
        const tension = await this.api(`/api/narrative/${this.currentBookId}/tension`);
        if (tension && tension.length > 0) {
            this.renderTensionChart(tension);
        }
        // Load emotion profile
        const emotions = await this.api(`/api/narrative/${this.currentBookId}/emotions`);
        if (emotions && emotions.length > 0) {
            this.renderEmotionChart(emotions);
        }
        // Load character arcs
        const arcs = await this.api(`/api/narrative/${this.currentBookId}/character-arcs`);
        if (arcs && Object.keys(arcs).length > 0) {
            this.renderCharacterArcs(arcs);
        }
        // Load pacing
        const pacing = await this.api(`/api/narrative/${this.currentBookId}/pacing`);
        if (pacing) this.renderPacingDiagnosis(pacing);
        // Load completeness
        const comp = await this.api(`/api/narrative/${this.currentBookId}/completeness`);
        if (comp) this.renderArcCompleteness(comp);
    },
    renderTensionChart(data) {
        const container = document.getElementById('analysisTensionChart');
        if (!data || data.length < 2) { container.innerHTML = '<p class="memory-helper-text">数据不足</p>'; return; }
        const w = 500, h = 120, pad = 30;
        const n = data.length;
        const xStep = (w - pad * 2) / (n - 1);
        let path = '';
        let conflictPath = '';
        data.forEach((d, i) => {
            const x = pad + i * xStep;
            const y = h - pad - (d.tension / 100) * (h - pad * 2);
            const cy = h - pad - (d.conflict_level / 100) * (h - pad * 2);
            path += (i === 0 ? 'M' : 'L') + `${x},${y}`;
            conflictPath += (i === 0 ? 'M' : 'L') + `${x},${cy}`;
        });
        let dots = data.map((d, i) => {
            const x = pad + i * xStep;
            const y = h - pad - (d.tension / 100) * (h - pad * 2);
            return `<circle cx="${x}" cy="${y}" r="3" fill="#a78bfa" class="chart-dot" onclick="App.toast('${d.chapter_title}: 张力${d.tension}', 'info')"><title>${d.chapter_title}: 张力${d.tension}</title></circle>`;
        }).join('');
        container.innerHTML = `<svg viewBox="0 0 ${w} ${h}">
            <line x1="${pad}" y1="${h-pad}" x2="${w-pad}" y2="${h-pad}" stroke="#444" stroke-width="1"/>
            <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${h-pad}" stroke="#444" stroke-width="1"/>
            <text x="${pad-5}" y="${pad+4}" fill="#888" font-size="10" text-anchor="end">100</text>
            <text x="${pad-5}" y="${h-pad+4}" fill="#888" font-size="10" text-anchor="end">0</text>
            <path d="${path}" fill="none" stroke="#a78bfa" stroke-width="2"/>
            <path d="${conflictPath}" fill="none" stroke="#f87171" stroke-width="1" stroke-dasharray="4"/>
            ${dots}
            <text x="${w-pad}" y="${h-8}" fill="#888" font-size="9">章节</text>
            <text x="${pad+4}" y="${pad-4}" fill="#a78bfa" font-size="9">张力</text>
            <text x="${pad+40}" y="${pad-4}" fill="#f87171" font-size="9">冲突</text>
        </svg>`;
    },
    renderEmotionChart(data) {
        const container = document.getElementById('emotionChart');
        if (!data || data.length < 2) { container.innerHTML = '<p class="memory-helper-text">数据不足</p>'; return; }
        const emotionKeys = ['压抑', '紧张', '热血', '温柔', '悲伤', '释然'];
        const colors = ['#6366f1', '#ef4444', '#f59e0b', '#10b981', '#3b82f6', '#8b5cf6'];
        const w = 500, h = 120, pad = 30;
        const n = data.length;
        const xStep = (w - pad * 2) / (n - 1);
        let paths = emotionKeys.map((key, ki) => {
            let d = '';
            data.forEach((ch, i) => {
                const x = pad + i * xStep;
                const val = (ch.emotions && ch.emotions[key]) || 0;
                const y = h - pad - val * (h - pad * 2);
                d += (i === 0 ? 'M' : 'L') + `${x},${y}`;
            });
            return `<path d="${d}" fill="none" stroke="${colors[ki]}" stroke-width="1.5" opacity="0.8"/>`;
        }).join('');
        let legend = emotionKeys.map((key, ki) =>
            `<text x="${pad + ki * 50}" y="${h - 2}" fill="${colors[ki]}" font-size="9">${key}</text>`
        ).join('');
        container.innerHTML = `<svg viewBox="0 0 ${w} ${h}">
            <line x1="${pad}" y1="${h-pad}" x2="${w-pad}" y2="${h-pad}" stroke="#444" stroke-width="1"/>
            ${paths}${legend}
        </svg>`;
    },
    renderCharacterArcs(arcs) {
        const container = document.getElementById('characterArcChart');
        const names = Object.keys(arcs).slice(0, 6);
        if (names.length === 0) { container.innerHTML = '<p class="memory-helper-text">暂无角色数据</p>'; return; }
        const colors = ['#a78bfa', '#34d399', '#fbbf24', '#60a5fa', '#f472b6', '#f87171'];
        const w = 500, laneH = 24, pad = 30;
        const h = pad * 2 + names.length * laneH;
        let svg = `<svg viewBox="0 0 ${w} ${h}">`;
        names.forEach((name, ni) => {
            const y = pad + ni * laneH;
            const pts = arcs[name];
            svg += `<text x="4" y="${y + 14}" fill="${colors[ni]}" font-size="10">${name}</text>`;
            svg += `<line x1="${pad + 50}" y1="${y + laneH}" x2="${w - pad}" y2="${y + laneH}" stroke="#333" stroke-width="0.5"/>`;
            if (pts && pts.length > 0) {
                const maxCh = Math.max(...pts.map(p => p.chapter_index), 1);
                pts.forEach(p => {
                    const x = pad + 50 + (p.chapter_index / maxCh) * (w - pad * 2 - 50);
                    const barH = (p.presence || 0) / 100 * laneH * 0.8;
                    svg += `<rect x="${x-2}" y="${y + laneH - barH}" width="4" height="${barH}" fill="${colors[ni]}" opacity="0.7">
                        <title>${p.chapter_title}: 出场${p.presence}%</title></rect>`;
                });
            }
        });
        container.innerHTML = svg + '</svg>';
    },
    renderPacingDiagnosis(pacing) {
        const container = document.getElementById('pacingDiagnosis');
        if (!pacing || !pacing.issues || pacing.issues.length === 0) {
            const overall = pacing?.overall === 'too_flat' ? '整体偏平' : (pacing?.overall === 'too_intense' ? '整体偏紧' : '节奏均衡');
            container.innerHTML = `<p class="memory-helper-text">平均张力: ${pacing?.avg_tension || '-'} | ${overall}</p>`;
            return;
        }
        container.innerHTML = pacing.issues.map(issue => `
            <div class="pacing-issue severity-${issue.severity}">
                <div class="issue-message">${this.escHtml(issue.message)}</div>
                <div class="issue-suggestion">${this.escHtml(issue.suggestion || '')}</div>
            </div>
        `).join('');
    },
    renderArcCompleteness(comp) {
        const container = document.getElementById('arcCompleteness');
        if (!comp || comp.score === undefined) { container.innerHTML = '<p class="memory-helper-text">数据不足</p>'; return; }
        container.innerHTML = `
            <div class="arc-score">${comp.score}/100</div>
            <div class="arc-structure">${this.escHtml(comp.structure || '')}</div>
            <div style="margin-top:8px;">
                <div class="arc-check"><i class="fas fa-${comp.has_setup ? 'check' : 'times'}"></i> 开端铺垫</div>
                <div class="arc-check"><i class="fas fa-${comp.has_rising ? 'check' : 'times'}"></i> 上升发展</div>
                <div class="arc-check"><i class="fas fa-${comp.has_climax ? 'check' : 'times'}"></i> 高潮 ${comp.climax_chapter ? '(' + this.escHtml(comp.climax_chapter) + ')' : ''}</div>
                <div class="arc-check"><i class="fas fa-${comp.has_resolution ? 'check' : 'times'}"></i> 收束结局</div>
            </div>
        `;
    }
};

// 初始化
document.addEventListener('DOMContentLoaded', () => App.init());
