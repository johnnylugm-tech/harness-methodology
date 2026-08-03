// The H1-anchor rule, as the workflow JS applies it to text an agent handed
// back rather than to a file on disk.
//
// The two are separate checks on purpose. `scripts/file_loader.py` validates
// the artefact where it lives; this validates the relay, because the relay is
// a sub-agent and file_loader.py's own docstring lists what that has cost —
// Bug v5 (a fine-tuned model prepending "Acknowledged" after any tool call),
// Bug v8 (the loader returning invented content for a file that did not
// match). Separate checks, one RULE: whatever comes back must begin with the
// anchor exactly as the file on disk must.
//
// Before Round 34 this side used /^#\s+[^\n]*<phrase>/m over text.slice(0,500)
// — "any H1 line near the top that contains the phrase" — so
// "Acknowledged.\n\n# Software Requirements Specification — taskq" passed,
// which is Bug v5 arriving through the check built to stop it.
//
// `export`ed here so `node --test` can exercise it directly; the generator
// (js_blocks.render_load_file_via_python) strips the `export` keyword when
// inlining this same source into a self-contained phase workflow file (the
// Claude Code Workflow runtime forbids `import`/`export` at runtime).
// tests/fixtures/anchor_semantics_cases.json is shared with the Python side,
// so a divergence in either direction fails a test.

export function firstLineHasAnchor(text, expectPrefix) {
  // An empty anchor means the CALLER decided one applies but supplied nothing.
  // file_loader treats "" as "no anchor configured" and skips its check; here
  // that would turn a caller bug into "accept anything", so it is a failure.
  if (!expectPrefix) return false
  const nl = text.indexOf('\n')
  const firstLine = nl === -1 ? text : text.slice(0, nl)
  return firstLine.startsWith(expectPrefix)
}
