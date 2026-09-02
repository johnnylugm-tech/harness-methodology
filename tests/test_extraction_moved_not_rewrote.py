"""An extracted helper must be the code that was there, not code like it.

Round 81 站6-9. Round 80 froze this repo's four largest functions rather than
split them, and the reason it recorded was:

    區塊抽取會改動函式自身文字,byte-equal 規則不適用

Half true. Extraction changes a function's own text — the run leaves and a call
site arrives — but it does not change the text of the run. And because a
module-level function's body and a run inside another module-level function
both sit at ONE indent level, the moved lines need no reindentation at all:
they are byte-identical, and Round 49-B's rule applies unchanged.

That is what this file checks, against the file as it stood before the
extraction rather than against the extraction's own output. `tests/golden/
extraction/<module>.py.before` is that file. It is large and it is meant to be:
a recording of the source at the moment of the move is the only thing that can
answer "was this a move?" afterwards.

WHY NOT REGENERATE IT LIKE THE OTHER GOLDENS

tests/golden/god_file_split/surface.json carries fingerprints and is
regenerable with REGEN_SPLIT_GOLDEN when a function is deliberately changed.
This one is not regenerable at all. Once an extracted body is edited on purpose,
its entry is REMOVED from _EXTRACTED with the reason in the commit — the claim
"this is the code that was there" has an expiry date, and pretending otherwise
by re-recording it would delete the only evidence the move ever happened.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.dataflow import _bound, _loaded
from tests.support.pipeline import _lookup, original_statements, reconstructed

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden" / "extraction"

#: label -> what was extracted, where from, and against which recording.
#: One entry per extraction EVENT, not per module: `cli/phase_cmds.py` is
#: extracted twice in this round, and 站7's helpers are byte-present in the
#: file as 站6 left it, not in the file 站6 started from.
#: An entry leaves this dict the moment one of its bodies is deliberately
#: edited — with the reason in that commit.
_EXTRACTED: "dict[str, dict]" = {
    "phase_cmds._advance_prechecks": {
        "module": "cli/phase_cmds.py",
        "before": "phase_cmds.py.before",
        "caller": "_advance_prechecks",
        "prefix": "_precheck_",
        "generated_tail": True,
        # Round 87 站5 added `_precheck_p3_criteria_review` — NEW work in the
        # caller, not an edit to any moved body. Reconstruction therefore
        # returns thirteen statements `phase_cmds.py.before` never had, and
        # that one claim has expired: `_advance_prechecks` is no longer the
        # function that was there, because the P3 exit now has a check it did
        # not have. The other three claims are untouched and still run — every
        # extracted body is still byte-identical, reads only what it is given,
        # and has its early return propagated.
        #
        # This is Round 83 站1's shape exactly, and the comment below (written
        # when that round's key was removed) invites it back with its own
        # reason. The alternative was worse both ways: naming the new helper
        # something that does not match `prefix` games the guard, and removing
        # the whole entry deletes three live claims to retire one.
        "reconstructible": False,
        "helpers": (
            "_precheck_cleared_dir_evidence",
            "_precheck_backup_artifacts",
            "_precheck_manifest_and_p1_baselines",
            "_precheck_per_fr_gate1_and_phase_truth",
            "_precheck_early_stage_pass",
            "_precheck_deliverable_anchors",
            "_precheck_scope_violations",
            "_precheck_p3_security_and_quality",
            "_precheck_stage_pass_staging",
        ),
    },
    "phase_cmds.cmd_advance_phase": {
        "module": "cli/phase_cmds.py",
        # 站7's recording is the file as 站6 LEFT it. Using 站6's recording
        # would ask whether these bodies existed before an extraction they were
        # not part of, which is a different and false question.
        "before": "phase_cmds.py.before-station7",
        "caller": "cmd_advance_phase",
        # `_advance_step_`, not `_advance_`: this file already had
        # `_advance_prechecks`, `_advance_fsm` and `_advance_commit_targets`,
        # and the shorter prefix pulled all three into the pipeline view — five
        # tests passed for a broader reason than they meant to.
        "prefix": "_advance_step_",
        "generated_tail": True,
        "helpers": (
            "_advance_step_refuse_phase_9",
            "_advance_step_refuse_uncommitted",
            "_advance_step_refuse_open_obligations",
            "_advance_step_run_fsm_transition",
            "_advance_step_seed_p8_archive",
            "_advance_step_write_next_plan_header",
            "_advance_step_commit_and_push",
        ),
    },
    # `harness_bridge.finalize_gate` LEFT this dict in Round 83 站5, under the
    # rule stated in this module's docstring: an entry goes the moment one of
    # its bodies is deliberately edited, with the reason in that commit.
    #
    # Round 83 站1 had already retired one of its four claims — the round added
    # a block to the CALLER (the `_unsourced` raise beside `_unmeasured`), so
    # "undoing the extraction gives back the function that was there" stopped
    # being true, while all sixteen extracted bodies were still byte-identical.
    # That was recorded with a `reconstructible: False` key and a filter on the
    # reconstruction test's parametrize, and the other three claims kept
    # running. Both are gone with the entry: a key no entry carries and a
    # filter that excludes nothing is machinery for a case that no longer
    # exists, and the round that next needs it can add it with its own reason.
    #
    # 站5 then edited `_stage_declared_absent` itself. It stopped asking
    # whether a declared dimension is in THIS gate — a question whose answer is
    # always the framework's own gate layering, 79 rows on taskq-cc-new and not
    # one a real finding — and asks whether any gate config contains it at all.
    # The body is no longer the code that was moved, so the claim "this is the
    # code that was there" has expired for this extraction event, and
    # re-recording harness_bridge.py.before to make it pass again would delete
    # the only evidence the move ever happened.
    #
    # tests/golden/extraction/harness_bridge.py.before is deliberately KEPT
    # after its last reader: it is the recording of the tree at the moment of
    # Round 81 站8's move, and deleting it is the thing the docstring above
    # forbids doing indirectly.
    "fr_cmds.cmd_run_fr_step": {
        "module": "cli/fr_cmds.py",
        "before": "fr_cmds.py.before",
        "caller": "cmd_run_fr_step",
        # `_frstep_`, chosen against the file's existing `_fr_step_already_done`
        # and `_fr_prompt_*`: 站7 shipped a prefix that collided with three
        # untouched functions and only the reconstruction assertion noticed.
        "prefix": "_frstep_",
        # This function's tail is not one of its extractable runs, so nothing
        # generated a fall-through for it.
        "generated_tail": False,
        "helpers": (
            "_frstep_skip_if_already_done",
            "_frstep_route_dispatch_error",
            "_frstep_gate1_paper_trail",
            "_frstep_push_checkpoint",
        ),
    },
}


def _function(module: str, name: str) -> "tuple[ast.FunctionDef, str]":
    """The def and the text of the file that now defines it.

    Round 82 站1. This used to search the caller's own tree, which stopped
    being true the moment 站2-站6 moved the helpers into modules of their own.
    `_lookup` follows the object instead of the path — and it matters for more
    than finding the def: `test_no_helper_reads_a_name_nobody_gives_it`
    resolves free names against module scope, and after the move that is the
    HELPER's module, not the caller's.
    """
    return _lookup(module, name)


def _body_text(source: str, func: "ast.FunctionDef") -> str:
    """The helper's body, docstring excluded — the part that was moved.

    A trailing `return None` is excluded too. It is generated rather than
    moved: mypy requires the fall-through path of an `int | None` return to be
    explicit, and the runs themselves never contain one — the extraction rule
    refuses a run that returns None, because the call site uses exactly that to
    mean "the helper did not return".
    """
    lines = source.splitlines(keepends=True)
    first = func.body[0]
    start = func.body[1].lineno if (
        isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str) and len(func.body) > 1
    ) else first.lineno

    end = func.end_lineno
    last = func.body[-1]
    if isinstance(last, ast.Return) and isinstance(last.value, ast.Constant) \
            and last.value.value is None:
        end = last.lineno - 1
        while end > start and lines[end - 1].lstrip().startswith("#"):
            end -= 1

    return "".join(lines[start - 1:end])


@pytest.mark.parametrize("label", sorted(_EXTRACTED))
def test_every_extracted_body_is_byte_identical_to_what_it_replaced(label):
    spec = _EXTRACTED[label]
    before = (GOLDEN / spec["before"]).read_text(encoding="utf-8")

    rewritten = []
    for name in spec["helpers"]:
        func, home = _function(spec["module"], name)
        body = _body_text(home, func)
        if body not in before:
            rewritten.append(name)

    assert not rewritten, (
        f"these helpers are not the code that was extracted — their bodies do "
        f"not appear verbatim in {spec['before']}:\n  "
        + "\n  ".join(rewritten)
        + "\n\nAn extraction is a MOVE. If one of these was deliberately "
          "changed, remove it from _EXTRACTED in that commit and say why; do "
          "not re-record the 'before' file, which would delete the only "
          "evidence the move happened."
    )


@pytest.mark.parametrize("label", sorted(_EXTRACTED))
def test_no_helper_reads_a_name_nobody_gives_it(label):
    """The static replacement for "every extracted run must be covered".

    The plan gated extraction on the suite executing each run, because a
    miscomputed parameter list surfaces as a `NameError` and a `NameError` is
    only loud where something runs. That gate was unreachable honestly — the
    runs inside `_advance_prechecks` sit behind its manifest-integrity check,
    and getting a fixture past it means hand-writing finalize receipts, which
    tests/test_evidence_outlives_the_phase.py already adjudicated as "writing
    fake gate evidence to test a guard is the thing the guard exists to stop".

    This asks the same question exhaustively instead. Every name a helper reads
    must be its own parameter, something bound inside it (at any depth — a
    comprehension variable is read in the scope that binds it), or a
    module-level name. Nothing left over means no path through it can raise
    `NameError`, whether or not any test walks that path.

    ruff's F821 is the primary net for this and is the one that actually fired:
    the first generated call site passed a comprehension variable as a
    parameter and F821 said so before any test ran. This assertion is the part
    F821 cannot express — it is scoped to the helpers this round created, so a
    later edit that reintroduces a free name in one of them is attributed to
    the extraction rather than to whoever touches the file next.
    """
    import builtins

    spec = _EXTRACTED[label]

    def bound_at_any_depth(nodes: "list[ast.stmt]") -> "set[str]":
        """Unlike `_bound`, descends into nested scopes: for the NameError
        question a comprehension variable IS resolved where it is read."""
        names: set[str] = set()
        for node in nodes:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(
                    child.ctx, (ast.Store, ast.Del)
                ):
                    names.add(child.id)
                elif isinstance(child, ast.arg):
                    names.add(child.arg)
                elif isinstance(child, (ast.Import, ast.ImportFrom)):
                    for alias in child.names:
                        names.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(child, ast.ExceptHandler) and child.name:
                    names.add(child.name)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef)):
                    names.add(child.name)
        return names

    unresolved: "list[str]" = []
    for name in spec["helpers"]:
        func, home = _function(spec["module"], name)
        # Module scope is the HELPER's own, read fresh per helper: after 站2-站6
        # they no longer all live in one file, and asking the caller's module
        # whether a name resolves would answer about the wrong namespace.
        module_scope = _bound(list(ast.parse(home).body)) | set(dir(builtins))
        params = {a.arg for a in func.args.args + func.args.kwonlyargs}
        loose = _loaded(func.body) - bound_at_any_depth(func.body) - params - module_scope
        if loose:
            unresolved.append(f"{name}: {sorted(loose)}")

    assert not unresolved, (
        "these extracted helpers read names that are neither their parameters, "
        "nor bound inside them, nor module-level — every one is a NameError "
        "waiting for the right input:\n  " + "\n  ".join(unresolved)
    )


@pytest.mark.parametrize(
    "label",
    sorted(k for k, v in _EXTRACTED.items() if v.get("reconstructible", True)),
)
def test_undoing_the_extraction_gives_back_the_original_function(label):
    """The complete claim: same statements, same ORDER, nothing added or lost.

    Skipped for an entry marked `reconstructible: False` — a caller that has
    since gained NEW work is no longer the function that was there, while its
    extracted bodies can still be byte-identical. That entry's reason is beside
    the key, and its other three claims keep running.

    Byte-identity proves each body was not rewritten. The data-flow rule proves
    no binding escapes. Neither says the call sites are in the order the runs
    were, and a reordering would be invisible to both. This puts the helpers
    back where they are called, strips the propagation scaffolding, and asks
    whether what comes out is the function that was there.

    It is also what Round 80's re-open condition — "a behavioural golden
    pinning finalize_gate's verdict / exit code / BLOCK text, with its coverage
    measured" — was reaching for. That condition assumed decomposition meant
    rewriting, and a behavioural golden is how you check a rewrite. This is not
    a rewrite, and an AST identity is a stronger answer than any matrix of
    fixtures: it holds for every input, including the ones nobody thought of.
    """
    spec = _EXTRACTED[label]
    rebuilt = reconstructed(spec["module"], spec["caller"],
                            helper_prefix=spec["prefix"],
                            generated_tail=spec["generated_tail"])
    original = original_statements(spec["before"], spec["caller"])

    got = [ast.dump(n) for n in rebuilt]
    want = [ast.dump(n) for n in original]

    assert got == want, (
        f"undoing {label}'s extraction does not give back the function that "
        f"was there: {len(got)} statements against {len(want)}.\n"
        + "\n".join(
            f"  [{i}] differs" for i in range(min(len(got), len(want)))
            if got[i] != want[i]
        )
    )


@pytest.mark.parametrize("label", sorted(_EXTRACTED))
def test_the_caller_still_propagates_every_early_return(label):
    """A helper that can return a code, called without checking it, is a
    silently disabled check — the shape Round 43 named (detected, no executor).
    """
    spec = _EXTRACTED[label]
    caller, _ = _function(spec["module"], spec["caller"])
    helpers = set(spec["helpers"])

    can_return = {
        name for name in helpers
        if any(isinstance(n, ast.Return) and n.value is not None
               for n in ast.walk(_function(spec["module"], name)[0]))
    }

    checked: "set[str]" = set()
    body = caller.body
    for index, stmt in enumerate(body):
        if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)):
            continue
        func = stmt.value.func
        # `self._stage_x(...)` as well as `_precheck_x(...)`: finalize_gate's
        # helpers are methods, and a check that only knew the bare-name shape
        # reported every one of them as unchecked.
        called = (func.attr if isinstance(func, ast.Attribute)
                  else getattr(func, "id", None))
        if called not in helpers:
            continue
        target = stmt.targets[0]
        following = body[index + 1] if index + 1 < len(body) else None
        if (isinstance(target, ast.Name) and isinstance(following, ast.If)
                and any(isinstance(n, ast.Return) for n in ast.walk(following))):
            checked.add(called)

    assert can_return <= checked, (
        f"these helpers can return an exit code that "
        f"{spec['caller']} never looks at, so the check they perform "
        f"cannot block anything: {sorted(can_return - checked)}"
    )
