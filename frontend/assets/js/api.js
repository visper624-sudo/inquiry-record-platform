/* ============================================================================
   统一 API 客户端：Cookie 会话、错误规范化、401 自动跳转
   ========================================================================== */
(function () {
  'use strict';

  class ApiError extends Error {
    constructor(message, status, code, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.payload = payload;
    }
  }

  function redirectToLogin() {
    const here = location.pathname.split('/').pop() + location.search;
    if (location.pathname.endsWith('/login.html')) return;
    location.replace('/login.html?next=' + encodeURIComponent(here));
  }

  async function request(method, path, options = {}) {
    const opts = {
      method,
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
      ...options.fetchOptions
    };
    let body = options.body;
    if (options.json !== undefined) {
      opts.headers['Content-Type'] = 'application/json; charset=utf-8';
      body = JSON.stringify(options.json);
    } else if (options.form !== undefined) {
      const form = new URLSearchParams();
      Object.entries(options.form).forEach(([k, v]) => form.append(k, v == null ? '' : v));
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=utf-8';
      body = form.toString();
    } else if (options.files) {
      const fd = new FormData();
      Object.entries(options.files.fields || {}).forEach(([k, v]) => fd.append(k, v == null ? '' : v));
      options.files.list.forEach((f) => fd.append(f.field || 'file', f.file, f.name));
      body = fd;
    }
    if (body !== undefined && body !== null) opts.body = body;

    let resp;
    try {
      resp = await fetch(path, opts);
    } catch (err) {
      throw new ApiError('网络请求失败，请检查服务是否已启动', 0, 'network_error', err);
    }
    const ctype = resp.headers.get('Content-Type') || '';
    let payload = null;
    if (ctype.includes('application/json')) {
      try { payload = await resp.json(); } catch (e) { payload = null; }
    }
    if (resp.status === 401) {
      redirectToLogin();
      throw new ApiError('登录状态已失效，正在跳转登录页', 401, 'unauthorized', payload);
    }
    if (!resp.ok || (payload && payload.ok === false)) {
      const err = (payload && payload.error) || {};
      throw new ApiError(err.message || `请求失败（HTTP ${resp.status}）`, resp.status, err.code || 'error', payload);
    }
    if (!payload) return null;
    return payload.data !== undefined ? payload.data : payload;
  }

  const API = {
    ApiError,
    get: (path, params) => {
      if (params) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([k, v]) => {
          if (v !== undefined && v !== null && v !== '') query.append(k, v);
        });
        const qs = query.toString();
        path = qs ? path + (path.includes('?') ? '&' : '?') + qs : path;
      }
      return request('GET', path);
    },
    post: (path, json) => request('POST', path, { json }),
    patch: (path, json) => request('PATCH', path, { json }),
    put: (path, json) => request('PUT', path, { json }),
    del: (path) => request('DELETE', path),
    upload: (path, file, fields) => request('POST', path, { files: { fields, list: [{ field: 'file', file, name: file.name }] } }),
    async stream(path, body, onEvent, options = {}) {
      const resp = await fetch(path, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify(body || {}),
        signal: options.signal
      });
      if (resp.status === 401) {
        redirectToLogin();
        throw new ApiError('登录状态已失效，正在跳转登录页', 401, 'unauthorized');
      }
      if (!resp.ok) {
        let message = `请求失败（HTTP ${resp.status}）`;
        try {
          const data = await resp.json();
          message = data?.error?.message || message;
        } catch (e) { /* 非 JSON 响应 */ }
        throw new ApiError(message, resp.status, 'stream_error');
      }
      if (!resp.body) throw new ApiError('当前浏览器不支持流式响应', 0, 'stream_unsupported');
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      const dispatch = (raw) => {
        let event = 'message';
        const dataLines = [];
        raw.split(/\r?\n/).forEach((line) => {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^\s/, ''));
        });
        if (!dataLines.length) return;
        let data = dataLines.join('\n');
        try { data = JSON.parse(data); } catch (e) { /* 保留原始文本 */ }
        onEvent(event, data);
      };
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (raw.trim()) dispatch(raw);
        }
      }
      if (buffer.trim()) dispatch(buffer);
    },
    async download(path, filename) {
      const resp = await fetch(path, { credentials: 'same-origin' });
      if (!resp.ok) {
        let msg = '文件下载失败';
        try { const data = await resp.json(); msg = data.error?.message || msg; } catch (e) { /* ignore */ }
        throw new ApiError(msg, resp.status, 'download_failed');
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
  };

  window.API = API;
  window.ApiError = ApiError;
})();
