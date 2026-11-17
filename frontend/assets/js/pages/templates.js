/* 问询模板智能匹配页 */
(function () {
  'use strict';

  const state = {
    recordId: '', record: null, recommendations: [], library: [],
    categories: [], selected: new Set(), judgement: null, keyword: '', category: ''
  };

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    const user = await Layout.initLayout({ active: '' });
    if (!user) return;
    state.recordId = query('record');
    if (!state.recordId) {
      document.getElementById('case-summary').innerHTML = '<div class="banner red"><span class="ico">!</span><div>缺少笔录编号，请从台账进入</div></div>';
      return;
    }
    document.getElementById('btn-back').addEventListener('click', () => { location.href = '/index.html'; });
    document.getElementById('tpl-kw').addEventListener('input', UI.debounce((ev) => {
      state.keyword = ev.target.value.trim();
      renderLibrary();
    }, 220));
    document.getElementById('tpl-cat').addEventListener('change', (ev) => {
      state.category = ev.target.value;
      renderLibrary();
    });
    document.getElementById('btn-start').addEventListener('click', startInquiry);
    document.getElementById('reco-grid').addEventListener('click', onRecoClick);
    document.getElementById('tpl-grid').addEventListener('click', onLibraryClick);
    document.getElementById('selected-list').addEventListener('click', onSelectedRemove);
    await load();
  }

  async function load() {
    try {
      const data = await API.get(`/api/records/${state.recordId}/templates`);
      state.record = data.record;
      state.recommendations = data.recommendations || [];
      state.library = data.library || [];
      state.categories = data.categories || [];
      state.judgement = data.judgement;
      state.selected = new Set(data.selected || []);
      renderCase();
      renderRecommendations();
      renderLibrary();
      renderCategories();
      renderSelection();
    } catch (err) {
      UI.handleError(err, '模板数据加载失败');
    }
  }

  function renderCase() {
    const r = state.record;
    const j = state.judgement;
    document.getElementById('case-summary').innerHTML = `
      <div class="flex justify-between items-start">
        <div>
          <div class="tiny muted">当前笔录</div>
          <div class="case-no">${UI.esc(r.case_no)}</div>
        </div>
        <span class="pill pill-${UI.stageTone(r.stage)}">${UI.esc(r.stage_label)}</span>
      </div>
      <div class="mt-12">
        <div class="kv"><span class="k">案件类别</span><span class="v"><span class="tag">${UI.esc(r.case_category)}</span></span></div>
        <div class="kv"><span class="k">报案人</span><span class="v">${UI.esc(r.reporter_name || '—')} · ${UI.esc(r.reporter_phone || '—')}</span></div>
        <div class="kv"><span class="k">接报单位</span><span class="v">${UI.esc(r.org_name || '—')}</span></div>
        <div class="kv"><span class="k">接报时间</span><span class="v">${UI.fmtDT(r.report_time || r.created_at)}</span></div>
        <div class="kv"><span class="k">案情摘要</span><span class="v" style="color:var(--text-400)">已录入 ${(r.brief || '').length} 字</span></div>
      </div>
      <div class="brief-block">${UI.esc(r.brief || '')}</div>
      ${j ? `<div class="banner" style="margin-top:12px">
        <span class="ico">◎</span>
        <div><b>初步研判：</b>${UI.esc(j.category)}，置信度 ${UI.esc(j.confidence)}%。${UI.esc((j.basis || [])[0] || '')}</div>
      </div>` : ''}`;
  }

  function renderRecommendations() {
    const grid = document.getElementById('reco-grid');
    document.getElementById('reco-meta').textContent =
      `已分析案情文本 · 命中信号词 ${state.recommendations.reduce((n, r) => n + r.signal_hits.length, 0)} 次`;
    if (!state.recommendations.length) {
      grid.innerHTML = '<div class="empty-state">未获得推荐结果，请检查案情文本或从下方模板库选择</div>';
      return;
    }
    grid.innerHTML = state.recommendations.map((item, idx) => {
      const selected = state.selected.has(item.template_id);
      const signals = (item.signals || []).slice(0, 9);
      const hitSet = new Set(item.signal_hits || []);
      return `
      <div class="reco-card ${idx === 0 ? 'top1' : ''} ${selected ? 'selected' : ''}" data-id="${UI.esc(item.template_id)}">
        <div class="reco-head">
          ${UI.scoreRing(item.match_score)}
          <div class="reco-title">
            <b>${UI.esc(item.name)}</b>
            <span class="tag" style="margin-left:6px">${UI.esc(item.category)}</span>
            <div class="desc">${UI.esc(item.description)}</div>
          </div>
          <button class="btn btn-sm ${selected ? 'btn-primary' : ''}" data-act="toggle" data-id="${UI.esc(item.template_id)}">
            ${selected ? '已选' : '选择'}
          </button>
        </div>
        <div class="flex gap-4" style="margin-top:9px; flex-wrap:wrap">
          ${signals.map((s) => `<span class="chip ${hitSet.has(s) ? 'hit' : ''}">${UI.esc(s)}</span>`).join('')}
        </div>
        <div class="reco-reason">
          <div class="reason-line"><span class="dot">●</span><span>${UI.esc(item.reason)}</span></div>
          <div class="breakdown">
            <div class="bd"><div class="k">信号词命中率 60%</div><div class="v">${Math.round(item.breakdown.signal * 100)}%</div></div>
            <div class="bd"><div class="k">语义相似度 25%</div><div class="v">${Math.round(item.breakdown.semantic * 100)}%</div></div>
            <div class="bd"><div class="k">手法关键词 15%</div><div class="v">${Math.round(item.breakdown.keyword * 100)}%</div></div>
          </div>
          <div class="tiny muted" style="margin-top:6px">共 ${item.question_count} 个预置问题；命中信号词 ${(item.signal_hits || []).length} 个</div>
        </div>
        <button class="btn-text btn-sm" data-act="expand" style="margin-top:6px; padding-left:0">为什么推荐？▾</button>
      </div>`;
    }).join('');
  }

  function renderCategories() {
    const select = document.getElementById('tpl-cat');
    select.innerHTML = '<option value="">全部分类</option>' + state.categories
      .map((c) => `<option value="${UI.esc(c)}">${UI.esc(c)}</option>`).join('');
    select.value = state.category;
  }

  function renderLibrary() {
    const grid = document.getElementById('tpl-grid');
    const kw = state.keyword;
    const list = state.library.filter((t) => {
      const matchKw = !kw || t.name.includes(kw) || t.category.includes(kw)
        || (t.signals || []).some((s) => s.includes(kw));
      const matchCat = !state.category || t.category === state.category;
      return matchKw && matchCat;
    });
    if (!list.length) {
      grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">没有匹配的模板</div>';
      return;
    }
    grid.innerHTML = list.map((t) => {
      const selected = state.selected.has(t.template_id);
      return `
      <div class="tpl-item ${selected ? 'selected' : ''}" data-id="${UI.esc(t.template_id)}">
        <div class="t-name">${UI.esc(t.name)}${t.is_general ? '<span class="tag gold">通用</span>' : ''}</div>
        <div class="t-cat">${UI.esc(t.category)} · ${t.question_count} 问</div>
        <div class="t-count mono">${UI.esc(t.template_id)}</div>
        <div class="check-mark">✓</div>
      </div>`;
    }).join('');
  }

  function renderSelection() {
    const ids = Array.from(state.selected);
    const count = document.getElementById('sel-count');
    const chips = document.getElementById('sel-chips');
    const startBtn = document.getElementById('btn-start');
    document.getElementById('sel-hint').textContent = `${ids.length} 个`;
    count.textContent = ids.length;
    startBtn.disabled = ids.length === 0;
    if (!ids.length) {
      chips.innerHTML = '<span class="tiny muted">点击上方推荐卡或模板库卡片进行选择</span>';
    } else {
      chips.innerHTML = ids.map((id) => {
        const tpl = state.library.find((t) => t.template_id === id);
        return `<span class="chip">${UI.esc(tpl ? tpl.name : id)}<span class="x" data-remove="${UI.esc(id)}">×</span></span>`;
      }).join('');
    }
    const selectedList = document.getElementById('selected-list');
    if (!ids.length) {
      selectedList.innerHTML = '<div class="small muted">尚未选择模板</div>';
    } else {
      selectedList.innerHTML = ids.map((id) => {
        const tpl = state.library.find((t) => t.template_id === id);
        return `<div class="flex justify-between items-center" style="padding:6px 0;border-bottom:1px dashed var(--line-soft)">
          <div><div style="font-size:12.5px;font-weight:600">${UI.esc(tpl ? tpl.name : id)}</div>
          <div class="tiny muted">${UI.esc(tpl ? tpl.category : '')} · ${tpl ? tpl.question_count : 0} 问</div></div>
          <button class="btn-text danger btn-sm" data-remove="${UI.esc(id)}">移除</button>
        </div>`;
      }).join('');
    }
  }

  function toggle(id) {
    if (state.selected.has(id)) state.selected.delete(id);
    else state.selected.add(id);
    renderSelection();
    renderRecommendations();
    renderLibrary();
  }

  function onRecoClick(ev) {
    const expandBtn = ev.target.closest('[data-act="expand"]');
    if (expandBtn) {
      const card = expandBtn.closest('.reco-card');
      card.classList.toggle('expanded');
      expandBtn.textContent = card.classList.contains('expanded') ? '收起依据 ▴' : '为什么推荐？▾';
      return;
    }
    const card = ev.target.closest('.reco-card');
    if (!card) return;
    toggle(card.dataset.id);
  }

  function onLibraryClick(ev) {
    const item = ev.target.closest('.tpl-item');
    if (!item) return;
    toggle(item.dataset.id);
  }

  async function onSelectedRemove(ev) {
    const btn = ev.target.closest('[data-remove]');
    const chipClose = ev.target.closest('.x');
    const id = btn ? btn.dataset.remove : chipClose ? chipClose.dataset.remove : null;
    if (!id) return;
    state.selected.delete(id);
    renderSelection();
    renderRecommendations();
    renderLibrary();
  }

  document.addEventListener('click', (ev) => {
    const chipClose = ev.target.closest('.chip .x');
    if (chipClose) {
      state.selected.delete(chipClose.dataset.remove);
      renderSelection();
      renderRecommendations();
      renderLibrary();
    }
  });

  async function startInquiry(ev) {
    const btn = document.getElementById('btn-start');
    UI.setButtonBusy(btn, true, '生成大纲中…');
    const payload = { template_ids: Array.from(state.selected) };
    try {
      const data = await API.post(`/api/records/${state.recordId}/templates`, payload);
      const total = data.merged.total;
      UI.toast(`已合并生成 ${total} 个问询问题，正在进入三栏工作台`, 'ok');
      setTimeout(() => { location.href = `/workbench.html?record=${state.recordId}`; }, 420);
    } catch (err) {
      if (err.code === 'outline_exists' || err.status === 409) {
        const ok = await UI.confirm('该笔录已生成问询大纲，重新选择模板将覆盖现有问答内容（已回答内容会丢失）。确认继续？',
          { danger: true, confirmText: '覆盖并重建大纲' });
        if (ok) {
          try {
            const data = await API.post(`/api/records/${state.recordId}/templates`, { ...payload, replace: true });
            UI.toast(`已重建大纲，共 ${data.merged.total} 个问题`, 'ok');
            setTimeout(() => { location.href = `/workbench.html?record=${state.recordId}`; }, 400);
          } catch (err2) {
            UI.handleError(err2, '重建大纲失败');
          }
        }
      } else {
        UI.handleError(err, '选定模板失败');
      }
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }
})();
