/** Map viewport content into a lens sector without rasterising it or using SVG filters. */
export function sourceTransform(cx, cy, dx, dy, radial, tangent, angle = 0) {
  const nx = Math.cos(angle),
    ny = Math.sin(angle);
  const a = radial * nx * nx + tangent * ny * ny;
  const b = (radial - tangent) * nx * ny;
  const d = radial * ny * ny + tangent * nx * nx;
  return [a, b, b, d, dx - a * cx - b * cy, dy - b * cx - d * cy];
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
    this.surface.append(sheen);
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
    root.dataset.lensRenderer = 'dom-optics-v3';
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
    const button = this.root.querySelector('button[data-section]');
    if (button) {
      this.baseWidth = button.getBoundingClientRect().width * 1.46;
      this.baseHeight = this.baseWidth / 1.22;
    }
    const key = [
      innerWidth,
      innerHeight,
      this.baseWidth,
      this.baseHeight,
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
    this.dirty = false;
  }
  draw({ x, y, w, h, lift, flow = 0 }, enabled) {
    const rect = this.root.getBoundingClientRect();
    this.surface.hidden = !(
      enabled &&
      this.available &&
      this.baseWidth &&
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
    const width = (w + (this.baseWidth - w) * lift) * (1 + 0.18 * flow * lift);
    const height = (h + (this.baseHeight - h) * lift) * (1 - 0.2 * flow * lift);
    const cx = Math.max(
      width / 2 + 2,
      Math.min(
        innerWidth - width / 2 - 2,
        rect.left + this.root.clientLeft + x + w / 2,
      ),
    );
    const cy = Math.max(
      height / 2 + 2,
      Math.min(
        innerHeight - height / 2 - 2,
        rect.top + this.root.clientTop + y + h / 2,
      ),
    );
    const radius = 23 + (Math.min(width, height) / 2 - 23) * lift;
    Object.assign(this.surface.style, {
      left: cx - width / 2 + 'px',
      top: cy - height / 2 + 'px',
      width: width + 'px',
      height: height + 'px',
      borderRadius: radius + 'px',
      opacity: String(Math.min(1, lift * 4)),
    });
    const zoom = 1 + 0.46 * lift;
    for (const [index, layer] of this.layers.entries()) {
      let matrix;
      if (!index)
        matrix = sourceTransform(cx, cy, width / 2, height / 2, zoom, zoom);
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
      for (const [key, value] of Object.entries({ x, y, w, h }))
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
