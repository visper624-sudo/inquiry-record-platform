/* 三栏沉浸式问询工作台 */
(function () {
  'use strict';

  const state = {
    recordId: '', record: null, outline: [], flow: { flows: [], key_gaps: [], stats: {}, completeness: 0 },
    advice: [], adviceStats: {}, materials: [], progress: { total: 0, answered: 0, percent: 0 },
    judgement: null, permissions: { can_edit: true }, timer: 0, timerId: null,
    pendingSave: new Map(), user: null, sectionOpen: {}, newRowSection: null, lastSection: '',
    llm: null, llmMeta: null, streaming: null
  };
  let streamRenderTimer = null;

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    state.user = await Layout.initLayout({ active: '' });
    if (!state.user) return;
    state.recordId = query('record');
    if (!state.recordId) {
      document.getElementById('col-left').innerHTML = '<div class="banner red"><span class="ico">!</span><div>缺少笔录编号，请从台账进入</div></div>';
      return;
    }
    bindEvents();
    await load();
    startTimer();
    applyRoleUI();
    if (query('drawer') === '1') {
      setTimeout(() => openMaterialDrawer(), 260);
    }
    if (query('replay') === '1') {
      try {
        const data = await API.get(`/api/records/${state.recordId}/ai/last-stream`);
        const stream = data && data.stream;
        if (stream && (stream.thoughts || []).length) {
          state.streaming = {
            text: stream.text || '', thoughts: stream.thoughts || [],
            elapsed: stream.latency_ms || 0, model: stream.model || '',
            provider: stream.provider || '', frozen: true,
            capturedAt: stream.captured_at || ''
          };
          renderMid();
        } else {
          UI.toast('暂无流式回放数据，请先执行一次流式研判', 'info');
        }
      } catch (e) { /* 忽略回放失败 */ }
    }
    if (query('ai') === '1' && query('replay') !== '1') {
      setTimeout(() => runDeepAnalysis(), 320);
    }
  }

  /* ------------------------------------------------------------ 大模型深度研判（SSE 流式） */
  const THOUGHT_LABELS = {
    category: '类型', summary: '摘要', confidence: '置信度', title: '指引',
    content: '内容', question: '追问', why: '理由'
  };

  function renderStreamHtml() {
    const s = state.streaming;
    if (!s) return '';
    const seconds = ((s.elapsed || 0) / 1000).toFixed(1);
    const frozen = Boolean(s.frozen);
    const thoughts = (s.thoughts || []).slice(-14).map((t) => `
      <div class="thought thought-${UI.esc(t.kind)}">
        <span class="k">${UI.esc(THOUGHT_LABELS[t.kind] || t.kind)}</span>
        <span class="v">${UI.esc(t.text)}</span>
      </div>`).join('');
    const rawText = (s.text || '').slice(-220);
    return `
      <div class="stream-box ${frozen ? 'frozen' : ''}">
        <div class="stream-head">
          <span class="stream-dot ${frozen ? 'frozen' : ''}"></span>
          <span class="strong">${frozen ? '模型输出回放' : '模型推理中'}</span>
          <span class="tag purple no-dot">${UI.esc(frozen ? `上次真实调用 · ${s.model || 'deepseek-chat'}` : (s.model || s.provider || 'deepseek-chat'))}</span>
          <span class="stream-timer mono">${seconds}s</span>
          <span class="stream-count mono">${(s.text || '').length} 字符</span>
        </div>
        <div class="stream-thoughts">
          ${thoughts || '<div class="thought muted">已连接模型，等待首个结构化片段…</div>'}
        </div>
        <div class="stream-raw mono">${UI.esc(rawText)}<span class="caret"></span></div>
        ${s.fallback ? `<div class="banner warn" style="margin-top:8px"><span class="ico">△</span>
          <div>模型调用失败，将自动回退规则引擎：${UI.esc(s.fallback)}</div></div>` : ''}
      </div>`;
  }

  function renderStreamPanel() {
    const host = document.getElementById('llm-stream-host');
    if (host) host.innerHTML = renderStreamHtml();
  }

  function scheduleStreamRender() {
    if (streamRenderTimer) return;
    streamRenderTimer = setTimeout(() => {
      streamRenderTimer = null;
      renderStreamPanel();
    }, 70);
  }

  async function runDeepAnalysis(btn) {
    if (state.streaming) {
      UI.toast('正在研判中，请稍候…', 'info');
      return;
    }
    state.streaming = {
      text: '', thoughts: [], started: performance.now(), elapsed: 0,
      model: '', provider: '', fallback: ''
    };
    renderMid();
    const timer = setInterval(() => {
      if (!state.streaming) return;
      state.streaming.elapsed = performance.now() - state.streaming.started;
      const node = document.querySelector('#llm-stream-host .stream-timer');
      if (node) node.textContent = `${(state.streaming.elapsed / 1000).toFixed(1)}s`;
    }, 120);

    let doneData = null;
    const controller = new AbortController();
    const holdMode = query('hold') === '1';
    try {
      await API.stream(`/api/records/${state.recordId}/ai/analyze/stream`, {}, (event, data) => {
        if (!state.streaming) return;
        if (event === 'start') {
          state.streaming.model = data.model || '';
          state.streaming.provider = data.provider || '';
          renderStreamPanel();
        } else if (event === 'delta') {
          state.streaming.text += data.text || '';
          scheduleStreamRender();
        } else if (event === 'thought') {
          state.streaming.thoughts.push(data);
          scheduleStreamRender();
          // 截图/演示模式：收到足够思维链片段后主动断开，定格流式界面
          if (holdMode && state.streaming.thoughts.length >= 9) {
            controller.abort();
          }
        } else if (event === 'fallback') {
          state.streaming.fallback = data.reason || '未知原因';
          scheduleStreamRender();
        } else if (event === 'done') {
          doneData = data;
        } else if (event === 'error') {
          throw new Error(data.message || '流式研判失败');
        }
      }, { signal: controller.signal });
      if (!doneData) throw new Error('未收到研判结果');
      state.llm = doneData.llm || state.llm;
      state.llmMeta = doneData.meta || null;
      state.advice = doneData.advice || state.advice;
      state.flow = doneData.flow || state.flow;
      state.adviceStats = doneData.advice_stats || state.adviceStats;
      state.streaming = null;
      clearInterval(timer);
      renderMid();
      renderRight();
      updateGapMarks();
      const m = state.llmMeta || {};
      if (m.fallback) {
        UI.toast(`大模型调用失败，已回退规则引擎：${m.reason || ''}`, 'warn', 5200);
      } else {
        UI.toast(`流式研判完成：${m.model || 'LLM'} · ${((m.latency_ms || 0) / 1000).toFixed(1)}s · `
          + `${m.tokens || '-'} tokens · ${(doneData.analysis.followups || []).length} 条追问`, 'ok', 4200);
      }
    } catch (err) {
      clearInterval(timer);
      if (holdMode && err && err.name === 'AbortError') {
        // 演示定格：保留流式面板，等待截图后由人工刷新
        renderStreamPanel();
        return;
      }
      state.streaming = null;
      renderMid();
      UI.handleError(err, '深度研判失败');
    } finally {
      if (btn) UI.setButtonBusy(btn, false);
    }
  }

  /* 只读复核角色：隐藏写入类操作入口 */
  function applyRoleUI() {
    const canEdit = state.permissions.can_edit;
    ['btn-save', 'btn-complete'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('hidden', !canEdit);
    });
    if (!canEdit) {
      const meta = document.getElementById('wb-case-meta');
      if (meta && !meta.dataset.readonly) {
        meta.dataset.readonly = '1';
        meta.insertAdjacentHTML('beforeend', ' · <span style="color:#b7791f">只读复核（研判员）</span>');
      }
    }
  }

  function bindEvents() {
    document.getElementById('btn-material').addEventListener('click', openMaterialDrawer);
    document.getElementById('btn-save').addEventListener('click', saveProgress);
    document.getElementById('btn-complete').addEventListener('click', completeInquiry);
    document.getElementById('btn-export').addEventListener('click', exportDocx);
    document.getElementById('btn-preview').addEventListener('click', () => {
      location.href = `/document.html?record=${state.recordId}`;
    });

    const left = document.getElementById('col-left');
    left.addEventListener('input', onAnswerInput);
    left.addEventListener('click', onLeftClick);
    left.addEventListener('focusout', onQuestionBlur);

    document.getElementById('col-mid').addEventListener('click', onMidClick);
    document.getElementById('col-right').addEventListener('click', onRightClick);

    window.addEventListener('beforeunload', () => {
      state.pendingSave.forEach((entry) => { clearTimeout(entry.timer); });
    });
  }

  /* ------------------------------------------------------------ 数据加载 */
  async function load() {
    try {
      const data = await API.get(`/api/records/${state.recordId}/workbench`);
      applyData(data, true);
      renderAll();
      restoreScroll();
    } catch (err) {
      UI.handleError(err, '工作台加载失败');
      document.getElementById('col-left').innerHTML =
        `<div class="banner red"><span class="ico">!</span><div>${UI.esc(err.message)}</div></div>`;
    }
  }

  function applyData(data, initial) {
    if (data.outline) state.outline = data.outline;
    if (data.flow) state.flow = data.flow;
    if (data.advice) state.advice = data.advice;
    if (data.advice_stats) state.adviceStats = data.advice_stats;
    if (data.materials) state.materials = data.materials;
    if (data.progress) state.progress = data.progress;
    if (data.judgement) state.judgement = data.judgement;
    if (data.llm) state.llm = data.llm;
    if (data.permissions) state.permissions = data.permissions;
    if (data.record) state.record = data.record;
    if (initial) {
      state.timer = Number(data.record && data.record.elapsed_sec) || 0;
      if (data.record) state.record = data.record;
      (data.flow.flows || []).forEach((f) => { state.sectionOpen[f.key] = true; });
    }
  }

  function renderAll() {
    renderTop();
    renderLeft();
    renderMid();
    renderRight();
  }

  /* ------------------------------------------------------------ 顶部状态 */
  function renderTop() {
    const r = state.record || {};
    document.getElementById('wb-case-no').textContent = r.case_no || '—';
    document.getElementById('wb-case-meta').textContent =
      `${r.case_category || ''} · 报案人 ${r.reporter_name || '—'} · ${r.org_name || ''}`;
    const stage = document.getElementById('wb-stage');
    stage.className = `pill pill-${UI.stageTone(r.stage || 'inquiry')}`;
    stage.textContent = r.stage_label || '问询中';
    updateProgress();
    document.getElementById('mat-count').textContent = state.materials.length;
    updateTimerText();
  }

  function updateProgress() {
    const p = state.progress || { total: 0, answered: 0, percent: 0 };
    document.getElementById('wb-progress-bar').style.width = `${p.percent}%`;
    document.getElementById('wb-progress-text').textContent = `${p.answered} / ${p.total} 已答`;
    document.getElementById('wb-progress-bar').parentElement.parentElement
      .querySelector('.progress').classList.toggle('green', p.percent >= 80);
  }

  function sectionProgress(section) {
    const items = section.items || [];
    const answered = items.filter((i) => (i.answer || '').trim()).length;
    return { answered, total: items.length };
  }

  /* ------------------------------------------------------------ 左栏 */
  function gapKeySet() {
    const set = new Set();
    (state.flow.key_gaps || []).forEach((g) => set.add(`${g.flow}|${g.field}`));
    return set;
  }

  function renderLeft() {
    const host = document.getElementById('col-left');
    const gaps = gapKeySet();
    const canEdit = state.permissions.can_edit;
    host.innerHTML = state.outline.map((section) => {
      const prog = sectionProgress(section);
      const collapsed = state.sectionOpen[section.key] === false;
      const rows = (section.items || []).map((item) => {
        const isGap = item.flow && item.field && gaps.has(`${item.flow}|${item.field}`) && !(item.answer || '').trim();
        return `
        <div class="qa-row ${isGap ? 'gap-key' : ''}" data-qa-id="${UI.esc(item.id)}"
             data-field="${UI.esc(item.field || '')}" data-flow="${UI.esc(item.flow || '')}">
          <div class="qa-q">
            <span class="qmark">问</span>
            <span class="qtext" data-editable="question">${UI.esc(item.question)}</span>
            <span class="qa-actions">
              ${canEdit ? `<button class="btn-text btn-sm" data-act="edit-q" title="编辑问题">编辑</button>
              <button class="btn-text btn-sm danger" data-act="del" title="删除问答">删除</button>` : ''}
            </span>
          </div>
          <div class="qa-meta">
            <span class="tag ${item.source === 'ai_suggested' ? 'purple' : item.source === 'template' ? '' : 'green'}">${UI.esc(item.source_label)}</span>
            ${item.flow ? `<span class="tiny muted">${UI.esc(flowName(item.flow))} · ${UI.esc(fieldLabel(item.flow, item.field))}</span>` : ''}
            ${isGap ? '<span class="tag red">关键要素未采集</span>' : ''}
          </div>
          <div class="qa-answer">
            <textarea data-act="answer" ${canEdit ? '' : 'readonly'} rows="1"
              placeholder="${canEdit ? '录入被询问人答复…（自动保存）' : '只读复核角色'}">${UI.esc(item.answer || '')}</textarea>
          </div>
        </div>`;
      }).join('');
      return `
      <div class="section-card ${collapsed ? 'collapsed' : ''}" data-section="${UI.esc(section.key)}">
        <div class="section-head">
          <span class="t">${UI.esc(section.title)}</span>
          <span class="count">${prog.answered}/${prog.total} 已答</span>
          <span class="chev">▾</span>
        </div>
        <div class="section-body">
          ${rows || '<div class="empty-state" style="padding:18px">本章节暂无问题</div>'}
          ${canEdit ? `<button class="qa-add" data-act="add">＋ 新增问答</button>` : ''}
          <div class="new-row-host" data-section="${UI.esc(section.key)}"></div>
        </div>
      </div>`;
    }).join('');
    autoResizeAll();
  }

  function flowName(key) {
    return { fund: '资金流', communication: '通信流', network: '网络流', personnel: '人员流', delivery: '寄递流' }[key] || key;
  }

  function fieldLabel(flow, field) {
    const f = (state.flow.flows || []).find((x) => x.key === flow);
    const def = f && (f.fields || []).find((x) => x.key === field);
    return def ? def.label : field;
  }

  function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(220, Math.max(38, textarea.scrollHeight))}px`;
  }

  function autoResizeAll() {
    document.querySelectorAll('#col-left textarea').forEach(autoResize);
  }

  /* ------------------------------------------------------------ 答复自动保存 */
  function onAnswerInput(ev) {
    const textarea = ev.target.closest('textarea[data-act="answer"]');
    if (!textarea) return;
    autoResize(textarea);
    if (!state.permissions.can_edit) return;
    const row = textarea.closest('.qa-row');
    const qaId = row.dataset.qaId;
    textarea.classList.remove('saved');
    textarea.classList.add('saving');
    const value = textarea.value;
    if (state.pendingSave.has(qaId)) clearTimeout(state.pendingSave.get(qaId).timer);
    const timer = setTimeout(() => saveAnswer(qaId, value, textarea), 800);
    state.pendingSave.set(qaId, { timer, value });
  }

  async function saveAnswer(qaId, value, textarea) {
    state.pendingSave.delete(qaId);
    try {
      const data = await API.patch(`/api/records/${state.recordId}/qa/${qaId}`, { answer: value });
      applyData(data);
      updateProgress();
      renderMid();
      renderRight();
      updateGapMarks();
      updateSectionCounters(data.outline);
      textarea.classList.remove('saving');
      textarea.classList.add('saved');
      setTimeout(() => textarea.classList.remove('saved'), 900);
    } catch (err) {
      textarea.classList.remove('saving');
      UI.handleError(err, '答复保存失败');
    }
  }

  function updateGapMarks() {
    const gaps = gapKeySet();
    document.querySelectorAll('#col-left .qa-row').forEach((row) => {
      const field = row.dataset.field;
      const flow = row.dataset.flow;
      const textarea = row.querySelector('textarea');
      const isGap = field && flow && gaps.has(`${flow}|${field}`) && !textarea.value.trim();
      row.classList.toggle('gap-key', Boolean(isGap));
    });
  }

  function updateSectionCounters(outline) {
    if (!outline) return;
    outline.forEach((section) => {
      const card = document.querySelector(`.section-card[data-section="${section.key}"]`);
      if (!card) return;
      const answered = (section.items || []).filter((i) => (i.answer || '').trim()).length;
      card.querySelector('.count').textContent = `${answered}/${section.items.length} 已答`;
    });
  }

  /* ------------------------------------------------------------ 左栏交互 */
  async function onLeftClick(ev) {
    const head = ev.target.closest('.section-head');
    if (head) {
      const card = head.closest('.section-card');
      const key = card.dataset.section;
      card.classList.toggle('collapsed');
      state.sectionOpen[key] = !card.classList.contains('collapsed');
      return;
    }
    const row = ev.target.closest('.qa-row');
    const actBtn = ev.target.closest('[data-act]');
    if (actBtn) {
      const act = actBtn.dataset.act;
      if (act === 'del' && row) return deleteQa(row);
      if (act === 'edit-q' && row) return startEditQuestion(row);
      if (act === 'add') {
        const card = ev.target.closest('.section-card');
        return showNewRow(card.dataset.section);
      }
    }
    if (row && ev.target.matches('[data-editable="question"]')) {
      const qtext = row.querySelector('[data-editable="question"]');
      if (qtext.getAttribute('contenteditable') === 'true') qtext.focus();
    }
  }

  function startEditQuestion(row) {
    const qtext = row.querySelector('[data-editable="question"]');
    if (qtext.getAttribute('contenteditable') === 'true') return;
    qtext.setAttribute('contenteditable', 'true');
    qtext.style.outline = '1px dashed var(--navy-500)';
    qtext.style.background = '#f5f9ff';
    qtext.dataset.original = qtext.textContent;
    qtext.focus();
    const range = document.createRange();
    range.selectNodeContents(qtext);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    UI.toast('问题已进入编辑态，失去焦点即自动保存', 'info', 2200);
  }

  async function onQuestionBlur(ev) {
    const qtext = ev.target.closest('[data-editable="question"]');
    if (!qtext || qtext.getAttribute('contenteditable') !== 'true') return;
    qtext.setAttribute('contenteditable', 'false');
    qtext.style.outline = '';
    qtext.style.background = '';
    const value = qtext.textContent.trim();
    const row = qtext.closest('.qa-row');
    if (!value) {
      qtext.textContent = qtext.dataset.original || '';
      return UI.toast('问题内容不能为空', 'warn');
    }
    if (value === qtext.dataset.original) return;
    try {
      const data = await API.patch(`/api/records/${state.recordId}/qa/${row.dataset.qaId}`, { question: value });
      applyData(data);
      UI.toast('问题已更新', 'ok', 1600);
    } catch (err) {
      qtext.textContent = qtext.dataset.original || '';
      UI.handleError(err, '问题更新失败');
    }
  }

  async function deleteQa(row) {
    const ok = await UI.confirm('确认删除该条问答？删除后不可恢复，且不再进入文书正文。',
      { danger: true, confirmText: '删除' });
    if (!ok) return;
    try {
      const data = await API.del(`/api/records/${state.recordId}/qa/${row.dataset.qaId}`);
      applyData(data);
      renderAll();
      UI.toast('已删除该条问答', 'ok');
    } catch (err) {
      UI.handleError(err, '删除失败');
    }
  }

  function showNewRow(sectionKey) {
    const host = document.querySelector(`.new-row-host[data-section="${sectionKey}"]`);
    if (!host) return;
    if (host.querySelector('.new-row')) {
      host.querySelector('textarea').focus();
      return;
    }
    host.innerHTML = `
      <div class="qa-row new-row" data-new="1">
        <div class="qa-q">
          <span class="qmark">问</span>
          <input class="input input-sm" data-new-question placeholder="输入新增问题（可标注所属章节）" style="flex:1">
        </div>
        <div class="qa-answer">
          <textarea data-new-answer rows="2" placeholder="录入答复（可留空，后续补充）"></textarea>
        </div>
        <div class="flex gap-8 mt-8" style="margin-left:17px">
          <button class="btn btn-sm btn-primary" data-act="save-new">保存问答</button>
          <button class="btn btn-sm" data-act="cancel-new">取消</button>
        </div>
      </div>`;
    host.querySelector('[data-new-question]').focus();
    host.querySelector('[data-act="save-new"]').addEventListener('click', () => saveNewRow(host, sectionKey));
    host.querySelector('[data-act="cancel-new"]').addEventListener('click', () => { host.innerHTML = ''; });
    host.querySelector('[data-new-answer]').addEventListener('input', (e) => autoResize(e.target));
  }

  async function saveNewRow(host, sectionKey) {
    const question = host.querySelector('[data-new-question]').value.trim();
    const answer = host.querySelector('[data-new-answer]').value;
    if (!question) return UI.toast('请填写问题内容', 'warn');
    try {
      const data = await API.post(`/api/records/${state.recordId}/qa`, {
        section: sectionKey, question, answer, source: 'manual'
      });
      applyData(data);
      renderAll();
      UI.toast('已新增问答并归入对应章节', 'ok');
    } catch (err) {
      UI.handleError(err, '新增问答失败');
    }
  }

  /* ------------------------------------------------------------ 中栏 AI */
  function engineTag(engine) {
    return engine === 'llm'
      ? '<span class="tag purple">大模型</span>'
      : '<span class="tag">规则</span>';
  }

  function llmMetaLine(llm) {
    if (state.llmMeta) {
      const m = state.llmMeta;
      if (m.fallback) {
        return `已回退规则引擎：${UI.esc(m.reason || '调用失败')}（${m.latency_ms || 0} ms）`;
      }
      const tokens = m.tokens ? ` · ${m.tokens} tokens` : '';
      return `引擎 ${UI.esc(m.provider || 'llm')}/${UI.esc(m.model || '')} · 耗时 ${m.latency_ms || 0} ms${tokens}`;
    }
    if (llm.active_engine === 'llm') {
      return `密钥已配置 · 当前引擎 ${UI.esc(llm.engine_label)}`;
    }
    return `未配置可用密钥：${UI.esc(llm.note || '当前由规则引擎生成')}`;
  }

  function renderMid() {
    const host = document.getElementById('col-mid');
    const j = state.judgement || { category: state.record ? state.record.case_category : '', confidence: 0, basis: [] };
    const llm = state.llm || { active_engine: 'rule_engine', engine_label: '规则引擎（离线可用）', note: '' };
    const insight = state.advice.find((a) => a.kind === 'llm_judgement');
    const followups = state.advice.filter((a) => a.kind === 'followup');
    const guides = state.advice.filter((a) => a.kind === 'investigation');
    const adopted = state.adviceStats.adopted || 0;
    const total = state.adviceStats.total || 0;
    const canEdit = state.permissions.can_edit;
    const pendingFollowups = followups.filter((a) => a.status === 'pending').length;
    const llmFollowups = followups.filter((a) => a.engine === 'llm').length;

    host.innerHTML = `
      <div class="ai-card">
        <div class="ai-head">
          <span class="badge-ai">规则</span><b>涉诈类型判定</b>
          <span class="tag ${j.confidence >= 70 ? 'green' : 'gold'}" style="margin-left:auto">置信度 ${UI.esc(j.confidence)}%</span>
        </div>
        <div class="ai-body">
          <div style="font-size:14.5px;font-weight:700;color:var(--navy-700);margin-bottom:8px">${UI.esc(j.category || '—')}</div>
          <ul class="basis-list">${(j.basis || []).map((b) => `<li>${UI.esc(b)}</li>`).join('') || '<li>暂无依据</li>'}</ul>
        </div>
      </div>

      <div class="ai-card llm-card" style="margin-top:10px">
        <div class="ai-head">
          <span class="badge-ai">大模型</span><b>深度研判</b>
          <span class="tag ${llm.active_engine === 'llm' ? 'purple' : 'gold'}" style="margin-left:auto">${UI.esc(llm.engine_label)}</span>
        </div>
        <div class="ai-body">
          <div id="llm-stream-host">${state.streaming ? renderStreamHtml() : ''}</div>
          ${state.streaming ? '' : (insight ? `
            <div style="font-size:13.5px;font-weight:700;color:var(--purple-600);margin-bottom:6px">
              ${UI.esc((insight.title || '').replace('大模型研判：', ''))}</div>
            <div style="margin-bottom:6px;color:var(--text-800)">${UI.esc(insight.content)}</div>
            <ul class="basis-list">${(insight.evidence || []).map((b) => `<li>${UI.esc(b)}</li>`).join('')}</ul>`
            : `<div class="muted" style="line-height:1.8">
                 尚未执行深度研判。点击「DeepSeek 流式研判」后，中栏将<b>实时展示模型推理片段与增量结构化结果</b>：
                 类型判定、3 条侦查指引、针对真实缺口的追问；服务端校验通过后才落库为「待采纳」卡片。
               </div>`)}
          <div class="flex justify-between items-center" style="margin-top:10px; gap:8px">
            <span class="tiny ${state.streaming ? '' : (state.llmMeta && state.llmMeta.fallback ? '' : 'muted')}">
              ${state.streaming
                ? (state.streaming.frozen ? '上次真实调用的流式输出回放（SSE 记录）' : 'SSE 流式输出中 · 逐块渲染模型推理过程')
                : llmMetaLine(llm)}</span>
            ${canEdit ? (state.streaming
              ? `<button class="btn btn-sm btn-gold nowrap" disabled>${state.streaming.frozen ? '回放中' : '研判中…'}</button>`
              : `<button class="btn btn-sm btn-gold nowrap" data-act="deep-analyze">
                  ${insight ? '重新流式研判' : 'DeepSeek 流式研判'}</button>`) : ''}
          </div>
        </div>
      </div>

      <div class="ai-card" style="margin-top:10px">
        <div class="ai-head">
          <span class="badge-ai">AI 追问</span><b>按缺口严重度排序</b>
          <span class="tag red" style="margin-left:auto">${pendingFollowups} 项待采纳${llmFollowups ? ` · 大模型 ${llmFollowups}` : ''}</span>
        </div>
        ${followups.length ? followups.map((a) => renderFollowCard(a, canEdit)).join('')
          : '<div class="ai-body muted">五流要素已齐全，暂无追问建议</div>'}
        <div class="ai-body" style="border-top:1px solid var(--line-soft); padding-top:10px">
          <div class="flex justify-between items-center">
            <span class="tiny muted">AI 建议采纳率 ${UI.esc(state.adviceStats.adoption_rate || 0)}%（${adopted}/${total}）</span>
            <button class="btn-text btn-sm" data-act="refresh">↻ 刷新规则建议</button>
          </div>
        </div>
      </div>

      <div class="ai-card" style="margin-top:10px">
        <div class="ai-head">
          <span class="badge-ai">AI 指引</span><b>侦查指引建议</b>
        </div>
        <div class="ai-body">
          ${guides.length ? guides.map((g, i) => `
            <div style="padding:6px 0; ${i ? 'border-top:1px dashed var(--line-soft)' : ''}">
              <div style="font-size:12.5px;font-weight:600;color:var(--text-800);margin-bottom:2px">
                ${UI.esc(g.title)} ${engineTag(g.engine)}</div>
              <div style="font-size:12.3px">${UI.esc(g.content)}</div>
            </div>`).join('') : '<div class="muted">等待研判结果</div>'}
        </div>
      </div>

      <div class="ai-card" style="margin-top:10px">
        <div class="ai-head"><span class="badge-ai">合规</span><b>文书与采纳规则</b></div>
        <div class="ai-body small muted">
          AI 建议一律为「待采纳」状态，必须由办案民警点击「加入问询」后才写入正式问答；材料文件不参与正文组装。
          大模型输出经服务端 JSON 校验（字段白名单、问句格式、缺口一致性），不合法内容直接丢弃并回退规则引擎。
        </div>
      </div>`;
  }

  function renderFollowCard(advice, canEdit) {
    const adopted = advice.status === 'adopted';
    const priority = advice.payload && advice.payload.key_item ? '关键' : '补充';
    return `
    <div class="follow-card ${adopted ? 'adopted' : ''}" data-advice="${UI.esc(advice.id)}">
      <div class="follow-title">
        <span class="pri">${priority}</span>
        <span>${UI.esc(advice.title)}</span>
        ${engineTag(advice.engine)}
        ${adopted ? '<span class="tag green" style="margin-left:auto">已采纳</span>' : ''}
      </div>
      <div class="follow-question">${UI.esc(advice.content)}</div>
      <div class="follow-actions">
        ${canEdit && !adopted ? `<button class="btn btn-sm btn-primary" data-act="adopt">＋ 加入问询</button>` : ''}
        <span class="follow-evidence">${UI.esc((advice.evidence || []).filter(Boolean).join('；'))}</span>
      </div>
    </div>`;
  }

  async function onMidClick(ev) {
    const deepBtn = ev.target.closest('[data-act="deep-analyze"]');
    if (deepBtn) {
      await runDeepAnalysis(deepBtn);
      return;
    }
    const refresh = ev.target.closest('[data-act="refresh"]');
    if (refresh) {
      UI.setButtonBusy(refresh, true, '研判中…');
      try {
        const data = await API.post(`/api/records/${state.recordId}/advice/refresh`, {});
        state.advice = data.advice;
        state.flow = data.flow;
        renderMid();
        renderRight();
        updateGapMarks();
        UI.toast('已根据当前问答重新生成研判建议', 'ok');
      } catch (err) {
        UI.handleError(err, '研判刷新失败');
      } finally {
        UI.setButtonBusy(refresh, false);
      }
      return;
    }
    const adoptBtn = ev.target.closest('[data-act="adopt"]');
    if (adoptBtn) {
      const card = adoptBtn.closest('[data-advice]');
      const adviceId = card.dataset.advice;
      UI.setButtonBusy(adoptBtn, true, '采纳中…');
      try {
        const data = await API.post(`/api/records/${state.recordId}/advice/${adviceId}/adopt`, {});
        applyData(data);
        renderAll();
        const section = data.qa ? data.qa.id : '';
        const row = section ? document.querySelector(`.qa-row[data-qa-id="${data.qa.id}"]`) : null;
        if (row) {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
          row.style.background = '#fff8e8';
          setTimeout(() => { row.style.background = ''; }, 1600);
        }
        UI.toast('已加入正式问询（来源标记为 AI 建议），请补充答复', 'ok');
      } catch (err) {
        UI.handleError(err, '采纳失败');
      } finally {
        UI.setButtonBusy(adoptBtn, false);
      }
    }
  }

  /* ------------------------------------------------------------ 右栏五流 */
  function renderRight() {
    const host = document.getElementById('col-right');
    const flow = state.flow;
    const stats = flow.stats || {};
    host.innerHTML = `
      <div class="radar-box">
        <div class="rt"><span>五流完整度</span><b>${UI.esc(flow.completeness || 0)}%</b></div>
        <div style="display:flex;justify-content:center">${UI.radarSvg(flow.flows || [])}</div>
        <div class="radar-legend">
          <span><i style="background:#0f8a4d"></i>已收集 ${stats.collected || 0}</span>
          <span><i style="background:#b7791f"></i>需补充 ${stats.needs_supplement || 0}</span>
          <span><i style="background:#c0392b"></i>重点缺口 ${stats.key_gap || 0}</span>
          <span><i style="background:#9aa3b2"></i>不涉及 ${stats.not_involved || 0}</span>
        </div>
      </div>
      ${(flow.flows || []).map((f) => renderFlowCard(f)).join('')}`;
  }

  function renderFlowCard(flow) {
    const required = (flow.fields || []).filter((x) => x.required);
    const optionalCollected = (flow.fields || []).filter((x) => !x.required && x.status === 'collected');
    const rows = required.map((field) => {
      const missing = field.status !== 'collected';
      const cls = missing ? (field.key_item ? 'key-missing' : 'missing') : '';
      const value = missing ? '未采集' : field.value;
      const warn = field.warning ? `<span class="warn" title="${UI.esc(field.warning)}">⚠</span>` : '';
      const title = field.evidence_span ? `证据摘要：${field.evidence_span}` : (field.hint || '');
      return `<div class="flow-field ${cls}" title="${UI.esc(title)}">
        <span class="fi">${missing ? '○' : '✓'}</span>
        <span class="fl">${UI.esc(field.label)}</span>
        <span class="fv">${UI.esc(value)}${warn}</span>
      </div>`;
    }).join('');
    const extras = optionalCollected.map((field) => `
      <div class="flow-field" title="${UI.esc(field.evidence_span || '')}">
        <span class="fi">＋</span><span class="fl">${UI.esc(field.label)}</span>
        <span class="fv">${UI.esc(field.value)}</span>
      </div>`).join('');
    const canMark = flow.involved && flow.can_mark_not_involved;
    return `
    <div class="flow-card" style="margin-top:10px" data-flow="${UI.esc(flow.key)}">
      <div class="flow-head">
        <span class="name">${UI.esc(flow.name)}</span>
        <span class="pill pill-${UI.flowTone(flow.status)}">${UI.esc(flow.status_label)}</span>
        <span class="cov">${flow.collected_count}/${flow.required_total}</span>
      </div>
      <div class="flow-bar">
        <div class="progress ${flow.status === 'key_gap' ? 'red' : flow.status === 'needs_supplement' ? 'amber' : 'green'}">
          <i style="width:${flow.status === 'not_involved' ? 100 : flow.coverage_pct}%"></i>
        </div>
      </div>
      <div class="flow-body">
        <div class="flow-fields">${rows || '<div class="muted tiny">该流无必填要素</div>'}${extras}</div>
        ${flow.gaps && flow.gaps.length ? `<div class="flow-extra">
          <b style="color:var(--red-600)">缺口 ${flow.gaps.length} 项：</b>${flow.gaps.map((g) => UI.esc(g.label)).join('、')}
        </div>` : ''}
        ${flow.status === 'not_involved' ? '<div class="flow-extra">已人工标记为「不涉及」，不参与完整度统计</div>' : ''}
        <div class="flow-extra flex justify-between items-center">
          <span>${flow.status === 'collected' ? '必备要素已收集完整' : '录入答复后自动刷新'}</span>
          ${canMark ? '<button class="btn-text btn-sm" data-act="not-involved">标记不涉及</button>'
            : flow.status === 'not_involved' ? '<button class="btn-text btn-sm" data-act="involved">恢复涉及</button>' : ''}
        </div>
      </div>
    </div>`;
  }

  async function onRightClick(ev) {
    const notInvolved = ev.target.closest('[data-act="not-involved"]');
    const involved = ev.target.closest('[data-act="involved"]');
    const card = ev.target.closest('[data-flow]');
    if (!card) return;
    const flowKey = card.dataset.flow;
    if (!notInvolved && !involved) return;
    const confirmText = notInvolved
      ? `确认「${flowName(flowKey)}」在本案中不涉及？标记后该流不再产生缺口与追问建议。`
      : `确认「${flowName(flowKey)}」重新纳入调查？`;
    const ok = await UI.confirm(confirmText, { confirmText: '确认' });
    if (!ok) return;
    try {
      const data = await API.post(`/api/records/${state.recordId}/flow/${flowKey}/exclude`,
        { involved: Boolean(involved) });
      state.flow = data.flow;
      renderRight();
      renderMid();
      updateGapMarks();
      UI.toast(notInvolved ? '已标记为不涉及' : '已恢复纳入调查', 'ok');
    } catch (err) {
      UI.handleError(err, '五流状态更新失败');
    }
  }

  /* ------------------------------------------------------------ 计时器 */
  function startTimer() {
    if (state.timerId) clearInterval(state.timerId);
    state.timerId = setInterval(() => {
      state.timer += 1;
      updateTimerText();
    }, 1000);
  }

  function updateTimerText() {
    document.getElementById('wb-timer').textContent = UI.fmtDuration(state.timer);
  }

  /* ------------------------------------------------------------ 保存 / 完成 / 导出 */
  async function saveProgress() {
    const btn = document.getElementById('btn-save');
    UI.setButtonBusy(btn, true, '保存中…');
    try {
      const anchor = currentAnchor();
      const data = await API.post(`/api/records/${state.recordId}/save`,
        { elapsed_sec: state.timer, scroll_anchor: anchor, last_section: anchor.section });
      UI.toast(`已暂存工作现场（含计时 ${UI.durationText(state.timer)}）`, 'ok');
      if (state.record) state.record.snapshot = { scroll_anchor: anchor };
      return data;
    } catch (err) {
      UI.handleError(err, '暂存失败');
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }

  function currentAnchor() {
    const scroll = document.getElementById('col-left');
    const cards = Array.from(document.querySelectorAll('.section-card'));
    let section = 'process';
    for (let i = cards.length - 1; i >= 0; i -= 1) {
      if (cards[i].offsetTop <= scroll.scrollTop + 90) { section = cards[i].dataset.section; break; }
    }
    return { section, index: Math.round(scroll.scrollTop) };
  }

  async function completeInquiry() {
    const pending = state.progress.total - state.progress.answered;
    const gaps = (state.flow.key_gaps || []).length;
    const message = gaps
      ? `仍有 ${gaps} 项五流关键要素缺失、${pending} 条问答未作答。完成后笔录将封存，可继续导出文书，确认完成问询？`
      : `确认完成问询？完成后笔录封存，未作答条目不会进入文书正文。`;
    const ok = await UI.confirm(message, { confirmText: '完成并封存' });
    if (!ok) return;
    const btn = document.getElementById('btn-complete');
    UI.setButtonBusy(btn, true, '封存中…');
    try {
      const data = await API.post(`/api/records/${state.recordId}/complete`, { elapsed_sec: state.timer });
      UI.toast('问询已封存，正在生成红头文书预览', 'ok');
      setTimeout(() => { location.href = data.redirect || `/document.html?record=${state.recordId}`; }, 600);
    } catch (err) {
      UI.handleError(err, '完成问询失败');
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }

  async function exportDocx() {
    const btn = document.getElementById('btn-export');
    const caseNo = (state.record && state.record.case_no) || '笔录';
    UI.setButtonBusy(btn, true, '生成中…');
    try {
      await API.download(`/api/records/${state.recordId}/document.docx`, `${caseNo}_询问笔录.docx`);
      UI.toast('已生成 DOCX 红头文书并下载', 'ok');
    } catch (err) {
      UI.handleError(err, '文书导出失败');
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }

  /* ------------------------------------------------------------ 材料抽屉 */
  async function openMaterialDrawer() {
    const drawer = UI.drawer({
      title: '案件辅助材料（与正文物理隔离）',
      width: 470,
      body: `
        <div class="banner warn" style="margin-bottom:12px">
          <span class="ico">!</span>
          <div><b>材料不进入正式笔录。</b>通话记录、转账流水、聊天截图仅作为研判算法输入，
            在数据库层与问答正文无任何关联字段，导出文书时也不会被拼接。</div>
        </div>
        <div class="dropzone" id="dropzone" style="${state.permissions.can_edit ? '' : 'display:none'}">
          <div style="font-size:22px;opacity:.45;margin-bottom:4px">⇪</div>
          拖拽文件到此处，或点击选择文件<br>
          <span class="tiny">支持 PNG / JPG / PDF / DOCX / XLSX / TXT，单文件 ≤ 20MB</span>
        </div>
        <input type="file" id="file-input" class="hidden" multiple>
        <div class="flex justify-between items-center" style="margin:14px 0 8px">
          <span class="small strong">已上传材料 <b id="drawer-count">${state.materials.length}</b> 份</span>
          <span class="tiny muted">SHA-256 去重 · 按机构鉴权下载</span>
        </div>
        <div id="drawer-list"></div>`,
      footer: [{ text: '关闭', onClick: (api) => api.close() }]
    });
    renderMaterialList();

    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('file-input');
    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('dragover', (ev) => {
      ev.preventDefault();
      dropzone.classList.add('drag');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag'));
    dropzone.addEventListener('drop', (ev) => {
      ev.preventDefault();
      dropzone.classList.remove('drag');
      uploadFiles(Array.from(ev.dataTransfer.files), drawer);
    });
    fileInput.addEventListener('change', (ev) => uploadFiles(Array.from(ev.target.files), drawer));
    document.getElementById('drawer-list').addEventListener('click', (ev) => {
      const del = ev.target.closest('[data-del-material]');
      if (!del) return;
      deleteMaterial(del.dataset.delMaterial, drawer);
    });
  }

  function renderMaterialList() {
    const host = document.getElementById('drawer-list');
    if (!host) return;
    document.getElementById('drawer-count').textContent = state.materials.length;
    if (!state.materials.length) {
      host.innerHTML = '<div class="empty-state" style="padding:22px">尚未上传材料</div>';
      return;
    }
    host.innerHTML = state.materials.map((m) => `
      <div class="material-item">
        <div class="mi-ico">${/\.(png|jpg|jpeg|webp|gif)$/i.test(m.file_name) ? '🖼' : '📄'}</div>
        <div style="flex:1;min-width:0">
          <div class="mi-name" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${UI.esc(m.file_name)}</div>
          <div class="mi-meta">${UI.esc(m.size_text)} · ${UI.esc(m.uploader_name || '')} · ${UI.fmtDT(m.created_at)}</div>
          <div class="mi-meta" title="${UI.esc(m.note || '')}">${UI.esc(m.note || '')}</div>
        </div>
        <a class="btn btn-sm" href="/api/materials/${UI.esc(m.id)}/download" target="_blank" rel="noopener">下载</a>
        ${state.permissions.can_edit ? `<button class="btn-text btn-sm danger" data-del-material="${UI.esc(m.id)}">删除</button>` : ''}
      </div>`).join('');
  }

  async function uploadFiles(files, drawer) {
    if (!files.length) return;
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) {
        UI.toast(`${file.name} 超过 20MB 限制`, 'warn');
        continue;
      }
      try {
        const data = await API.upload(`/api/records/${state.recordId}/materials`, file, { note: '办案民警上传的研判辅助材料' });
        state.materials = data.items;
        document.getElementById('mat-count').textContent = state.materials.length;
        renderMaterialList();
        UI.toast(data.duplicated ? `${file.name} 已存在，自动去重` : `${file.name} 上传成功（仅进入研判）`, 'ok');
      } catch (err) {
        UI.handleError(err, `${file.name} 上传失败`);
      }
    }
  }

  async function deleteMaterial(materialId, drawer) {
    const ok = await UI.confirm('确认删除该材料？不影响已录入的问答正文。', { danger: true });
    if (!ok) return;
    try {
      const data = await API.del(`/api/records/${state.recordId}/materials/${materialId}`);
      state.materials = data.items;
      document.getElementById('mat-count').textContent = state.materials.length;
      renderMaterialList();
      UI.toast('材料已删除', 'ok');
    } catch (err) {
      UI.handleError(err, '删除材料失败');
    }
  }

  /* ------------------------------------------------------------ 断点恢复 */
  function restoreScroll() {
    const anchor = state.record && state.record.snapshot && state.record.snapshot.scroll_anchor;
    if (!anchor) return;
    const scroll = document.getElementById('col-left');
    if (anchor.index) scroll.scrollTop = Number(anchor.index) || 0;
    if (anchor.section) state.lastSection = anchor.section;
    setTimeout(() => {
      if (!anchor.index && anchor.section) {
        const card = document.querySelector(`.section-card[data-section="${anchor.section}"]`);
        if (card) card.scrollIntoView({ block: 'start' });
      }
    }, 120);
  }
})();
