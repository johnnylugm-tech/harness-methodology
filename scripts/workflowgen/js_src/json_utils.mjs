// Pure JSON-extraction helpers shared by every workflow that needs to pull
// a JSON object out of a heavy-cognition agent's free-form prose response
// (Peer Review / structured-B-review style loops — playbook §5.2's
// documented exception: complex nested verdict arrays stay prose + parser
// rather than `schema:`, because a big schema forced onto a heavy-cognition
// agent is what caused the v2 "subagent completed without calling
// StructuredOutput" failure class).
//
// `export`ed here so `node --test` can exercise them directly; the
// generator (js_blocks.render_json_utils) strips the `export` keyword when
// inlining this same source into a self-contained phase workflow file (the
// Claude Code Workflow runtime forbids `import`/`export` at runtime).

export function balancedJsonAt(text, start) {
  if (text[start] !== '{' && text[start] !== '[') return null
  let depth = 0, inStr = false, esc = false
  for (let i = start; i < text.length; i++) {
    const c = text[i]
    if (esc) { esc = false; continue }
    if (c === '\\') { esc = true; continue }
    if (c === '"') { inStr = !inStr; continue }
    if (inStr) continue
    if (c === '{' || c === '[') depth++
    else if (c === '}' || c === ']') { depth--; if (depth === 0) return text.slice(start, i + 1) }
  }
  return null
}

export function extractLastJson(text) {
  if (typeof text !== 'string') return null
  let last = null
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{' || text[i] === '[') {
      const block = balancedJsonAt(text, i)
      if (block) { try { last = JSON.parse(block); i += block.length - 1 } catch {} }
    }
  }
  return last
}

export function parseAgentJson(text, label) {
  const parsed = extractLastJson(text)
  if (parsed !== null) return parsed
  throw new Error('PARSE_FAIL [' + label + ']: no balanced JSON. tail=' + (text ?? '').toString().slice(-200))
}
