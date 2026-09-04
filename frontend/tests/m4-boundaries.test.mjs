import assert from 'node:assert/strict'
import test from 'node:test'

import { parseAppRoute, pathForRoute } from '../src/routing.ts'
import {
  reconcileEvidenceVerifications,
  scoreIdentityKey,
} from '../src/scoreVerification.ts'

test('candidate detail path round-trips encoded ids and direct navigation', () => {
  const route = parseAppRoute('/candidates/ada%2Flovelace')
  assert.deepEqual(route, { view: 'candidate', candidateId: 'ada/lovelace' })
  assert.equal(pathForRoute(route), '/candidates/ada%2Flovelace')
  assert.deepEqual(parseAppRoute('/candidates'), { view: 'ranked', candidateId: null })
  assert.deepEqual(parseAppRoute('/candidates/%E0%A4%A'), { view: 'brief', candidateId: null })
})

test('v1 evidence verification cannot survive a v2 score identity', () => {
  const verified = new Map([
    ['e1', {
      evidenceId: 'e1', sessionId: 'session', scoreId: 'score-v1',
      inputFingerprint: 'fingerprint-v1',
    }],
  ])
  const v1 = new Set([scoreIdentityKey({
    sessionId: 'session', scoreId: 'score-v1', inputFingerprint: 'fingerprint-v1',
  })])
  const v2 = new Set([scoreIdentityKey({
    sessionId: 'session', scoreId: 'score-v2', inputFingerprint: 'fingerprint-v2',
  })])
  assert.equal(reconcileEvidenceVerifications(verified, v1).size, 1)
  assert.equal(reconcileEvidenceVerifications(verified, v2).size, 0)
})
