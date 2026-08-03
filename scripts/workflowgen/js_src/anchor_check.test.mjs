import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { firstLineHasAnchor } from './anchor_check.mjs'

const FIXTURE = new URL('../../../tests/fixtures/anchor_semantics_cases.json', import.meta.url)
const cases = JSON.parse(readFileSync(FIXTURE, 'utf-8')).cases

for (const c of cases) {
  test(`firstLineHasAnchor: ${c.name}`, () => {
    assert.equal(firstLineHasAnchor(c.content, c.prefix), c.expected)
  })
}

test('a falsy prefix is not an anchor and must not silently pass everything', () => {
  // file_loader treats "" as "no anchor configured" and skips the check.
  // Here the caller has already decided an anchor applies, so an empty one is
  // a caller bug, not a licence to accept any content.
  assert.equal(firstLineHasAnchor('# Anything At All\n\nbody', ''), false)
})

test('the check reads the first line only, not a window of the head', () => {
  // The rejected implementation looked at text.slice(0, 500) with a multiline
  // regex, so an anchor anywhere in the first 500 characters passed.
  const text = 'noise\n'.repeat(3) + '# Traceability Matrix\n\nrows'
  assert.equal(firstLineHasAnchor(text, '# Traceability Matrix'), false)
})
