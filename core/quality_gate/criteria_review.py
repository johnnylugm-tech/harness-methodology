"""One reader who sees the requirement and the assertion at the same time.

Round 87 站5, the station Round 87 deliberately did not build. Its re-open
condition — 老闆 agreeing to a new dispatch at P3 — is met.

WHAT WENT WRONG, AND WHY NOTHING SAW IT

taskq-redo's FR-07 chain, each link consistent with the one before it:

    SPEC.md    `| **v1** | 建立 tasks、api_keys 兩表 | drop 兩表 |`
    SRS AC-7.1 weakened to "upgrade head and downgrade base both exit 0"
    test_fr07.py:217  `# v1 tables must remain (downgrade base leaves v1 in place)`
                :218  `assert "tasks" in table_names_after`
    v1_initial.py     `def downgrade(): pass`

Every adjacent pair agrees, so no adjacent comparison can find the defect.
Only the ENDS disagree, and nothing in the framework read both ends. The
requirement source here is SPEC.md, not SRS.md: by the time the chain reaches
SRS the requirement has already been narrowed, and a review anchored on the
narrowed text approves the inversion.

No mechanical rule decides this one. Round 87 站3 measured AC-prose numeric
extraction at 86% noise, and this is a semantic inversion rather than a
number — the assertion that fires when the requirement HOLDS instead of when
it is violated. An LLM is the only instrument that reads it. What this module
owns is everything around that judgement: which sources the reviewer was
given, that the reviewer's citations landed on both ends, and that the
assertions it approved are still the assertions on disk.

WHY THE DIGEST IS OVER DECLARED TEST FUNCTIONS AND NOT OVER THE FILE

The first draft pinned each test file's sha256. Measured against four corpus
projects' FR-07 history, an FR's test file is rewritten 2-7 times after
`test(RED)` — MIRROR alignment, GREEN, coverage tests plus `# pragma: no
cover`, Gate 2 fix rounds, and P3-exit lint cleanup — so a file digest goes
stale in 4 of 4 projects and mostly for reasons that touch no assertion.

Restricted to the FR's TEST_SPEC-declared test functions, AST-normalised:

    taskq-cc      5 declared   0 changed
    taskq-new     7 declared   0 changed
    taskq-redo    5 declared   1 changed  (test_v2_unique_index_survives_round_trip)
    taskq-cc-new  5 declared   1 changed  (test_round_trip_reversibility_v3_data_move)

Two projects need no re-review at all; the other two changed one reviewed
assertion each, and a block naming that test is the correct answer there.
Comments, pragmas, `type: ignore` and formatting are invisible to `ast.dump`,
which is exactly the difference between "the file moved" and "what I approved
moved".
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from core.quality_gate.agent_b_approvals import _resolve_citation_path
from core.quality_gate.parsers import extract_fr_section
from core.utils.project_layout import ProjectLayout

__all__ = [
    "REVIEW_BLOCK_KEY",
    "approval_defects",
    "review_prompt",
    "review_sources",
]

#: The key under which `review-fr-tests` records what the harness measured.
#: Written by the harness, never by the reviewer — Round 33/Round 44's rule
#: that the framework owns its own numbers.
REVIEW_BLOCK_KEY = "criteria_review"

#: A declared name may carry a parametrize suffix or empty call parens in
#: TEST_SPEC.md (`test_foo[case-1]`, `test_foo()`). The same normalisation
#: `cli.fr_prompts._shared._compute_fr_spec_data` applies before matching.
_DECL_SUFFIX = re.compile(r"(?:\[.*\]|\(\))$")


def _normalise_declared(name: str) -> str:
    return _DECL_SUFFIX.sub("", str(name).strip().strip("`").strip())


def _requirement_source(project: Path) -> "tuple[str, Path]":
    """Which document states the requirement, project-relative name and path.

    SPEC.md when the project has one — the origin of the chain, and the only
    end that has not already been narrowed. SRS.md is the honest fallback for
    a project with no canonical spec; the choice travels in the approval so a
    reader knows which document the verdict was about.
    """
    layout = ProjectLayout(project)
    spec = layout.spec_path
    if spec.is_file():
        return "SPEC.md", spec
    srs = layout.srs_path
    return str(srs.relative_to(project)) if srs.is_absolute() else str(srs), srs


def _declared_tests(project: Path, fr_id: str) -> list[str]:
    """The test function names TEST_SPEC.md declares for this FR.

    Straight from `spec_coverage._parse_test_spec` — the same parser
    `finalize-gate`'s S4 spec-coverage check reads, and the one Round 73/74
    taught to stop dropping rows silently. A second parser here would be a
    second denominator.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    items = _parse_test_spec(ProjectLayout(project).test_spec_path)
    names = (_normalise_declared(i["test_fn"]) for i in items if i["fr_id"] == fr_id)
    return sorted({n for n in names if n})


def _test_files(project: Path, fr_id: str) -> list[str]:
    """Project-relative test files this FR's tests live in.

    `scan_test_fr_coverage` is the framework's existing answer to "which tests
    are about this FR" — the map the traceability dimension already scores.
    It counts a file whose NAME encodes the FR (`test_fr07.py`) as well as one
    carrying a `[FR-07]` annotation, and both are how projects actually write
    them. Measured over eleven corpus projects: 100 of 101 FRs resolve at
    least one file.

    `test_outcomes` is deliberately not passed: a skipped test is precisely
    what a criteria review needs to look at, and filtering to passing ones
    would hide it.
    """
    from core.quality_gate.spec_coverage import _get_test_directories
    from core.traceability.scanner import scan_test_fr_coverage

    files: set[str] = set()
    for test_dir in _get_test_directories(project):
        files |= set(scan_test_fr_coverage(test_dir, None, None, project).get(fr_id, []))
    return sorted(files)


def _assertion_digests(project: Path, test_files: list[str],
                       declared: list[str]) -> dict[str, str]:
    """`{test_fn: sha256(ast.dump(node))}` for each declared test found.

    A declared name with no function on disk is simply absent from the map;
    its later appearance changes the map and is a reason to re-review, which
    is the same thing `spec_coverage.delivery_outcome` calls `absent`.

    Class-nested test functions are included under their bare name, matching
    how `_parse_test_spec` declares them and how `_outcome_key_names` reads
    them back out of a JUnit report.
    """
    wanted = set(declared)
    digests: dict[str, str] = {}
    for rel in test_files:
        path = project / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue

        def walk(body: list) -> None:
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in wanted:
                        digests[node.name] = hashlib.sha256(
                            ast.dump(node).encode("utf-8")).hexdigest()
                elif isinstance(node, ast.ClassDef):
                    walk(node.body)

        walk(tree.body)
    return dict(sorted(digests.items()))


def review_sources(project: "str | Path", fr_id: str) -> dict:
    """Everything the criteria review is about, measured by the harness.

    Returned as one dict so the producer (`review-fr-tests`) and the enforcer
    (advance-phase's P3 precheck) cannot drift: the same call decides what the
    reviewer is shown and what a stored approval is checked against.
    """
    root = Path(project).resolve()
    req_rel, req_path = _requirement_source(root)
    excerpt = extract_fr_section(req_path, fr_id) if req_path.is_file() else ""
    declared = _declared_tests(root, fr_id)
    files = _test_files(root, fr_id)
    return {
        "fr_id": fr_id,
        "requirement_path": req_rel,
        "requirement_excerpt": excerpt,
        "test_files": files,
        "declared_tests": declared,
        "assertion_digests": _assertion_digests(root, files, declared),
    }


def review_prompt(project: "str | Path", fr_id: str,
                  sources: "dict | None" = None) -> str:
    """The reviewer's prompt: the requirement in full, the tests by path.

    The requirement excerpt is embedded; the tests are named and read from
    disk. That is `buildBPrompt`'s doctrine ("the DOC blocks are a SUMMARY
    snapshot; re-read the file you cite"), and it keeps this prompt off the
    relay Round 86 站2 measured — `dispatch --prompt-file` goes disk to CLI.

    The verdict question is counterfactual on purpose. "Is this AC covered?"
    is answered yes by a test named after it; "which assertion fails if the
    requirement is violated?" is the question `test_fr07.py:218` cannot
    survive.
    """
    root = Path(project).resolve()
    s = sources if sources is not None else review_sources(root, fr_id)
    files = s["test_files"]
    file_lines = "\n".join(f"  - {f}" for f in files) or "  (none found)"
    declared = ", ".join(s["declared_tests"]) or "(none declared)"
    return (
        f"You are the CRITERIA REVIEWER for {fr_id}. You review ONE question, "
        "and you have Read and Bash tools to answer it.\n\n"
        f"THE REQUIREMENT — verbatim from {s['requirement_path']}, the document "
        "the project was asked to build. This is the ONLY statement of the "
        "requirement you may use. Do not substitute a restatement of it from "
        "SRS.md, TEST_SPEC.md, a test docstring or a code comment: a "
        "restatement is what is under review.\n"
        "=== BEGIN REQUIREMENT ===\n"
        f"{s['requirement_excerpt'] or '(no section found — say so and REJECT)'}\n"
        "=== END REQUIREMENT ===\n\n"
        f"THE TESTS — read each of these files in full with Read or Bash before "
        f"you answer:\n{file_lines}\n"
        f"TEST_SPEC.md declares these test functions for {fr_id}: {declared}\n\n"
        "YOUR QUESTION, for every normative requirement in the block above:\n"
        "  Name the assertion — file and line — that FAILS if that requirement "
        "is violated.\n"
        "REJECT when any of these is true:\n"
        "  - no assertion would fail when the requirement is violated;\n"
        "  - the assertion fails when the requirement HOLDS (an inverted "
        "assertion: the test pins the defect in place);\n"
        "  - the assertion is true regardless of the implementation "
        "(`>= 0`, a value the test itself just wrote, a name that only "
        "reports what the code did);\n"
        "  - the assertion checks a number the test hardcoded rather than the "
        "one the requirement states.\n"
        "A test whose NAME matches the requirement is not evidence. Only its "
        "assertions are.\n\n"
        "SCHEMA — return ONLY this JSON object as your final message, no "
        "markdown fences and no prose around it:\n"
        '{"review_status":"APPROVE"|"REJECT","reason":"<>=40 chars saying '
        'which requirement maps to which assertion, or which one does not>",'
        '"citations":["file:line", ...],"docs_embedded":["SPEC.md", ...],'
        '"gaps":[{"severity":"low|medium|high","evidence_type":'
        '"real_invention|over_interpretation|methodology_artifact",'
        '"canonical_ref":"<file:line>","message":"...","fr_id":"' + fr_id + '"}]}\n'
        "CITATIONS ARE CHECKED MECHANICALLY, and an approval that fails the "
        "check is rejected at the phase exit:\n"
        f"  - at least one citation into {s['requirement_path']}, and at least "
        "one into one of the test files listed above;\n"
        "  - write paths exactly as listed above (project-relative). "
        "`path:N`, `path:N-M` and `path:N:M` all parse; a path that does not "
        "resolve, or a line past the end of the file, is rejected;\n"
        f"  - `docs_embedded` must contain {s['requirement_path']}.\n"
        "Your APPROVE also pins the assertions you read: if any declared test "
        "above is edited afterwards, this approval expires and the review runs "
        "again."
    )


def approval_defects(project: "str | Path", fr_id: str,
                     approval: dict, sources: "dict | None" = None) -> list[str]:
    """Why this stored approval does not stand, as human-readable reasons.

    Everything here is measured by the harness against the tree as it is now.
    Nothing reads the reviewer's prose — Round 77's lesson, where a verdict
    was built on an agent's own excerpt of what it had read.

    Deliberately NOT re-implemented here: `review_status`, the minimum reason
    length, and whether each citation resolves at all.
    `agent_b_approvals.verify_agent_b_approvals_core` already owns those, and
    both callers of this function run it alongside. `docs_embedded` IS checked
    here rather than there, because `REQUIRED_EMBEDDED_DOCS[3]` is empty: the
    document P3 requires is whichever one `_requirement_source` resolved, and
    a static per-phase list cannot name it.
    """
    root = Path(project).resolve()
    s = sources if sources is not None else review_sources(root, fr_id)
    block = approval.get(REVIEW_BLOCK_KEY)
    if not isinstance(block, dict):
        return [
            f"no `{REVIEW_BLOCK_KEY}` block — this approval was hand-written or "
            "written by a harness older than Round 87 站5, so nothing records "
            "which requirement text and which assertions it was about. Re-run "
            "the review."
        ]

    defects: list[str] = []

    recorded_req = str(block.get("requirement_path", ""))
    if recorded_req != s["requirement_path"]:
        defects.append(
            f"reviewed against {recorded_req or '(unrecorded)'}, but the "
            f"requirement now comes from {s['requirement_path']}"
        )

    if not s["test_files"]:
        defects.append(
            f"no test file is named after or annotated with {fr_id}, so there "
            "is nothing for a criteria review to be about — put this FR's "
            f"tests in a file named test_fr<NN>.py, or annotate them [{fr_id}] "
            "(NFR-05), and re-run the review"
        )

    recorded_declared = list(block.get("declared_tests") or [])
    if recorded_declared != s["declared_tests"]:
        added = sorted(set(s["declared_tests"]) - set(recorded_declared))
        gone = sorted(set(recorded_declared) - set(s["declared_tests"]))
        defects.append(
            "TEST_SPEC.md declares a different set of tests than the review saw"
            + (f"; added {added}" if added else "")
            + (f"; removed {gone}" if gone else "")
        )

    recorded_digests = block.get("assertion_digests") or {}
    if not isinstance(recorded_digests, dict):
        recorded_digests = {}
    current = s["assertion_digests"]
    changed = sorted(n for n, d in recorded_digests.items()
                     if n in current and current[n] != d)
    vanished = sorted(n for n in recorded_digests if n not in current)
    appeared = sorted(n for n in current if n not in recorded_digests)
    if changed:
        defects.append(
            "assertions changed after the review approved them: "
            + ", ".join(changed)
        )
    if vanished:
        defects.append(
            "reviewed test(s) no longer exist on disk: " + ", ".join(vanished)
        )
    if appeared:
        defects.append(
            "declared test(s) appeared after the review: " + ", ".join(appeared)
        )

    embedded = approval.get("docs_embedded")
    embedded = embedded if isinstance(embedded, list) else []
    req_names = {s["requirement_path"], Path(s["requirement_path"]).name}
    if not any(str(e) in req_names or Path(str(e)).name in req_names
               for e in embedded):
        defects.append(
            f"docs_embedded does not list {s['requirement_path']} — the review "
            "must record that it read the requirement source"
        )

    citations = approval.get("citations")
    citations = citations if isinstance(citations, list) else []
    resolved = []
    for raw in citations:
        rel = str(raw).strip().split(":", 1)[0]
        path = _resolve_citation_path(root, rel)
        if path is not None:
            resolved.append(path.resolve())
    req_abs = (root / s["requirement_path"]).resolve()
    test_abs = {(root / f).resolve() for f in s["test_files"]}
    if req_abs not in resolved:
        defects.append(
            f"no citation into {s['requirement_path']} — the review must cite "
            "the requirement it judged against"
        )
    if s["test_files"] and not (set(resolved) & test_abs):
        defects.append(
            "no citation into any of this FR's test files — the review must "
            "cite the assertion it judged"
        )
    return defects
