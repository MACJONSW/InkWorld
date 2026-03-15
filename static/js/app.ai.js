/**
 * 墨境 · AI 长篇小说写作平台 - AI 工作台模块
 */
Object.assign(window.App, {
    // ==================== Agent 面板 ====================
    selectAgent(agent) {
        const agentMap = this.getAgentMap();
        if (!document.querySelector(`.agent-btn[data-agent="${agent}"]`) || !agentMap[agent]) {
            agent = 'planner';
        }
        const groupMap = this.getAgentGroupMap();
        const targetGroup = groupMap[agent];
        if (targetGroup) {
            this.switchAgentGroup(targetGroup, null, false);
        }
        document.querySelectorAll('.agent-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`.agent-btn[data-agent="${agent}"]`)?.classList.add('active');

        document.querySelectorAll('.agent-panel').forEach(p => p.classList.remove('active'));
        const panels = this.getAgentPanelMap();
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
        const form = await this.showFormDialog({
            title: '添加人物记录',
            description: '手动补充人物历史、性格或伏笔线索。',
            submitLabel: '保存记录',
            fields: [
                {
                    key: 'character_name',
                    label: '角色名',
                    value: characterName || '',
                    required: true
                },
                {
                    key: 'entry_type',
                    label: '记录类型',
                    type: 'select',
                    value: 'note',
                    options: [
                        { value: 'event', label: '事件' },
                        { value: 'note', label: '备注' },
                        { value: 'personality', label: '性格' },
                        { value: 'foreshadow', label: '伏笔' }
                    ]
                },
                {
                    key: 'summary',
                    label: '摘要',
                    type: 'textarea',
                    rows: 3,
                    required: true
                },
                {
                    key: 'details',
                    label: '详细说明',
                    type: 'textarea',
                    rows: 4,
                    trim: false
                }
            ]
        });
        if (!form?.character_name || !form?.summary) return;

        const res = await this.api(`/api/character-history/${this.currentBookId}`, 'POST', {
            character_name: form.character_name,
            entry_type: form.entry_type || 'note',
            summary: form.summary,
            details: form.details || '',
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
        const form = await this.showFormDialog({
            title: `编辑${characterName || '人物'}记录`,
            submitLabel: '更新记录',
            fields: [
                {
                    key: 'entry_type',
                    label: '记录类型',
                    type: 'select',
                    value: entryType || 'note',
                    options: [
                        { value: 'event', label: '事件' },
                        { value: 'note', label: '备注' },
                        { value: 'personality', label: '性格' },
                        { value: 'foreshadow', label: '伏笔' }
                    ]
                },
                {
                    key: 'summary',
                    label: '摘要',
                    type: 'textarea',
                    rows: 3,
                    value: currentSummary || '',
                    required: true
                },
                {
                    key: 'details',
                    label: '详细说明',
                    type: 'textarea',
                    rows: 4,
                    value: currentDetails || '',
                    trim: false
                }
            ]
        });
        if (!form?.summary) return;

        const res = await this.api(`/api/character-history/${this.currentBookId}/${historyId}`, 'PUT', {
            summary: form.summary,
            details: form.details || '',
            entry_type: form.entry_type || 'note',
            is_manual: true
        });
        if (res && !res.error) {
            this.loadCharacterReminders();
            this.toast('人物记录已更新', 'success');
        }
    },

    async deleteCharacterHistory(historyId) {
        if (!this.currentBookId || !historyId) return;
        const confirmed = await this.showConfirmDialog({
            title: '删除人物记录',
            message: '确定删除这条人物记录？这会影响后续人物提醒与检索结果。',
            confirmLabel: '删除',
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
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

});
