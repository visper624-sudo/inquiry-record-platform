/* 历史笔录导入与回溯 */
(function () {
  'use strict';

  const state = { result: null, user: null, samples: [] };

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    state.user = await Layout.initLayout({ active: 'import' });
    if (!state.user) return;
    document.getElementById('btn-back').addEventListener('click', () => { location.href = '/index.html'; });
    document.getElementById('btn-clear').addEventListener('click', () => {
      document.getElementById('raw-text').value = '';
      document.getElementById('file-input').value = '';
    });
    document.getElementById('btn-parse').addEventListener('click', parse);
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
      const file = ev.dataTransfer.files[0];
      if (file) parseFile(file);
    });
    fileInput.addEventListener('change', (ev) => {
      const file = ev.target.files[0];
      if (file) parseFile(file);
    });
    await loadSamples();
    const sampleQuery = query('sample');
    if (sampleQuery !== '') {
      const idx = Number(sampleQuery) || 0;
      if (state.samples[idx]) {
        document.getElementById('raw-text').value = state.samples[idx].text;
        await parse();
      }
    }
  }

  async function loadSamples() {
    try {
      const data = await API.get('/api/import/samples');
      state.samples = data.items || [];
      const host = document.getElementById('samples');
      if (!state.samples.length) {
        host.innerHTML = '<div class="small muted">未找到 data/demo/history_samples.json 样例文件</div>';
        return;
      }
      host.innerHTML = state.samples.map((item, idx) => `
        <div class="sample-item" data-idx="${idx}">
          <div style="flex:1">
            <b>${UI.esc(item.name)}</b>
            <span>${UI.esc(item.description)}</span>
            <div class="tiny" style="color:var(--red-600);margin-top:3px">
              预期缺口：${UI.esc((item.expected_gaps || []).join('、'))}
            </div>
          </div>
        </div>`).join('');
      host.querySelectorAll('.sample-item').forEach((el) => {
        el.addEventListener('click', () => {
          document.getElementById('raw-text').value = state.samples[Number(el.dataset.idx)].text;
          UI.toast('已载入样例文本，点击「解析笔录」开始回溯', 'info');
        });
      });
    } catch (err) {
      UI.handleError(err, '样例加载失败');
    }
  }

  async function parse() {
    const text = document.getElementById('raw-text').value.trim();
    if (!text) return UI.toast('请上传文件或粘贴笔录文本', 'warn');
    await runParse(() => API.post('/api/import/parse', { text, filename: '粘贴文本.txt' }));
  }

  async function parseFile(file) {
    if (!/\.(docx|txt|md)$/i.test(file.name)) {
      return UI.toast('仅支持 DOCX / TXT / MD 文件，或直接粘贴文本', 'warn');
    }
    await runParse(() => API.upload('/api/import/parse', file));
  }

  async function runParse(task) {
    const btn = document.getElementById('btn-parse');
    UI.setButtonBusy(btn, true, '解析中…');
    try {
      const data = await task();
      state.result = data;
      renderResult(data);
      UI.toast(`解析完成：${data.stats.total_pairs} 组问答，识别 ${data.key_gaps.length} 项重点缺口`, 'ok');
    } catch (err) {
      UI.handleError(err, '解析失败');
      document.getElementById('result-body').innerHTML =
        `<div class="banner red"><span class="ico">!</span><div>${UI.esc(err.message)}</div></div>`;
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }

  function renderResult(data) {
    const stats = data.stats;
    const flow = data.flow_state;
    document.getElementById('result-meta').textContent =
      `${data.filename} · ${data.source_type.toUpperCase()} · 完整度 ${flow.completeness}%`;
    const sectionMap = { opening: '固定开头', contact: '接触引流', process: '被骗经过', loss: '资金损失', evidence: '证据补充' };
    document.getElementById('result-body').innerHTML = `
      <div class="flex gap-16" style="margin-bottom:14px; flex-wrap:wrap">
        <div class="stat-card" style="flex:1;min-width:120px;padding:12px 14px">
          <div class="stat-label">问答对</div><div class="stat-value" style="font-size:22px">${stats.total_pairs}</div>
          <div class="stat-foot">已作答 ${stats.answered_pairs} 组</div>
        </div>
        <div class="stat-card" style="flex:1;min-width:120px;padding:12px 14px">
          <div class="stat-label">五流完整度</div><div class="stat-value" style="font-size:22px">${flow.completeness}%</div>
          <div class="stat-foot">已识别要素字段 ${stats.inferred_fields} 个</div>
        </div>
        <div class="stat-card warn" style="flex:1;min-width:120px;padding:12px 14px">
          <div class="stat-label">重点缺口</div><div class="stat-value" style="font-size:22px">${data.key_gaps.length}</div>
          <div class="stat-foot">${data.key_gaps.length ? '建议针对性补充问询' : '未发现关键缺口'}</div>
        </div>
        <div class="stat-card" style="flex:1;min-width:120px;padding:12px 14px">
          <div class="stat-label">缺失章节</div><div class="stat-value" style="font-size:22px">${stats.missing_sections.length}</div>
          <div class="stat-foot">${UI.esc(stats.missing_sections.join('、') || '章节齐全')}</div>
        </div>
      </div>

      <div class="grid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px">
        ${flow.flows.map((f) => `
          <div style="border:1px solid var(--line);border-radius:9px;padding:9px 11px;background:#fff">
            <div class="flex justify-between items-center">
              <b style="font-size:12.5px">${UI.esc(f.name)}</b>
              <span class="pill pill-${UI.flowTone(f.status)}">${UI.esc(f.status_label)}</span>
            </div>
            <div class="progress ${f.status === 'key_gap' ? 'red' : f.status === 'needs_supplement' ? 'amber' : 'green'}" style="margin-top:6px">
              <i style="width:${f.coverage_pct}%"></i>
            </div>
            <div class="tiny muted" style="margin-top:3px">${f.collected_count}/${f.required_total} 项</div>
          </div>`).join('')}
      </div>

      <div class="banner ${data.key_gaps.length ? 'red' : 'green'}">
        <span class="ico">${data.key_gaps.length ? '!' : '✓'}</span>
        <div>${UI.esc(data.summary)}</div>
      </div>

      ${data.key_gaps.length ? `
      <div style="margin-top:12px">
        <div class="strong small" style="margin-bottom:6px">重点缺口清单（可直接转为继续问询草稿）</div>
        <div class="gap-list">
          ${data.key_gaps.map((g) => `<div class="gap-item">
            <span class="flow">${UI.esc(g.flow)}</span>
            <span>${UI.esc(g.label)}</span>
            <span class="tiny muted" style="margin-left:auto">${UI.esc(g.hint)}</span>
          </div>`).join('')}
        </div>
      </div>` : ''}

      <div class="flex justify-between items-center" style="margin:16px 0 8px">
        <span class="strong small">还原的问答大纲（按章节归类）</span>
        <button class="btn btn-primary btn-sm" id="btn-commit">转存为新问询会话 →</button>
      </div>
      <div class="table-wrap" style="border:1px solid var(--line);border-radius:9px;max-height:360px;overflow:auto">
        <table class="data">
          <thead><tr><th style="width:78px">章节</th><th style="width:44%">问句</th><th>答复</th><th style="width:96px">识别要素</th></tr></thead>
          <tbody>
          ${data.qa_pairs.map((pair) => `
            <tr>
              <td><span class="tag">${UI.esc(sectionMap[pair.section] || pair.section)}</span></td>
              <td style="font-size:12.5px">${UI.esc(pair.question)}</td>
              <td style="font-size:12.5px;color:var(--text-500)">${UI.esc(pair.answer || '（未作答）')}</td>
              <td class="tiny muted">${pair.flow ? UI.esc(flowLabel(pair.flow)) + '<br>' + UI.esc(pair.field) : '—'}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
    document.getElementById('btn-commit').addEventListener('click', openCommit);
  }

  function flowLabel(key) {
    return { fund: '资金流', communication: '通信流', network: '网络流', personnel: '人员流', delivery: '寄递流' }[key] || key;
  }

  async function openCommit() {
    const data = state.result;
    if (!data) return;
    const suggested = data.draft.suggested_category || '其他';
    const body = `
      <div class="banner" style="margin-bottom:14px">
        <span class="ico">i</span>
        <div>转存将新建一份处于「问询进行中」阶段的笔录，并把解析出的 ${data.stats.total_pairs} 组问答写入对应章节，
          同时保留五流缺口，可直接在工作台继续补充问询。</div>
      </div>
      <div class="form-grid two">
        <div class="field"><label>案件类别</label>
          <input class="input" id="c-category" value="${UI.esc(suggested)}">
          <div class="form-hint">系统按问答内容自动推测，可手工修正</div>
        </div>
        <div class="field"><label>报案人</label><input class="input" id="c-reporter" placeholder="例如：周敏"></div>
      </div>
      <div class="field"><label>简要案情</label>
        <textarea class="textarea" id="c-brief" rows="4">历史笔录回溯导入：${UI.esc(data.draft.suggested_title || '')}。${UI.esc(data.summary)}</textarea>
      </div>`;
    UI.modal({
      title: '转存为新问询会话',
      size: 'wide',
      body,
      footer: [
        { text: '取消' },
        {
          text: '确认转存并进入工作台',
          kind: 'btn-primary',
          autoClose: false,
          onClick: async (api) => {
            const payload = {
              job_id: data.job_id,
              case_category: document.getElementById('c-category').value.trim(),
              reporter_name: document.getElementById('c-reporter').value.trim(),
              brief: document.getElementById('c-brief').value.trim()
            };
            try {
              const res = await API.post('/api/import/commit', payload);
              api.close();
              UI.toast(`已转存为 ${res.case_no}，正在进入工作台`, 'ok');
              setTimeout(() => { location.href = res.redirect; }, 500);
            } catch (err) {
              UI.handleError(err, '转存失败');
            }
            return false;
          }
        }
      ]
    });
  }
})();
