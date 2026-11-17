/* 操作审计流水 */
(function () {
  'use strict';

  const state = { page: 1, pageSize: 15, action: '', keyword: '' };

  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    const user = await Layout.initLayout({ active: 'audit' });
    if (!user) return;
    if (!user.permissions.can_view_audit) {
      document.querySelector('main').innerHTML =
        '<div class="banner red"><span class="ico">!</span><div>当前账号无审计查询权限</div></div>';
      return;
    }
    document.getElementById('btn-search').addEventListener('click', () => {
      state.action = document.getElementById('action').value;
      state.keyword = document.getElementById('kw').value.trim();
      state.page = 1;
      load();
    });
    document.getElementById('kw').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') document.getElementById('btn-search').click();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.getElementById('action').value = '';
      document.getElementById('kw').value = '';
      state.action = ''; state.keyword = ''; state.page = 1;
      load();
    });
    await load();
  }

  async function load() {
    const tbody = document.getElementById('rows');
    tbody.innerHTML = '<tr><td colspan="7"><div class="skeleton" style="height:180px"></div></td></tr>';
    try {
      const data = await API.get('/api/audit', {
        page: state.page, page_size: state.pageSize, action: state.action, keyword: state.keyword
      });
      renderActions(data.actions);
      renderChain(data.chain);
      document.getElementById('audit-summary').textContent = `共 ${data.total} 条审计流水`;
      if (!data.items.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state">暂无审计记录</div></td></tr>';
        return;
      }
      tbody.innerHTML = data.items.map((row) => `
        <tr>
          <td class="small mono">${UI.fmtDT(row.created_at)}</td>
          <td>${UI.esc(row.user_name || '—')}<br><span class="tiny muted mono">${UI.esc(row.badge_no || '')}</span></td>
          <td><span class="tag">${UI.esc(row.action_label)}</span></td>
          <td class="mono small">${UI.esc(row.target_id || '—')}<br>
            <span class="tiny muted">${UI.esc(row.target_type)}</span></td>
          <td class="audit-detail">${UI.esc(formatDetail(row.detail))}</td>
          <td class="mono small">${UI.esc(row.ip || '—')}</td>
          <td class="hash">${UI.esc(row.hash_short)}</td>
        </tr>`).join('');
      UI.renderPager(document.getElementById('pager'), data, (page) => {
        state.page = page;
        load();
      });
    } catch (err) {
      UI.handleError(err, '审计流水加载失败');
      tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">${UI.esc(err.message)}</div></td></tr>`;
    }
  }

  function formatDetail(detail) {
    if (!detail || !Object.keys(detail).length) return '—';
    return Object.entries(detail).map(([k, v]) => {
      const text = typeof v === 'object' ? JSON.stringify(v) : String(v);
      return `${k}=${text.length > 60 ? text.slice(0, 60) + '…' : text}`;
    }).join('；');
  }

  function renderActions(actions) {
    const select = document.getElementById('action');
    if (select.options.length > 1) return;
    actions.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.key;
      option.textContent = item.name;
      select.appendChild(option);
    });
  }

  function renderChain(chain) {
    const badge = document.getElementById('chain-badge');
    const banner = document.getElementById('chain-banner');
    if (chain.ok) {
      badge.textContent = `哈希链校验通过 · ${chain.total} 条`;
      badge.className = 'tag green';
      banner.className = 'banner green';
      banner.innerHTML = `<span class="ico">✓</span><div>已校验 ${chain.total} 条审计记录，<b>哈希链完整、未被篡改</b>。每条记录均包含上一记录的摘要指纹。</div>`;
    } else {
      badge.textContent = `哈希链断裂 · ${chain.broken_at}`;
      badge.className = 'tag red';
      banner.className = 'banner red';
      banner.innerHTML = `<span class="ico">!</span><div>哈希链在记录 <b>${UI.esc(chain.broken_at)}</b> 处校验失败，请立即排查数据完整性。</div>`;
    }
  }
})();
