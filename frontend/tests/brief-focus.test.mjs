import assert from 'node:assert/strict'
import test from 'node:test'

import { focusBriefError } from '../src/pages/briefErrorFocus.ts'

test('positive-keyword rejection focuses its textarea, not location', () => {
  let focused = ''
  let queried = ''
  const textarea = { focus: () => (focused = 'positive-keywords') }
  const fallback = { focus: () => (focused = 'summary') }
  const root = {
    querySelector(selector) {
      queried = selector
      return selector.includes('positive_keywords') ? textarea : null
    },
  }

  focusBriefError('positive_keywords', fallback, root)

  assert.match(queried, /positive_keywords.*textarea/)
  assert.equal(focused, 'positive-keywords')
})
