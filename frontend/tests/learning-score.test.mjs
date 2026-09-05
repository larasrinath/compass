import test from 'node:test';
import assert from 'node:assert/strict';
import { computeScore, SCORE_CRITERIA } from '../src/learn/content.ts';
const c = (state, weight = 1) => ({name: state, weight, state, note: ''});
test('unknown information changes bounds, not the observed-score denominator', () => {
  assert.deepEqual(computeScore([c('matched'), c('unknown')]), {scored:true, score:100, confidence:50, low:50, high:100});
});
test('all unknown is unavailable, not a zero score', () => {
  assert.equal(computeScore([c('unknown')]).scored, false);
  assert.equal(computeScore([c('unknown')]).reason, 'Active criteria lack evidence');
});
test('empty criteria differ from missing evidence', () => {
  assert.equal(computeScore([]).reason, 'No active criteria');
});
test('complete evidence collapses the range', () => {
  assert.deepEqual(computeScore([c('matched'), c('not-matched')]), {scored:true, score:50, confidence:100, low:50, high:50});
});
test('retrieval can resolve uncertainty without improving the score', () => {
  assert.equal(computeScore([c('matched'), c('unknown')]).score, 100);
  assert.equal(computeScore([c('matched'), c('not-matched')]).score, 50);
});
test('the running example and the profile tour agree', () => {
  assert.deepEqual(computeScore(SCORE_CRITERIA), {scored:true, score:89, confidence:75, low:67, high:92});
});
