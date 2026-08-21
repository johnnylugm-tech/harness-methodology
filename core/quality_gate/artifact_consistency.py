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

__all__ = ["ac_label_shape", "check_ac_identifiers", "check_ac_test_spec_coverage",
           "check_forward_refs", "check_nfr_adr_coverage",
           "check_module_fr_coverage"]

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
    """ADR.md — under the canonical `adr/` sub-dir if present, else directly in
    the architecture dir. Uses ProjectLayout (no hand-built phase-dir path)."""
    arch = ProjectLayout(project).phase2_architecture_dir
    sub = arch / "adr" / "ADR.md"
    return sub if sub.exists() else arch / "ADR.md"


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
_AC_ID = re.compile(r"\bAC-[A-Za-z]?\d+(?:\.\d+)*(?:-\d+)*(?![\w]|\.\d)")
# `_AC_ID` declares the canonical id shape (`AC-1.1`, `AC-N3.4`). The
# coverage check below also reads TEST_SPEC, where the same id routinely
# appears as a sub-assertion rule_id prefix without the dash
# (`AC1.1-status-201`) — a workflow-agent convention, not a deviation a
# second regex would have prevented. `_AC_ID_BROAD` keeps the same body
# with the dash made optional, and the compare in
# `check_ac_test_spec_coverage` normalises both sides to zero-padded
# canonical form before the set difference so the dash gap does not read
# as 94 false-positive violations. Bumping the regex would break the other
# five call sites (`check_ac_identifiers`, the bullet label tests,
# `_AC_ID_LOOSE`'s refuse-channel), so the change is local to the
# coverage check by design.
_AC_ID_BROAD = re.compile(
    r"\bAC-?[A-Za-z]?\d+(?:\.\d+)*(?:-[a-zA-Z][\w-]*)?\b")


def _normalise_ac_token(token: str) -> str | None:
    """Reduce `AC1.1`, `AC-1.1`, `AC-N3.4`, `AC-N9.1` to a canonical form
    that compares equal regardless of dash or zero-pad. The digit run
    is zero-padded to two places so `AC-N9.1` and `AC-N09.1` map to the
    same key; the tail (`.K.L.M`) is preserved because `_AC_ID` already
    treats it as part of the id.

    A trailing `-suffix` (TEST_SPEC sub-assertion rule_ids routinely
    carry one, e.g. `AC9.1-status-503`) is dropped before normalisation;
    the suffix names the predicate, not the criterion it sits under.

    Returns None when the token does not look like an AC reference at
    all (e.g. `AC-1` with no criterion index, or a stray `ACX1` whose
    letter is not the NFR marker). The caller drops None tokens — they
    are not part of the population to compare.
    """
    m = re.match(
        r"^AC-?([A-Za-z])?(\d+)((?:\.\d+)*)(?:-[a-zA-Z][\w-]*)?$", token)
    if not m:
        return None
    letter, num, tail = m.groups()
    letter = letter or ""
    if letter and letter != "N":
        # `_AC_ID` permits any leading letter; we only normalise the
        # NFR split, because that is the only place where a zero-pad gap
        # is observable today. Anything else is a corpus-wide identifier
        # scheme and we leave it alone — a project that invents a new
        # letter and forgets to zero-pad gets a parse-gap row, not a
        # silent normalisation.
        return token
    return f"AC-{letter}{int(num):02d}{tail}"
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
_BULLET = re.compile(r"^\s*[-*]\s+(.+)$", re.MULTILINE)
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


def _srs_acceptance_criteria(project: Path) -> dict[str, list[str]]:
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
    criteria = _srs_acceptance_criteria(project)
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
    for req_id, bullets in _srs_acceptance_criteria(project).items():
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

    test_spec = ProjectLayout(project).test_spec_path
    cited: set[str] = set()
    if test_spec.exists():
        # Use `_AC_ID_BROAD` here (not `_AC_ID`) because TEST_SPEC
        # sub-assertion rule_ids are routinely written without the dash
        # (`AC1.1-status-201`); `_AC_ID` would parse that as zero tokens
        # and the dash gap would read as "every AC is uncited". The SRS
        # side stays `_AC_ID` so a typo never silently passes — that
        # question is `check_ac_identifiers`'s job, not this one's.
        for token in _AC_ID_BROAD.findall(
                test_spec.read_text(encoding="utf-8", errors="replace")):
            normalised = _normalise_ac_token(token)
            if normalised is not None:
                cited.add(normalised)

    violations: list[Violation] = []
    for ac in sorted(declared):
        # Normalise the declared side too: SRS is zero-padded but the
        # declared dict is keyed by the raw `_AC_ID` match, so `AC-N9.1`
        # and `AC-N09.1` must compare equal before the set difference
        # can be trusted.
        normalised_declared = _normalise_ac_token(ac)
        if normalised_declared is not None and normalised_declared in cited:
            continue
        violations.append(Violation(
            check_type="ac_no_test_case", rule_id=declared[ac], severity="error",
            file="02-architecture/TEST_SPEC.md",
            message=(f"{ac} ({declared[ac]}) is an acceptance criterion in SRS.md "
                     f"that no TEST_SPEC case cites — this is the last point at "
                     f"which the requirement could still have been caught")))
    return violations
