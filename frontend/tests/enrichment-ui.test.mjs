import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const detail = readFileSync(`${root}/src/pages/CandidateDetailPage.tsx`, 'utf8')
const raw = readFileSync(`${root}/src/components/RawTextViewer.tsx`, 'utf8')
const sections = readFileSync(
  `${root}/src/components/SectionAvailabilityMap.tsx`,
  'utf8',
)

test('candidate detail preserves explicit staged retrieval alongside evidence', () => {
  assert.match(detail, /Retrieve up to three more sections/)
  assert.match(detail, /SourceCheck/)
  assert.match(detail, /selected\.length >= 3/)
  assert.match(detail, /getProfileSections/)
  assert.match(detail, /section !== 'experience'/)
  assert.doesNotMatch(`${detail}${sections}`, /send now|draft message/i)
})

test('raw highlighting uses Unicode code points and a withheld state', () => {
  assert.match(raw, /Array\.from\(section\.raw_text\)/)
  assert.match(raw, /Provenance withheld/)
  assert.match(raw, /!selected\.provenance_available/)
  assert.doesNotMatch(raw, /dangerouslySetInnerHTML|innerHTML/)
  assert.doesNotMatch(raw, /section\.raw_text\.slice/)
})
