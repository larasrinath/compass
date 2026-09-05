import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const app = readFileSync(`${root}/src/App.tsx`, 'utf8')
const brief = readFileSync(`${root}/src/pages/BriefPage.tsx`, 'utf8')
const search = readFileSync(`${root}/src/pages/SearchPage.tsx`, 'utf8')
const css = readFileSync(`${root}/src/index.css`, 'utf8')

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    )
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrast(first, second) {
  const lighter = Math.max(luminance(first), luminance(second))
  const darker = Math.min(luminance(first), luminance(second))
  return (lighter + 0.05) / (darker + 0.05)
}

test('workflow updates are announced and decorations are hidden', () => {
  assert.match(`${app}${brief}${search}`, /aria-live="polite"/)
  assert.match(app, /className="skip-link"/)
  assert.equal(app.match(/aria-hidden="true"/g)?.length, 6)
})

test('footer text meets WCAG AA contrast', () => {
  const foreground = css.match(/--ink-faint:\s*(#[0-9a-f]{6})/i)?.[1]
  assert.notEqual(foreground, undefined)
  assert.ok(contrast(foreground, '#f4f2ec') >= 4.5)
})
