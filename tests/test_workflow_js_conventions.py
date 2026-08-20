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

from pathlib import Path

import pytest

from scripts.workflow_audit.js_lint import (
    comment_line_numbers,
    find_banned_constructs,
    strip_comments_and_strings,
)
from scripts.workflow_audit.js_parse import node_available, parse_problem
from scripts.workflowgen.artifact_limits import (
    MAX_BYTES,
    RUNALL_FILE,
    RUNALL_MAX_BYTES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".claude" / "workflows"

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
#
# Round 60 站1: both ceilings and the parse wrapper now live in
# scripts/workflowgen/artifact_limits.py and scripts/workflow_audit/js_parse.py,
# because `generate_workflows.py --write` has to apply them too. This file
# keeps guarding the SHIPPED files — a hand edit never passes the generator.
GENERATED_FILES = [*PHASE_FILES, RUNALL_FILE]


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
    not node_available(),
    reason="node not found on PATH — syntax gate needs Node.js (dev-only dependency)",
)
@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_node_check_syntax(filename):
    """Parse each SHIPPED file the way the RUNTIME parses it.

    Round 23 站2 — this test used to run `node --check <file>` directly and
    was a dead guard: a `.js` path with no package.json "type" is parsed as
    CommonJS, `export const meta` is a syntax error immediately, and
    `node --check` returns 0 anyway. Verified against a file containing a
    deliberate unescaped-apostrophe error — exit 0, no diagnostic. Every
    workflow file starts with `export const meta`, so the check could never
    fail for any of them.

    Round 60 站1 moved the wrapper into scripts/workflow_audit/js_parse.py so
    `generate_workflows.py --write` applies the same parse before it writes.
    What stays here is the shipped-file half: the generator cannot see an
    edit made directly to `.claude/workflows/`.
    """
    problem = parse_problem(_read(filename))
    assert problem is None, f"{filename}: {problem}"


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


def _ratchet_line() -> str:
    source = (
        REPO_ROOT / "scripts" / "workflowgen" / "artifact_limits.py"
    ).read_text(encoding="utf-8")
    return next(
        ln for ln in source.splitlines() if ln.startswith("RUNALL_MAX_BYTES")
    )


def test_the_ratchet_notes_arithmetic_closes():
    """Round 65 站0 — every entry's `Previous:` must be the ceiling before it.

    The note is a chain, newest first: each entry states the delta it applied
    and the value it applied it to, so `Previous + delta` has to equal the
    ceiling the entry produced — the constant for the newest entry, and the
    next entry's `Previous` for every other. Six of the seven links held when
    this was written; the newest read `+35 … Previous: 348608` while the
    constant was still 348608, which is a ceiling that did not move written up
    as one that did.

    Round 64 added the sibling check below, on `Measured`. It passed: 348336
    was this tree's size. The entry lied in the one field the guard did not
    read — which is why this one reads the fields as a system rather than one
    at a time.
    """
    import re

    line = _ratchet_line()
    deltas = [int(m) for m in re.findall(r"(?<![\w.])([+-]\d+)\s*—", line)]
    previous = [int(m) for m in re.findall(r"Previous:\s*(\d+)", line)]
    assert deltas, "the RUNALL_MAX_BYTES note no longer states any delta"
    assert len(previous) >= len(deltas), (
        f"{len(deltas)} deltas but only {len(previous)} `Previous:` values — "
        f"every entry that moved the ceiling has to say what it moved it from"
    )
    produced = RUNALL_MAX_BYTES
    for index, (delta, prior) in enumerate(zip(deltas, previous)):
        assert prior + delta == produced, (
            f"entry {index} of the RUNALL_MAX_BYTES note says it applied "
            f"{delta:+d} to {prior}, which is {prior + delta}; the ceiling it "
            f"produced is {produced}. An entry that leaves the ceiling where it "
            f"was must record +0, not the size delta of the file underneath it"
        )
        produced = prior


def test_the_ratchet_note_reports_the_size_it_measured():
    """Round 64 站0 — the ceiling's own note must describe THIS tree.

    `RUNALL_MAX_BYTES`'s inline history opens with the current entry's
    measurement. Measured 2026-08-20: the newest entry claims 348693, a
    number run-all.js has never had — 348457 at the commit that wrote it
    (983b46e), 346724 today, because 6e7942e shrank the file by 1733 bytes
    and left the note alone. A ceiling standing 2276 bytes above a size
    nobody re-measured is not a ratchet; it is a memoir.

    The rule this pins is the cheapest one that stays honest: whatever the
    newest entry says it measured has to be what the shipped file weighs.
    Growing run-all then costs a re-measurement, which is the deliberate
    act the note itself asks for.
    """
    import re

    source = (
        REPO_ROOT / "scripts" / "workflowgen" / "artifact_limits.py"
    ).read_text(encoding="utf-8")
    line = next(
        ln for ln in source.splitlines() if ln.startswith("RUNALL_MAX_BYTES")
    )
    claimed = re.search(r"Measured (\d+)", line)
    assert claimed, "the RUNALL_MAX_BYTES note no longer states what it measured"
    size = len(_read(RUNALL_FILE).encode("utf-8"))
    assert int(claimed.group(1)) == size, (
        f"the RUNALL_MAX_BYTES note says it measured {claimed.group(1)} bytes; "
        f"{RUNALL_FILE} weighs {size}. Re-measure and update the entry — the "
        f"ceiling is only a ratchet while the number under it is this tree's"
    )


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_no_shipped_workflow_classifies_a_block_by_string_length(filename):
    """Round 64 站0 — `length < 10` misclassified a 9-char `SAB: PASS`.

    9fd9a12 pinned the absence of that token, but over `GENERATORS` — the
    eight phase generators. run-all.js and its driver are not in that dict:
    `spec_runall.py` writes the `session_limit_blocked` branch itself, from
    code no phase generator produces, so the same magic number could come
    back there with the guard still green. This scans the SHIPPED files,
    which is where the runtime reads them (Round 36).

    `length < 100` is the review-reason min-length check and out of scope.
    """
    import re

    hits = re.findall(r"\blength\s*<\s*10\b", _read(filename))
    assert not hits, (
        f"{filename}: {len(hits)} occurrence(s) of the magic number "
        f"`length < 10` — a short but non-empty reply would again be "
        f"misclassified as a session-limit block"
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


@pytest.mark.skipif(
    not node_available(),
    reason="node not found on PATH — syntax gate needs Node.js (dev-only dependency)",
)
def test_node_check_wrapper_actually_rejects_broken_syntax():
    """Negative control for the wrapper above — without it, this passes.

    The apostrophe is `f4be095`'s exact defect: it closes the single-quoted
    string early and everything after it is parsed as code.
    """
    broken = "export const meta = { d: 'it's broken' }\n"
    assert parse_problem(broken) is not None


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
def test_the_wrapper_records_before_it_rethrows(filename):
    """A dead sub-agent cannot log itself; the wrapper observes the outcome.

    If the catch block ever stops pushing a record, failed dispatches vanish
    from the population again — which is the exact asymmetry that made the
    failure rate a P3-only number.

    Round 64 站0 restores this guard. 6e7942e deleted the mechanism it
    watches in a commit whose message says the wrapper's *comment* was
    trimmed, and 020695e rewrote this registry entry into the opposite
    claim. Measured on the corpus: the wrapper was still producing rows
    4.5 hours before it was removed, and the 11 EMPTY/ERROR rows it caught
    across six projects exist in no other record.
    """
    text = _read(filename)
    if "await dispatch(" not in text:
        pytest.skip(f"{filename} dispatches no agents")
    catch_start = text.index("} catch (err) {")
    catch_end = text.index("throw err", catch_start)
    assert "__dispatchLog.push(" in text[catch_start:catch_end], (
        f"{filename}: the wrapper rethrows without recording the failure"
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
def test_the_preamble_does_not_suppress_the_agent(filename):
    """The bookkeeping preamble rides on a prompt whose reply matters.

    Round 64 站1: its old wording — "ignore its output, and do NOT mention it
    in your reply" — is the same suppress-verification clause 6e7942e removed
    from recordBlock's prompt, on a preamble prepended to nearly every
    dispatch in the run rather than to one.
    """
    text = _read(filename)
    if "__dispatchFlushPreamble" not in text:
        pytest.skip(f"{filename} dispatches no agents")
    start = text.index("function __dispatchFlushPreamble()")
    end = text.index("\n}\n", start)
    body = text[start:end]
    for clause in ("do NOT mention it", "ignore its output", "forget this block"):
        assert clause not in body, (
            f"{filename}: the bookkeeping preamble tells the agent to {clause!r}"
        )


@pytest.mark.parametrize("filename", [RUNALL_FILE])
def test_record_block_is_an_accountable_dispatch(filename):
    """recordBlock's dispatch is schema'd and its result is used, like every
    other verified dispatch in this file — not fired and discarded.

    recordBlock only exists in run-all.js (spec_runall composes it; the eight
    phase files and harness-repair.js do not call it).
    """
    text = _read(filename)
    start = text.index("async function recordBlock(")
    end = text.index("\n}\n", start)
    body = text[start:end]
    assert "schema: RECORD_BLOCK_SCHEMA" in body, (
        f"{filename}: recordBlock's dispatch no longer carries a schema"
    )
    assert "const result = await dispatch(" in body, (
        f"{filename}: recordBlock no longer captures its dispatch's result"
    )
    assert "Do nothing else" not in body, (
        f"{filename}: recordBlock's prompt reintroduced a suppress-verification clause"
    )
    assert "rather than retrying" not in body, (
        f"{filename}: recordBlock's prompt reintroduced a suppress-retry clause"
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


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_no_shipped_workflow_hardcodes_the_project_layout_into_a_coverage_run(filename):
    """Round 65 站0 — the coverage command must not out-guess resolve_targets.

    `03-development/tests` and `03-development/src` are the FIRST choice of
    `ProjectLayout.active_test_dir` / `active_src_dir`, not the only one: both
    fall back to root `tests/` and `src/`, documented and exercised. Gate 3
    re-measures coverage through `resolve_targets`, which honours the fallback
    and an explicit `.coveragerc` `[run] source` on top of it.

    So on a root-layout project `b12ff21`'s command exits 4 with no tests
    collected, `coverage_raw.txt` is written empty, and the same prompt tells
    the agent that a mismatch against the framework's own measurement is
    reported CRITICAL — a difference the framework manufactured, charged to
    the project. Round 32 站3 removed the fifth hand-rolled copy of this exact
    probe from `harness/tool_runners.py`; this is the same copy one layer up,
    in a prompt.

    The resolved targets travel in `.sessi-work/phaseN_ctx.json`, which
    `load-context` writes from `resolve_targets` and which these workflows
    already read for other fields.
    """
    for lineno, line in enumerate(_read(filename).splitlines(), 1):
        if "-m pytest" not in line or "--cov=" not in line:
            continue
        assert "03-development/" not in line, (
            f"{filename}:{lineno} builds a coverage run against a hardcoded "
            f"03-development/ path. The project's test and source directories "
            f"are resolve_targets' answer, not the prompt's"
        )
