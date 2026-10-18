const SVG = 'http://www.w3.org/2000/svg';
let nextLensId = 0;
const node = (name, attributes = {}) => {
  const element = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes))
    element.setAttribute(key, value);
  return element;
};

/** Live DOM optics. The source is the current page, never a screenshot or canned texture. */
export class NavigationLens {
  constructor(root) {
    this.root = root;
    this.available =
      !!window.SVGFEDisplacementMapElement &&
      CSS.supports('filter', 'url(#lens)') &&
      CSS.supports('clip-path', 'path("M0,0 L1,0 L1,1 Z")');
    this.ready = false;
    this.dirty = true;
    this.id = `majlis-lens-${++nextLensId}`;
    this.surface = document.createElement('div');
    this.surface.className = 'nav-lens';
    this.surface.setAttribute('aria-hidden', 'true');
    this.surface.inert = true;
    this.surface.innerHTML =
      '<div class="nav-lens-filter"><div class="nav-lens-crop"><div class="nav-lens-scene"></div></div></div><div class="nav-lens-rim"></div>';
    this.filtered = this.surface.querySelector('.nav-lens-filter');
    this.rim = node('svg', {
      class: 'nav-lens-rim',
      preserveAspectRatio: 'none',
    });
    this.surface.querySelector('.nav-lens-rim').replaceWith(this.rim);
    this.scene = this.surface.querySelector('.nav-lens-scene');
    this.defs = node('svg', {
      width: 0,
      height: 0,
      'aria-hidden': 'true',
      class: 'nav-lens-definitions',
    });
    document.body.append(this.defs, this.surface);
    if (this.available) {
      try {
        this.createFilter();
      } catch {
        // A disabled canvas/SVG implementation must not break navigation.
        this.available = false;
        this.defs.replaceChildren();
      }
    }
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
        source === this.defs ||
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
    this.onScroll = () => {
      this.scrollY = window.scrollY;
    };
    window.addEventListener('scroll', this.onScroll, { passive: true });
  }

  createFilter() {
    const filter = node('filter', {
      id: this.id,
      filterUnits: 'userSpaceOnUse',
      primitiveUnits: 'userSpaceOnUse',
      'color-interpolation-filters': 'sRGB',
    });
    this.filter = filter;
    this.maps = [];
    for (let channel = 0; channel < 3; channel++) {
      // This image encodes displacement vectors only; it contains no application pixels.
      const map = node('feImage', {
        href: this.displacementMap(channel),
        preserveAspectRatio: 'none',
        result: `map${channel}`,
      });
      const aspect = node('feColorMatrix', {
        in: `map${channel}`,
        type: 'matrix',
        result: `aspect${channel}`,
      });
      const displace = node('feDisplacementMap', {
        in: 'SourceGraphic',
        in2: `aspect${channel}`,
        xChannelSelector: 'R',
        yChannelSelector: 'G',
        result: `bent${channel}`,
      });
      const color = Array.from({ length: 20 }, () => 0);
      color[channel * 5 + channel] = 1;
      color[18] = 1;
      filter.append(
        map,
        aspect,
        displace,
        node('feGaussianBlur', {
          in: `bent${channel}`,
          stdDeviation: '.22',
          result: `smooth${channel}`,
        }),
        node('feColorMatrix', {
          in: `smooth${channel}`,
          type: 'matrix',
          values: color.join(' '),
          result: `color${channel}`,
        }),
      );
      this.maps.push({ map, aspect, displace });
    }
    filter.append(
      node('feBlend', {
        in: 'color0',
        in2: 'color1',
        mode: 'screen',
        result: 'rg',
      }),
      node('feBlend', { in: 'rg', in2: 'color2', mode: 'screen' }),
    );
    this.defs.append(filter);
    this.filtered.style.filter = `url(#${this.id})`;
  }

  displacementMap(channel) {
    const size = 256,
      canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext('2d'),
      image = ctx.createImageData(size, size);
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) {
        const u = (x + 0.5) / size - 0.5,
          v = (y + 0.5) / size - 0.5;
        const r = Math.min(
          1,
          Math.pow(
            Math.pow(Math.abs(u * 2), 2.3) + Math.pow(Math.abs(v * 2), 2.3),
            1 / 2.3,
          ),
        );
        const edge = Math.max(0, Math.min(1, (r - 0.58) / 0.42));
        const shoulder = edge * edge * (3 - 2 * edge);
        // Central magnification and a steep curved rim; wavelength separation is confined to the rim.
        const factor =
          -0.34 +
          0.88 * shoulder * shoulder +
          (channel - 1) * 0.1 * Math.pow(r, 8);
        const at = (y * size + x) * 4;
        image.data[at] = Math.round(255 * (0.5 + u * factor));
        image.data[at + 1] = Math.round(255 * (0.5 + v * factor));
        image.data[at + 2] = 128;
        image.data[at + 3] = 255;
      }
    ctx.putImageData(image, 0, 0);
    return canvas.toDataURL();
  }

  scheduleSync() {
    if (this.pendingSync) return;
    this.pendingSync = requestAnimationFrame(() => {
      this.pendingSync = 0;
      if (this.dirty && this.available) this.syncScene();
    });
  }

  prepare() {
    if (!this.available) return;
    this.dirty = true;
    this.scrollY = window.scrollY;
    this.ready = this.available;
    const button = this.root.querySelector('button[data-section]');
    if (button) {
      const r = button.getBoundingClientRect();
      this.baseWidth = r.width * 1.46;
      this.baseHeight = this.baseWidth / 1.22;
      this.configureGeometry();
    }
    this.scheduleSync();
  }

  /** Make an inert live DOM mirror. Fixed elements keep their viewport coordinates. */
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
        original === this.defs ||
        original.classList?.contains('nav-glass')
      ) {
        copy.remove();
        return;
      }
      // A mirror must not create duplicate IDs, control IDs or accessibility entries.
      if (original.id) {
        const computed = getComputedStyle(original);
        for (let i = 0; i < computed.length; i++) {
          const property = computed[i];
          copy.style.setProperty(property, computed.getPropertyValue(property));
        }
      }
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
        original === this.defs ||
        original.tagName === 'SCRIPT' ||
        original.tagName === 'DIALOG'
      )
        continue;
      const copy = original.cloneNode(true);
      normal.append(copy);
      sanitize(original, copy);
    }
    this.scene.replaceChildren(normal, fixed);
    this.normal = normal;
    this.dirty = false;
  }

  configureGeometry() {
    const width = this.baseWidth,
      height = this.baseHeight,
      margin = 40;
    Object.assign(this.filtered.style, {
      left: -margin + 'px',
      top: -margin + 'px',
      width: width + 2 * margin + 'px',
      height: height + 2 * margin + 'px',
    });
    this.filter.setAttribute('x', '0');
    this.filter.setAttribute('y', '0');
    this.filter.setAttribute('width', width + 2 * margin);
    this.filter.setAttribute('height', height + 2 * margin);
    for (const { map, aspect, displace } of this.maps) {
      map.setAttribute('x', margin);
      map.setAttribute('y', margin);
      map.setAttribute('width', width);
      map.setAttribute('height', height);
      const ratio = height / width;
      aspect.setAttribute(
        'values',
        `1 0 0 0 0 0 ${ratio} 0 0 ${0.5 * (1 - ratio)} 0 0 1 0 0 0 0 0 1 0`,
      );
      displace.setAttribute('scale', width);
    }
    const points = [];
    for (let i = 0; i <= 96; i++) {
      const a = (i / 96) * Math.PI * 2,
        co = Math.cos(a),
        si = Math.sin(a);
      points.push([
        width / 2 +
          (width / 2) * Math.sign(co) * Math.pow(Math.abs(co), 2 / 2.3),
        height / 2 +
          (height / 2) * Math.sign(si) * Math.pow(Math.abs(si), 2 / 2.3),
      ]);
    }
    const d =
      points
        .map((p, i) => (i ? 'L' : 'M') + p.map((v) => v.toFixed(3)).join(','))
        .join(' ') + 'Z';
    this.surface.style.clipPath = `path('${d}')`;
    this.rim.setAttribute('viewBox', `0 0 ${width} ${height}`);
    this.rim.replaceChildren(
      node('path', {
        d,
        fill: 'none',
        stroke:
          document.documentElement.dataset.theme === 'dark'
            ? 'rgba(225,239,255,.50)'
            : 'rgba(83,110,146,.35)',
        'stroke-width': '.8',
        'vector-effect': 'non-scaling-stroke',
      }),
    );
  }

  draw({ x, y, w, h, lift, flow = 0 }, enabled) {
    const visible =
      enabled && this.available && this.ready && this.baseWidth && lift > 0.002;
    this.surface.hidden = !visible;
    if (!visible) return;
    if (this.dirty) this.syncScene();
    const rect = this.root.getBoundingClientRect();
    const width = (w + (this.baseWidth - w) * lift) * (1 + 0.18 * flow * lift);
    const height = (h + (this.baseHeight - h) * lift) * (1 - 0.2 * flow * lift);
    // Size is calibrated in tab pitches, not inherited from the wide resting pill.
    const centerX = Math.max(
      width / 2 + 2,
      Math.min(
        window.innerWidth - width / 2 - 2,
        rect.left + this.root.clientLeft + x + w / 2,
      ),
    );
    const centerY = Math.max(
      height / 2 + 2,
      Math.min(
        window.innerHeight - height / 2 - 2,
        rect.top + this.root.clientTop + y + h / 2,
      ),
    );
    const sx = width / this.baseWidth,
      sy = height / this.baseHeight;
    Object.assign(this.surface.style, {
      left: centerX - this.baseWidth / 2 + 'px',
      top: centerY - this.baseHeight / 2 + 'px',
      width: this.baseWidth + 'px',
      height: this.baseHeight + 'px',
      opacity: String(Math.min(1, lift * 8)),
      transform: `scale(${sx},${sy})`,
    });
    this.scene.style.width = window.innerWidth + 'px';
    this.scene.style.height = window.innerHeight + 'px';
    // Inverse source scaling keeps real content aligned; only refraction deforms it.
    this.scene.style.transformOrigin = '0 0';
    this.scene.style.transform = `translate(${40 + this.baseWidth / 2 - centerX / sx}px,${40 + this.baseHeight / 2 - centerY / sy}px) scale(${1 / sx},${1 / sy})`;
    this.normal.style.transform = `translateY(${-window.scrollY}px)`;
  }

  dispose() {
    cancelAnimationFrame(this.pendingSync);
    this.observer.disconnect();
    document.removeEventListener('input', this.onInput, true);
    window.removeEventListener('scroll', this.onScroll);
    this.surface.remove();
    this.defs.remove();
  }
}
