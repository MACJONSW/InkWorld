/**
 * 墨境 · AI 长篇小说写作平台 - 主应用逻辑
 */
const App = window.App = {
    authToken: localStorage.getItem('auth_token') || '',
    currentUser: null,
    authDialogResolve: null,
    formDialogResolve: null,
    confirmDialogResolve: null,
    formDialogFieldsConfig: [],
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
    lastEditorPointer: null,
    slashMenuState: null,
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

    showFormDialog(config = {}) {
        const modal = document.getElementById('formDialogModal');
        const titleEl = document.getElementById('formDialogTitle');
        const descEl = document.getElementById('formDialogDescription');
        const fieldsEl = document.getElementById('formDialogFields');
        const submitEl = document.getElementById('formDialogSubmit');
        if (!modal || !titleEl || !descEl || !fieldsEl || !submitEl) {
            return Promise.resolve(null);
        }

        const fields = Array.isArray(config.fields) ? config.fields : [];
        this.formDialogFieldsConfig = fields;
        titleEl.textContent = config.title || '输入信息';
        descEl.textContent = config.description || '';
        descEl.style.display = config.description ? 'block' : 'none';
        submitEl.textContent = config.submitLabel || '确认';
        fieldsEl.innerHTML = fields.map((field) => this.renderFormDialogField(field)).join('');
        modal.style.display = 'flex';

        requestAnimationFrame(() => {
            fieldsEl.querySelector('[data-dialog-field]')?.focus();
        });

        return new Promise((resolve) => {
            this.formDialogResolve = resolve;
        });
    },

    renderFormDialogField(field) {
        const escapeHtml = (value) => String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
        const key = field.key || '';
        const id = `formDialogField-${key}`;
        const label = escapeHtml(field.label || key);
        const placeholder = escapeHtml(field.placeholder || '');
        const initial = field.value ?? '';
        const rows = field.rows || 4;
        const requiredMark = field.required ? ' <span style="color:var(--danger);">*</span>' : '';
        const help = field.help ? `<div class="settings-hint">${escapeHtml(field.help)}</div>` : '';

        if ((field.type || 'text') === 'textarea') {
            return `
                <div class="form-group">
                    <label for="${id}">${label}${requiredMark}</label>
                    <textarea id="${id}" data-dialog-field="${escapeHtml(key)}" rows="${rows}" placeholder="${placeholder}">${escapeHtml(initial)}</textarea>
                    ${help}
                </div>
            `;
        }

        if (field.type === 'select') {
            const options = (field.options || []).map((option) => {
                const value = option.value ?? option;
                const text = option.label ?? option;
                return `<option value="${escapeHtml(value)}" ${String(value) === String(initial) ? 'selected' : ''}>${escapeHtml(text)}</option>`;
            }).join('');
            return `
                <div class="form-group">
                    <label for="${id}">${label}${requiredMark}</label>
                    <select id="${id}" data-dialog-field="${escapeHtml(key)}">
                        ${options}
                    </select>
                    ${help}
                </div>
            `;
        }

        const inputType = field.type || 'text';
        const min = field.min !== undefined ? ` min="${field.min}"` : '';
        const max = field.max !== undefined ? ` max="${field.max}"` : '';
        const step = field.step !== undefined ? ` step="${field.step}"` : '';
        return `
            <div class="form-group">
                <label for="${id}">${label}${requiredMark}</label>
                <input type="${escapeHtml(inputType)}" id="${id}" data-dialog-field="${escapeHtml(key)}" value="${escapeHtml(initial)}" placeholder="${placeholder}"${min}${max}${step}>
                ${help}
            </div>
        `;
    },

    submitFormDialog() {
        const values = {};
        for (const field of this.formDialogFieldsConfig || []) {
            const el = document.querySelector(`[data-dialog-field="${field.key}"]`);
            if (!el) continue;
            const rawValue = field.type === 'checkbox' ? !!el.checked : (el.value ?? '');
            const value = typeof rawValue === 'string' && field.trim !== false ? rawValue.trim() : rawValue;
            if (field.required && (typeof value === 'string' ? !value.trim() : !value)) {
                this.toast(`${field.label || field.key}不能为空`, 'warning');
                el.focus();
                return;
            }
            values[field.key] = value;
        }
        this.finishFormDialog(values);
    },

    cancelFormDialog() {
        this.finishFormDialog(null);
    },

    finishFormDialog(result) {
        document.getElementById('formDialogModal').style.display = 'none';
        const resolve = this.formDialogResolve;
        this.formDialogResolve = null;
        this.formDialogFieldsConfig = [];
        if (resolve) {
            resolve(result);
        }
    },

    showConfirmDialog(config = {}) {
        const modal = document.getElementById('confirmDialogModal');
        const titleEl = document.getElementById('confirmDialogTitle');
        const messageEl = document.getElementById('confirmDialogMessage');
        const confirmBtn = document.getElementById('confirmDialogConfirmBtn');
        const cancelBtn = document.getElementById('confirmDialogCancelBtn');
        if (!modal || !titleEl || !messageEl || !confirmBtn || !cancelBtn) {
            return Promise.resolve(false);
        }

        titleEl.textContent = config.title || '请确认';
        messageEl.textContent = config.message || '确认执行这项操作吗？';
        confirmBtn.textContent = config.confirmLabel || '确认';
        cancelBtn.textContent = config.cancelLabel || '取消';
        confirmBtn.className = `btn ${config.confirmClass || 'btn-danger'}`;
        cancelBtn.className = `btn ${config.cancelClass || 'btn-ghost'}`;
        modal.style.display = 'flex';

        requestAnimationFrame(() => {
            cancelBtn.focus();
        });

        return new Promise((resolve) => {
            this.confirmDialogResolve = resolve;
        });
    },

    acceptConfirmDialog() {
        this.finishConfirmDialog(true);
    },

    cancelConfirmDialog() {
        this.finishConfirmDialog(false);
    },

    finishConfirmDialog(result) {
        document.getElementById('confirmDialogModal').style.display = 'none';
        const resolve = this.confirmDialogResolve;
        this.confirmDialogResolve = null;
        if (resolve) {
            resolve(!!result);
        }
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

    getRoleRegistry() {
        return window.APP_ROLE_REGISTRY || { routing_roles: [], agent_groups: [], agents: [] };
    },

    getRoutingRoles() {
        return this.getRoleRegistry().routing_roles || [];
    },

    getAgentRegistry() {
        return this.getRoleRegistry().agents || [];
    },

    getAgentMap() {
        return this.getAgentRegistry().reduce((acc, agent) => {
            acc[agent.id] = agent;
            return acc;
        }, {});
    },

    getAgentGroupMap() {
        return this.getAgentRegistry().reduce((acc, agent) => {
            acc[agent.id] = agent.group;
            return acc;
        }, {});
    },

    getAgentPanelMap() {
        return this.getAgentRegistry().reduce((acc, agent) => {
            acc[agent.id] = agent.panel_id;
            return acc;
        }, {});
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
        const typeLabel = type === 'volume' ? '卷' : type === 'chapter' ? '章' : '场景';
        const form = await this.showFormDialog({
            title: `新建${typeLabel}`,
            description: '为新节点输入一个清晰的名称。',
            submitLabel: '创建',
            fields: [
                {
                    key: 'title',
                    label: `${typeLabel}名称`,
                    placeholder: `输入${typeLabel}标题`,
                    required: true
                }
            ]
        });
        const title = form?.title;
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
        const form = await this.showFormDialog({
            title: '重命名节点',
            submitLabel: '保存',
            fields: [
                {
                    key: 'title',
                    label: '名称',
                    value: oldTitle,
                    required: true
                }
            ]
        });
        const newTitle = form?.title;
        if (newTitle && newTitle !== oldTitle) {
            await this.api(`/api/nodes/${nodeId}`, 'PUT', { title: newTitle });
            this.loadDocTree();
            if (nodeId === this.currentNodeId) {
                document.getElementById('editorPath').textContent = newTitle;
            }
        }
    },

    async deleteNode(nodeId) {
        const confirmed = await this.showConfirmDialog({
            title: '删除节点',
            message: '确定删除此节点及其所有子节点？此操作不可撤销。',
            confirmLabel: '删除',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
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
                this.closeSlashMenu();
            }
        });

        const rememberPointer = (event) => {
            this.lastEditorPointer = { x: event.clientX, y: event.clientY };
        };
        editor.addEventListener('pointerdown', rememberPointer);
        editor.addEventListener('pointerup', rememberPointer);
        editor.addEventListener('mousemove', rememberPointer);

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
            e.preventDefault();
            this.openSlashMenu();
            return;
        }

        // Esc 关闭菜单/停止生成/消除ghost text
        if (e.key === 'Escape') {
            this.closeSlashMenu();
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
        this.closeSlashMenu();
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
    openSlashMenu() {
        const menu = document.getElementById('slashMenu');
        if (!menu) return;
        this.removeSelectionPopup();
        const info = this.getSlashMenuContext();
        this.slashMenuState = info;
        menu.innerHTML = this.renderSlashMenu(info);
        menu.style.display = 'block';
        menu.style.visibility = 'hidden';
        this.positionSlashMenu(menu, info);
        menu.style.visibility = 'visible';
    },

    closeSlashMenu() {
        const menu = document.getElementById('slashMenu');
        if (menu) {
            menu.style.display = 'none';
            menu.style.visibility = '';
        }
        this.slashMenuState = null;
    },

    getSlashMenuContext() {
        const editor = document.getElementById('editorArea');
        const info = this.getEditorSelectionInfo();
        const fullText = editor?.innerText || '';
        const contextText = (info.selectedText || info.contextText || fullText.slice(-2000) || '').trim();
        return {
            ...info,
            contextText,
            previewText: (info.selectedText || contextText || '').trim()
        };
    },

    getEditorSelectionInfo() {
        const editor = document.getElementById('editorArea');
        const sel = window.getSelection();
        const fallback = { selectedText: '', contextText: '', hasSelection: false, rect: null };
        if (!editor || !sel || !sel.rangeCount) return fallback;
        const range = sel.getRangeAt(0);
        if (!editor.contains(range.commonAncestorContainer)) return fallback;

        const selectedText = sel.toString().trim();
        const rect = this.getRangeRect(range);
        const caretOffset = this.getCaretCharacterOffsetWithin(editor, range);
        const fullText = editor.innerText || '';
        let contextText = '';

        if (selectedText) {
            contextText = selectedText;
        } else if (typeof caretOffset === 'number') {
            const before = Math.max(0, caretOffset - 800);
            const after = Math.min(fullText.length, caretOffset + 1200);
            contextText = fullText.slice(before, after);
        }

        return {
            selectedText,
            contextText,
            hasSelection: !!selectedText,
            rect
        };
    },

    getRangeRect(range) {
        if (!range) return null;
        const rects = range.getClientRects();
        const rect = rects?.length ? rects[0] : range.getBoundingClientRect();
        if (!rect) return null;
        if (rect.width === 0 && rect.height === 0 && rect.top === 0 && rect.left === 0) return null;
        return rect;
    },

    getCaretCharacterOffsetWithin(element, range = null) {
        const sel = window.getSelection();
        if (!element || !(range || sel?.rangeCount)) return null;
        const targetRange = range || sel.getRangeAt(0);
        const preCaretRange = targetRange.cloneRange();
        preCaretRange.selectNodeContents(element);
        preCaretRange.setEnd(targetRange.endContainer, targetRange.endOffset);
        return preCaretRange.toString().length;
    },

    renderSlashMenu(info) {
        const preview = info.previewText ? this.escHtml(info.previewText.slice(0, 72)) : '';
        const title = info.hasSelection ? '基于选中文本' : '基于当前位置';
        const subtitle = info.hasSelection
            ? '直接围绕当前选区做续写、改写和扩写'
            : '围绕当前光标位置继续创作或调度智能体';
        const primaryItems = info.hasSelection
            ? [
                { type: 'command', action: 'continue', icon: 'fa-pen-fancy', label: '续写选中后文', desc: '沿当前语气和信息继续推进' },
                { type: 'command', action: 'rewrite', icon: 'fa-rotate', label: '改写选中内容', desc: '保留信息，重写表达' },
                { type: 'command', action: 'expand_env', icon: 'fa-expand', label: '扩写选中内容', desc: '补细节、动作或环境' },
                { type: 'command', action: 'simplify_dialogue', icon: 'fa-comment-dots', label: '精简这段对话', desc: '收束冗余台词，保留力度' },
                { type: 'command', action: 'add_tension', icon: 'fa-bolt', label: '增强这一段张力', desc: '提高冲突、压迫和悬念' },
                { type: 'command', action: 'inner_monologue', icon: 'fa-brain', label: '补这一段内心戏', desc: '增加角色内在反应' }
            ]
            : [
                { type: 'command', action: 'continue', icon: 'fa-pen-fancy', label: '在当前位置续写', desc: '按当前上下文继续写下去' },
                { type: 'command', action: 'rewrite', icon: 'fa-rotate', label: '改写当前段落', desc: '重整语句和节奏' },
                { type: 'command', action: 'expand_env', icon: 'fa-mountain-sun', label: '扩写当前段落', desc: '补环境、动作或氛围' },
                { type: 'command', action: 'add_tension', icon: 'fa-bolt', label: '增加紧张感', desc: '提高冲突与推进力度' },
                { type: 'command', action: 'inner_monologue', icon: 'fa-brain', label: '加入内心独白', desc: '强化角色主观体验' }
            ];
        const secondaryItems = [
            { type: 'command', action: 'smart_continue', icon: 'fa-forward', label: '智能续写', desc: '走专用续写工作流' },
            { type: 'handler', action: 'triggerConflictDesign', icon: 'fa-fire', label: '冲突设计', desc: '生成当前情节的冲突方案' },
            { type: 'handler', action: 'triggerBrainstorm', icon: 'fa-lightbulb', label: '联想风暴', desc: '发散更多走向与灵感' },
            { type: 'handler', action: 'triggerPlanAndSolve', icon: 'fa-layer-group', label: 'Plan模式', desc: '先规划再执行生成' },
            { type: 'handler', action: 'triggerHallucinationCheck', icon: 'fa-shield-halved', label: '幻觉检测', desc: '校验当前内容是否自洽' }
        ];

        return `
            <div class="slash-menu-header">
                <div class="slash-menu-title">${title}</div>
                <div class="slash-menu-subtitle">${subtitle}</div>
                ${preview ? `<div class="slash-menu-preview">${preview}</div>` : ''}
            </div>
            <div class="slash-menu-group-label">${info.hasSelection ? '选区操作' : '写作操作'}</div>
            ${primaryItems.map((item) => this.renderSlashMenuItem(item)).join('')}
            <div class="slash-menu-divider"></div>
            <div class="slash-menu-group-label">高级辅助</div>
            ${secondaryItems.map((item) => this.renderSlashMenuItem(item)).join('')}
        `;
    },

    renderSlashMenuItem(item) {
        const onclick = item.type === 'handler'
            ? `App.closeSlashMenu(); App.${item.action}();`
            : `App.execSlash('${item.action}')`;
        return `
            <button class="slash-menu-item" type="button" onclick="${onclick}">
                <i class="fas ${item.icon}"></i>
                <span class="slash-menu-item-main">
                    <span class="slash-menu-item-label">${item.label}</span>
                    <span class="slash-menu-item-desc">${item.desc}</span>
                </span>
            </button>
        `;
    },

    positionSlashMenu(menu, info) {
        const editor = document.getElementById('editorArea');
        const editorRect = editor?.getBoundingClientRect();
        let anchorX = 0;
        let anchorY = 0;

        if (info?.hasSelection && info.rect) {
            anchorX = info.rect.left + info.rect.width / 2;
            anchorY = info.rect.bottom + 12;
        } else if (this.lastEditorPointer) {
            anchorX = this.lastEditorPointer.x;
            anchorY = this.lastEditorPointer.y + 12;
        } else if (editorRect) {
            anchorX = editorRect.left + editorRect.width / 2;
            anchorY = editorRect.top + editorRect.height / 2;
        } else {
            anchorX = window.innerWidth / 2;
            anchorY = window.innerHeight / 2;
        }

        const width = menu.offsetWidth || 320;
        const height = menu.offsetHeight || 280;
        const minLeft = editorRect ? editorRect.left + 16 : 16;
        const maxLeft = editorRect ? editorRect.right - width - 16 : window.innerWidth - width - 16;
        const minTop = editorRect ? editorRect.top + 16 : 16;
        const maxTop = editorRect ? editorRect.bottom - height - 16 : window.innerHeight - height - 16;

        let left = anchorX - width / 2;
        let top = anchorY;

        if (top > maxTop && info?.rect) {
            top = info.rect.top - height - 12;
        }
        if (top > maxTop && editorRect) {
            top = editorRect.top + (editorRect.height - height) / 2;
        }

        left = Math.min(Math.max(left, minLeft), Math.max(minLeft, maxLeft));
        top = Math.min(Math.max(top, minTop), Math.max(minTop, maxTop));

        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
    },

    execSlash(command) {
        const slashContext = this.slashMenuState || this.getSlashMenuContext();
        this.closeSlashMenu();
        const editor = document.getElementById('editorArea');
        const text = editor.innerText;

        // 智能续写走专用流程
        if (command === 'smart_continue') {
            this.switchRightTab('agent');
            this.selectAgent('continuation');
            this.runContinuation();
            return;
        }

        const contextText = (slashContext.selectedText || slashContext.contextText || text.slice(-2000)).trim();
        if (!contextText) {
            this.toast('当前位置缺少可处理的上下文', 'warning');
            return;
        }

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
        const form = await this.showFormDialog({
            title: '创建版本',
            description: '为当前章节保存一个新的版本标签。',
            submitLabel: '创建版本',
            fields: [
                {
                    key: 'label',
                    label: '版本标签',
                    value: String.fromCharCode(65 + Math.floor(Math.random() * 26)),
                    placeholder: '如 A / B / C',
                    required: true
                }
            ]
        });
        const label = form?.label;
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
        const confirmed = await this.showConfirmDialog({
            title: '删除设定',
            message: '确定删除此设定条目？删除后无法恢复。',
            confirmLabel: '删除',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
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

};

// 初始化
document.addEventListener('DOMContentLoaded', () => App.init());
