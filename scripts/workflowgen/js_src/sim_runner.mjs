// sim_runner.mjs — dynamic-workflow simulation testbed (Round 12 站1).
//
// WHY THIS EXISTS: commit cef32c4 (v2.13.3) shipped after v2.13.2 crashed
// every P3 run with a ReferenceError on its very first per-FR verify —
// its own root-cause line: "the test harness has no coverage for the
// dynamic-workflow execution substrate". The 8 generated workflow files
// under .claude/workflows/ only ever executed inside a live E2E run on a
// target project; every edit shipped blind. This runner executes the real
// generated files in Node with the Claude-Code-Workflow globals mocked,
// so the CONTROL FLOW (phase ordering, gate loops, early-return shapes,
// retry bounds, use of runtime-unavailable APIs like spawnSync) is
// exercised on every test run.
//
// HONEST BOUNDARY (do not oversell): this simulates the workflow JS
// runtime's *API surface* (agent/phase/log/args/budget, top-level await,
// top-level return via async-function wrapping). It does NOT simulate the
// OS sandbox, permission walls, real subprocesses, or real LLM behaviour.
// Those are covered by the spawn-substrate preflight probe at run-phase
// entry (Round 12 站0b) and by live E2E runs. The two layers together —
// logic here, substrate there — replace "test in production".
//
// Mocked global surface (measured from the 8 generated files, Round 12):
//   agent(prompt, {label, phase, agentType, schema}) -> scripted response
//   phase(title), log(msg)                           -> event capture
//   args                                             -> opts.args (mutable)
//   budget                                           -> undefined (files
//     guard with `typeof budget !== 'undefined'`)
// No file uses parallel()/pipeline() today; add them here if a future
// generator emits them (the wrapper would otherwise throw ReferenceError,
// which is exactly the class of bug this runner exists to catch).

import { readFile } from 'node:fs/promises'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

/**
 * Execute one generated workflow file under mocked runtime globals.
 *
 * @param {string} filePath absolute path to a .claude/workflows/*.js file
 * @param {(call: {label:string, phase:string, prompt:string, schema:object|undefined, index:number}, events:object) => any} respond
 *        scripted agent responder; return value is what `await agent(...)`
 *        yields inside the workflow (string, object, or null).
 * @param {{args?: object}} [opts]
 * @returns {{result:any, events:{phases:string[], agents:{label:string, phase:string}[], logs:string[]}}}
 *          result === the workflow's top-level return value, or
 *          {__fell_through:true, meta} when the file body ran to the end.
 */
export async function runWorkflow(filePath, respond, opts = {}) {
  const src = await readFile(filePath, 'utf8')
  // The real runtime evaluates the file body with top-level return/await;
  // reproduce that by stripping the single `export ` and wrapping in an
  // async function. Everything else is byte-identical to the shipped file.
  const body = src.replace(/^export const meta/m, 'const meta')
  if (body === src) {
    throw new Error(`no "export const meta" found in ${filePath} — not a workflow file?`)
  }
  const events = { phases: [], agents: [], logs: [] }
  const phaseFn = (title) => { events.phases.push(String(title)) }
  const logFn = (msg) => { events.logs.push(String(msg)) }
  let agentIndex = 0
  const agentFn = async (prompt, options = {}) => {
    const call = {
      label: String(options.label ?? ''),
      phase: String(options.phase ?? ''),
      prompt: String(prompt ?? ''),
      schema: options.schema,
      index: agentIndex++,
    }
    events.agents.push({ label: call.label, phase: call.phase })
    return respond(call, events)
  }
  const fn = new AsyncFunction(
    'agent', 'phase', 'log', 'args', 'budget',
    `${body}\n;return { __fell_through: true, meta }`,
  )
  const result = await fn(
    agentFn, phaseFn, logFn,
    opts.args ?? { repo: '/sim/project' },
    undefined,
  )
  return { result, events }
}

/**
 * Schema-driven happy-path responder with ordered regex overrides.
 *
 * Resolution order per agent call:
 *   1. first override whose `match` tests true against the call label
 *      (respond may be a value or a fn(call, events));
 *   2. schema present -> synthesize a passing object from its shape
 *      (known verdict/rc/ctx/delta shapes first, then a generic
 *      property-type walk);
 *   3. no schema -> a long generic success string (length > the
 *      session-limit guards' `length < 10` checks).
 *
 * @param {{match: RegExp, respond: any}[]} [overrides]
 */
export function makeHappyResponder(overrides = []) {
  return (call, events) => {
    for (const o of overrides) {
      if (o.match.test(call.label)) {
        const value = (typeof o.respond === 'function') ? o.respond(call, events) : o.respond
        // Foot-gun guard (hit on first p6 probe): a schema call answered
        // with a plain string fails the workflow's object checks — skip
        // string overrides for schema calls and fall through to the
        // schema synthesizer instead.
        if (call.schema && typeof value === 'string') break
        return value
      }
    }
    // A/B machine file loader (phase1/2/6): the workflow validates
    // length >= 50 plus an optional `--expect-prefix "X"` heading anchor
    // embedded in the prompt's read-file command. Synthesize matching
    // content instead of forcing every scenario pack to re-derive it.
    if (/^loadpy-/.test(call.label)) {
      const m = call.prompt.match(/--expect-prefix\s+"([^"]+)"/)
      const heading = m ? m[1].replace(/^#\s*/, '') : 'Simulated Document'
      return `# ${heading}\n\n` + 'simulated document body for the workflow logic testbed. '.repeat(4)
    }
    const schema = call.schema
    if (schema && typeof schema === 'object' && schema.properties) {
      const req = Array.isArray(schema.required) ? schema.required : []
      const has = (k) => k in schema.properties
      if (has('pass') && has('reason')) {
        // VERDICT_SCHEMA family. reason carries the deterministic marker
        // strings some call sites startsWith()/regex on; the happy default
        // covers the common `RC=0` / GATE1 helper cases.
        return { pass: true, reason: 'GATE1_VERIFIED_PASS' }
      }
      if (has('rc')) {
        // RC family (RC_SCHEMA + ENV_CHECK_SCHEMA). ENV_CHECK_SCHEMA
        // (Round 23) adds a `ready` field for the Bug #127 cross-check;
        // if the schema declares it, include `ready: true` so the
        // generic happy responder doesn't return a schema-invalid
        // `{rc: 0}` to the env-check orchestrator.
        return has('ready') ? { rc: 0, ready: true } : { rc: 0 }
      }
      if (has('fr_ids')) return { fr_ids: ['FR-01'], fr_count: 1 }
      if (has('pass_fr_ids')) {
        // Empty fast-path list -> the full per-FR loop runs, which is the
        // higher-coverage branch for a logic testbed.
        return { pass_fr_ids: [], fail_fr_ids: ['FR-01'] }
      }
      if (has('manifest_qc')) return { manifest_qc: true, d4_rc: 0, detail: 'sim ok' }
      if (has('current_phase')) {
        // PHASE_SCHEMA: every phase's advance-verify checks
        // current_phase >= N+1; 99 satisfies all of them.
        return { current_phase: 99 }
      }
      const out = {}
      for (const [key, spec] of Object.entries(schema.properties)) {
        const t = (spec && spec.type) || 'string'
        if (t === 'boolean') out[key] = true
        else if (t === 'integer' || t === 'number') out[key] = 0
        else if (t === 'array') out[key] = []
        else if (t === 'object') out[key] = {}
        else out[key] = 'SIM_OK'
      }
      for (const k of req) { if (!(k in out)) out[k] = 'SIM_OK' }
      return out
    }
    return 'SIMULATED-OK: step executed successfully in the workflow logic testbed. PASS.'
  }
}

/** Responder that returns null for every agent — the runtime's
 *  session-limit / terminal-API-error shape. */
export const nullResponder = () => null
