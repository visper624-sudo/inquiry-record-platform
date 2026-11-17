/* 笔录文书预览与导出 */
(function () {
  'use strict';

  const state = { recordId: '', model: null, user: null };
  const CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    state.user = await Layout.initLayout({ active: '' });
    if (!state.user) return;
    state.recordId = query('record');
    if (!state.recordId) {
      document.getElementById('paper-host').innerHTML =
        '<div class="banner red"><span class="ico">!</span><div>缺少笔录编号，请从台账进入</div></div>';
      return;
    }
    document.getElementById('btn-back').addEventListener('click', () => {
      location.href = `/workbench.html?record=${state.recordId}`;
    });
    document.getElementById('btn-ledger').addEventListener('click', () => { location.href = '/index.html'; });
    document.getElementById('btn-print').addEventListener('click', () => window.print());
    document.getElementById('btn-export').addEventListener('click', exportDocx);
    await load();
  }

  async function load() {
    try {
      const data = await API.get(`/api/records/${state.recordId}/document`);
      state.model = data.document;
      renderPaper();
      renderChecks();
    } catch (err) {
      UI.handleError(err, '文书加载失败');
      document.getElementById('paper-host').innerHTML =
        `<div class="banner red"><span class="ico">!</span><div>${UI.esc(err.message)}</div></div>`;
    }
  }

  function renderPaper() {
    const m = state.model;
    const c = m.case;
    const metaRows = [
      ['案件编号', c.case_no, '案件类别', c.category],
      ['询问时间', `${c.inquiry_start} 至 ${c.inquiry_end}`, '询问地点', c.location],
      ['询问人', `${c.police_name}（警号 ${c.police_badge}）`, '记录人', c.police_name],
      ['被询问人', c.reporter_name || '（待填）', '身份证号', c.reporter_id_no || '（待填）'],
      ['联系方式', c.reporter_phone || '（待填）', '询问时长', c.duration]
    ];
    const sections = m.sections.map((section, idx) => `
      <div class="sec-title">${CN[idx] || idx + 1}、${UI.esc(section.title)}</div>
      ${section.items.map((item) => `
        <div class="qa"><span class="mark">问：</span>${UI.esc(item.question)}</div>
        <div class="qa"><span class="mark">答：</span>${UI.esc(item.answer)}</div>`).join('')}
    `).join('');

    const hasOpening = m.sections.some((s) => s.key === 'opening' && s.items.length);
    const intro = hasOpening ? '' : `
        <div class="qa" style="margin-top:14px">
          <span class="mark">问：</span>我们是${UI.esc(m.org_header)}民警（警号 ${UI.esc(c.police_badge)}），现就你报案的${UI.esc(c.category)}案件依法向你询问，请你如实回答。你听清楚了吗？
        </div>
        <div class="qa"><span class="mark">答：</span>听清楚了，我会如实回答。</div>`;
    document.getElementById('paper-host').innerHTML = `
      <article class="paper">
        <div class="red-head">${UI.esc(m.org_header)}</div>
        <div class="doc-title">${UI.esc(m.title)}</div>
        <table class="meta-table">
          ${metaRows.map((row) => `<tr>
            <td class="l">${UI.esc(row[0])}</td><td class="v">${UI.esc(row[1] || '　')}</td>
            <td class="l">${UI.esc(row[2])}</td><td class="v">${UI.esc(row[3] || '　')}</td>
          </tr>`).join('')}
        </table>
        ${intro}
        ${sections}
        <div class="qa" style="margin-top:26px">（以下无正文）</div>
        <div class="sign">
          被询问人（签名）：<span class="line"></span><br>
          日　期：${UI.esc(m.signature.date_text)}<br>
          询问人（签名）：<span class="line"></span>　记录人（签名）：<span class="line"></span><br>
          ${UI.esc(m.signature.org_name)}（盖章）
        </div>
        <div class="gen-note">本笔录由智能问询笔录平台生成 · 数据为虚构脱敏演示数据 · 生成时间 ${UI.esc(m.meta.generated_at)}</div>
      </article>`;

    document.getElementById('doc-summary').textContent =
      `${c.case_no} · 已作答 ${m.meta.answered_items} 条 · 自动过滤未作答 ${m.meta.filtered_items} 条`;
    if (query('bottom') === '1') {
      setTimeout(() => window.scrollTo(0, document.body.scrollHeight), 120);
    }
  }

  function renderChecks() {
    const m = state.model;
    const checks = m.checks || [];
    const passed = checks.filter((c) => c.passed).length;
    document.getElementById('check-badge').textContent = `${passed}/${checks.length} 通过`;
    document.getElementById('check-badge').className =
      `tag ${passed === checks.length ? 'green' : 'gold'}`;
    document.getElementById('checks').innerHTML = checks.map((item) => `
      <div class="check-item">
        <span class="st ${item.passed ? 'ok' : 'no'}">${item.passed ? '✓' : '!'}</span>
        <span class="ct"><b>${UI.esc(item.item)}</b><span>${UI.esc(item.detail)}</span></span>
      </div>`).join('');
  }

  async function exportDocx() {
    const btn = document.getElementById('btn-export');
    UI.setButtonBusy(btn, true, '生成中…');
    try {
      const name = `${state.model.case.case_no}_询问笔录.docx`;
      await API.download(`/api/records/${state.recordId}/document.docx`, name);
      UI.toast('DOCX 红头文书已下载，可直接用 Word 打开校对', 'ok');
    } catch (err) {
      UI.handleError(err, '文书导出失败');
    } finally {
      UI.setButtonBusy(btn, false);
    }
  }
})();
