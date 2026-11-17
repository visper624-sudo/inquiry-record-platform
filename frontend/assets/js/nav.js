/* ============================================================================
   全局布局：顶栏、导航与登录态守卫
   ========================================================================== */
(function () {
  'use strict';

  const NAV = [
    { key: 'ledger', label: '笔录台账', href: '/index.html' },
    { key: 'import', label: '历史笔录导入', href: '/import.html' },
    { key: 'audit', label: '审计流水', href: '/audit.html', perm: 'can_view_audit' },
    { key: 'settings', label: '系统配置', href: '/settings.html', perm: 'can_manage_settings' }
  ];

  function avatarText(name) {
    return (name || '警').slice(-2, -1) || '警';
  }

  async function initLayout(options = {}) {
    const active = options.active || '';
    let user = window.__USER__;
    if (!user) {
      try {
        const data = await API.get('/api/auth/me');
        user = data.user;
        window.__USER__ = user;
      } catch (err) {
        if (err.status !== 401) UI.toast('无法获取登录信息：' + err.message, 'err');
        return null;
      }
    }
    const host = document.getElementById('app-header');
    if (host) {
      const navHtml = NAV.filter((item) => !item.perm || user.permissions[item.perm])
        .map((item) => `<a href="${item.href}" class="${item.key === active ? 'active' : ''}">${item.label}</a>`)
        .join('');
      host.innerHTML = `
        <header class="app-header">
          <div class="brand">
            <div class="brand-mark">警</div>
            <div class="brand-text">
              <b>智能问询笔录平台</b>
              <span>Anti-Fraud Inquiry Console</span>
            </div>
          </div>
          <nav class="app-nav">${navHtml}</nav>
          <div class="user-area">
            <div class="user-chip">
              <div class="user-avatar">${UI.esc(avatarText(user.name))}</div>
              <div>
                <div class="u-name">${UI.esc(user.name)} · ${UI.esc(user.role_name)}</div>
                <div class="u-org">${UI.esc(user.org_name)} · 警号 ${UI.esc(user.badge_no)}</div>
              </div>
            </div>
            <button class="btn-ghost-light" id="btn-logout">退出</button>
          </div>
        </header>`;
      host.querySelector('#btn-logout').addEventListener('click', async () => {
        const ok = await UI.confirm('确认退出当前账号？', { title: '退出登录' });
        if (!ok) return;
        try { await API.post('/api/auth/logout', {}); } catch (err) { /* 忽略 */ }
        location.replace('/login.html');
      });
    }
    return user;
  }

  function query(name, fallback = '') {
    return new URLSearchParams(location.search).get(name) || fallback;
  }

  window.Layout = { initLayout, NAV };
  window.query = query;
})();
