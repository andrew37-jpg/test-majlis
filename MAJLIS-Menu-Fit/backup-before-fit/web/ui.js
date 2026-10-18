import { NavigationLens } from './nav-lens.js?v=dom-optics-v3';
export const escapeHtml = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (char) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[char],
  );
export const money = (value) =>
  new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    maximumFractionDigits: 2,
  }).format(value / 100);
export const date = (value) =>
  value
    ? new Intl.DateTimeFormat('ru-RU', {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      }).format(new Date(value.length === 10 ? value + 'T12:00:00' : value))
    : 'Дата уточняется';
export const dateTime = (value) =>
  new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
export function rublesToKopecks(value) {
  const text = String(value).trim().replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(text))
    throw new Error('Укажите сумму с точностью до копейки.');
  const [whole, fraction = ''] = text.split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents) || cents <= 0 || cents > 1000000000000)
    throw new Error('Проверьте сумму заказа.');
  return cents;
}
export function searchParams(filters, before = null) {
  const params = new URLSearchParams({
    q: filters.q.trim(),
    bucket: filters.bucket,
  });
  if (filters.cabinet) params.set('cabinet', filters.cabinet);
  if (before) params.set('before', before);
  return params.toString();
}
const paths = {
  box: '<path d="m12 3 9 5v9l-9 5-9-5V8zM3 8l9 5 9-5M12 13v9M7.5 5.5l9 5"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 4 3c-1 .5-1 1-1 2M12 17h.01"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M18 18a8 8 0 0 1-14-6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  chat: '<path d="M21 11a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  link: '<path d="M14 3h7v7M21 3l-9 9M10 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
  moon: '<path d="M20 14A9 9 0 0 1 10 3a9 9 0 1 0 10 11Z"/>',
};
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.box}</svg>`;

/** One interruptible, non-oscillating frame loop owns geometry and interaction. */
export class LiquidNavigation {
  constructor(root, onNavigate) {
    this.root = root;
    this.onNavigate = onNavigate;
    this.itemsKey = '';
    this.current = '';
    this.motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    this.frame = 0;
    this.last = 0;
    this.holdUntil = 0;
    const reset = (force = false) => {
      const r = this.root.getBoundingClientRect();
      const key = [r.left, r.top, r.width, r.height, this.motion?.matches].join(
        ':',
      );
      if (force !== true && key === this.geometryKey) return;
      this.cancel();
      this.measure();
      this.snap();
      this.lens?.prepare();
    };
    if (this.motion?.addEventListener)
      this.motion.addEventListener('change', () => reset(true));
    else this.motion?.addListener?.(() => reset(true));
    if (typeof ResizeObserver === 'function') {
      this.resize = new ResizeObserver(() => reset());
      this.resize.observe(root);
    }
    window.addEventListener('resize', reset);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) reset(true);
    });
    new MutationObserver(() => {
      this.lens?.prepare();
      this.wake();
    }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    root.addEventListener('pointerdown', (e) => this.down(e));
    window.addEventListener('pointermove', (e) => this.move(e));
    window.addEventListener('pointerup', (e) => this.up(e));
    window.addEventListener('pointercancel', () => this.cancel());
    root.addEventListener('lostpointercapture', () => {
      if (this.gesture) this.cancel();
    });
    root.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.cancel();
    });
    root.addEventListener('click', (e) => {
      const button = e.target.closest('button[data-section]');
      if (!button) return;
      // Pointer navigation has already committed on release; keyboard/AT clicks use this path.
      if (e.detail && performance.now() < this.suppressClickUntil) {
        e.preventDefault();
        return;
      }
      this.holdUntil = performance.now() + 100;
      this.onNavigate(button.dataset.section);
      this.wake();
    });
  }
  update(sections, current) {
    const key = JSON.stringify(sections),
      rebuild = key !== this.itemsKey;
    if (rebuild) {
      this.cancel();
      this.itemsKey = key;
      this.root.innerHTML =
        '<span class="nav-glass" aria-hidden="true"><span class="nav-glass-material"></span></span>' +
        sections
          .map(
            ([id, symbol, label]) =>
              `<button type="button" data-section="${escapeHtml(id)}">${icon(symbol)}<span>${escapeHtml(label)}</span></button>`,
          )
          .join('');
      this.indicator = this.root.querySelector('.nav-glass');
      this.material = this.root.querySelector('.nav-glass-material');
      this.lens?.dispose();
      this.lens = new NavigationLens(this.root);
    }
    this.current = current;
    this.root.hidden = false;
    this.buttons = [...this.root.querySelectorAll('button[data-section]')];
    this.buttons.forEach((b) => {
      const active = b.dataset.section === current;
      b.classList.toggle('active', active);
      if (active) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    this.measure();
    if (rebuild || !this.visual) this.snap();
    else this.wake();
    if (rebuild) this.lens.prepare();
  }
  measure() {
    if (!this.buttons?.length) return;
    const rect = this.root.getBoundingClientRect();
    this.rect = rect;
    this.geometryKey = [
      rect.left,
      rect.top,
      rect.width,
      rect.height,
      this.motion?.matches,
    ].join(':');
    this.boxes = this.buttons.map((b) => {
      const r = b.getBoundingClientRect();
      return {
        x: r.left - rect.left - this.root.clientLeft,
        y: r.top - rect.top - this.root.clientTop,
        w: r.width,
        h: r.height,
      };
    });
  }
  target() {
    return this.boxes?.[
      this.buttons.findIndex((b) => b.dataset.section === this.current)
    ];
  }
  snap() {
    const target = this.target();
    if (!target) return;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.last = 0;
    this.holdUntil = 0;
    this.visual = { ...target, lift: 0, flow: 0 };
    this.paint();
  }
  nearest(x) {
    return this.boxes.reduce(
      (best, b, i) =>
        Math.abs(x - b.x - b.w / 2) <
        Math.abs(x - this.boxes[best].x - this.boxes[best].w / 2)
          ? i
          : best,
      0,
    );
  }
  down(e) {
    if (
      !e.isPrimary ||
      e.button !== 0 ||
      !e.target.closest('button[data-section]') ||
      this.gesture
    )
      return;
    this.measure();
    this.lens?.prepare();
    this.gesture = {
      id: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      x: e.clientX - this.rect.left - this.root.clientLeft,
      moved: false,
    };
    try {
      this.root.setPointerCapture?.(e.pointerId);
    } catch {
      /* Older WebViews may reject capture. */
    }
    this.wake();
  }
  move(e) {
    const g = this.gesture;
    if (!g || g.id !== e.pointerId) return;
    if (
      Math.abs(e.clientY - g.startY) > 35 &&
      Math.abs(e.clientY - g.startY) > Math.abs(e.clientX - g.startX)
    ) {
      this.cancel();
      return;
    }
    if (e.clientY < this.rect.top - 45 || e.clientY > this.rect.bottom + 45) {
      this.cancel();
      return;
    }
    g.moved ||= Math.abs(e.clientX - g.startX) > 6;
    g.x = Math.max(
      this.boxes[0].x + this.boxes[0].w / 2,
      Math.min(
        this.boxes.at(-1).x + this.boxes.at(-1).w / 2,
        e.clientX - this.rect.left - this.root.clientLeft,
      ),
    );
    this.wake();
  }
  up(e) {
    const g = this.gesture;
    if (!g || g.id !== e.pointerId) return;
    if (e.clientY < this.rect.top - 45 || e.clientY > this.rect.bottom + 45) {
      this.cancel();
      return;
    }
    // Pointerup may carry the final position even when the last move was coalesced.
    const finalX = e.clientX - this.rect.left - this.root.clientLeft;
    const index = this.nearest(finalX);
    this.gesture = null;
    this.suppressClickUntil = performance.now() + 800;
    if (this.root.hasPointerCapture?.(e.pointerId))
      this.root.releasePointerCapture(e.pointerId);
    const moved = g.moved || Math.abs(e.clientX - g.startX) > 6;
    this.holdUntil = moved ? 0 : performance.now() + 90;
    this.onNavigate(this.buttons[index].dataset.section);
    this.wake();
  }
  cancel() {
    const g = this.gesture;
    this.gesture = null;
    this.holdUntil = 0;
    if (g) {
      this.suppressClickUntil = performance.now() + 800;
      if (this.root.hasPointerCapture?.(g.id))
        this.root.releasePointerCapture(g.id);
    }
    this.wake();
  }
  wake() {
    if (!this.frame && this.visual)
      this.frame = requestAnimationFrame((t) => this.tick(t));
  }
  tick(time) {
    this.frame = 0;
    const target = this.target();
    if (!target) return;
    const reduced = !!this.motion?.matches;
    const dt = this.last ? Math.min(40, time - this.last) : 16;
    this.last = time;
    let goal = {
      ...target,
      lift: this.gesture || time < this.holdUntil ? 1 : 0,
    };
    if (this.gesture) {
      const box = this.boxes[this.nearest(this.gesture.x)];
      goal = { ...box, x: this.gesture.x - box.w / 2, lift: 1 };
    }
    if (reduced) goal = { ...target, lift: 0 };
    const previousX = this.visual.x;
    let error = 0;
    for (const key of ['x', 'y', 'w', 'h', 'lift']) {
      const speed =
        key === 'lift'
          ? goal.lift > this.visual.lift
            ? 14
            : 42
          : this.gesture
            ? 10
            : 70;
      this.visual[key] +=
        (goal[key] - this.visual[key]) *
        (reduced ? 1 : 1 - Math.exp(-dt / speed));
      error = Math.max(error, Math.abs(goal[key] - this.visual[key]));
    }
    // Reference frames become wider/flatter in travel; no spring or overshoot.
    const flow = reduced
      ? 0
      : Math.min(
          1,
          ((Math.abs(this.visual.x - previousX) / dt) * 160) / target.w,
        );
    this.visual.flow += (flow - this.visual.flow) * (1 - Math.exp(-dt / 55));
    error = Math.max(error, Math.abs(flow - this.visual.flow));
    if (error < 0.001) {
      this.visual = { ...goal, flow: 0 };
      this.last = 0;
    }
    this.paint();
    if (error >= 0.001 || this.gesture || time < this.holdUntil) this.wake();
  }
  paint() {
    if (!this.visual || !this.indicator) return;
    const { x, y, w, h, lift, flow } = this.visual;
    for (const [key, value] of Object.entries({ x, y, w, h }))
      this.root.style.setProperty('--nav-' + key, value + 'px');
    const opticallyEnabled =
      !this.motion?.matches &&
      !window.matchMedia?.('(forced-colors: active)').matches;
    this.material.style.transform = `scale(${(1 + 0.3 * lift) * (1 + 0.12 * flow * lift)},${(1 + 0.32 * lift) * (1 - 0.13 * flow * lift)})`;
    this.material.style.borderRadius = 23 + 10 * lift + 'px';
    this.root.classList.toggle('nav-interacting', lift > 0.002);
    this.lens?.draw(this.visual, opticallyEnabled);
    // The committed/preview material must never disappear on a silent renderer failure.
    this.material.style.opacity = '';
  }
}
