"""Playbook §4 runtime-convention lint for the generated workflow files —
the 8 phase files (Round 11 station 5) plus run-all.js, which inlines all
eight of them (Round 23 站2). Guards against `scripts/workflowgen/` ever
regenerating a construct the Claude Code Workflow runtime rejects at
load time — see docs/WORKFLOW_PLAYBOOK.md §4 and
scripts/workflow_audit/js_lint.py's module docstring for why this is a
comment/string-aware scan rather than a substring search.

`bug-hunt-crg.js` and `standalone-mutmut.js` are intentionally out of
scope: they are not among the 8 phase files and are not
workflowgen-generated (Round 11 plan's 明確不做 list).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.workflow_audit.js_lint import (
    comment_line_numbers,
    find_banned_constructs,
    strip_comments_and_strings,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".claude" / "workflows"
MAX_BYTES = 524288  # 512 KiB — playbook §4 hard error (validator + runtime)

PHASE_FILES = [
    "phase1-requirements.js",
    "phase2-architecture.js",
    "phase3-implementation.js",
    "phase4-testing.js",
    "phase5-verification.js",
    "phase6-quality.js",
    "phase7-risk.js",
    "phase8-config.js",
]

# run-all.js inlines all eight bodies into one file (Round 23 站2). It is
# workflowgen-generated like the others, so every convention below applies to
# it too — and the 512 KB cap applies with far less headroom, which is why it
# additionally carries its own ratchet.
RUNALL_FILE = "run-all.js"
GENERATED_FILES = [*PHASE_FILES, RUNALL_FILE]

# Headroom ratchet, separate from the runtime's hard cap. run-all grows at
# roughly eight times the rate of any single phase file, and the failure mode
# at 512 KB is the runtime refusing to parse — not a warning. Raising this
# number is a deliberate act: the right first response to hitting it is to
# shorten prompts in scripts/workflowgen/, not to move the ceiling.
RUNALL_MAX_BYTES = 345400  # 2026-08-17: +400 — 59579b3's retry hint now names both failure modes of an unresolvable citation instead of only the out-of-range one: (a) the cited file does not exist (the common case — an out-of-tree path like `spec_parser.py` in a project that has none), (b) the line number is past the end. Three prompt lines in js_blocks.render_shell_wrapper_retry, inlined into phase1/phase2/phase6 and summed again in run-all. Measured 345314; the ceiling is set 86 bytes above it rather than exactly at it so the next reader is not forced to re-ratchet for a typo fix. Previous: 345000. 2026-08-14: v33b P2 citation-validator fix (run-all.js halt on taskq-super). Inlines the prompt rule additions (positive + negative citation example + DIGITS-after-colon rule) into every phase's buildBPrompt, plus the abLoop try/catch + reject-block prepend into Phase 2's sub-task A/B loop. Phase 1/2/6 all gain ~50 lines; run-all.js is their sum + the inlined copy. Pure bug-fix growth (mirrors Phase 1's existing pattern); no new agents or dispatches. Previous: 340000.


def _read(filename: str) -> str:
    return (WORKFLOWS_DIR / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_no_banned_runtime_constructs(filename):
    violations = find_banned_constructs(_read(filename))
    assert not violations, f"{filename}: playbook §4 banned construct(s) found: {violations}"


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_under_512kb_hard_cap(filename):
    size = len(_read(filename).encode("utf-8"))
    assert size <= MAX_BYTES, f"{filename}: {size} bytes exceeds the {MAX_BYTES}-byte runtime parse limit"


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_meta_is_first_statement(filename):
    stripped = strip_comments_and_strings(_read(filename)).lstrip()
    assert stripped.startswith("export const meta"), (
        f"{filename}: `export const meta` is not the first statement "
        f"(validator hard error) — found: {stripped[:40]!r}"
    )


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not found on PATH — syntax gate needs Node.js (dev-only dependency)",
)
@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_node_check_syntax(filename, tmp_path):
    """Parse each file the way the RUNTIME parses it.

    Round 23 站2 — this test used to run `node --check <file>` directly and
    was a dead guard: a `.js` path with no package.json "type" is parsed as
    CommonJS, `export const meta` is a syntax error immediately, and
    `node --check` returns 0 anyway. Verified against a file containing a
    deliberate unescaped-apostrophe error — exit 0, no diagnostic. Every
    workflow file starts with `export const meta`, so the check could never
    fail for any of them.

    The runtime evaluates the file body with top-level await and top-level
    return, i.e. as a function body — which is exactly what
    scripts/workflowgen/js_src/sim_runner.mjs reproduces. Wrapping the same
    way before `node --check` makes this a real parse of real script text.
    (The bug this now catches is not hypothetical: run-all's first draft put
    an apostrophe inside meta.description and broke the whole file.)
    """
    src = _read(filename)
    body = re.sub(r"^export const meta", "const meta", src, count=1, flags=re.MULTILINE)
    assert body != src, f"{filename}: no `export const meta` to unwrap"
    wrapped = tmp_path / "wrapped.cjs"
    wrapped.write_text(
        "(async function (agent, phase, log, args, budget) {\n" + body + "\n})\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "--check", str(wrapped)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"{filename}: node --check failed:\n{result.stderr}"


def test_runall_stays_within_its_headroom_ratchet():
    """The 512 KB cap above is the cliff; this is the guard rail.

    run-all inlines eight bodies, so it absorbs eight files worth of
    growth. Hitting this means shortening prompts, not raising the number
    — see RUNALL_MAX_BYTES.
    """
    size = len(_read(RUNALL_FILE).encode("utf-8"))
    assert size <= RUNALL_MAX_BYTES, (
        f"{RUNALL_FILE}: {size} bytes over the {RUNALL_MAX_BYTES}-byte headroom "
        f"ratchet ({100 * size / MAX_BYTES:.0f}% of the runtime cap)"
    )


class TestScannerHandlesRegexLiterals:
    """Round 23 站3 — a `/.../` literal may contain a quote character.

    `persistApproval` does `approvalPayload.replace(/'/g, "'\\''")`. Before
    the scanner knew about regex literals, that apostrophe opened a phantom
    string and everything after it was misclassified until the quote count
    happened to re-balance. In the eight per-phase files it re-balanced by
    luck; in run-all.js, where eight bodies follow one another, it did not,
    and Python source inside a bash command string (`os.path.getsize`)
    surfaced as a live `path.*` violation.

    This matters more than a false positive: comment_line_numbers DELETES
    what it classifies, so a desynced scanner could drop a prompt line
    containing `https://` as if it were a comment.
    """

    REAL_CASE = "const escaped = payload.replace(/'/g, \"'\\\\''\")\nconst after = 1\n"

    def test_quote_inside_a_regex_does_not_open_a_string(self):
        masked = strip_comments_and_strings(self.REAL_CASE)
        assert "const after = 1" in masked, (
            "code after a regex containing an apostrophe was swallowed as string"
        )

    def test_url_in_a_string_is_not_mistaken_for_a_comment(self):
        js = "const a = 1\nconst u = 'see https://example.com/x for details'\n"
        assert comment_line_numbers(js) == set()

    def test_division_is_still_division(self):
        js = "const half = total / 2\nconst rest = other / 3\n"
        assert "const half = total / 2" in strip_comments_and_strings(js)

    def test_pure_comment_lines_are_reported(self):
        js = "// leading note\nconst a = 1 // trailing\n/* block\n   more */\n"
        assert comment_line_numbers(js) == {1, 3, 4}

    def test_comment_text_inside_a_template_literal_is_not_a_comment(self):
        js = "const p = `line one\n// not a comment, this is prompt text\n`\n"
        assert comment_line_numbers(js) == set()


def test_node_check_wrapper_actually_rejects_broken_syntax(tmp_path):
    """Negative control for the wrapper above — without it, this passes."""
    broken = tmp_path / "broken.cjs"
    broken.write_text(
        "(async function () {\nconst meta = { d: 'it's broken' }\n})\n", encoding="utf-8",
    )
    assert subprocess.run(
        ["node", "--check", str(broken)], capture_output=True, text=True, timeout=30,
    ).returncode != 0


# ---------------------------------------------------------------------------
# Round 26 站5 — every dispatch goes through the observability wrapper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_every_dispatch_goes_through_the_wrapper(filename):
    """No raw `await agent(` outside the wrapper's own single call.

    The harness has two dispatch substrates and instrumented one: per-FR steps go
    through run-fr-step -> core/agent_spawner.spawn(), which logs cost/turns/
    outcome, while everything the workflow dispatches itself was invisible.
    taskq-plus: 42 spawn-log entries, all phase 3, so run-report's "42 dispatches,
    failure rate 9.52%" was a P3/P4 number presented as the run's.

    generate_workflows._inject_dispatch_wrapper rewrites the call sites (118 in
    run-all), which means a future spec module could still hand-write a raw one.
    This is the guard that makes that fail instead of silently leaving a hole.
    """
    text = _read(filename)
    if "await dispatch(" not in text:
        pytest.skip(f"{filename} dispatches no agents")
    # The wrapper itself must call agent() — that is the one legitimate site.
    assert text.count("res = await agent(") == 1, (
        f"{filename}: expected exactly one `res = await agent(` (the wrapper's own "
        f"call); found {text.count('res = await agent(')}"
    )
    assert text.count("await agent(") == 1, (
        f"{filename}: {text.count('await agent(') - 1} raw `await agent(` call(s) "
        f"bypass the dispatch() wrapper, so those dispatches never reach "
        f"sessions_spawn.log"
    )


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_the_wrapper_is_declared_exactly_once(filename):
    """run-all inlines eight phase bodies; eight copies would be a SyntaxError.

    spec_runall consumes generate_raw() for exactly this reason — the composite is
    wrapped once, over the assembled file.
    """
    text = _read(filename)
    if "await dispatch(" not in text:
        pytest.skip(f"{filename} dispatches no agents")
    assert text.count("async function dispatch(") == 1
    assert text.count("const __dispatchLog = []") == 1


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_the_wrapper_records_before_it_rethrows(filename):
    """A dead sub-agent cannot log itself; the wrapper observes the outcome.

    If the catch block ever stops pushing a record, failed dispatches vanish from
    the population again — which is the exact asymmetry that made the failure rate
    a P3-only number.
    """
    text = _read(filename)
    if "await dispatch(" not in text:
        pytest.skip(f"{filename} dispatches no agents")
    catch_start = text.index("} catch (err) {")
    catch_end = text.index("throw err", catch_start)
    assert "__dispatchLog.push(" in text[catch_start:catch_end], (
        f"{filename}: the wrapper rethrows without recording the failure"
    )


# ---------------------------------------------------------------------------
# Round 50 站0 — every halt goes through the halt helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_every_terminal_halt_goes_through_the_halt_helper(filename):
    """No bare `return { error: ... }`.

    Round 48 站2 gave run-all six recordBlock call sites, all on the phase
    loop's boundary. Measured 2026-08-13 across the shipped workflows: the
    eight phase files return `{ error: ... }` from 55 distinct top-level sites
    and 38 nested ones. Every one of the 55 reaches the loop and is recorded —
    under the single step name `phase-error`.

    So the event is not lost; its coordinate is. A full P1–P8 run produced one
    workflow_blocks.jsonl row, and that row says the phase and nothing about
    which of Phase 6's eight halts fired. Reading the message and matching it
    back to a source line is the manual step the ledger exists to remove.

    Two smaller consequences of the same shape: a phase workflow launched on
    its own (not through run-all) records nothing at all, and the ~30 halts
    that the CLI raised and a workflow retried past never reach this ledger
    either — they are in degradations.jsonl under a different vocabulary.

    The helper is the one place that knows both the shape to return and the
    coordinate to record, so the two cannot come apart.
    """
    code = strip_comments_and_strings(_read(filename))
    bare = code.count("return { error:")
    assert bare == 0, (
        f"{filename}: {bare} bare `return {{ error: ... }}` site(s). Each is a "
        f"halt whose step name is lost by the time it is recorded — call the "
        f"halt() helper instead so the coordinate travels with the event"
    )


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_the_halt_helper_is_declared_exactly_once(filename):
    """Same rule the dispatch wrapper follows: one declaration per file."""
    text = _read(filename)
    if "halt(" not in strip_comments_and_strings(text):
        pytest.skip(f"{filename} has no halt sites")
    assert strip_comments_and_strings(text).count("function halt(") == 1, (
        f"{filename}: halt() must be declared exactly once"
    )
