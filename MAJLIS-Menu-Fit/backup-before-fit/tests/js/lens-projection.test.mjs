import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(
  new URL('../../web/nav-lens.js', import.meta.url),
  'utf8',
);
const { sourceTransform } = await import(
  'data:text/javascript;base64,' + Buffer.from(source).toString('base64')
);
const apply = (m, x, y) => [
  m[0] * x + m[2] * y + m[4],
  m[1] * x + m[3] * y + m[5],
];
const close = (a, b) =>
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-8));
test('live-source magnification stays centred at a moved viewport anchor', () => {
  const m = sourceTransform(220, 730, 85, 70, 1.46, 1.46);
  close(apply(m, 220, 730), [85, 70]);
  close(apply(m, 230, 740), [99.6, 84.6]);
});
test('edge projection reflects radial detail and keeps tangent detail enlarged', () => {
  for (const angle of [0, 0.5, Math.PI / 2, Math.PI]) {
    const n = [Math.cos(angle), Math.sin(angle)],
      t = [-n[1], n[0]];
    const m = sourceTransform(100, 200, 80, 60, -1.15, 1.12, angle);
    close(apply(m, 100, 200), [80, 60]);
    close(apply(m, 100 + 10 * n[0], 200 + 10 * n[1]), [
      80 - 11.5 * n[0],
      60 - 11.5 * n[1],
    ]);
    close(apply(m, 100 + 10 * t[0], 200 + 10 * t[1]), [
      80 + 11.2 * t[0],
      60 + 11.2 * t[1],
    ]);
  }
});
