/* 系统配置页 */
(function () {
  'use strict';

  const state = { user: null, fiveFlow: null, templates: [], llm: null, canManage: false };

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    state.user = await Layout.initLayout({ active: 'settings' });
    if (!state.user) return;
    state.canManage = Boolean(state.user.permissions.can_manage_settings);
    const badge = document.getElementById('perm-badge');
    badge.textContent = state.canManage ? '管理员 · 可读写' : '只读浏览';
    badge.className = `tag ${state.canManage ? 'green' : 'gold'}`;
    if (!state.canManage) {
      document.getElementById('btn-save-flow').disabled = true;
      document.getElementById('btn-reset-flow').disabled = true;
      document.getElementById('btn-save-llm').disabled = true;
      document.getElementById('btn-test-llm').disabled = true;
    }
    bindTabs();
    bindActions();
    await Promise.all([loadFiveFlow(), loadTemplates(), loadLlm(), loadSystem()]);
    const tab = query('tab');
    if (tab) {
      const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (btn) btn.click();
    }
    if (query('test') === '1' && state.canManage) {
      setTimeout(() => testLlm(), 400);
    }
  }

  function bindTabs() {
    document.getElementById('tabs').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.tab-btn');
      if (!btn) return;
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));
      document.getElementById(`pane-${btn.dataset.tab}`).classList.add('active');
    });
  }

  function bindActions() {
    document.getElementById('btn-save-flow').addEventListener('click', saveFiveFlow);
    document.getElementById('btn-reset-flow').addEventListener('click', resetFiveFlow);
    document.getElementById('btn-save-llm').addEventListener('click', saveLlm);
    document.getElementById('btn-test-llm').addEventListener('click', testLlm);
    document.getElementById('btn-clear-key').addEventListener('click', clearKey);
    document.getElementById('tpl-rows').addEventListener('click', onTemplateAction);
  }

  /* ------------------------------------------------------------ 五流口径 */
  async function loadFiveFlow() {
    try {
      const data = await API.get('/api/settings/five-flow');
      state.fiveFlow = data.config;
      renderFiveFlow(data.overridden);
    } catch (err) {
      UI.handleError(err, '五流口径加载失败');
    }
  }

  function renderFiveFlow(overridden) {
    const host = document.getElementById('flow-config');
    host.innerHTML = `<div class="tiny muted" style="margin-bottom:10px">
      数据来源：${overridden ? '<b>系统内自定义口径（已覆盖 JSON 默认值）</b>' : 'data/five_flow_fields.json 默认口径'}
    </div>` + state.fiveFlow.flows.map((flow) => `
      <div class="card" style="margin-bottom:12px; border:1px solid var(--line); box-shadow:none">
        <div class="card-head" style="background:#fbfcfe">
          <h3>${UI.esc(flow.name)}
            <span class="tag">优先级 ${flow.priority}</span>
            <span class="tiny muted">${UI.esc(flow.description)}</span>
          </h3>
        </div>
        <div class="card-body" style="padding:6px 18px">
          <div class="field-table">
            <div class="field-line head">
              <span class="fl">要素名称</span><span>必填</span><span>关键要素</span>
              <span>抽取方式</span><span>提示</span>
            </div>
            ${flow.fields.map((field) => `
              <div class="field-line" data-flow="${UI.esc(flow.key)}" data-field="${UI.esc(field.key)}">
                <span class="fl"><b>${UI.esc(field.label)}</b>
                  <span class="mono">${UI.esc(field.key)}</span></span>
                <span>
                  <label class="switch"><input type="checkbox" class="f-required" ${field.required ? 'checked' : ''}
                    ${state.canManage ? '' : 'disabled'}><span></span></label>
                </span>
                <span>
                  <label class="switch"><input type="checkbox" class="f-key" ${field.key_item ? 'checked' : ''}
                    ${state.canManage ? '' : 'disabled'}><span></span></label>
                </span>
                <span class="tiny muted">${(field.patterns || []).length ? '正则 ' + field.patterns.length + ' 条' : '语义提取'}</span>
                <span class="tiny muted">${UI.esc(field.hint || '')}</span>
              </div>`).join('')}
          </div>
        </div>
      </div>`).join('');
  }

  async function saveFiveFlow() {
    if (!state.canManage) return;
    const config = JSON.parse(JSON.stringify(state.fiveFlow));
    document.querySelectorAll('.field-line[data-field]').forEach((row) => {
      const flowKey = row.dataset.flow;
      const fieldKey = row.dataset.field;
      const flow = config.flows.find((f) => f.key === flowKey);
      const field = flow && flow.fields.find((f) => f.key === fieldKey);
      if (!field) return;
      field.required = row.querySelector('.f-required').checked;
      field.key_item = row.querySelector('.f-key').checked;
    });
    try {
      const data = await API.put('/api/settings/five-flow', config);
      state.fiveFlow = data.config;
      UI.toast('五流口径已保存，后续录入将按新规则判定缺口', 'ok');
    } catch (err) {
      UI.handleError(err, '保存失败');
    }
  }

  async function resetFiveFlow() {
    const ok = await UI.confirm('恢复默认五流口径将覆盖当前自定义配置，确认继续？', { danger: true });
    if (!ok) return;
    try {
      const data = await API.post('/api/settings/five-flow/reset', {});
      state.fiveFlow = data.config;
      renderFiveFlow(false);
      UI.toast('已恢复默认口径', 'ok');
    } catch (err) {
      UI.handleError(err, '恢复失败');
    }
  }

  /* ------------------------------------------------------------ 模板库 */
  async function loadTemplates() {
    try {
      const data = await API.get('/api/settings/templates');
      state.templates = data.items;
      document.getElementById('tpl-meta').textContent =
        `共 ${data.items.length} 个模板 · 合计 ${data.items.reduce((n, t) => n + t.question_count, 0)} 条问题`;
      document.getElementById('tpl-rows').innerHTML = data.items.map((t) => `
        <tr data-id="${UI.esc(t.template_id)}">
          <td class="mono">${UI.esc(t.template_id)}</td>
          <td><b>${UI.esc(t.name)}</b>${t.is_general ? ' <span class="tag gold">通用</span>' : ''}</td>
          <td><span class="tag">${UI.esc(t.category)}</span></td>
          <td>${t.question_count}</td>
          <td>${(t.signals || []).slice(0, 10).map((s) => `<span class="chip" style="margin:1px">${UI.esc(s)}</span>`).join('')}
            ${t.signals.length > 10 ? `<span class="tiny muted">+${t.signals.length - 10}</span>` : ''}</td>
          <td><button class="btn btn-sm" data-act="edit-tpl" ${state.canManage ? '' : 'disabled'}>编辑</button></td>
        </tr>`).join('');
    } catch (err) {
      UI.handleError(err, '模板库加载失败');
    }
  }

  function onTemplateAction(ev) {
    const btn = ev.target.closest('[data-act="edit-tpl"]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const tpl = state.templates.find((t) => t.template_id === tr.dataset.id);
    if (!tpl) return;
    UI.modal({
      title: `编辑模板特征：${tpl.name}`,
      body: `
        <div class="banner" style="margin-bottom:14px">
          <span class="ico">i</span>
          <div>特征信号词直接参与模板推荐打分（权重 60%），修改后立即生效。每行一个或使用顿号 / 逗号分隔。</div>
        </div>
        <div class="field"><label>特征信号词（8~14 个）</label>
          <textarea class="textarea" id="e-signals" rows="5">${UI.esc((tpl.signals || []).join('\n'))}</textarea>
        </div>
        <div class="field"><label>手法关键词（3~6 个）</label>
          <textarea class="textarea" id="e-keywords" rows="3">${UI.esc((tpl.keywords || []).join('\n'))}</textarea>
        </div>`,
      footer: [
        { text: '取消' },
        {
          text: '保存', kind: 'btn-primary', autoClose: false,
          onClick: async (api) => {
            const signals = document.getElementById('e-signals').value.split(/[\n,，、]/).map((s) => s.trim()).filter(Boolean);
            const keywords = document.getElementById('e-keywords').value.split(/[\n,，、]/).map((s) => s.trim()).filter(Boolean);
            try {
              await API.patch(`/api/settings/templates/${tpl.template_id}`, { signals, keywords });
              api.close();
              UI.toast('模板特征已更新', 'ok');
              await loadTemplates();
            } catch (err) {
              UI.handleError(err, '保存失败');
            }
            return false;
          }
        }
      ]
    });
  }

  /* ------------------------------------------------------------ 大模型 */
  async function loadLlm() {
    try {
      const data = await API.get('/api/settings/llm');
      state.llm = data.config;
      document.getElementById('llm-provider').value = data.config.provider;
      document.getElementById('llm-model').value = data.config.model;
      document.getElementById('llm-base').value = data.config.base_url;
      document.getElementById('llm-temp').value = data.config.temperature;
      document.getElementById('llm-maxtokens').value = data.config.max_tokens;
      document.getElementById('llm-timeout').value = data.config.timeout_sec;
      document.getElementById('llm-enabled').checked = Boolean(data.config.enabled);
      const engine = document.getElementById('llm-engine');
      engine.textContent = data.engine_label;
      engine.className = `tag ${data.active_engine === 'llm' ? 'green' : ''}`;
      const keyState = document.getElementById('llm-key-state');
      keyState.innerHTML = data.key_configured
        ? `密钥状态：<b style="color:var(--green-600)">已配置</b>（来源：<span class="mono">${UI.esc(data.key_source)}</span>）`
        : `密钥状态：<b style="color:var(--amber-600)">未配置</b>（在下方填写，或设置环境变量 PRACTICE_LLM_API_KEY / DEEPSEEK_API_KEY）`;
      document.getElementById('llm-result').innerHTML =
        `<div class="banner ${data.active_engine === 'llm' ? 'green' : 'warn'}">
          <span class="ico">${data.active_engine === 'llm' ? '✓' : '△'}</span>
          <div>${UI.esc(data.note)}</div></div>`;
    } catch (err) {
      UI.handleError(err, '模型参数加载失败');
    }
  }

  async function saveLlm() {
    const payload = {
      provider: document.getElementById('llm-provider').value,
      model: document.getElementById('llm-model').value.trim(),
      base_url: document.getElementById('llm-base').value.trim(),
      temperature: Number(document.getElementById('llm-temp').value || 0.2),
      max_tokens: Number(document.getElementById('llm-maxtokens').value || 900),
      timeout_sec: Number(document.getElementById('llm-timeout').value || 40),
      enabled: document.getElementById('llm-enabled').checked
    };
    const keyInput = document.getElementById('llm-key');
    if (keyInput.value.trim()) payload.api_key = keyInput.value.trim();
    try {
      const data = await API.put('/api/settings/llm', payload);
      keyInput.value = '';
      document.getElementById('llm-engine').textContent = data.status.engine_label;
      document.getElementById('llm-engine').className = `tag ${data.status.active_engine === 'llm' ? 'green' : ''}`;
      await loadLlm();
      UI.toast('模型参数已保存' + (payload.api_key ? '，密钥已写入用户目录' : ''), 'ok');
    } catch (err) {
      UI.handleError(err, '保存失败');
    }
  }

  async function clearKey() {
    const ok = await UI.confirm('清除本机保存的大模型密钥？清除后研判将回退规则引擎。', { danger: true });
    if (!ok) return;
    try {
      await API.put('/api/settings/llm', { clear_key: true });
      await loadLlm();
      UI.toast('密钥已清除', 'ok');
    } catch (err) {
      UI.handleError(err, '清除失败');
    }
  }

  async function testLlm() {
    const box = document.getElementById('llm-result');
    box.innerHTML = '<div class="skeleton" style="height:40px"></div>';
    try {
      const data = await API.post('/api/settings/llm/test', {});
      if (data.reachable) {
        box.innerHTML = `<div class="banner green">
          <span class="ico">✓</span>
          <div><b>连通成功</b>：${UI.esc(data.model || '')} · 延迟 ${UI.esc(data.latency_ms)} ms
          ${data.tokens ? `· ${data.tokens} tokens` : ''}<br>
          <span class="tiny">模型返回：${UI.esc(data.sample || '')}</span></div></div>`;
      } else {
        box.innerHTML = `<div class="banner warn"><span class="ico">△</span>
          <div>${UI.esc(data.note || '未启用大模型，当前使用规则引擎')}</div></div>`;
      }
    } catch (err) {
      box.innerHTML = `<div class="banner red"><span class="ico">!</span><div>${UI.esc(err.message)}</div></div>`;
    }
  }

  /* ------------------------------------------------------------ 系统信息 */
  async function loadSystem() {
    try {
      const data = await API.get('/api/settings/system');
      const counts = data.counts;
      document.getElementById('sys-cards').innerHTML = [
        ['笔录总数', counts.records, '含已完成与进行中'],
        ['问答条目', counts.qa_items, '已删除条目不计'],
        ['模板数量', counts.templates, '19 类 + 通用'],
        ['材料文件', counts.materials, '与正文物理隔离']
      ].map(([label, value, foot]) => `
        <div class="stat-card">
          <div class="stat-label">${label}</div>
          <div class="stat-value">${value}</div>
          <div class="stat-foot">${foot}</div>
        </div>`).join('');
      document.getElementById('sys-version').textContent = `v${data.version}`;
      document.getElementById('sys-info').innerHTML = `
        <div class="field-row"><span class="fl">数据库文件</span><span class="mono tiny">${UI.esc(data.db_path)}</span></div>
        <div class="field-row"><span class="fl">研判引擎</span><span class="tag">${UI.esc(data.engine)}</span></div>
        <div class="field-row"><span class="fl">审计哈希链</span>
          <span class="tag ${data.chain.ok ? 'green' : 'red'}">${data.chain.ok ? '完整' : '断裂'} · ${data.chain.total} 条</span></div>
        <div class="field-row"><span class="fl">账号数量</span><span>${counts.users}</span></div>
        <div class="field-row"><span class="fl">导入任务</span><span>${counts.imports}</span></div>`;
      document.getElementById('sys-roles').innerHTML = Object.entries(data.roles).map(([key, name]) => `
        <div class="field-row"><span class="fl"><b>${UI.esc(name)}</b><span class="tiny muted mono">${UI.esc(key)}</span></span>
          <span class="tiny muted">${{ police: '创建笔录、执行问询、采纳 AI 建议', analyst: '只读复核五流与研判结论', admin: '账号、模板、口径与系统配置' }[key] || ''}</span>
        </div>`).join('');
    } catch (err) {
      UI.handleError(err, '系统信息加载失败');
    }
  }
})();
