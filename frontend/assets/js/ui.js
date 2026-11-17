/* ============================================================================
   通用 UI 组件：Toast / Modal / Drawer / 确认框 / 分页 / 格式化
   ========================================================================== */
(function () {
  'use strict';

  const esc = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const fmtDT = (iso) => {
    if (!iso) return '—';
    const d = new Date(String(iso).replace(' ', 'T'));
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  const fmtDate = (iso) => (fmtDT(iso) || '').slice(0, 10);

  const fmtDuration = (seconds) => {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const p = (n) => String(n).padStart(2, '0');
    return h > 0 ? `${p(h)}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
  };

  const durationText = (seconds) => {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    if (total >= 3600) return `${Math.floor(total / 3600)} 小时 ${Math.floor((total % 3600) / 60)} 分钟`;
    if (total >= 60) return `${Math.floor(total / 60)} 分钟`;
    return `${total} 秒`;
  };

  const debounce = (fn, wait = 300) => {
    let timer = null;
    const wrapped = (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
    wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
    return wrapped;
  };

  /* -------------------------------------------------------------- Toast */
  function ensureToastWrap() {
    let wrap = document.querySelector('.toast-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'toast-wrap';
      document.body.appendChild(wrap);
    }
    return wrap;
  }

  function toast(message, type = 'info', timeout = 3200) {
    const wrap = ensureToastWrap();
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span>${type === 'ok' ? '✓' : type === 'err' ? '!' : type === 'warn' ? '△' : 'i'}</span><span>${esc(message)}</span>`;
    wrap.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .25s, transform .25s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(12px)';
      setTimeout(() => el.remove(), 260);
    }, timeout);
    return el;
  }

  /* -------------------------------------------------------------- Modal */
  function openModal(options = {}) {
    const {
      title = '', body = '', size = '', footer = [], onMount = null, closable = true
    } = options;
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal ${size}" role="dialog" aria-modal="true">
        <div class="modal-head">
          <h3>${esc(title)}</h3>
          ${closable ? '<button class="close" data-close aria-label="关闭">×</button>' : ''}
        </div>
        <div class="modal-body"></div>
        <div class="modal-foot"></div>
      </div>`;
    const bodyEl = overlay.querySelector('.modal-body');
    if (typeof body === 'string') bodyEl.innerHTML = body;
    else bodyEl.appendChild(body);
    const footEl = overlay.querySelector('.modal-foot');
    const api = { overlay, el: overlay.querySelector('.modal'), bodyEl, footEl, close };

    footer.forEach((btn) => {
      const el = document.createElement('button');
      el.className = `btn ${btn.kind || ''}`;
      el.textContent = btn.text;
      el.addEventListener('click', async () => {
        if (!btn.onClick) return close();
        el.disabled = true;
        try {
          const result = await btn.onClick(api);
          if (result !== false && btn.autoClose !== false) close();
        } finally {
          el.disabled = false;
        }
      });
      footEl.appendChild(el);
    });
    if (!footer.length) footEl.style.display = 'none';

    function close() {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      if (typeof options.onClose === 'function') options.onClose();
    }
    function onKey(ev) {
      if (ev.key === 'Escape' && closable) close();
    }
    document.addEventListener('keydown', onKey);
    overlay.addEventListener('mousedown', (ev) => {
      if (ev.target === overlay && closable) close();
    });
    overlay.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
    document.body.appendChild(overlay);
    if (typeof onMount === 'function') onMount(api);
    return api;
  }

  function confirmDialog(message, options = {}) {
    return new Promise((resolve) => {
      let settled = false;
      const modal = openModal({
        title: options.title || '操作确认',
        size: 'narrow',
        body: `<div class="banner ${options.danger ? 'red' : ''}"><span class="ico">${options.danger ? '!' : 'i'}</span><div>${esc(message)}</div></div>`,
        footer: [
          { text: '取消', onClick: () => { settled = true; resolve(false); } },
          {
            text: options.confirmText || '确认',
            kind: options.danger ? 'btn-danger' : 'btn-primary',
            onClick: () => { settled = true; resolve(true); }
          }
        ],
        onClose: () => { if (!settled) resolve(false); }
      });
      return modal;
    });
  }

  /* -------------------------------------------------------------- Drawer */
  function openDrawer(options = {}) {
    const { title = '', body = '', footer = [], onMount = null, width = 420 } = options;
    const overlay = document.createElement('div');
    overlay.className = 'drawer-overlay';
    const drawer = document.createElement('aside');
    drawer.className = 'drawer';
    drawer.style.width = width + 'px';
    drawer.innerHTML = `
      <div class="drawer-head">
        <h3 style="font-size:14px">${esc(title)}</h3>
        <button class="close btn-text" data-close aria-label="关闭" style="font-size:20px;line-height:1">×</button>
      </div>
      <div class="drawer-body"></div>
      <div class="drawer-foot"></div>`;
    const bodyEl = drawer.querySelector('.drawer-body');
    if (typeof body === 'string') bodyEl.innerHTML = body;
    else bodyEl.appendChild(body);
    document.body.appendChild(overlay);
    document.body.appendChild(drawer);
    const footEl = drawer.querySelector('.drawer-foot');
    footer.forEach((btn) => {
      const el = document.createElement('button');
      el.className = `btn ${btn.kind || ''}`;
      el.textContent = btn.text;
      el.style.marginLeft = '6px';
      el.addEventListener('click', () => btn.onClick && btn.onClick(api));
      footEl.appendChild(el);
    });
    if (!footer.length) footEl.style.display = 'none';
    const api = { drawer, bodyEl, footEl, close };
    function close() {
      overlay.remove();
      drawer.remove();
      document.removeEventListener('keydown', onKey);
      if (typeof options.onClose === 'function') options.onClose();
    }
    function onKey(ev) { if (ev.key === 'Escape') close(); }
    overlay.addEventListener('click', close);
    drawer.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
    document.addEventListener('keydown', onKey);
    if (typeof onMount === 'function') onMount(api);
    return api;
  }

  /* -------------------------------------------------------------- 分页 */
  function renderPager(container, { page, pages, total, pageSize }, onGo) {
    if (!container) return;
    const btn = (label, target, disabled, active) =>
      `<button class="page-btn ${active ? 'active' : ''}" ${disabled ? 'disabled' : ''} data-page="${target}">${label}</button>`;
    const parts = [];
    parts.push(`<span>共 <b>${total}</b> 条 · 第 ${page}/${pages} 页</span>`);
    parts.push(btn('‹', page - 1, page <= 1));
    const start = Math.max(1, page - 2);
    const end = Math.min(pages, start + 4);
    for (let i = start; i <= end; i += 1) parts.push(btn(i, i, false, i === page));
    parts.push(btn('›', page + 1, page >= pages));
    container.innerHTML = parts.join('');
    container.querySelectorAll('[data-page]').forEach((el) => {
      el.addEventListener('click', () => {
        const target = Number(el.dataset.page);
        if (target >= 1 && target <= pages && target !== page) onGo(target);
      });
    });
  }

  /* -------------------------------------------------------------- 其他 */
  const statusPill = (text, tone = 'gray') => `<span class="pill pill-${tone}">${esc(text)}</span>`;

  const stageTone = (stage) => ({
    intake: 'gray', templates: 'purple', inquiry: 'blue', completed: 'green'
  }[stage] || 'gray');

  const flowTone = (status) => ({
    collected: 'green', needs_supplement: 'amber', key_gap: 'red', not_involved: 'gray'
  }[status] || 'gray');

  function scoreRing(score, size = 60) {
    const radius = (size - 8) / 2;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - Math.min(100, Math.max(0, score)) / 100);
    const color = score >= 70 ? '#0f8a4d' : score >= 45 ? '#b7791f' : '#6b7280';
    return `<div class="score-ring" style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}">
        <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" fill="none" stroke="#eef1f7" stroke-width="6"></circle>
        <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" fill="none" stroke="${color}" stroke-width="6"
          stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"></circle>
      </svg>
      <div class="num">${Math.round(score)}<small>%</small></div>
    </div>`;
  }

  function radarSvg(flows, size = 210) {
    const cx = size / 2;
    const cy = size / 2 + 4;
    const radius = size * 0.33;
    const n = flows.length || 5;
    const point = (idx, ratio) => {
      const angle = -Math.PI / 2 + (idx * 2 * Math.PI) / n;
      return [cx + Math.cos(angle) * radius * ratio, cy + Math.sin(angle) * radius * ratio];
    };
    const rings = [0.25, 0.5, 0.75, 1].map((ratio) => {
      const pts = Array.from({ length: n }, (_, i) => point(i, ratio).join(',')).join(' ');
      return `<polygon points="${pts}" fill="none" stroke="#e6ebf3" stroke-width="1"></polygon>`;
    }).join('');
    const axes = Array.from({ length: n }, (_, i) => {
      const [x, y] = point(i, 1);
      return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#e6ebf3" stroke-width="1"></line>`;
    }).join('');
    const values = flows.map((f) => Math.max(6, Math.min(100, Number(f.radar_value) || 0)) / 100);
    const poly = flows.map((f, i) => point(i, values[i]).join(',')).join(' ');
    const dots = flows.map((f, i) => {
      const [x, y] = point(i, values[i]);
      const color = f.status === 'key_gap' ? '#c0392b' : f.status === 'needs_supplement' ? '#b7791f'
        : f.status === 'not_involved' ? '#9aa3b2' : '#1f5299';
      return `<circle cx="${x}" cy="${y}" r="3.4" fill="${color}"></circle>`;
    }).join('');
    const labels = flows.map((f, i) => {
      const [x, y] = point(i, 1.24);
      const color = f.status === 'key_gap' ? '#c0392b' : '#4b5563';
      return `<text x="${x}" y="${y + 3}" text-anchor="middle" font-size="10.5" fill="${color}"
        font-family="Microsoft YaHei">${esc(f.name)}</text>`;
    }).join('');
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      ${rings}${axes}
      <polygon points="${poly}" fill="rgba(31,82,153,.14)" stroke="#1f5299" stroke-width="1.6"></polygon>
      ${dots}${labels}
    </svg>`;
  }

  function setButtonBusy(button, busy, text) {
    if (!button) return;
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.disabled = true;
      if (text) button.textContent = text;
    } else {
      button.disabled = false;
      if (text) button.textContent = text;
      else if (button.dataset.originalText) button.textContent = button.dataset.originalText;
    }
  }

  function handleError(err, fallback = '操作失败') {
    if (err && err.status === 401) return;
    const message = (err && err.message) || fallback;
    toast(message, 'err', 4200);
    if (window.console) console.warn('[api]', err);
  }

  /* -------------------------------------------------------------- 浮动气泡 */
  let floatPop = null;
  function ensureFloatPop() {
    if (!floatPop) {
      floatPop = document.createElement('div');
      floatPop.className = 'float-pop';
      document.body.appendChild(floatPop);
    }
    return floatPop;
  }
  function showPop(anchor) {
    const tpl = anchor.querySelector('.pop');
    if (!tpl) return;
    const pop = ensureFloatPop();
    pop.innerHTML = tpl.innerHTML;
    pop.style.display = 'block';
    const rect = anchor.getBoundingClientRect();
    const width = 430;
    let left = Math.min(window.innerWidth - width - 20, Math.max(16, rect.left));
    pop.style.width = width + 'px';
    pop.style.left = left + 'px';
    pop.style.top = Math.min(window.innerHeight - 40, rect.bottom + 10) + 'px';
  }
  function hidePop() {
    if (floatPop) floatPop.style.display = 'none';
  }
  document.addEventListener('mouseover', (ev) => {
    const anchor = ev.target.closest && ev.target.closest('.brief-hover');
    if (anchor) showPop(anchor);
  });
  document.addEventListener('mouseout', (ev) => {
    const anchor = ev.target.closest && ev.target.closest('.brief-hover');
    if (anchor && !anchor.contains(ev.relatedTarget)) hidePop();
  });
  document.addEventListener('scroll', hidePop, true);

  window.UI = {
    esc, fmtDT, fmtDate, fmtDuration, durationText, debounce,
    toast, modal: openModal, confirm: confirmDialog, drawer: openDrawer,
    renderPager, statusPill, stageTone, flowTone, scoreRing, radarSvg, setButtonBusy, handleError,
    hidePop
  };
})();
