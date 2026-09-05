import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { JSDOM } from 'jsdom'
import { createServer } from 'vite'
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://127.0.0.1:5173/how-it-works'})
globalThis.window = dom.window
globalThis.document = dom.window.document
Object.defineProperty(globalThis,'navigator',{configurable:true,value:dom.window.navigator})
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.Node = dom.window.Node
globalThis.IS_REACT_ACT_ENVIRONMENT = true
window.scrollTo = () => {}
let requests = 0
globalThis.fetch = () => { requests++; throw new Error('Guide must not make network requests') }
const {render,screen,cleanup} = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
const vite = await createServer({root:fileURLToPath(new URL('..',import.meta.url)),cacheDir:`node_modules/.vite-learn-${process.pid}`,appType:'custom',optimizeDeps:{noDiscovery:true},server:{hmr:false,middlewareMode:true}})
const {default:LearnPage} = await vite.ssrLoadModule('/src/learn/LearnPage.tsx')
const {CHAPTERS} = await vite.ssrLoadModule('/src/learn/content.ts')
await vite.close()
test.afterEach(cleanup)
test.after(()=>dom.window.close())
test('every chapter renders without a session, network calls, or storage changes', () => {
  window.localStorage.setItem('saved-work','unchanged')
  for (const item of CHAPTERS) {
    render(React.createElement(LearnPage,{chapter:item.id,onNavigate:()=>{}}))
    assert.ok(screen.getByRole('heading',{level:1}))
    cleanup()
  }
  assert.equal(requests,0)
  assert.equal(window.localStorage.getItem('saved-work'),'unchanged')
})
test('chapter index delegates navigation to the main app', async () => {
  let destination
  render(React.createElement(LearnPage,{chapter:null,onNavigate:value=>{destination=value}}))
  const user=userEvent.setup({document:window.document})
  await user.click(screen.getByRole('button',{name:/How to review a candidate/}))
  assert.deepEqual(destination,{name:'learn',chapter:'tour-review'})
})
test('unknown chapters offer a route back instead of silently showing the wrong chapter', async () => {
  let destination
  render(React.createElement(LearnPage,{chapter:'does-not-exist',onNavigate:value=>{destination=value}}))
  assert.ok(screen.getByRole('heading',{name:'Chapter not found'}))
  await userEvent.setup({document:window.document}).click(screen.getByRole('button',{name:'All chapters'}))
  assert.deepEqual(destination,{name:'learn'})
})
test('example retrieval cannot call the real API', async () => {
  render(React.createElement(LearnPage,{chapter:'tour-review',onNavigate:()=>{}}))
  const user=userEvent.setup({document:window.document})
  await user.click(screen.getByRole('button',{name:'Step 6: Decide whether you need more'}))
  await user.click(screen.getByRole('button',{name:'Simulate requesting Skills'}))
  assert.ok(screen.getByRole('button',{name:'Example request queued — no live download'}).disabled)
  assert.equal(requests,0)
})

test('results walkthrough requires review, selection, then View comparison', async () => {
  render(React.createElement(LearnPage,{chapter:'review-and-compare',onNavigate:()=>{}}))
  const user=userEvent.setup({document:window.document})
  assert.ok(screen.getByRole('button',{name:'Confirm list & show ranking'}).disabled)
  await user.type(screen.getByRole('textbox',{name:'What did you check?'}),'Checked names and duplicate sources.')
  await user.click(screen.getByRole('button',{name:'Confirm list & show ranking'}))
  await user.click(screen.getByRole('button',{name:'Compare candidates'}))
  await user.click(screen.getByRole('checkbox',{name:'Compare Robin Serrano'}))
  assert.ok(screen.getByRole('button',{name:'View comparison (1/3)'}).disabled)
  await user.click(screen.getByRole('checkbox',{name:'Compare Marta Voss'}))
  assert.equal(screen.queryByRole('table'),null)
  await user.click(screen.getByRole('button',{name:'View comparison (2/3)'}))
  assert.ok(screen.getByRole('heading',{name:'Compare the evidence'}))
  assert.ok(screen.getAllByText('Evidence found').length)
  assert.ok(screen.getAllByText('No exact match').length)
  await user.click(screen.getByRole('button',{name:/Simulate page reload/}))
  assert.equal(screen.queryByRole('table'),null)
  assert.equal(requests,0)
})

test('saved search preview stops at three and opens the full example pool', async () => {
  render(React.createElement(LearnPage,{chapter:'return-to-work',onNavigate:()=>{}}))
  const user=userEvent.setup({document:window.document})
  assert.equal(screen.getAllByRole('button',{name:/^Review /}).length,3)
  await user.click(screen.getByRole('button',{name:/Go backend engineer.*Open results/}))
  assert.equal(screen.getAllByRole('button',{name:/^Review /}).length,5)
  await user.click(screen.getByRole('button',{name:'Review Jamie Chen'}))
  assert.ok(screen.getByRole('heading',{name:'Jamie Chen'}))
  assert.equal(requests,0)
})

test('weights use versioned number fields and verification stays local', async () => {
  render(React.createElement(LearnPage,{chapter:'priorities-verify',onNavigate:()=>{}}))
  const user=userEvent.setup({document:window.document})
  assert.equal(screen.getAllByRole('spinbutton').length,7)
  assert.equal(screen.queryByRole('slider'),null)
  const field=screen.getByRole('spinbutton',{name:'Required skills weight'})
  await user.clear(field)
  await user.type(field,'40')
  await user.click(screen.getByRole('button',{name:'Save scoring weights'}))
  assert.ok(screen.getByRole('heading', {name:'Scoring weights'}))
  assert.ok(screen.getByRole('button', {name:'Save scoring weights'}).disabled)
  assert.ok(screen.getByRole('button',{name:'Record checks'}).disabled)
  for (const checkbox of screen.getAllByRole('checkbox').slice(0,10)) await user.click(checkbox)
  await user.type(screen.getByRole('textbox',{name:'Verification note'}),'Checked all ten example sources.')
  assert.equal(screen.getByRole('button',{name:'Record checks'}).disabled,false)
  await user.click(screen.getByRole('button',{name:'Record checks'}))
  assert.ok(screen.getByRole('button',{name:'Example check recorded'}).disabled)
  assert.equal(requests,0)
  assert.equal(window.localStorage.getItem('saved-work'),'unchanged')
})


test('request walkthrough exposes each explanation and supports direct step selection', async () => {
  render(React.createElement(LearnPage,{chapter:'after-a-request',onNavigate:()=>{}}))
  assert.equal(screen.getAllByRole('button',{name:/^Step [1-7]:/}).length,7)
  assert.ok(screen.getByText(/The complete response is committed to local storage/))
  const step=screen.getByRole('button',{name:'Step 3: Local LinkedIn connector'})
  await userEvent.setup({document:window.document}).click(step)
  assert.equal(step.getAttribute('aria-pressed'),'true')
  assert.ok(screen.getByText(/Network request and response:/))
  assert.equal(requests,0)
})


test('candidate tour lets the user read a passage before checking it', async () => {
  render(React.createElement(LearnPage,{chapter:'tour-review',onNavigate:()=>{}}))
  const user = userEvent.setup({document:window.document})
  await user.click(screen.getByRole('button',{name:'Step 3: Open the passage and check its context'}))
  assert.equal(screen.queryByRole('checkbox'), null)
  await user.click(screen.getByRole('button',{name:/Built reporting services using PostgreSQL/}))
  const checkbox = screen.getByRole('checkbox',{name:'I checked this passage against the criterion'})
  assert.equal(checkbox.checked, false)
  await user.click(checkbox)
  assert.ok(screen.getByText('Example source checked. Score unchanged.'))
  assert.equal(requests,0)
})
