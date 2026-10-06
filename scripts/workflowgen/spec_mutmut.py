"""`standalone-mutmut` assembly — the mutation_testing dimension, run on its own.

Round 114 站1. This workflow used to be hand-maintained, and the exemption
rotted the way every unchecked copy does. Measured against `build_parser()`:
it passed `--paths-to-mutate` and `--timeout` to `mutation-test-score`, which
takes `--project` only, so every run exited 2. It also outlived the rules it
restated — its preflight demanded `test -f` of each scope path, and the scope
Round 113 站6 writes into setup.cfg is package directories — and it had the
agent compute a "raw" and an "adjusted" kill rate from `mutmut results`
output, a second statement of a number the framework already computes,
records and gates on.

So it is generated now, and it is thin: the framework runs mutmut and writes
`.methodology/mutation_score.json`; this workflow launches that command,
relays the file the command wrote, and compares its score with the threshold
the gate configs declare. The agent transcribes one exit code and nothing
else. Scope comes from setup.cfg (the SAB, via Round 113 站6) and nowhere
else, so the old `mutmut_target` / `exclude_*` arguments are gone on purpose.

Entry: `Workflow({ scriptPath: '<repo>/harness/.claude/workflows/standalone-mutmut.js',
args: { repo } })`.
"""
from __future__ import annotations

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

_HEADER = """\
// standalone-mutmut — the mutation_testing dimension, run on its own
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/spec_mutmut.py. Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write
//
// The framework computes the score (harness_cli.py mutation-test-score writes
// .methodology/mutation_score.json); this workflow relays that file and
// compares it with the gate threshold. It never computes a kill rate itself.
"""

_META_PHASES = ["Mutation Testing", "Report"]

_DESCRIPTION = (
    "Run the mutation_testing dimension on its own: mutation-test-score, "
    "then the score the framework recorded against the gate threshold"
)


def mutation_threshold() -> float:
    """The mutation_testing threshold every gate config declares.

    Read through core.quality_gate.gate_thresholds — the module every gate
    already takes its thresholds from — at generation time, never typed here.
    The gates that score the dimension must agree; if one day they do not, a
    standalone run has no single answer to give and generation stops.
    """
    from core.quality_gate.gate_thresholds import GATE_CONFIG_NAMES, load_gate_dimensions

    found = {
        float(dim["threshold"])
        for gate in GATE_CONFIG_NAMES
        for dim in load_gate_dimensions(gate)
        if dim["name"] == "mutation_testing"
    }
    if len(found) != 1:
        raise AssertionError(
            f"gate configs disagree on the mutation_testing threshold: {sorted(found)}")
    return found.pop()


def _render_run() -> str:
    log_path = "/tmp/standalone_mutmut.log"
    return (
        B.render_phase_header("Mutation Testing")
        + f"const THRESHOLD = {mutation_threshold()}  // harness/gate_configs mutation_testing\n"
        + "const mutCmd = PY + ' ' + REPO + '/harness_cli.py mutation-test-score --project ' + REPO\n"
        + "const runRc = await agent(\n"
        + "  'You MUST use the Bash tool. mutmut can take up to 3600s, and a synchronous call is cut off at ~10 min, so run it BACKGROUNDED:\\n'\n"
        + f"  + '  a. Launch: `nohup sh -c ' + JSON.stringify(mutCmd + '; echo RC=$?') + ' > {log_path} 2>&1 & echo $!` — note the PID.\\n'\n"
        + f"  + '  b. Poll every {S.POLL_INTERVAL_S}s: `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE` (cap {S.mutation_poll_cap()} polls). Past the cap: `kill <PID>` and report rc -1.\\n'\n"
        + f"  + '  c. DONE: `tail -1 {log_path}` prints RC=<n>.\\n'\n"
        + "  + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC= (-1 if it never finished). Do not run mutmut yourself, do not compute any rate, do not edit any file.',\n"
        + "  { label: 'mutation-run', phase: 'Mutation Testing', agentType: 'general-purpose', schema: RC_SCHEMA },\n"
        + ")\n"
        + "if (!(runRc && runRc.rc === 0)) {\n"
        + "  return { error: 'mutation-test-score did not produce a score', rc: runRc ? runRc.rc : null, "
        + f"note: 'Read {log_path}: the command prints why (no [mutmut] in setup.cfg, mutmut missing, a crash). Nothing here substitutes a number for it.' }}\n"
        + "}\n"
    )


def _render_report() -> str:
    return (
        B.render_phase_header("Report")
        + "const scoreText = await loadFileViaPython('.methodology/mutation_score.json', '', 'Report')\n"
        + "let recorded = null\n"
        + "try { recorded = JSON.parse(scoreText) } catch (e) { recorded = null }\n"
        + "if (!recorded || typeof recorded.score !== 'number') {\n"
        + "  return { error: 'mutation_score.json was not relayed intact', detail: String(scoreText).slice(0, 200) }\n"
        + "}\n"
        + "const survivorsText = await loadFileViaPython('.methodology/mutation_survivors.json', '', 'Report')\n"
        + "let survivors = null\n"
        + "try { survivors = JSON.parse(survivorsText) } catch (e) { survivors = null }\n"
        + "const report = {\n"
        + "  score: recorded.score, killed: recorded.killed, survived: recorded.survived,\n"
        + "  threshold: THRESHOLD, paths_to_mutate: recorded.paths_to_mutate,\n"
        + "  survivor_count: survivors ? survivors.survivor_count : null,\n"
        + "  top_survivors: survivors && Array.isArray(survivors.survivors) ? survivors.survivors.slice(0, 5) : [],\n"
        + "  source: '.methodology/mutation_score.json (written by mutation-test-score)',\n"
        + "}\n"
        + "log('mutation score ' + recorded.score + '% (threshold ' + THRESHOLD + '%)')\n"
        + "if (recorded.score < THRESHOLD) {\n"
        + "  return Object.assign({ error: 'mutation score below the gate threshold' }, report)\n"
        + "}\n"
        + "return Object.assign({ status: 'PASS' }, report)\n"
    )


def generate_mutmut() -> str:
    from .generate_workflows import _inject_dispatch_wrapper, _wrap_top_level_boundary

    parts = [
        _HEADER,
        "",
        _render_meta(
            name="standalone-mutmut",
            description=_DESCRIPTION,
            phases=_META_PHASES,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        "",
        B.render_schemas(["RC_SCHEMA"]),
        B.render_load_file_via_python(),
        _render_run(),
        _render_report(),
    ]
    return _wrap_top_level_boundary(_inject_dispatch_wrapper("\n".join(parts)))
