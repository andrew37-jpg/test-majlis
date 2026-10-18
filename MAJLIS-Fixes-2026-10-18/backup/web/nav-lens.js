/** Map viewport content into a lens sector without rasterising it or using SVG filters. */
export function sourceTransform(cx, cy, dx, dy, radial, tangent, angle = 0) {
  const nx = Math.cos(angle),
    ny = Math.sin(angle);
  const a = radial * nx * nx + tangent * ny * ny;
  const b = (radial - tangent) * nx * ny;
  const d = radial * ny * ny + tangent * nx * nx;
  return [a, b, b, d, dx - a * cx - b * cy, dy - b * cx - d * cy];
}

/** Fit the optical surface to the measured button row, independently of content zoom. */
export function menuLensGeometry(state, boxes) {
  if (!boxes?.length) return { ...state, radius: Math.min(23, state.h / 2) };
  const left = Math.min(...boxes.map((b) => b.x)),
    top = Math.min(...boxes.map((b) => b.y));
  const right = Math.max(...boxes.map((b) => b.x + b.w)),
    bottom = Math.max(...boxes.map((b) => b.y + b.h));
  const baseWidth = Math.min(state.w, right - left),
    height = Math.min(state.h, bottom - top);
  const cx = Math.max(
    left + baseWidth / 2,
    Math.min(right - baseWidth / 2, state.x + state.w / 2),
  );
  const lift = Math.max(0, Math.min(1, state.lift || 0)),
    flow = Math.max(0, Math.min(1, state.flow || 0));
  const width = Math.min(
    baseWidth * (1 + 0.075 * flow * lift),
    2 * (cx - left),
    2 * (right - cx),
  );
  // Keep the outer bounds fixed vertically. Curvature, not scale, carries motion.
  const radius = Math.min(23, height / 2);
  const bend = flow * lift;
  const direction = Math.max(-1, Math.min(1, state.direction || 0));
  const leftRadius =
    radius + (height / 2 - radius) * bend * (0.6 - 0.4 * direction);
  const rightRadius =
    radius + (height / 2 - radius) * bend * (0.6 + 0.4 * direction);
  return {
    cornerRadius:
      bend < 0.001
        ? radius + 'px'
        : `${leftRadius}px ${rightRadius}px ${rightRadius}px ${leftRadius}px`,
    x: cx - width / 2,
    y: top + (bottom - top - height) / 2,
    w: width,
    h: height,
    radius: Math.min(23, height / 2),
  };
}

/** A live DOM lens. No feImage/displacement support assumption or captured screenshots. */
export class NavigationLens {
  constructor(root) {
    this.root = root;
    this.available = true;
    this.ready = false;
    this.dirty = true;
    this.surface = document.createElement('div');
    this.surface.className = 'nav-lens';
    this.surface.hidden = true;
    this.surface.setAttribute('aria-hidden', 'true');
    this.surface.inert = true;
    this.layers = [];
    const masks =
      CSS.supports('mask-image', 'radial-gradient(black, transparent)') ||
      CSS.supports('-webkit-mask-image', 'radial-gradient(black, transparent)');
    const sectors =
      masks && CSS.supports('clip-path', 'polygon(0% 0%,100% 0%,100% 100%)')
        ? 4
        : 0;
    for (let i = -1; i < sectors; i++) {
      const slice = document.createElement('div');
      slice.className =
        i < 0 ? 'nav-lens-slice' : 'nav-lens-slice nav-lens-edge';
      const scene = document.createElement('div');
      scene.className = 'nav-lens-scene';
      slice.append(scene);
      if (i >= 0) {
        const points = ['50% 50%'];
        for (let step = 0; step <= 8; step++) {
          const angle = (i * Math.PI) / 2 - Math.PI / 4 + (step * Math.PI) / 16;
          points.push(
            `${50 + 200 * Math.cos(angle)}% ${50 + 200 * Math.sin(angle)}%`,
          );
        }
        slice.style.clipPath = `polygon(${points.join(',')})`;
      }
      this.layers.push({ slice, scene, angle: (i * Math.PI) / 2 });
      this.surface.append(slice);
    }
    const sheen = document.createElement('div');
    sheen.className = 'nav-lens-sheen';
    this.ink = document.createElement('div');
    this.ink.className = 'nav-lens-ink';
    this.inkPlane = document.createElement('div');
    this.inkPlane.className = 'nav-lens-ink-scene';
    this.ink.append(this.inkPlane);
    this.surface.append(this.ink, sheen);
    // Preserve ID-based presentation without duplicate IDs or hundreds of frozen declarations.
    const aliasRules = (rules) =>
      [...rules]
        .map((rule) => {
          if (rule.selectorText?.includes('#')) {
            const selector = rule.selectorText.replace(
              /#([\w-]+)/g,
              '[data-lens-source-id="$1"]',
            );
            return `.nav-lens :is(${selector}) {${rule.style.cssText}}`;
          }
          if (rule.cssRules) {
            const children = aliasRules(rule.cssRules);
            return children
              ? rule.cssText.slice(0, rule.cssText.indexOf('{') + 1) +
                  children +
                  '}'
              : '';
          }
          return '';
        })
        .join('\n');
    const style = document.createElement('style');
    style.textContent = [...document.styleSheets]
      .map((sheet) => {
        try {
          return aliasRules(sheet.cssRules);
        } catch {
          return '';
        }
      })
      .join('\n');
    this.surface.append(style);
    document.body.append(this.surface);
    this.observer = new MutationObserver(() => {
      this.dirty = true;
      this.scheduleSync();
    });
    this.observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['class'],
      childList: true,
    });
    for (const source of document.body.children) {
      if (
        source === root ||
        source === this.surface ||
        source.tagName === 'SCRIPT'
      )
        continue;
      this.observer.observe(source, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
      });
    }
    this.onInput = () => {
      this.dirty = true;
    };
    document.addEventListener('input', this.onInput, true);
    root.dataset.lensRenderer = 'menu-fit-v4';
  }
  scheduleSync() {
    if (this.pendingSync || !this.available) return;
    this.pendingSync = requestAnimationFrame(() => {
      this.pendingSync = 0;
      if (this.dirty) this.safeSync();
    });
  }
  prepare() {
    if (!this.available) return;
    const rect = this.root.getBoundingClientRect();
    this.boxes = [...this.root.querySelectorAll('button[data-section]')].map(
      (button) => {
        const r = button.getBoundingClientRect();
        return {
          x: r.left - rect.left - this.root.clientLeft,
          y: r.top - rect.top - this.root.clientTop,
          w: r.width,
          h: r.height,
        };
      },
    );
    this.inkBoxes = [...this.root.querySelectorAll('button[data-section]')].map(
      (button) => {
        const children = [...button.children].map((child) =>
          child.getBoundingClientRect(),
        );
        const x =
          Math.min(...children.map((r) => r.left)) -
          rect.left -
          this.root.clientLeft;
        const y =
          Math.min(...children.map((r) => r.top)) -
          rect.top -
          this.root.clientTop;
        return {
          x,
          y,
          w:
            Math.max(...children.map((r) => r.right)) -
            rect.left -
            this.root.clientLeft -
            x,
          h:
            Math.max(...children.map((r) => r.bottom)) -
            rect.top -
            this.root.clientTop -
            y,
        };
      },
    );
    this.poseKey = [rect.left, rect.top, rect.width, rect.height].join(':');
    const key = [
      this.poseKey,
      innerWidth,
      innerHeight,
      ...this.boxes.flatMap((b) => [b.x, b.y, b.w, b.h]),
      document.documentElement.dataset.theme,
    ].join(':');
    if (key !== this.sourceKey) {
      this.dirty = true;
      this.sourceKey = key;
    }
    if (this.dirty || !this.ready) this.scheduleSync();
  }
  safeSync() {
    try {
      this.syncScene();
      this.ready = true;
    } catch {
      // Keep the real material visible if the optional optical layer fails.
      this.ready = this.available = false;
      this.surface.hidden = true;
    }
  }
  syncScene() {
    const normal = document.createElement('div'),
      fixed = document.createElement('div');
    normal.className = 'nav-lens-document';
    fixed.className = 'nav-lens-fixed';
    const bodyStyle = getComputedStyle(document.body);
    for (const property of [
      'padding',
      'margin',
      'font',
      'line-height',
      'color',
      'background',
      'box-sizing',
    ])
      normal.style.setProperty(property, bodyStyle.getPropertyValue(property));
    normal.style.width = window.innerWidth + 'px';
    normal.style.minHeight = document.body.scrollHeight + 'px';
    const sanitize = (original, copy) => {
      if (
        ['SCRIPT', 'IFRAME', 'VIDEO', 'AUDIO', 'CANVAS'].includes(
          copy.tagName,
        ) ||
        original.classList?.contains('nav-lens') ||
        original.classList?.contains('nav-lens-definitions')
      ) {
        copy.remove();
        return;
      }
      // A mirror must not create duplicate IDs, control IDs or accessibility entries.
      const sourceId = original.id;
      copy.removeAttribute('id');
      copy.removeAttribute('name');
      copy.removeAttribute('autofocus');
      if (
        copy.matches(
          'a, button, input, select, textarea, [tabindex], [contenteditable]',
        )
      ) {
        copy.setAttribute('tabindex', '-1');
        copy.removeAttribute('contenteditable');
      }
      for (const attr of [...copy.attributes])
        if (attr.name.startsWith('on') || attr.name.startsWith('data-'))
          copy.removeAttribute(attr.name);
      if (sourceId) copy.setAttribute('data-lens-source-id', sourceId);
      if ('value' in original && 'value' in copy) copy.value = original.value;
      if ('checked' in original) copy.checked = original.checked;
      const originals = [...original.children],
        copies = [...copy.children];
      for (let i = 0; i < originals.length; i++)
        sanitize(originals[i], copies[i]);
      if (getComputedStyle(original).position === 'fixed') {
        const r = original.getBoundingClientRect();
        Object.assign(copy.style, {
          position: 'absolute',
          left: r.left + 'px',
          top: r.top + 'px',
          width: r.width + 'px',
          height: r.height + 'px',
          minHeight: '0',
          bottom: 'auto',
          right: 'auto',
          margin: '0',
          transform: 'none',
        });
        fixed.append(copy);
      }
    };
    for (const original of [...document.body.children]) {
      if (
        original === this.surface ||
        original.tagName === 'SCRIPT' ||
        original.tagName === 'DIALOG'
      )
        continue;
      const copy = original.cloneNode(true);
      normal.append(copy);
      sanitize(original, copy);
    }
    for (const button of [
      ...normal.querySelectorAll('.bottom-nav button'),
      ...fixed.querySelectorAll('.bottom-nav button'),
    ]) {
      const content = document.createElement('span');
      content.className = 'nav-lens-content';
      content.append(...button.childNodes);
      button.append(content);
    }
    for (const [index, layer] of this.layers.entries()) {
      const content = index ? normal.cloneNode(true) : normal;
      const pinned = index ? fixed.cloneNode(true) : fixed;
      layer.scene.replaceChildren(content, pinned);
      layer.normal = content;
      layer.nav =
        pinned.querySelector('.bottom-nav') ||
        content.querySelector('.bottom-nav');
      if (!layer.nav || !layer.nav.querySelector('button'))
        throw new Error('Missing optical source');
      for (const material of layer.scene.querySelectorAll(
        '.nav-glass-material',
      )) {
        material.style.opacity = '1';
        material.style.transform = 'none';
        material.style.borderRadius = '23px';
      }
    }
    this.inkNav = this.layers[0].nav.cloneNode(true);
    this.inkPlane.replaceChildren(this.inkNav);
    this.inkContents = [...this.inkNav.querySelectorAll('.nav-lens-content')];
    this.dirty = false;
  }
  draw(state, enabled) {
    const { lift } = state;
    const rect = this.root.getBoundingClientRect();
    const poseChanged =
      [rect.left, rect.top, rect.width, rect.height].join(':') !== this.poseKey;
    if (poseChanged) this.prepare();
    this.surface.hidden = !(
      enabled &&
      this.available &&
      this.boxes?.length &&
      lift > 0.002 &&
      rect.width > 0 &&
      rect.height > 0
    );
    if (this.surface.hidden) return;
    if (this.dirty) this.safeSync();
    if (!this.ready) {
      this.surface.hidden = true;
      return;
    }
    const glass = poseChanged
      ? menuLensGeometry(state, this.boxes)
      : state.glass || menuLensGeometry(state, this.boxes);
    const { x, y, w: width, h: height, radius } = glass;
    const left = rect.left + this.root.clientLeft + x,
      top = rect.top + this.root.clientTop + y;
    const cx = left + width / 2,
      cy = top + height / 2;
    Object.assign(this.surface.style, {
      left: left + 'px',
      top: top + 'px',
      width: width + 'px',
      height: height + 'px',
      borderRadius: glass.cornerRadius || radius + 'px',
      opacity: String(Math.min(1, lift * 4)),
    });
    // This transparent layer contains each real label/icon once. Only fully contained ink grows.
    this.inkPlane.style.transform = `translate(${-left}px,${-top}px)`;
    this.inkContents.forEach((content, index) => {
      const box = this.inkBoxes[index];
      if (!box) return;
      const clearance = Math.min(
        box.x - x,
        x + width - box.x - box.w,
        box.y - y,
        y + height - box.y - box.h,
      );
      const t = Math.max(0, Math.min(1, clearance / 6)),
        weight = t * t * (3 - 2 * t);
      const maxZoom = Math.max(
        1,
        Math.min(
          1.18,
          (height - 16) / box.h,
          (this.boxes[index].w - 12) / box.w,
        ),
      );
      content.style.transform = `scale(${1 + (maxZoom - 1) * Math.max(0, (lift - 0.25) / 0.75) * weight})`;
    });
    for (const [index, layer] of this.layers.entries()) {
      let matrix;
      if (!index) matrix = sourceTransform(cx, cy, width / 2, height / 2, 1, 1);
      else {
        const angle = layer.angle,
          dx = 0.48 * width * Math.cos(angle),
          dy = 0.48 * height * Math.sin(angle);
        matrix = sourceTransform(
          cx + dx,
          cy + dy,
          width / 2 + dx,
          height / 2 + dy,
          -1.15,
          1.12,
          angle,
        );
        layer.slice.style.opacity = String(
          Math.max(0, (lift - 0.35) / 0.65) * 0.88,
        );
      }
      Object.assign(layer.scene.style, {
        width: innerWidth + 'px',
        height: innerHeight + 'px',
        transform: `matrix(${matrix.join(',')})`,
      });
      layer.normal.style.transform = `translateY(${-window.scrollY}px)`;
      for (const [key, value] of Object.entries({ x, y, w: width, h: height }))
        layer.nav.style.setProperty('--nav-' + key, value + 'px');
    }
  }
  dispose() {
    cancelAnimationFrame(this.pendingSync);
    this.observer.disconnect();
    document.removeEventListener('input', this.onInput, true);
    this.surface.remove();
  }
}
