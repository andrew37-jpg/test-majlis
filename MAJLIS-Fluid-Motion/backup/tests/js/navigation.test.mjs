import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const lensSource = readFileSync(
  new URL('../../web/nav-lens.js', import.meta.url),
  'utf8',
);
const { menuLensGeometry } = await import(
  'data:text/javascript;base64,' + Buffer.from(lensSource).toString('base64')
);
const source = readFileSync(new URL('../../web/ui.js', import.meta.url), 'utf8')
  .replace(/^import[\s\S]*?;\n/, '')
  .replaceAll('export ', '');
function setup({ reduced = false } = {}) {
  let time = 1000,
    queue = new Map(),
    next = 0;
  const calls = [],
    handlers = {},
    windowHandlers = {};
  const buttons = ['orders', 'catalog', 'help'].map((id, i) => ({
    dataset: { section: id },
    getBoundingClientRect: () => ({
      left: 6 + i * 100,
      top: 6,
      width: 100,
      height: 62,
    }),
    classList: { toggle() {} },
    setAttribute() {},
    removeAttribute() {},
  }));
  const root = {
    hidden: false,
    clientLeft: 0,
    clientTop: 0,
    style: { setProperty() {} },
    classList: { toggle() {} },
    querySelectorAll: () => buttons,
    getBoundingClientRect: () => ({
      left: 0,
      top: 0,
      bottom: 76,
      width: 312,
      height: 76,
    }),
    addEventListener: (n, f) => (handlers[n] = f),
    setPointerCapture() {},
    hasPointerCapture: () => false,
  };
  const media = { matches: reduced, addEventListener() {} };
  const context = {
    menuLensGeometry,
    window: {
      matchMedia: () => media,
      addEventListener: (n, f) => (windowHandlers[n] = f),
    },
    document: { addEventListener() {}, documentElement: {} },
    MutationObserver: class {
      observe() {}
    },
    performance: { now: () => time },
    requestAnimationFrame: (f) => {
      queue.set(++next, f);
      return next;
    },
    cancelAnimationFrame: (id) => queue.delete(id),
  };
  vm.createContext(context);
  vm.runInContext(source + '\nglobalThis.Nav=LiquidNavigation;', context);
  const nav = new context.Nav(root, (id) => {
    calls.push(id);
    nav.current = id;
    nav.wake();
  });
  nav.buttons = buttons;
  nav.current = 'orders';
  nav.indicator = {};
  nav.material = { style: {} };
  nav.measure();
  nav.snap();
  const event = (x, y = 37) => ({
    isPrimary: true,
    button: 0,
    pointerId: 1,
    clientX: x,
    clientY: y,
    target: { closest: () => buttons[0] },
  });
  const advance = (ms) => {
    for (let n = 0; n < ms; n += 16) {
      time += 16;
      const pending = [...queue.values()];
      queue.clear();
      pending.forEach((f) => f(time));
    }
  };
  return { nav, event, advance, calls, handlers, windowHandlers, root, queue };
}
test('drag previews without navigation, commits nearest section once on release', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(250);
  assert.equal(b.calls.length, 0);
  assert.ok(b.nav.visual.x > 150);
  b.nav.up(b.event(256));
  assert.deepEqual(b.calls, ['help']);
  let prevented = false;
  b.handlers.click({
    target: { closest: () => b.nav.buttons[0] },
    detail: 1,
    preventDefault() {
      prevented = true;
    },
  });
  assert.ok(prevented);
  assert.equal(b.calls.length, 1);
});
test('cancel returns to committed selection without opening previewed section', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(150);
  b.nav.cancel();
  b.advance(1200);
  assert.deepEqual(b.calls, []);
  assert.equal(b.nav.current, 'orders');
  assert.equal(b.nav.visual.x, 6);
  assert.equal(b.nav.visual.lift, 0);
  assert.equal(b.queue.size, 0);
});
test('vertical exit cancels a gesture and its subsequent release', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(80, -60));
  b.nav.up(b.event(256));
  assert.deepEqual(b.calls, []);
});
test('reversal continues from current position and does not overshoot', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(80);
  const x = b.nav.visual.x;
  b.nav.move(b.event(56));
  assert.equal(b.nav.visual.x, x);
  b.advance(16);
  assert.ok(b.nav.visual.x < x && b.nav.visual.x >= 6);
  b.nav.cancel();
  b.advance(1200);
  assert.equal(b.nav.visual.x, 6);
});
test('reduced motion suppresses optics while preserving drag release navigation', () => {
  const b = setup({ reduced: true });
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(100);
  assert.equal(b.nav.visual.x, 6);
  assert.equal(b.nav.visual.lift, 0);
  b.nav.up(b.event(256));
  b.advance(16);
  assert.equal(b.nav.visual.x, 206);
  assert.deepEqual(b.calls, ['help']);
});
test('keyboard click navigates without waiting for animation', () => {
  const b = setup();
  b.handlers.click({ detail: 0, target: { closest: () => b.nav.buttons[1] } });
  assert.deepEqual(b.calls, ['catalog']);
  assert.equal(b.nav.visual.x, 6);
  b.advance(1500);
  assert.equal(b.nav.visual.x, 106);
  assert.equal(b.nav.visual.lift, 0);
});

test('resize notification with unchanged geometry does not interrupt a held gesture', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(100);
  b.windowHandlers.resize();
  assert.ok(b.nav.gesture);
  b.nav.up(b.event(256));
  assert.deepEqual(b.calls, ['help']);
});

test('release outside the panel cancels even if the last move was missed', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.nav.up(b.event(256, -100));
  assert.deepEqual(b.calls, []);
});

test('rapid alternating clicks keep one frame callback and settle on the last selection', () => {
  const b = setup();
  for (let i = 0; i < 50; i++) {
    b.handlers.click({
      detail: 0,
      target: { closest: () => b.nav.buttons[i % 3] },
    });
    assert.equal(b.queue.size, 1);
    b.advance(16);
  }
  b.advance(1600);
  assert.equal(b.nav.current, 'catalog');
  assert.equal(b.nav.visual.x, 106);
  assert.equal(b.nav.visual.lift, 0);
  assert.equal(b.nav.visual.flow, 0);
  assert.equal(b.queue.size, 0);
});

test('a ready but blank optical renderer cannot hide the real selection material', () => {
  const b = setup();
  b.nav.lens = { available: true, ready: true, prepare() {}, draw() {} };
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(250);
  assert.equal(b.nav.material.style.opacity, '');
  assert.ok(b.nav.visual.lift > 0.9);
  b.nav.cancel();
  b.advance(1500);
  assert.equal(b.nav.material.style.opacity, '');
  assert.equal(b.nav.visual.lift, 0);
});

test('release uses its final position when the last pointermove was not delivered', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.up(b.event(256));
  assert.deepEqual(b.calls, ['help']);
  assert.equal(b.nav.holdUntil, 0);
  b.advance(1500);
  assert.equal(b.nav.visual.x, 206);
});

test('holding and moving never scales the real material outside the button row', () => {
  const b = setup();
  b.nav.down(b.event(56));
  b.nav.move(b.event(256));
  b.advance(200);
  assert.equal(b.nav.material.style.transform, 'none');
  const held = menuLensGeometry(b.nav.visual, b.nav.boxes);
  assert.equal(held.h, 62);
  assert.equal(held.y, 6);
  b.nav.cancel();
  b.advance(1500);
  assert.equal(b.nav.material.style.transform, 'none');
  assert.equal(b.nav.material.style.borderRadius, '23px');
});

test('off-centre press stays centred on its button until a drag is recognised', () => {
  const b = setup();
  b.nav.down(b.event(110));
  b.advance(700);
  assert.equal(b.nav.visual.x, 106);
  b.nav.move(b.event(114));
  b.advance(100);
  assert.equal(b.nav.visual.x, 106);
  b.nav.move(b.event(150));
  b.advance(500);
  assert.ok(Math.abs(b.nav.visual.x + 50 - 150) < 0.01);
  b.nav.up(b.event(150));
  b.advance(1000);
  assert.equal(b.nav.visual.x, 106);
  assert.deepEqual(b.calls, ['catalog']);
});
