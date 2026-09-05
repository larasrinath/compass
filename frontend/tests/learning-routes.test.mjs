import assert from 'node:assert/strict'
import test from 'node:test'
import { parseAppRoute, pathForRoute } from '../src/routing.ts'
import { CHAPTERS } from '../src/learn/content.ts'

test('guide index and all chapters round-trip through real URLs', () => {
  for (const chapter of [null, ...CHAPTERS.map(item => item.id)]) {
    const route = { view: 'learn', candidateId: null, chapter }
    assert.deepEqual(parseAppRoute(pathForRoute(route)), route)
  }
})
test('malformed chapter encodings return to the guide index', () => {
  assert.deepEqual(parseAppRoute('/how-it-works/%E0%A4%A'), {view:'learn', candidateId:null, chapter:null})
})
test('existing routes retain their behavior', () => {
  for (const route of [
    {view:'brief',candidateId:null}, {view:'search',candidateId:null},
    {view:'saved',candidateId:null}, {view:'ranked',candidateId:null},
    {view:'candidate',candidateId:'person/with spaces'},
  ]) assert.deepEqual(parseAppRoute(pathForRoute(route)),route)
})
