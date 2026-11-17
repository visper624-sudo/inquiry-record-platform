/* 登录页 */
(function () {
  'use strict';

  const form = document.getElementById('login-form');
  const errorEl = document.getElementById('login-error');
  const btn = document.getElementById('btn-login');

  // 登录页不加载 nav.js，不能依赖 window.query；同时做站内跳转校验
  function safeNext() {
    const raw = new URLSearchParams(location.search).get('next') || '/index.html';
    const ok = raw.startsWith('/') && !raw.startsWith('//')
      && !raw.includes('\\') && !raw.includes('://');
    return ok ? raw : '/index.html';
  }

  // 已登录则直接进入控制台
  API.get('/api/auth/me').then(() => {
    location.replace(safeNext());
  }).catch(() => { /* 未登录，正常停留 */ });

  document.querySelectorAll('.account-row').forEach((row) => {
    row.addEventListener('click', () => {
      document.getElementById('badge').value = row.dataset.badge;
      document.getElementById('password').value = '123456';
      errorEl.textContent = '';
      document.getElementById('badge').focus();
    });
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    errorEl.textContent = '';
    const badge_no = document.getElementById('badge').value.trim();
    const password = document.getElementById('password').value;
    if (!badge_no || !password) {
      errorEl.textContent = '请输入警号与密码';
      return;
    }
    UI.setButtonBusy(btn, true, '登录中…');
    try {
      const data = await API.post('/api/auth/login', { badge_no, password });
      window.__USER__ = data.user;
      location.replace(safeNext());
    } catch (err) {
      errorEl.textContent = err.message || '登录失败';
      document.getElementById('password').select();
    } finally {
      UI.setButtonBusy(btn, false, '登 录');
    }
  });
})();
