import {test} from 'node:test';
import assert from 'node:assert/strict';
import {percent, delta, coverage} from '../src/format.ts';
const plain = s => s.replace(/\s/g, '');
test('frações, zero e ausências são distintos', () => {
  assert.equal(plain(percent(.125)), '12,5%');
  assert.equal(plain(percent(0)), '0%');
  assert.equal(percent(null), '—');
  assert.equal(percent(NaN), '—');
});
test('variações usam pontos percentuais com sinal', () => {
  assert.equal(delta(.031), '+3,1 p.p.');
  assert.equal(delta(-.031), '−3,1 p.p.');
  assert.equal(delta(null), '—');
});
test('cobertura incompleta não é extrapolada', () => {
  assert.equal(coverage(2, 1, 3), null);
  assert.equal(coverage(0, 0, 0), null);
  assert.equal(coverage(2, 0, 2), 0);
  assert.equal(coverage(4, 3, 4), .75);
  assert.equal(coverage(50, 50, 100, 75), .75);
  assert.equal(coverage(50, 50, 100, 101), null);
});
