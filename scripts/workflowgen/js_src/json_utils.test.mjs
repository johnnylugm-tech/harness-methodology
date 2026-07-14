import { test } from 'node:test'
import assert from 'node:assert/strict'
import { balancedJsonAt, extractLastJson, parseAgentJson } from './json_utils.mjs'

test('balancedJsonAt: simple object', () => {
  assert.equal(balancedJsonAt('{"a":1}', 0), '{"a":1}')
})

test('balancedJsonAt: simple array', () => {
  assert.equal(balancedJsonAt('[1,2,3]', 0), '[1,2,3]')
})

test('balancedJsonAt: nested object/array', () => {
  const text = '{"a":[1,{"b":2}],"c":3}'
  assert.equal(balancedJsonAt(text, 0), text)
})

test('balancedJsonAt: braces inside a string are ignored', () => {
  const text = '{"msg":"contains { and } chars"}'
  assert.equal(balancedJsonAt(text, 0), text)
})

test('balancedJsonAt: escaped quote inside a string does not close it early', () => {
  const text = '{"msg":"she said \\"hi\\""}'
  assert.equal(balancedJsonAt(text, 0), text)
})

test('balancedJsonAt: escaped backslash before quote is handled', () => {
  // "path\\" -- the string ends after the escaped backslash, not consuming the closing quote
  const text = '{"path":"C:\\\\"}'
  assert.equal(balancedJsonAt(text, 0), text)
})

test('balancedJsonAt: non-brace start returns null', () => {
  assert.equal(balancedJsonAt('not json', 0), null)
})

test('balancedJsonAt: unbalanced input returns null', () => {
  assert.equal(balancedJsonAt('{"a":1', 0), null)
})

test('extractLastJson: picks the LAST of multiple JSON blocks in prose', () => {
  const text = 'first attempt: {"review_status":"REJECT"}\nfinal answer: {"review_status":"APPROVE"}'
  assert.deepEqual(extractLastJson(text), { review_status: 'APPROVE' })
})

test('extractLastJson: finds JSON preceded and followed by prose', () => {
  const text = 'Here is my analysis.\n{"pass": true, "reason": "looks good"}\nThat is all.'
  assert.deepEqual(extractLastJson(text), { pass: true, reason: 'looks good' })
})

test('extractLastJson: returns null for prose with no JSON', () => {
  assert.equal(extractLastJson('no json here at all'), null)
})

test('extractLastJson: returns null for non-string input', () => {
  assert.equal(extractLastJson(null), null)
  assert.equal(extractLastJson(undefined), null)
  assert.equal(extractLastJson(42), null)
})

test('extractLastJson: skips an unparseable brace run and still finds a later valid block', () => {
  const text = '{ this is not valid json } then later: {"ok": true}'
  assert.deepEqual(extractLastJson(text), { ok: true })
})

test('parseAgentJson: returns the parsed value when present', () => {
  assert.deepEqual(parseAgentJson('{"verdicts":[{"deliverable":"X"}]}', 'test-label'), {
    verdicts: [{ deliverable: 'X' }],
  })
})

test('parseAgentJson: throws with the label and a tail of the text on failure', () => {
  assert.throws(
    () => parseAgentJson('no json in this response', 'peer-review-r1'),
    (err) => err.message.includes('PARSE_FAIL [peer-review-r1]') && err.message.includes('no json in this response'),
  )
})
