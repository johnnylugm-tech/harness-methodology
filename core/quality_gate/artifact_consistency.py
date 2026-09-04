"""Cross-artifact consistency gates — machine-catch P1/P2 agent hallucinations.

Two decidable checks (no LLM), upgrading prompt rules the audit found agents
still violate:

  * check_forward_refs (audit issue 3): a `NN-stage/FILE.md` reference in a
    P1/P2 artifact must name a real framework deliverable for that stage —
    catches invented filenames (02-architecture/ARCHITECTURE.md when the P2
    deliverable is SAD.md), which otherwise 404 any downstream automation.

  * check_nfr_adr_coverage (audit issue 2): every SRS NFR must appear in
    ADR.md's traceability TABLE — catches an NFR silently dropped from the
    table (NFR-06 was dropped while still appearing in a prose roll-up, so a
    "mentioned anywhere" check would miss it).

  * check_module_fr_coverage: TRACEABILITY_MATRIX.md's own §3/§4 AC rows
    (Design column `` `module::func` `` citations + each FR/NFR subsection's
    trailing "**Linked Modules**: `module`" line) are the ground truth for
    which module owns which FR/NFR. TRACEABILITY_MATRIX.md's own §5.3
    reverse-coverage table must match that ground truth exactly (its own
    heading claims exhaustive coverage, so an omission is a self-contradiction
    — e.g. `taskq.store` cited under FR-05 by an AC row but absent from §5.3's
    `taskq.store` row). SPEC_TRACKING.md's narrower §5 Module Ownership table
    (explicitly scoped to "high-risk modules per C-11", not a completeness
    claim) is only checked for unbacked ownership claims — an FR/NFR the
    ground truth attributes exclusively to a *different* module (e.g.
    assigning FR-05 to `taskq.executor` when every FR-05 AC row only ever
    cites `taskq.cli`/`taskq.store`). Module *names* are project-specific
    (unlike FR-/NFR-IDs, a harness-wide structural convention), so this check
    never treats an external file (e.g. SPEC.md) as ground truth — only
    TRACEABILITY_MATRIX.md's own internally-established citations count.

Same shape as spec_alignment.py: decidable, ingestion-agnostic, never guesses
(an unrecognizable ADR table → needs_review, not a false verdict).
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.quality_gate.legal_artifacts import LEGAL_ARTIFACTS
from core.traceability.scanner import extract_nfr_ids_from_srs
from core.utils.project_layout import ProjectLayout

__all__ = ["ac_deferral_shape", "ac_label_shape", "check_ac_deferral_targets",
           "srs_acceptance_criteria",
           "check_ac_identifiers",
           "check_ac_test_spec_coverage", "check_forward_refs",
           "check_nfr_adr_coverage", "check_module_fr_coverage",
           "record_ac_deferrals"]

# Legal deliverable filenames per stage directory (forward-reference whitelist).
# Authoritative list lives in `core.quality_gate.legal_artifacts` (single source
# of truth shared with `harness_cli._PHASE_DELIVERABLES` via the same module).
# See legal_artifacts.py for the rationale and the prior DRY violation that
# motivated the consolidation.

# `02-architecture/adr/ADR.md` / `./01-requirements/SRS.md` — stage dir, optional
# sub-dirs, filename. The filename class excludes '/' so it is the last segment.
_REF = re.compile(r"(0\d-[a-z]+)/(?:[a-z0-9_]+/)*([A-Za-z_][A-Za-z0-9_.-]*\.(?:md|ya?ml))")
_NFR = re.compile(r"NFR-(\d+)")

# Strip paths mentioned inside markdown code spans / fenced blocks / HTML
# comments before forward-ref matching — these are documentation citations
# warning readers about illegal paths (e.g. `` `01-requirements/SPEC.md` `` is
# illegal), not actionable forward refs that downstream automation will
# follow. A "forward ref" semantically means "this file is meant to exist as a
# deliverable"; code-spanned paths are quoted text, never followed by indexing
# automation. Strips fenced blocks first (greedy, DOTALL), then HTML comments,
# then inline code spans (single AND double backtick).
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CODE_SPAN = re.compile(r"`+[^`\n]+`+")


def _strip_code_context(text: str) -> str:
    """Remove markdown code-span / fenced-code / HTML-comment regions from text
    before forward-ref scanning. Paths in these regions are documentation
    citations, not actionable forward refs (regression test: forward_ref
    mentioned inside backticks in a warning note must not trigger)."""
    text = _FENCED_CODE.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _CODE_SPAN.sub("", text)
    return text


def _adr_path(project: Path) -> Path:
    """ADR.md — whichever layout this project uses.

    Round 97: the resolution moved into `ProjectLayout.adr_path`, which the
    WRITER (`sab_amender`) also uses. Two functions answering "where is the
    ADR" is how the writer ended up creating an eight-line stub at a path the
    reader never looked at, on a project whose real 893-line ADR the framework
    had itself deployed one directory down.
    """
    return ProjectLayout(project).adr_path


def _scan_files(project: Path) -> list[Path]:
    layout = ProjectLayout(project)
    cands = [
        layout.traceability_matrix_path,
        layout.spec_tracking_path,
        layout.sad_path,
        _adr_path(project),
    ]
    return [p for p in cands if p.exists()]


def check_forward_refs(project: Path) -> list[Violation]:
    project = Path(project)
    violations: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    for path in _scan_files(project):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip code-span / fenced-code / HTML-comment context before matching:
        # paths quoted inside backticks (e.g. `` `01-requirements/SPEC.md` ``
        # in a warning note) are documentation, not actionable forward refs.
        text = _strip_code_context(text)
        for m in _REF.finditer(text):
            stage_dir, filename = m.group(1), m.group(2)
            legal = LEGAL_ARTIFACTS.get(stage_dir)
            if legal is None or filename in legal:
                continue  # unknown stage dir (don't second-guess) / legal name
            key = (path.name, stage_dir, filename)
            if key in seen:
                continue
            seen.add(key)
            violations.append(Violation(
                check_type="illegal_forward_ref", rule_id=filename, severity="error",
                message=(f"{path.name} references {stage_dir}/{filename}, which is not a "
                         f"framework deliverable for {stage_dir} (legal: "
                         f"{', '.join(sorted(legal))}) — invented filename / broken forward "
                         f"reference (any automation indexing it 404s)")))
    return violations


def _adr_table_nfrs(text: str) -> set[str] | None:
    """NFR-IDs listed in ADR.md's traceability TABLE, or None if no such table.

    A table = consecutive `|`-rows whose header contains 'adr' and ('served' or
    'fr'); NFRs are collected from the data rows only, so a prose roll-up
    outside the table does not count as coverage.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip().lower()
        if s.startswith("|") and "adr" in s and ("served" in s or "fr" in s):
            nfrs: set[str] = set()
            for row in lines[i + 1:]:
                t = row.strip()
                if not t.startswith("|"):
                    break
                if set(t) <= set("|-: "):
                    continue  # separator row
                nfrs.update(f"NFR-{int(n):02d}" for n in _NFR.findall(t))
            return nfrs
    return None


def check_nfr_adr_coverage(project: Path) -> list[Violation]:
    project = Path(project)
    srs_nfrs = {
        n for n in extract_nfr_ids_from_srs(ProjectLayout(project).srs_path)
        if n != "NFR-99"
    }
    if not srs_nfrs:
        return []
    adr = _adr_path(project)
    if not adr.exists():
        return [Violation(
            check_type="adr_missing", rule_id="ADR", severity="error",
            message=f"SRS.md declares {len(srs_nfrs)} NFR(s) but ADR.md is missing")]
    table_nfrs = _adr_table_nfrs(adr.read_text(encoding="utf-8", errors="replace"))
    if table_nfrs is None:
        return [Violation(
            check_type="adr_table_missing", rule_id="ADR", severity="info",
            message=("ADR.md has no recognizable traceability table (a header row with "
                     "'ADR' + 'served'/'FR') — NFR coverage cannot be verified (needs_review)"))]
    violations: list[Violation] = []
    for nfr in sorted(srs_nfrs - table_nfrs):
        violations.append(Violation(
            check_type="nfr_not_traced", rule_id=nfr, severity="error",
            message=(f"{nfr} is declared in SRS.md but absent from ADR.md's traceability "
                     f"table (a prose mention does not count) — NFR→ADR coverage gap")))
    return violations


# ── module_fr_coverage: TRACEABILITY_MATRIX.md-internal ground truth ────────
# Heading line carrying an FR-NN/NFR-NN token anywhere after the leading `#`s
# (real headings are e.g. "### 3.1 FR-01 — ..." / "### 4.1 NFR-01 — ...", not
# immediately after the hashes) — marks the start of that requirement's
# subsection.
_FR_NFR_HEADING = re.compile(r"^#{1,6}[^\n]*?\b(FR|NFR)-(\d+)\b", re.MULTILINE)
# Any heading line at all — the boundary that ends a subsection, regardless of
# whether the next heading itself carries an FR/NFR token (a subsection must
# not leak into the next unrelated heading, e.g. "## 5. Backward Traceability").
_ANY_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_FR_NFR_ID = re.compile(r"\b(FR|NFR)-(\d+)\b")
# `module.sub::func` or `module.sub::Class.method` — Design-column citation in
# an AC row. Requires `::` so a bare backtick token (a test name, a filename)
# is never mistaken for a module. The function part allows dots (`[\w.]+`, not
# `\w+`) — a real citation style in this project (`taskq.breaker::Breaker.tick`)
# was silently dropped by a bare `\w+`, which stops at the first `.` and then
# fails the whole match since the required trailing backtick is never reached.
_MODULE_FUNC_REF = re.compile(r"`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)::[\w.]+`")
# `module.sub` bare — used only inside a "**Linked Modules**" line, where no
# function suffix is expected.
_MODULE_BARE_REF = re.compile(r"`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)`")
_LINKED_MODULES_LINE = re.compile(r"^.*\*\*Linked Modules\*\*.*$", re.MULTILINE)
# A table row whose first cell is a bare backtick-quoted dotted module name —
# the shape both TRACEABILITY_MATRIX.md §5.3 and SPEC_TRACKING.md §5 use for
# their module-ownership rows.
_OWNERSHIP_ROW = re.compile(
    r"^\|\s*`([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)`.*$", re.MULTILINE)


def _zid(kind: str, num: str) -> str:
    return f"{kind}-{int(num):02d}"


def _module_to_frs_ground_truth(text: str) -> dict[str, set[str]]:
    """Derive ``{module: {FR-NN/NFR-NN}}`` from TRACEABILITY_MATRIX.md's own
    forward citations: AC-row `` `module::func` `` Design-column references,
    plus each subsection's trailing "**Linked Modules**: `module`" line (a
    secondary/derived relationship the AC table alone omits — e.g. a module
    that only *calls* another module's function, not one directly cited by an
    AC row's Design column).

    Subsections are delimited by ``#{1,6} ... FR-NN`` / ``NFR-NN`` heading
    lines (the same structural convention `spec_alignment.py`'s
    ``_FR_HEADING`` relies on for FR-ID recognition) — never by a specific
    prose subsection title (e.g. "Design Element -> FR/NFR Coverage Matrix"),
    which is not a harness-mandated convention and free to drift between P1
    runs. A subsection ends at the next heading of ANY kind, not only the
    next FR/NFR heading — otherwise the last subsection (e.g. NFR-10) would
    leak into every following section (the §5 reverse-coverage tables, §6-11)
    and pollute the ground truth with unrelated module mentions.
    """
    fr_headings = list(_FR_NFR_HEADING.finditer(text))
    heading_starts = [m.start() for m in _ANY_HEADING.finditer(text)]
    result: dict[str, set[str]] = {}
    for h in fr_headings:
        fr_id = _zid(h.group(1), h.group(2))
        end = next((pos for pos in heading_starts if pos > h.start()), len(text))
        section = text[h.end():end]
        modules: set[str] = set(m.group(1) for m in _MODULE_FUNC_REF.finditer(section))
        for line_m in _LINKED_MODULES_LINE.finditer(section):
            modules.update(m.group(1) for m in _MODULE_BARE_REF.finditer(line_m.group(0)))
        for mod in modules:
            result.setdefault(mod, set()).add(fr_id)
    return result


def _check_ownership_table(text: str, source_label: str,
                            module_to_frs: dict[str, set[str]],
                            check_missing: bool) -> list[Violation]:
    """Compare each module-ownership row's declared FR/NFR set against the
    ground truth derived from TRACEABILITY_MATRIX.md's own forward citations.

    Two directions, both severity="error" (confirmed, decidable defects):
      * missing (only when *check_missing*) — ground truth requires this
        FR/NFR for the module, the row omits it (matrix self-contradiction /
        stale coverage table). Only applied to TRACEABILITY_MATRIX.md's own
        §5.3, whose own heading ("Design Element -> FR/NFR Coverage Matrix")
        claims to be an exhaustive reverse index. SPEC_TRACKING.md's §5 is
        explicitly scoped to "high-risk modules per C-11" ownership
        assignment, not an exhaustiveness claim — demanding it list every
        FR/NFR a module touches would be a false positive against its own
        stated, narrower purpose.
      * mismatched — the row claims an FR/NFR the ground truth attributes
        exclusively to *other* module(s) (e.g. SPEC_TRACKING.md assigning
        FR-05 to `taskq.executor` when only `taskq.cli`/`taskq.store` are ever
        cited under FR-05). FR/NFRs never cited by *any* module in the ground
        truth are not flagged either way — no ground truth to judge an
        unbacked claim against, so silence rather than guess (same contract
        as check_nfr_adr_coverage's unrecognizable-table case).
    """
    fr_to_modules: dict[str, set[str]] = {}
    for mod, frs in module_to_frs.items():
        for fr in frs:
            fr_to_modules.setdefault(fr, set()).add(mod)

    violations: list[Violation] = []
    for row in _OWNERSHIP_ROW.finditer(text):
        module = row.group(1)
        row_text = row.group(0)
        declared = {_zid(k, n) for k, n in _FR_NFR_ID.findall(row_text)}
        required = module_to_frs.get(module, set())

        if check_missing:
            for missing in sorted(required - declared):
                violations.append(Violation(
                    check_type="module_coverage_gap", rule_id=f"{module}:{missing}",
                    severity="error",
                    message=(f"{source_label}: `{module}` is missing {missing} — "
                             f"TRACEABILITY_MATRIX.md's own AC/Linked-Modules citations "
                             f"attribute {missing} to `{module}` but this ownership row "
                             f"omits it")))

        for extra in sorted(declared - required):
            owners = fr_to_modules.get(extra)
            if not owners:
                continue  # no confirmed owner anywhere — nothing to contradict
            violations.append(Violation(
                check_type="module_ownership_mismatch", rule_id=f"{module}:{extra}",
                severity="error",
                message=(f"{source_label}: `{module}` claims {extra}, but "
                         f"TRACEABILITY_MATRIX.md's AC citations attribute {extra} "
                         f"only to {', '.join('`' + o + '`' for o in sorted(owners))}")))
    return violations


def check_module_fr_coverage(project: Path) -> list[Violation]:
    """P1 self/cross-consistency gate: TRACEABILITY_MATRIX.md's §3/§4 AC-row
    module citations are ground truth; its own §5.3 reverse-coverage table and
    SPEC_TRACKING.md's §5 Module Ownership table must agree with it (no
    omissions, no unbacked claims). Never uses an external file (e.g.
    SPEC.md) as ground truth — module names are project-specific, unlike the
    harness-wide FR-/NFR-ID convention, so only TRACEABILITY_MATRIX.md's own
    internally-established citations are trusted.
    """
    project = Path(project)
    layout = ProjectLayout(project)
    matrix_path = layout.traceability_matrix_path
    if not matrix_path.exists():
        return []
    matrix_text = matrix_path.read_text(encoding="utf-8", errors="replace")
    module_to_frs = _module_to_frs_ground_truth(matrix_text)
    if not module_to_frs:
        return []  # no forward citations found — nothing to check against

    violations = _check_ownership_table(
        matrix_text, matrix_path.name, module_to_frs, check_missing=True)

    spec_tracking_path = layout.spec_tracking_path
    if spec_tracking_path.exists():
        spec_tracking_text = spec_tracking_path.read_text(encoding="utf-8", errors="replace")
        violations += _check_ownership_table(
            spec_tracking_text, spec_tracking_path.name, module_to_frs, check_missing=False)
    return violations


# ── ac_traceability: the acceptance criterion is the wire (Round 51 站5) ────
#
# An FR reaches an implementation along one path: SPEC section -> SRS section
# -> acceptance criteria -> TEST_SPEC case -> test function -> code. Every
# check that exists works on the two ends. D4 spec-coverage compares
# TEST_SPEC's test names to the names in the test file; TRACEABILITY_MATRIX
# links an FR id to a file. Nothing counts acceptance criteria, and nothing
# asks whether each one produced a case.
#
# Measured on two trees built from a byte-identical SPEC.md:
#
#     SRS `AC-<n>.<m>` identifiers   taskq-advance 95     taskq-api 0
#     AC bullets under the ten FRs   taskq-advance 46     taskq-api 33
#     TEST_SPEC citing an AC id      taskq-advance  6     taskq-api 0
#
# FR-09 is the chain visible at once. SPEC.md L158 is a table row
# (`GET /v1/metrics` | `admin` | counts, latency quantiles, rate-limit
# rejections). taskq-api's SRS transcribes the row verbatim and then lists
# three acceptance criteria, none about `/v1/metrics`. No TEST_SPEC case, no
# test, and `app.py:295` mounts the endpoint with no auth dependency returning
# only a redacted DB URL. Every downstream check agreed with every other,
# because they were all reading the same silence.
#
# The framework's own AC parser has never seen an AC: `scripts/canonical_diff.py`
# names its output `total_ac` and requires an `AC`-prefixed HEADING, and run
# over both SRS files it returns 23 and 22 clauses — the FR and NFR section
# headings, one each.
#
# Scope, stated because the gap matters: these two catch an AC that exists and
# produced no case. They do NOT catch a SPEC table row that produced no AC —
# that needs the SPEC structured, and the available shortcut (a ratio of AC
# count to normative lines) would be a fresh proxy indicator, which is the
# defect this round is about. Recorded in docs/PROPOSAL_ADJUDICATIONS.md with
# its reopen condition.

# `**AC-9.5**:` / `- AC-01-1:` / `AC-N12.2` — an identifier that starts with
# AC and carries at least one digit. Deliberately permissive about the shape
# after `AC-`: taskq-advance numbers FR criteria `AC-9.5` and NFR criteria
# `AC-N12.2`, and a checker that insists on one of those spellings is checking
# a convention rather than the property (that a later artifact can cite it).
#
# Round 55: a dot must be followed by a word character, so a range expression
# stops being one identifier. `AC-1.1..AC-1.10` in a DERIVED note is two ids
# and a range operator; the old character class spanned both dots and returned
# the whole span as a single token no criterion will ever carry. Measured: 22
# of taskq-super's 133 "identifiers" and 1 of taskq-renew's 41 were ranges,
# and every one of them was permanently unattributable — which matters now
# that an unattributable id is a finding rather than an `info`.
#
# Round 56: the trailing `\b` was satisfiable by a PREFIX of the token. For
# `AC-1.2a` the dotted-suffix group matched `.2`, the boundary before `a`
# failed, the engine backtracked the group to zero repetitions, and `AC-1` —
# followed by `.`, a legal boundary — came back. That is not a refusal; it is
# a substitution, and it collapses `AC-1.1a` and `AC-1.1b` into one id that
# a single TEST_SPEC citation then covers. The trailing assertion now refuses
# a following word character outright, and refuses a following `.<digit>`
# too — which is what stops the backtrack, since giving the dotted group up
# always leaves a `.<digit>` ahead. `..` (the range operator) and `-label`
# (a branch suffix, deliberately truncated — see the test corpus) both still
# terminate a match.
#
# Round 69 站3: the body is a NAMED STRING, and the two spellings below are
# built from it. b128efb wrote a second pattern by hand instead — `_AC_ID_BROAD`
# — for a real reason (TEST_SPEC sub-assertion rule_ids drop the dash:
# `AC1.1-status-201`, and `_AC_ID` read zero tokens out of them, so on
# taskq-new 59 of 100 declared criteria were reported as cited by nothing).
# Writing it by hand re-opened both questions this comment block closes:
# `_AC_ID_BROAD` ended in `\b` and gave `AC-1` back for `AC-1.1a`, exactly the
# Round 56 substitution; and it stopped admitting the numeric branch suffix
# `AC-9.1-2`, which is how taskq-renew writes every one of its criteria.
# Measured violation counts for `check_ac_test_spec_coverage`:
#
#     project         _AC_ID both sides   b128efb   this
#     taskq-new              59              0        0
#     taskq-renew            11             40       11
#     taskq-advance          86             86       86
#     taskq-super           111            111      111
#     taskq / taskq-cc        0              0        0
#
# The 29 extra rows on taskq-renew were all false: `AC-01-1` normalised to
# None, and a None declared id was reported uncited unconditionally, even
# where TEST_SPEC cited it verbatim.
_AC_BODY = r"[A-Za-z]?\d+(?:\.\d+)*(?:-\d+)*(?![\w]|\.\d)"
# The canonical shape, dash required. Behaviour is byte-for-byte what it was.
_AC_ID = re.compile(rf"\bAC-{_AC_BODY}")
# The same shape as a TEST_SPEC citation, where the dash is routinely dropped.
# One difference from `_AC_ID`, and it is visible in the pattern rather than
# in a second author's reading of it. A predicate suffix needs no special
# case: the terminator already stops the match before `-latency-p95`.
_AC_ID_CITED = re.compile(rf"\bAC-?{_AC_BODY}")


def _with_dash(token: str) -> str:
    """`AC1.1` -> `AC-1.1`; anything already canonical is returned unchanged.

    Deliberately NOT zero-padding. b128efb's normaliser padded the digit run
    so `AC-N9.1` and `AC-N09.1` would compare equal, and that is the branch
    that returned None for `AC-9.1-2`. Measured across the corpus — taskq-
    advance (37 zero-padded SRS ids), taskq-super and taskq-renew — the number
    of declared criteria that would match only after padding is 0. It bought
    nothing and cost 29 false violations.
    """
    return token if token.startswith("AC-") else "AC-" + token[2:]


# ── deferral: a criterion nobody will test, said out loud (Round 69 站5) ─────
#
# 1547d71 added Step 1d to `harness/ssi/prompts/derive_test_cases.md` for a
# real reason: an NFR verified by `pip-licenses` / `import-linter` / `mutmut` /
# a docstring scanner trips none of the NP-01..NP-15 patterns, gets no test
# case from Steps 1/1b/1c, and this check — which asks only whether the id
# appears somewhere in TEST_SPEC.md — then reads it as a dropped requirement.
# Before Step 1d there was no legal move.
#
# Step 1d's legal move is a sentence, and this check was a substring search,
# so the prompt began teaching in writing how to satisfy the gate with one
# line of prose. `check_ac_test_spec_coverage`'s own docstring cites `AC-N7.2`
# ("`08-config/SBOM.json` exists") as the criterion that reached delivery
# unverified; `Deferred: AC-N7.2 — SBOM check` would have closed that gate.
#
# So the answer is three states, not two. A deferral is recognised, is NOT
# counted as coverage, must name what does verify the criterion, and is
# written to the degradation ledger — visible, non-blocking, not free.
_AC_DEFERRAL_LINE = re.compile(r"^[ \t>*\-]*Deferred:[ \t]*(?P<body>.+)$",
                               re.MULTILINE)
# Em dash is the canonical separator; the two near-misses are accepted because
# refusing them would silently push an honest deferral back into "cited", which
# is the state this whole mechanism exists to stop it reaching.
_DEFERRAL_SEPARATORS = ("—", "–", " - ")


def ac_deferral_shape() -> str:
    """The one spelling of the deferral line — for the prompt and the message.

    Same binding `spec_phase1.py` already has to `ac_label_shape()`: the
    prompt states the shape this module matches, from this module, so the two
    cannot disagree. tests/test_ac_deferral_is_not_coverage.py holds
    `derive_test_cases.md` to it verbatim.
    """
    return ("Deferred: AC-Nx.y[, AC-Nx.z, ...] — <which downstream phase or "
            "which tool verifies this>, not a TEST_SPEC case.")


def _parse_deferrals(text: str) -> "tuple[dict[str, str], set[str]]":
    """({attributed id: verifier clause}, unattributed ids) from *text*.

    Unattributed means the line named ids and no verifier — no `— <clause>`,
    or an empty one. A deferral that names nobody can never be asked whether
    the thing it points at ever ran.

    Round 83 站3: the clause was parsed here and dropped on the floor, and the
    function returned two sets. Keeping it is the whole of what makes the
    deferral answerable — `check_ac_deferral_targets` reads it to ask whether
    the verifier the line names actually exists. Callers that only want the id
    set take `set(attributed)`; a second parser for the same lines would be a
    second answer to "what does this line defer to".
    """
    attributed: dict[str, str] = {}
    unattributed: set[str] = set()
    for m in _AC_DEFERRAL_LINE.finditer(text):
        body = m.group("body")
        head, why = body, ""
        for sep in _DEFERRAL_SEPARATORS:
            if sep in body:
                head, _, why = body.partition(sep)
                break
        ids = {_with_dash(t) for t in _AC_ID_CITED.findall(head)}
        if why.strip():
            for _id in ids:
                attributed[_id] = why.strip()
        else:
            unattributed.update(ids)
    return attributed, unattributed
# What WANTED to be an identifier, for the parse-gap channel only. A different
# question from `_AC_ID` (which decides what IS one), not a second spelling of
# it: `check_ac_identifiers` reports a loose token only when `_AC_ID` finds no
# canonical id anywhere inside it, so a range expression — which yields two
# real ids — never appears here.
_AC_ID_LOOSE = re.compile(r"\bAC-(?:[\w\-]|\.(?!\.))*\w")
# The heading that opens a requirement's section, and the block that holds its
# criteria. Both SRS files write the block as a bolded label, not a heading.
_REQ_HEADING = re.compile(r"^#{1,6}\s+((?:FR|NFR)-\d+)\b[^\n]*$", re.MULTILINE)
# The bolded label that opens the criteria block, in the shape the Phase 1
# prompt permits. `_AC_LABEL_RE` is that shape on its own; `AC_LABEL_SHAPE`
# below is the sentence the prompt renders from it, so the two cannot drift.
#
# Case-insensitive: taskq-advance writes "**Acceptance Criteria**" and
# taskq-renew writes "**Acceptance criteria**", and a checker that reads one of
# them returns nothing for the other and reports zero findings.
#
# Round 55: the label may carry a qualifier before the closing `**`. The prompt
# says "under a `**Acceptance criteria**` label"; five of the seven projects
# read that as permitting `**Acceptance criteria (FR-01)**`, which is a fair
# reading, and the literal-only regex attributed 0 of taskq-super's 133
# identifiers and 5 of taskq-renew's 41. With no criteria attributed,
# `check_ac_test_spec_coverage` had no population and returned a clean bill —
# which is how `AC-N7.2` ("`08-config/SBOM.json` exists") reached delivery
# without a TEST_SPEC case, a test, or the file.
_AC_LABEL_RE = r"\*\*Acceptance criteria\b[^*\n]*\*\*"
_AC_BLOCK = re.compile(
    _AC_LABEL_RE + r"[^\n]*\n(.*?)(?=\n#{1,6}\s|\n\*\*[A-Z]|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# Round 83 站3: `(.+)$` under MULTILINE stops at the first newline, so a
# wrapped bullet — which is what a real acceptance criterion looks like once it
# is a sentence rather than a phrase — reached every consumer as its first line
# only. Measured on taskq-cc-new: 95 bullets parsed, and the 77 occurrences of
# the phrase `check_ac_verifier_is_nameable` looks for sit on continuation
# lines, so that check saw zero of its 77 subjects. The continuation shape is
# the one `scripts/extract_deferred_index.py` already uses for the same reason:
# a following line that is indented or blank and does not start a new bullet.
# Strictly more text per bullet, so the id search in `check_ac_identifiers` can
# only find MORE ids than before, never fewer.
_BULLET = re.compile(
    r"^[ \t]*[-*][ \t]+(.+(?:\n(?![ \t]*[-*][ \t])[ \t]+\S.*)*)",
    re.MULTILINE)
# `#### AC-1.1` — the criterion as its own heading. Both shapes are live in
# the corpus and reading one reads a third of it: taskq and taskq-renew write
# headings (the shape `scripts/canonical_diff.py` assumes), taskq-advance
# writes `- **AC-9.1**: …` bullets, taskq-plus and taskq-api write bullets
# with no identifier at all. A parser that sees only bullets returns nothing
# for the heading projects and reports zero findings — an abstention that
# reads as a clean bill, which is the defect Round 46 站1 named.
_AC_HEADING = re.compile(r"^#{1,6}\s+(AC-[A-Za-z]?\d[\w.\-]*)\b[^\n]*$", re.MULTILINE)


def ac_label_shape() -> str:
    """The criteria-block label, as one sentence the Phase 1 prompt renders.

    Round 55. The prompt used to state the shape in prose and this module
    matched it with a regex, and the two disagreed on whether a qualifier was
    allowed — five of seven projects wrote the qualified form the prompt reads
    as legal and the parser read as absent. Interpolating the prompt from here
    is the only arrangement in which widening one widens the other (Round 17
    站1's prompt-gate parity); `tests/test_ac_traceability.py` asserts that
    every example in this sentence is one `_AC_BLOCK` actually accepts.
    """
    return (
        "a `**Acceptance criteria**` label (case-insensitive, and it may carry "
        "a qualifier before the closing asterisks — `**Acceptance Criteria "
        "(FR-01)**` is read the same way)"
    )


def srs_acceptance_criteria(project: Path) -> dict[str, list[str]]:
    """requirement id -> its acceptance-criteria lines, in document order.

    Reads both spellings. A returned line is the raw text; the caller looks
    for an `AC-` identifier in it, which is present by construction for the
    heading shape and optional for the bullet shape.
    """
    srs = ProjectLayout(project).srs_path
    if not srs.exists():
        return {}
    text = srs.read_text(encoding="utf-8", errors="replace")
    heads = list(_REQ_HEADING.finditer(text))
    out: dict[str, list[str]] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        section = text[m.end():end]
        criteria = [h.group(1) for h in _AC_HEADING.finditer(section)]
        block = _AC_BLOCK.search(section)
        if block:
            criteria += [b.strip() for b in _BULLET.findall(block.group(1))]
        if criteria:
            out[m.group(1)] = criteria
    return out


def check_ac_identifiers(project: Path) -> list[Violation]:
    """Every acceptance criterion must carry a stable identifier.

    Without one, no downstream artifact can cite the criterion and no checker
    can count it — which is why taskq-api's TRACEABILITY_MATRIX reported
    "SRS Coverage 100% / Verification Rate 100%" over a requirement that had
    lost the only clause anyone would have implemented.
    """
    project = Path(project)
    criteria = srs_acceptance_criteria(project)
    violations: list[Violation] = []
    for req_id, bullets in sorted(criteria.items()):
        unnumbered = [b for b in bullets if not _AC_ID.search(b)]
        if unnumbered:
            violations.append(Violation(
                check_type="ac_unnumbered", rule_id=req_id, severity="error",
                file="01-requirements/SRS.md",
                message=(f"{req_id}: {len(unnumbered)} of {len(bullets)} acceptance "
                         f"criteria carry no `AC-` identifier, so no TEST_SPEC case "
                         f"can cite one and no check can count them — first: "
                         f"{unnumbered[0][:80]!r}")))

    # Round 46 applied to this checker itself. Two acceptance-criteria shapes
    # are read (a heading, or bullets under an Acceptance-criteria label) and a
    # project may write a third. When the SRS carries `AC-` identifiers this
    # parser did not attribute to any requirement, the honest report is "I
    # could not read these", not the zero findings that reads as clean.
    # Measured: taskq-renew's SRS has 97 `AC-` occurrences and nests most of
    # them under `- DERIVED:` parents outside the labelled block; five were
    # attributed. Chasing each project's markdown dialect would be fitting the
    # parser to its fixtures, which is the defect this round is about.
    #
    # Round 55 keeps this row at `info`, and the station's own red test that
    # said otherwise was wrong: `test_identifiers_outside_a_readable_shape_are
    # _reported_as_unread` already fixes the severity here, on the ground that
    # a shape the framework cannot read is the framework's debt (Round 32 站4).
    # That is right. The abstention that must block is a different sentence —
    # "this checker built no population at all" — and it belongs in
    # `check_ac_test_spec_coverage`, which is the check whose silence read as
    # a pass. Only the message widens here: the label shape now comes from
    # `ac_label_shape()` (the same string the Phase 1 prompt renders), and the
    # unread identifiers are listed in full rather than three at a time,
    # because three of 133 is not an actionable report.
    srs = ProjectLayout(project).srs_path
    if srs.exists():
        text = srs.read_text(encoding="utf-8", errors="replace")
        found = set(_AC_ID.findall(text))
        attributed = {ac for lines in criteria.values()
                      for line in lines for ac in _AC_ID.findall(line)}
        missed = found - attributed
        if missed:
            violations.append(Violation(
                check_type="ac_parse_gap", rule_id="SRS", severity="info",
                file="01-requirements/SRS.md",
                message=(f"{len(missed)} `AC-` identifier(s) in SRS.md are not "
                         f"inside a shape this check can read (a `#### AC-x` "
                         f"heading, or a bullet under "
                         f"{ac_label_shape()}) — "
                         f"they are unchecked, not clean; "
                         f"{sorted(missed)}")))
        # Round 56: the other half of the same sentence. A token that looks
        # like an identifier but does not match the canonical shape used to be
        # truncated into a DIFFERENT id; it is now refused, and refusing it
        # silently would be worse than the substitution was. Reported here, at
        # `info`, because a shape the framework cannot read is the framework's
        # debt (Round 32 站4) — the same grounds the row above stands on.
        malformed = sorted(
            {t for t in _AC_ID_LOOSE.findall(text) if not _AC_ID.search(t)}
        )
        if malformed:
            violations.append(Violation(
                check_type="ac_parse_gap", rule_id="SRS", severity="info",
                file="01-requirements/SRS.md",
                message=(f"{len(malformed)} token(s) in SRS.md start with `AC-` "
                         f"but do not match the canonical `AC-<n>[.<n>]` shape, "
                         f"so no identifier was read from them and no TEST_SPEC "
                         f"case can cite one; {malformed}")))
    return violations


def check_ac_test_spec_coverage(project: Path) -> list[Violation]:
    """Every identified acceptance criterion must be cited by a TEST_SPEC case.

    Reports nothing when the SRS declares no identified criteria — there is
    no population to check, and Round 46's rule is that abstaining is not
    passing; `check_ac_identifiers` above is the guard that says the
    population is missing. With criteria present and TEST_SPEC absent, every
    one is uncovered and each is named.

    Round 55 splits the empty case in two, because it was carrying two facts.
    An SRS with no `AC-` identifier anywhere really has no population, and
    `check_ac_identifiers`'s `ac_unnumbered` is the row for it. An SRS with
    identifiers this parser attributed to no requirement is a different
    animal: the population exists, this check did not build it, and returning
    `[]` published that as coverage. taskq-super carried 133 identifiers, 0
    attributed and 0 findings through Gate 4 — which is the shape Round 46 站1
    named, arriving in the check Round 51 wrote to close it.
    """
    project = Path(project)
    declared: dict[str, str] = {}
    for req_id, bullets in srs_acceptance_criteria(project).items():
        for bullet in bullets:
            for ac in _AC_ID.findall(bullet):
                declared.setdefault(ac, req_id)
    if not declared:
        srs = ProjectLayout(project).srs_path
        present = set(_AC_ID.findall(
            srs.read_text(encoding="utf-8", errors="replace"))) if srs.exists() else set()
        if not present:
            return []
        return [Violation(
            check_type="ac_population_unread", rule_id="SRS", severity="error",
            file="01-requirements/SRS.md",
            message=(
                f"SRS.md carries {len(present)} `AC-` identifier(s) and this "
                f"check attributed none of them to a requirement, so it "
                f"compared nothing against TEST_SPEC.md and would otherwise "
                f"report full coverage. Put each criterion under its "
                f"requirement's heading as a `#### AC-x.y` heading or as a "
                f"bullet under {ac_label_shape()}; e.g. {sorted(present)[:3]}"))]

    cited, deferred, unattributed = _test_spec_dispositions(project)

    violations: list[Violation] = []
    for ac in sorted(declared):
        key = _with_dash(ac)
        if key in cited:
            continue
        if key in unattributed:
            violations.append(Violation(
                check_type="ac_deferral_unattributed", rule_id=declared[ac],
                severity="error", file="02-architecture/TEST_SPEC.md",
                message=(f"{ac} ({declared[ac]}) is deferred in TEST_SPEC.md "
                         f"with no verifier named. Write it as "
                         f"`{ac_deferral_shape()}` — a deferral that names "
                         f"nobody can never be asked whether the thing it "
                         f"points at ran")))
            continue
        if key in deferred:
            violations.append(Violation(
                check_type="ac_deferred", rule_id=declared[ac],
                severity="info", file="02-architecture/TEST_SPEC.md",
                message=(f"{ac} ({declared[ac]}) has no TEST_SPEC case and is "
                         f"deferred to a named verifier. Recorded, not "
                         f"counted as coverage: nothing here checks that the "
                         f"named verifier exists or ever ran")))
            continue
        violations.append(Violation(
            check_type="ac_no_test_case", rule_id=declared[ac], severity="error",
            file="02-architecture/TEST_SPEC.md",
            message=(f"{ac} ({declared[ac]}) is an acceptance criterion in SRS.md "
                     f"that no TEST_SPEC case cites — this is the last point at "
                     f"which the requirement could still have been caught")))
    return violations


def _test_spec_dispositions(
    project: Path,
) -> "tuple[set[str], set[str], set[str]]":
    """(cited, deferred, unattributed) canonical ids from TEST_SPEC.md.

    The deferral lines are cut out of the text BEFORE `cited` is built. They
    contain the ids they defer, so scanning the whole file would put every
    deferred criterion straight back into "a TEST_SPEC case cites it" — which
    is exactly the conflation this split exists to end.
    """
    test_spec = ProjectLayout(project).test_spec_path
    if not test_spec.exists():
        return set(), set(), set()
    text = test_spec.read_text(encoding="utf-8", errors="replace")
    deferred, unattributed = _parse_deferrals(text)
    remainder = _AC_DEFERRAL_LINE.sub("", text)
    # `_AC_ID_CITED`, not `_AC_ID`: TEST_SPEC sub-assertion rule_ids are
    # routinely written without the dash (`AC1.1-status-201`), which `_AC_ID`
    # parses as zero tokens — the dash gap then reads as "every AC is
    # uncited". The SRS side stays `_AC_ID` so a typo never silently passes;
    # that question is `check_ac_identifiers`'s, not this one's.
    cited = {_with_dash(t) for t in _AC_ID_CITED.findall(remainder)}
    return cited, set(deferred) - cited, unattributed - cited


#: The phrase `R-CANONICAL-INTERP-001` used to hand Agent A as its
#: fidelity-preserving template. It names this framework as the thing that
#: decides an acceptance criterion, and no component of this framework reads an
#: AC and decides it — so every AC carrying it ships a false statement about
#: who checked it. Matched loosely on the two load-bearing words because the
#: template was prose and agents paraphrase it.
_HARNESS_AS_VERIFIER = re.compile(
    r"\bowned\s+by\s+the\s+(?:test\s+)?harness\b", re.IGNORECASE)


def check_ac_verifier_is_nameable(project: "str | Path") -> list[Violation]:
    """An AC may not name this framework as the thing that decides it.

    Round 83 站3. `harness/prompts/rules/R-CANONICAL-INTERP-001.md` existed to
    stop Agent A over-specifying ambiguous canonical terms — a real problem,
    and the rule's transcribe-verbatim half is untouched. What it also did was
    hand A a sentence to put in the AC:

        '<verbatim canonical phrase> — measurement / interpretation boundary
         is owned by the test harness per <canonical line>.'

    Nothing in this framework is that owner. No check reads an AC's prose and
    decides it; `check_ac_test_spec_coverage` asks whether the id is cited in
    TEST_SPEC, and that is the whole of it.

    Measured across the eleven projects on this machine: taskq-cc-new carries
    the phrase in 77 of its 95 acceptance criteria, taskq-api in 8, taskq-plus
    in 6, taskq-renew in 3, and six projects use it zero times. So it is not a
    default every project falls into — it is an escape hatch with no ceiling
    that one project took 77 times, including `AC-6.1` ("All data access flows
    through the `repository/` layer — measurement / interpretation boundary is
    owned by the test harness"), whose FR-06 passed Gate 1 at 100.0 three
    times while `repository/key_repo.py` remained an in-process dict.

    Narrow on purpose. It does not ask whether a deferral's verifier is a good
    one, or whether a human process is an acceptable verifier — a quarterly
    manual audit is a real answer and this says nothing about it. It asserts
    one thing the framework can assert with certainty: that it is not the
    verifier, because it knows what it runs.

    Never raises — an unreadable SRS is a worse reason to stop than the thing
    this was going to report.
    """
    project = Path(project)
    try:
        criteria = srs_acceptance_criteria(project)
    except OSError:
        return []
    violations: list[Violation] = []
    for req_id, bullets in sorted(criteria.items()):
        named = [b for b in bullets if _HARNESS_AS_VERIFIER.search(b)]
        if not named:
            continue
        violations.append(Violation(
            check_type="ac_verifier_is_the_harness", rule_id=req_id,
            severity="error", file="01-requirements/SRS.md",
            message=(
                f"{req_id}: {len(named)} of {len(bullets)} acceptance criteria "
                f"say the measurement is owned by the test harness. It is not "
                f"— no component of this framework reads an acceptance "
                f"criterion and decides it, so this ships a claim that someone "
                f"checked it when nobody did. Name the test function, tool or "
                f"downstream phase that measures it "
                f"(R-CANONICAL-INTERP-001), or raise NFR-99 if the canonical "
                f"line is genuinely ambiguous. First: {named[0][:100]!r}"
            )))
    return violations


#: A test function named inside a deferral's verifier clause. The convention
#: every one of them follows — `templates/TEST_SPEC.md`'s own shape, and the
#: same `test_*` prefix `_scan_test_functions` and `_parse_test_spec` key on.
#: Measured on taskq-cc-new: 35 deferral lines, 35 naming a test function.
_DEFERRAL_TEST_FN = re.compile(r"\b(test_[A-Za-z0-9_]+)\b")


def check_ac_deferral_targets(project: "str | Path") -> list[Violation]:
    """A deferral that points at a test nobody wrote verifies nothing.

    Round 83 站3. `record_ac_deferrals`'s own docstring has said since Round 68
    站1 that its row "is what a later round reads to ask whether any of the
    named verifiers was ever run, which nothing does today". This is that
    read, and it is a JOIN rather than a new mechanism: the deferral names a
    test function (`_parse_deferrals` already parsed the clause and dropped
    it), and `spec_coverage` already knows which declared test functions exist
    — it is the producer behind the `spec:undelivered` ledger row. Both halves
    were computed on every run; nothing put them together.

    Measured on taskq-cc-new (Phase 2, 2026-08-24):

        deferral lines                     : 35
        naming a test function             : 35   (all of them)
        first spec:undelivered "missing"   : 37, of which 35 are those tests
        last recorded row                  :  4, of which  2 still are

    Those two are `AC-N10.1` -> `test_nfr10_integration_coverage_ge_80` and
    `AC-N7.4` -> `test_sbom_at_08_config_with_required_schema`. Neither test
    ever existed. Both criteria travelled through Gate 4 (94.43 PASS) and out
    the far end of Phase 8 verified by nothing at all.

    A clause naming NO test function is left alone: that is the shape
    `_parse_deferrals`'s `unattributed` set already reports, and a deferral to
    a human process or an external tool is a legitimate thing to write. What
    cannot stand is a deferral that names a verifier and is wrong about it.

    Returns `Violation`s like its neighbours — severity `error`, which
    `preflight_artifact_consistency` makes blocking from phase 2 on.
    Never raises — an unreadable TEST_SPEC is a worse reason to stop than the
    thing this was going to report.
    """
    from core.quality_gate.spec_coverage import (
        _get_test_directories, _live_test_outcomes, _scan_test_functions,
        delivery_outcome,
    )
    from core.utils.lang_patterns import project_language

    # Round 88 站1: resolved — `ProjectLayout` resolves, so relativising the
    # TEST_SPEC path against an unresolved root raised. Same defect as three
    # siblings (see core/traceability/scanner.py::scan_test_fr_coverage).
    project = Path(project).resolve()
    try:
        test_spec = ProjectLayout(project).test_spec_path
        if not test_spec.exists():
            return []
        clauses, _unattributed = _parse_deferrals(
            test_spec.read_text(encoding="utf-8", errors="replace"))
        named = {ac: fns for ac, clause in clauses.items()
                 if (fns := _DEFERRAL_TEST_FN.findall(clause))}
        if not named:
            return []
        lang = project_language(project)
        actual: set[str] = set()
        for test_dir in _get_test_directories(project):
            actual |= _scan_test_functions(test_dir, lang)
        outcomes = _live_test_outcomes(project)
    except OSError:
        return []

    violations: list[Violation] = []
    for ac in sorted(named):
        # Round 87 站1: the same rule spec_coverage scores by. This check and
        # that score disagreed about the word "exists" for four rounds — one
        # blocked on a `def`, the other counted one — and a stub satisfied
        # both. `delivery_outcome` is the single definition; importing it is
        # the point, a local re-implementation here would recreate the split.
        graded = ((fn, delivery_outcome(fn, actual, outcomes)) for fn in named[ac])
        undelivered = sorted((fn, why) for fn, why in graded if why != "delivered")
        if not undelivered:
            continue
        detail = ", ".join(f"{fn} [{why}]" for fn, why in undelivered)
        violations.append(Violation(
            check_type="ac_deferral_target_missing", rule_id=ac,
            severity="error",
            file=str(test_spec.relative_to(project)),
            message=(
                f"{ac} is deferred to {detail}, which this run has no passing "
                f"result for. A deferral is a promise that something else "
                f"verifies the criterion; this one names a verifier that did "
                f"not run and pass, so the criterion is verified by nothing. "
                f"Write the test so it runs and passes, or change the deferral "
                f"to name what really checks it."
            )))
    return violations


def record_ac_deferrals(project: "str | Path") -> list[str]:
    """Write one ledger row naming every criterion deferred to a tool.

    Returns the ids recorded. Non-blocking must not mean free (Round 68 站1):
    the row is what a later round reads to ask whether any of the named
    verifiers was ever run. Round 83 站3 is that reader —
    `check_ac_deferral_targets` — so this row's remaining subject is the
    deferrals whose target DOES exist: still not a TEST_SPEC case, still worth
    a line, no longer unexamined.

    Never raises — a project whose TEST_SPEC cannot be read is a worse reason
    to stop a gate than the thing this was going to report.
    """
    from core.degradation_ledger import record_degradation

    project = Path(project)
    try:
        _, deferred, _ = _test_spec_dispositions(project)
    except OSError:
        return []
    if not deferred:
        return []
    ids = sorted(deferred)
    record_degradation(
        project, "gate:ac-deferred",
        f"{len(ids)} acceptance criterion(s) deferred to a named tool "
        f"instead of a TEST_SPEC case",
        why=("a deferral is a promise that something else verifies the "
             "criterion; check_ac_deferral_targets confirms the named test "
             "exists, and nothing confirms it ever RAN green — the criterion "
             "is on record as unverified here"),
        data={"deferred": ids}, owner="project",
    )
    return ids
