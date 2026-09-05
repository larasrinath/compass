import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultSearchKeywords } from '../src/searchSettings.ts'

const term = value => ({ term: value, aliases: [] })
test('nice-to-have-only and credential-only briefs produce search terms', () => {
  assert.equal(defaultSearchKeywords({optional_skills:[term('Go'),term('PostgreSQL')]}),'Go PostgreSQL')
  assert.equal(defaultSearchKeywords({required_credentials:[term('Master Anaplanner')]}),'Master Anaplanner')
})
test('optional skills do not narrow a query with primary criteria', () => {
  assert.equal(defaultSearchKeywords({required_skills:[term('Go')],optional_skills:[term('Kubernetes')]}),'Go')
})
test('equivalent terms do not consume the limited search slots twice', () => {
  assert.equal(defaultSearchKeywords({target_titles:[term('Engineer')],required_skills:[term('engineer'),term('Go')],positive_keywords:['Ｇｏ','Payments']}),'Engineer Go Payments')
})
test('search query respects the API limit without cutting a term', () => {
  const words=['a','b','c','d'].map(letter => term(letter.repeat(160)))
  const query=defaultSearchKeywords({required_skills:words})
  assert.equal(query.length,482)
  assert.equal(query.split(' ').length,3)
})
