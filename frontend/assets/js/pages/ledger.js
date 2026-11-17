/* 笔录管理与统计大盘 */
(function () {
  'use strict';

  const state = {
    page: 1, pageSize: 8, keyword: '', status: '', category: '',
    total: 0, pages: 1, items: [], user: null, meta: null
  };

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    state.user = await Layout.initLayout({ active: 'ledger' });
    if (!state.user) return;
    try { state.meta = await API.get('/api/meta'); } catch (e) { /* 忽略 */ }
    fillCategories();
    bindEvents();
    await Promise.all([loadStats(), loadList()]);
    const recordId = query('record');
    if (query('analysis') && recordId) {
      openAnalysis(recordId);
      return;
    }
    if (query('modal') === 'intake' && recordId) {
      await openIntake(recordId);
      if (query('sync') === '1') {
        setTimeout(async () => {
          const toggle = document.getElementById('btn-sync-toggle');
          if (!toggle) return;
          toggle.click();
          document.getElementById('sync-keyword').value = '李芳';
          await searchExternal();
        }, 260);
      }
    }
  }

  function fillCategories() {
    const select = document.getElementById('category');
    const categories = (state.meta && state.meta.categories) || [];
    categories.forEach((name) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
  }

  function bindEvents() {
    document.getElementById('btn-search').addEventListener('click', () => {
      state.keyword = document.getElementById('kw').value.trim();
      state.status = document.getElementById('status').value;
      state.category = document.getElementById('category').value;
      state.page = 1;
      loadList();
    });
    document.getElementById('kw').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') document.getElementById('btn-search').click();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.getElementById('kw').value = '';
      document.getElementById('status').value = '';
      document.getElementById('category').value = '';
      state.keyword = ''; state.status = ''; state.category = ''; state.page = 1;
      loadList();
    });
    document.getElementById('btn-new').addEventListener('click', () => openIntake());
    document.getElementById('btn-import').addEventListener('click', () => { location.href = '/import.html'; });
    document.getElementById('rows').addEventListener('click', onRowAction);
  }

  async function loadStats() {
    try {
      const data = await API.get('/api/stats/overview');
      renderStats(data);
    } catch (err) {
      UI.handleError(err, '统计加载失败');
    }
  }

  function renderStats(data) {
    const c = data.cards;
    const completion = c.total ? Math.round((c.completed / c.total) * 100) : 0;
    const cards = [
      { label: '笔录总数', value: c.total, foot: `今日新增 ${c.today_new} 件` },
      { label: '问询中', value: c.inquiry, foot: `待开始 ${c.pending} 件`, tone: 'blue' },
      { label: '已问询', value: c.completed, foot: `完成率 ${completion}%`, tone: 'ok' },
      { label: '重点缺口案件', value: c.key_gap_cases, foot: '五流关键要素缺失', tone: 'warn' },
      { label: '平均问询时长', value: c.avg_duration_min, unit: '分钟', foot: '按已完成案件统计' }
    ];
    document.getElementById('stat-grid').innerHTML = cards.map((card) => `
      <div class="stat-card ${card.tone || ''}">
        <div class="stat-label">${UI.esc(card.label)}</div>
        <div class="stat-value">${UI.esc(card.value)}${card.unit ? `<small>${UI.esc(card.unit)}</small>` : ''}</div>
        <div class="stat-foot">${UI.esc(card.foot)}</div>
      </div>`).join('');
  }

  async function loadList() {
    const tbody = document.getElementById('rows');
    tbody.innerHTML = '<tr><td colspan="8"><div class="skeleton" style="height:180px"></div></td></tr>';
    try {
      const data = await API.get('/api/records', {
        page: state.page, page_size: state.pageSize,
        keyword: state.keyword, status: state.status, category: state.category
      });
      state.items = data.items;
      state.total = data.total;
      state.pages = data.pages;
      document.getElementById('list-summary').textContent =
        `共 ${data.total} 条记录 · 第 ${data.page}/${data.pages} 页`;
      renderRows();
      UI.renderPager(document.getElementById('pager'), data, (page) => {
        state.page = page;
        loadList();
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state">加载失败：${UI.esc(err.message)}</div></td></tr>`;
    }
  }

  function actionLabel(stage) {
    if (stage === 'completed') return '查看文书';
    if (stage === 'intake' || stage === 'templates') return '继续建档';
    return '继续询问';
  }

  function renderRows() {
    const tbody = document.getElementById('rows');
    if (!state.items.length) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="empty-state"><div class="ico">⌕</div>没有符合条件的笔录记录</div></td></tr>';
      return;
    }
    tbody.innerHTML = state.items.map((row) => {
      const showGaps = row.stage === 'inquiry' || row.stage === 'completed';
      const gapTags = showGaps
        ? (row.gap_preview || []).map((g) => `<span class="tag red" style="margin-right:4px">${UI.esc(g)}</span>`).join('')
        : '';
      return `
      <tr data-id="${UI.esc(row.id)}" data-stage="${UI.esc(row.stage)}">
        <td><span class="cell-case">${UI.esc(row.case_no)}</span></td>
        <td><span class="tag">${UI.esc(row.case_category)}</span></td>
        <td class="cell-brief">
          <div class="brief-hover">
            <div class="brief-text">${UI.esc(row.brief)}</div>
            <div class="pop">
              <div><b>${UI.esc(row.case_no)}</b> · ${UI.esc(row.case_category)}</div>
              <div class="pop-divider"></div>
              <div style="line-height:1.8">${UI.esc(row.brief)}</div>
              <div class="pop-divider"></div>
              <div class="pop-row"><span class="k">报案人</span><span>${UI.esc(row.reporter_name || '—')} · ${UI.esc(row.reporter_phone || '—')}</span></div>
              <div class="pop-row"><span class="k">办案单位</span><span>${UI.esc(row.org_name || '—')}</span></div>
              <div class="pop-row"><span class="k">问询模板</span><span>${UI.esc((row.template_names || []).join('、') || '尚未选定')}</span></div>
              <div class="pop-row"><span class="k">五流完整度</span><span>${row.completeness || 0}%</span></div>
            </div>
          </div>
          ${gapTags ? `<div style="margin-top:4px">${gapTags}</div>` : ''}
        </td>
        <td>${UI.esc(row.reporter_name || '—')}</td>
        <td>${UI.esc(row.police_name || '—')}<br><span class="tiny muted">${UI.esc(row.org_name || '')}</span></td>
        <td><span class="pill pill-${UI.stageTone(row.stage)}">${UI.esc(row.status)}</span></td>
        <td class="small muted">${UI.fmtDT(row.updated_at)}</td>
        <td class="nowrap">
          <button class="btn btn-sm btn-primary" data-action="continue">${actionLabel(row.stage)}</button>
          <button class="btn btn-sm" data-action="analysis">解析结果</button>
        </td>
      </tr>`;
    }).join('');
  }

  async function onRowAction(ev) {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const recordId = tr.dataset.id;
    const action = btn.dataset.action;
    if (action === 'analysis') return openAnalysis(recordId);
    if (action === 'continue') return continueRecord(recordId, btn);
  }

  async function continueRecord(recordId, btn) {
    UI.setButtonBusy(btn, true, '恢复中…');
    try {
      const data = await API.get(`/api/records/${recordId}/resume`);
      location.href = data.redirect;
    } catch (err) {
      UI.handleError(err, '断点恢复失败');
      UI.setButtonBusy(btn, false);
    }
  }

  /* ------------------------------------------------------------ 新增笔录 */
  async function openIntake(recordId) {
    const editing = Boolean(recordId);
    let record = null;
    if (editing) {
      try {
        const data = await API.get(`/api/records/${recordId}`);
        record = data.record;
      } catch (err) {
        UI.handleError(err, '笔录加载失败');
        return;
      }
    }
    const categories = (state.meta && state.meta.categories) || [];
    const nowLocal = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const body = `
      <div class="banner" style="margin-bottom:16px">
        <span class="ico">i</span>
        <div>接报信息将作为问询模板推荐与红头文书的基础数据。<b>简要案情越完整，AI 模板推荐越准确</b>；可从外部业务系统一键检索回填。</div>
      </div>
      <div class="form-grid two">
        <div class="field">
          <label>案件类别<span class="req">*</span></label>
          <select class="select" id="f-category">
            <option value="">请选择案件类别</option>
            ${categories.map((c) => `<option value="${UI.esc(c)}" ${record && record.case_category === c ? 'selected' : ''}>${UI.esc(c)}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label>办案单位</label>
          <input class="input" id="f-unit" value="${UI.esc(record ? (record.unit || '') : (state.user.org_name || ''))}" placeholder="例如：城东分局长虹路派出所">
        </div>
      </div>
      <div class="field">
        <label>简要案情<span class="req">*</span></label>
        <textarea class="textarea" id="f-brief" rows="5" placeholder="请完整描述被骗经过：接触渠道、话术、涉案APP/网址、转账方式与金额、发现被骗过程…">${UI.esc(record ? record.brief : '')}</textarea>
        <div class="form-hint">不少于 10 个字。系统将据此预测涉诈类型并推荐问询模板。</div>
      </div>
      <div class="form-grid three">
        <div class="field">
          <label>报案人姓名</label>
          <input class="input" id="f-reporter" value="${UI.esc(record ? (record.reporter_name || '') : '')}" placeholder="例如：李芳">
        </div>
        <div class="field">
          <label>联系电话</label>
          <input class="input" id="f-phone" value="${UI.esc(record ? (record.reporter_phone || '') : '')}" placeholder="11 位手机号">
        </div>
        <div class="field">
          <label>身份证号</label>
          <input class="input" id="f-idno" value="${UI.esc(record ? (record.reporter_id_no || '') : '')}" placeholder="18 位身份证号">
        </div>
      </div>
      <div class="form-grid two">
        <div class="field">
          <label>接报时间</label>
          <input class="input" id="f-time" type="datetime-local" value="${UI.esc(record && record.report_time ? record.report_time.slice(0, 16) : nowLocal)}">
        </div>
        <div class="field">
          <label>案发地点</label>
          <input class="input" id="f-location" value="${UI.esc(record ? (record.case_location || '') : '')}" placeholder="例如：城东区长虹路88号">
        </div>
      </div>
      <div style="border-top:1px dashed var(--line); padding-top:14px; margin-top:6px">
        <button type="button" class="btn btn-sm" id="btn-sync-toggle">⇅ 一键同步其他系统信息</button>
        <div id="sync-panel" class="hidden" style="margin-top:12px; padding:14px; background:#fafbfe; border:1px dashed var(--line); border-radius:10px">
          <div class="flex gap-8 items-center">
            <select class="select" id="sync-system" style="width:160px">
              <option value="zhiyanpan">智研判平台</option>
              <option value="zhianguan">智案管平台</option>
            </select>
            <input class="input" id="sync-keyword" placeholder="输入案件编号或报案人姓名检索外部案件">
            <button type="button" class="btn btn-sm btn-primary" id="btn-sync-search" style="white-space:nowrap">检索</button>
          </div>
          <div id="sync-results" class="mt-12"><div class="tiny muted">输入关键字后点击检索，可只读拉取外部系统案件并回填表单。</div></div>
        </div>
      </div>`;

    const modal = UI.modal({
      title: editing ? `接报信息 · ${record.case_no}` : '新增笔录 · 接报建档',
      size: 'wide',
      body,
      footer: [
        { text: '取消' },
        {
          text: editing ? '保存接报信息' : '保存并进入模板选择',
          kind: 'btn-primary',
          autoClose: false,
          onClick: async (api) => {
            await submitIntake(api, recordId);
          }
        }
      ],
      onMount: () => {
        const toggle = document.getElementById('btn-sync-toggle');
        const panel = document.getElementById('sync-panel');
        toggle.addEventListener('click', () => panel.classList.toggle('hidden'));
        document.getElementById('btn-sync-search').addEventListener('click', searchExternal);
        if (!editing) document.getElementById('f-category').focus();
      }
    });
    return modal;
  }

  let syncedExternal = null;

  async function searchExternal() {
    const keyword = document.getElementById('sync-keyword').value.trim();
    const system = document.getElementById('sync-system').value;
    const box = document.getElementById('sync-results');
    if (!keyword) {
      box.innerHTML = '<div class="banner warn"><span class="ico">△</span><div>请输入案件编号或报案人姓名</div></div>';
      return;
    }
    box.innerHTML = '<div class="skeleton" style="height:70px"></div>';
    try {
      const data = await API.get('/api/mock/external/cases', { system, keyword });
      if (!data.items.length) {
        box.innerHTML = '<div class="empty-state" style="padding:18px">未检索到匹配的外部案件，可直接手工录入</div>';
        return;
      }
      box.innerHTML = `
        <div class="small muted" style="margin-bottom:6px">检索到 ${data.items.length} 条外部案件（只读），选择一条回填：</div>
        <div style="max-height:230px; overflow:auto; border:1px solid var(--line); border-radius:8px; background:#fff">
          <table class="data" style="font-size:12px">
            <tbody>
            ${data.items.map((item, idx) => `
              <tr>
                <td style="width:34px"><input type="radio" name="ext-case" value="${idx}" ${idx === 0 ? 'checked' : ''}></td>
                <td style="width:150px"><span class="mono" style="font-size:11.5px">${UI.esc(item.case_no)}</span></td>
                <td><span class="tag">${UI.esc(item.case_category)}</span> <span class="muted">${UI.esc(item.reporter_name)}</span>
                  <div class="tiny muted">${UI.esc(item.unit)} · ${UI.esc(item.report_time)}</div></td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div class="flex justify-between items-center mt-8">
          <span class="tiny muted">数据来源：${UI.esc(system === 'zhiyanpan' ? '智研判平台' : '智案管平台')}（Mock 只读接口）</span>
          <button type="button" class="btn btn-sm btn-gold" id="btn-sync-apply">确认回填到表单</button>
        </div>`;
      document.getElementById('btn-sync-apply').addEventListener('click', () => applyExternal(data.items));
    } catch (err) {
      box.innerHTML = `<div class="banner red"><span class="ico">!</span><div>检索失败：${UI.esc(err.message)}</div></div>`;
    }
  }

  function applyExternal(items) {
    const chosen = document.querySelector('input[name="ext-case"]:checked');
    const item = items[Number(chosen.value)];
    document.getElementById('f-category').value = item.case_category;
    document.getElementById('f-brief').value = item.brief;
    document.getElementById('f-reporter').value = item.reporter_name || '';
    document.getElementById('f-phone').value = item.reporter_phone || '';
    document.getElementById('f-location').value = item.location || '';
    document.getElementById('f-unit').value = item.unit || document.getElementById('f-unit').value;
    if (item.report_time) {
      document.getElementById('f-time').value = item.report_time.replace(' ', 'T').slice(0, 16);
    }
    syncedExternal = { source: item.system, id: item.id };
    UI.toast('已回填外部案件信息，请核对后保存', 'ok');
  }

  async function submitIntake(api, recordId) {
    const category = document.getElementById('f-category').value;
    const brief = document.getElementById('f-brief').value.trim();
    if (!category) return UI.toast('请选择案件类别', 'warn'), false;
    if (brief.length < 10) return UI.toast('简要案情不得少于 10 个字', 'warn'), false;
    const payload = {
      case_category: category,
      brief,
      reporter_name: document.getElementById('f-reporter').value.trim(),
      reporter_phone: document.getElementById('f-phone').value.trim(),
      reporter_id_no: document.getElementById('f-idno').value.trim(),
      report_time: (document.getElementById('f-time').value || '').replace('T', 'T'),
      case_location: document.getElementById('f-location').value.trim(),
      unit: document.getElementById('f-unit').value.trim()
    };
    const buttons = api.overlay.querySelectorAll('.modal-foot .btn');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      let targetId = recordId;
      if (recordId) {
        await API.patch(`/api/records/${recordId}`, payload);
        UI.toast('接报信息已保存', 'ok');
      } else {
        const data = await API.post('/api/records', {
          ...payload,
          external_source: syncedExternal ? syncedExternal.source : '',
          external_case_id: syncedExternal ? syncedExternal.id : ''
        });
        targetId = data.record.id;
        UI.toast('笔录已创建，正在进入模板选择', 'ok');
      }
      api.close();
      location.href = `/templates.html?record=${targetId}`;
    } catch (err) {
      UI.handleError(err, '保存失败');
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
    return false;
  }

  /* ------------------------------------------------------------ 解析结果 */
  async function openAnalysis(recordId) {
    const modal = UI.modal({
      title: '笔录解析结果',
      size: 'wide',
      body: '<div class="skeleton" style="height:280px"></div>',
      footer: [{ text: '关闭' }]
    });
    try {
      const data = await API.get(`/api/records/${recordId}/analysis`);
      const j = data.judgement;
      const flowCards = data.flow.flows.map((f) => `
        <div class="card-pad" style="border:1px solid var(--line);border-radius:9px;background:#fff">
          <div class="flex justify-between items-center">
            <b style="font-size:13px">${UI.esc(f.name)}</b>
            <span class="pill pill-${UI.flowTone(f.status)}">${UI.esc(f.status_label)}</span>
          </div>
          <div class="progress ${f.status === 'key_gap' ? 'red' : f.status === 'needs_supplement' ? 'amber' : 'green'}" style="margin-top:8px">
            <i style="width:${f.coverage_pct}%"></i>
          </div>
          <div class="tiny muted" style="margin-top:4px">已收集 ${f.collected_count}/${f.required_total} 项必填要素</div>
        </div>`).join('');
      modal.bodyEl.innerHTML = `
        <div class="flex justify-between items-start gap-16" style="margin-bottom:12px">
          <div style="flex:1">
            <div class="tiny muted">案件编号 / 涉诈类型判定</div>
            <div style="font-size:16px; font-weight:700; margin-top:2px">
              ${UI.esc(data.record.case_no)} · ${UI.esc(j.category)}
              <span class="pill pill-green no-dot" style="margin-left:6px">置信度 ${UI.esc(j.confidence)}%</span>
            </div>
            <div class="small muted" style="margin-top:4px">${UI.esc(data.summary)}</div>
          </div>
          <div class="text-right">
            <div class="tiny muted">问询进度</div>
            <div class="strong" style="font-size:16px">${data.progress.answered}/${data.progress.total}</div>
            <div class="tiny muted">材料 ${data.material_count} 份</div>
          </div>
        </div>
        <div class="grid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px">${flowCards}</div>
        ${data.flow.key_gaps.length ? `
          <div class="banner red" style="margin-bottom:10px">
            <span class="ico">!</span>
            <div><b>重点缺口 ${data.flow.key_gaps.length} 项：</b>
              ${data.flow.key_gaps.map((g) => `${UI.esc(g.flow_name)}·${UI.esc(g.label)}`).join('、')}</div>
          </div>` : `
          <div class="banner green" style="margin-bottom:10px"><span class="ico">✓</span><div>五流关键要素均已收集，可完成问询并导出文书</div></div>`}
        <div class="form-grid two" style="gap:16px">
          <div>
            <div class="strong small" style="margin-bottom:6px">类型判定依据</div>
            <ul class="basis-list small">${(j.basis || []).map((b) => `<li>${UI.esc(b)}</li>`).join('')}</ul>
          </div>
          <div>
            <div class="strong small" style="margin-bottom:6px">下一步侦查指引</div>
            <ul class="basis-list small">${(data.guides || []).map((g) => `<li>${UI.esc(g.content)}</li>`).join('')}</ul>
          </div>
        </div>
        <div class="small muted" style="margin-top:12px">已选模板：${UI.esc((data.templates || []).map((t) => t.name).join('、') || '尚未选定')} ·
          AI 建议采纳率：${data.advice_stats.adoption_rate}%</div>`;
    } catch (err) {
      modal.bodyEl.innerHTML = `<div class="banner red"><span class="ico">!</span><div>${UI.esc(err.message)}</div></div>`;
    }
  }
})();
