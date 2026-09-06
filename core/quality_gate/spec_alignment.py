"""PRD/canonical_spec ↔ SRS alignment gate (Direction A).

Fills the one boundary the pipeline never machine-checks: the front edge
canonical_spec → SRS.md. `phase_artifact_enforcer` records that the PRD is
"external, not checked here", and every *downstream* boundary is already
covered elsewhere:
  * preflight_fr_spec_consistency — SAD ↔ TEST_SPEC FR-set parity
  * preflight_traceability (4a/4b/4c) — SRS/SAD → code / test / NFR coverage

This module mechanically enforces the INGESTION MODE prompt rule
R-CANONICAL-INTERP-001 ("100% transcribe … cite <canonical-line>") that today
only Agent A/B (LLM) uphold.

Round 84: the canonical spec is `ProjectLayout.spec_path` (project-root
SPEC.md) and nothing else. It used to be whatever `PROJECT_BRIEF.md` declared
in a `canonical_spec:` field — one variable statement against five constant
ones (`project_layout.spec_path`, the workflow prompt's "canonical_spec = root
SPEC.md per harness SSOT", `canonical_diff.py`'s hardcoded `--spec`,
`ssot_manifest.py:369`, `hunt.py`'s `--spec` default). The variable was never
used to express a difference: all eleven corpus projects declared `SPEC.md`.

Removing the field removes the mode switch with it. What replaces it is not a
second mode but a question about subjects: this check compares two documents,
and asks only whether each one is there.

    SPEC.md   SRS.md   verdict
    present   present  compare (the real check)
    present   absent   srs_missing      — ingestion started and stopped
    absent    present  canonical_missing — the SRS came from somewhere
    absent    absent   []                — Phase 1 has produced nothing yet

The last row is N/A because there is no subject on either side, not because a
mode was declared. The third row is what the old code read as good news: with
the mode switch in place, deleting the canonical spec silently downgraded a
project to elicitation and this gate returned `[]` on a dropped requirement.

FR-IDs are compared as SETS over the two documents. Only *structural* FR forms
are read (never prose mentions), so a stray "FR-01" in a sentence cannot create
a phantom requirement.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX
from core.utils.project_layout import ProjectLayout

__all__ = ["check_spec_alignment", "spec_config_keys", "structural_fr_ids"]

# Structural FR-ID forms — never a bare prose mention:
#   heading   `### FR-01: ...`          (canonical SPEC.md / SRS flat layout)
#   heading   `### 3.1 FR-01 ...`       (SRS subsection-numbered layout:
#              SRS_SUBSECTION_PREFIX before FR-NN — the same SRS that uses
#              §3 Functional Requirements / §3.1 FR-01 / §3.2 FR-02 TOC
#              convention; without it the gate false-positives every FR as
#              "dropped" on a structurally complete SRS)
#   table id  `| FR-01 | ...`            (SRS §2 table layout; `\b` after
#              digit also catches `| FR-01.AC1 |` style AC rows)
#   json id   `"id": "FR-01"`            (SRS §7 FR:START machine block)
_FR_HEADING = re.compile(
    r"^#{1,6}\s*" + SRS_SUBSECTION_PREFIX + r"FR-(\d+)\b", re.MULTILINE)
_FR_TABLE = re.compile(r"^\|\s*FR-(\d+)\b", re.MULTILINE)
_FR_JSON = re.compile(r'"id"\s*:\s*"FR-(\d+)"')
# A deferral is a structure, not a sentence (Round 69 站5).
#
# This module's docstring states the contract every pattern above honours:
# "Only *structural* FR forms are read (never prose mentions), so a stray
# 'FR-01' in a sentence cannot create a phantom requirement." `_FR_DEFERRED`
# was the one exception — an unanchored scan of the whole file. While it only
# fed the dropped-requirement branch that was already too wide; 6181d52
# subtracted the same set on the INVENTED axis, where it does something
# stronger: an SRS with a complete `### FR-12:` section is silenced as long as
# the two words `FR-12-deferred` appear anywhere in the file, including in a
# sentence explaining that FR-12 was NOT deferred.
#
# The three forms below are the ones the corpus writes: heading (taskq-new
# SRS.md:1402), table row (taskq SRS.md:599-603), bold bullet (taskq-super
# SRS.md:1085). Restricting to them changes the verdict on none of the nine
# corpus projects.
#
# (?<!N): "NFR-06-deferred" must not phantom-excuse FR-06 from front-edge
# coverage (parity-locked by tests/test_fr_token_parity.py).
_FR_DEFERRED_FORMS = (
    re.compile(r"^#{1,6}\s*" + SRS_SUBSECTION_PREFIX
               + r"(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
    re.compile(r"^\|\s*(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
    re.compile(r"^\s*[-*]\s*\*{0,2}\s*(?<!N)FR-(\d+)-deferred\b", re.MULTILINE),
)


def _fid(num: str) -> str:
    """Zero-pad an FR number so `FR-1` and `FR-01` compare equal."""
    return f"FR-{int(num):02d}"


def structural_fr_ids(text: str) -> set[str]:
    """The FR IDs a document structurally declares — headings, table rows, JSON.

    Public since Round 86 站3: `scripts/canonical_diff.py` reports the same
    SPEC-vs-SRS difference to Agent B, and writing a second FR regex there is
    how a document comes to have two answers to "which requirements are in
    it". `cli/project_cmds.py`'s narrower `^###\\s+FR-(\\d+)\\s*:` is NOT folded
    in — that would widen the Phase 1 FR-id fallback, which Round 84 已列為
    明列不做.
    """
    ids: set[str] = set()
    for pat in (_FR_HEADING, _FR_TABLE, _FR_JSON):
        ids.update(_fid(m) for m in pat.findall(text))
    return ids


#: A configuration key the canonical spec declares, quoted in one of its
#: tables. ALL-CAPS **with an underscore** — the underscore is what makes this
#: decidable rather than a guess. Round 87 站4 measured the alternatives on
#: eight corpus projects: every backtick identifier in a SPEC table is 81
#: names of which roughly half are the framework's OWN vocabulary (dimension
#: names out of `## framework 對齊`), and dropping those still leaves
#: `DEBUG` / `INFO` / `WARNING` / `ERROR` (log-level VALUES, not keys),
#: `TBD` / `TODO`, `Makefile` and column names like `created_at`. Requiring an
#: underscore removes every one of them and keeps all twelve real keys.
_SPEC_CONFIG_KEY = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})`")
_TABLE_SEPARATOR = re.compile(r"^\|[-:| ]+\|$")


def spec_config_keys(text: str) -> set[str]:
    """Configuration keys the canonical spec declares in its tables.

    Round 87 站4. The requirement chain has four links — SPEC to SRS/SAD, to
    TEST_SPEC, to the delivered source — and the framework checked the first
    one, in the wrong direction: `canonical_diff._best_match_ratio` scores how
    much of what Agent A WROTE is backed by the canonical text, which its own
    docstring says is "the anti-over-spec goal … to detect A ADDING content".
    Nothing asked what the canonical text declared and never arrived.

    Measured on taskq-redo, whose SPEC §5.1 declares twelve environment
    variables:

        reach TEST_SPEC.md and src   MAX_CONCURRENT DRAIN_TIMEOUT
                                     RATE_BURST RATE_PER_SEC
        reach neither, built anyway  DB_POOL_SIZE TASK_TIMEOUT (carried by
                                     FR-06/FR-08 prose)
        reach neither, never built   DB_URL CORS_ORIGINS LOG_LEVEL
                                     LOG_FORMAT HOST PORT

    Its `srs_vs_spec_diff.json` scored that SRS `invention_count: 0,
    high_score_count: 22` — full marks. SPEC.md's own line 24 asks for
    "no invention, **no omission**"; only the first half had an executor.

    The set is keys, not values: a spec may say `TASKQ_LOG_LEVEL` defaults to
    `INFO`, and `INFO` is not something the project must read.
    """
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        if _TABLE_SEPARATOR.match(stripped):
            continue
        keys.update(
            name for name in _BACKTICKED.findall(stripped)
            if _SPEC_CONFIG_KEY.match(name)
        )
    return keys


def _deferred_fr_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for pat in _FR_DEFERRED_FORMS:
        ids.update(_fid(m) for m in pat.findall(text))
    return ids


def check_spec_alignment(project: Path) -> list[Violation]:
    """Return Violations for canonical spec (SPEC.md) ↔ SRS FR-set divergence.

    error == blocking defect (dropped / invented requirement, or a broken
    ingestion setup); info == needs_review (canonical not mechanically
    enumerable). Empty list == aligned, or nothing to compare yet (see the
    module docstring's four-row table).
    """
    project = Path(project)
    layout = ProjectLayout(project)
    canonical_path = layout.spec_path
    srs_path = layout.srs_path

    if not canonical_path.exists():
        if not srs_path.exists():
            # No subject on either side — Phase 1 has not produced requirements
            # yet. Not a mode, and not a finding: naming a defect here would
            # accuse every project (and this framework repo itself, whose
            # pre-push runs `run-phase --phase 1`) of losing a file it never had.
            return []
        return [Violation(
            check_type="canonical_missing", rule_id="SA", severity="error",
            message=(f"SRS.md exists at {srs_path} but the canonical spec is "
                     f"missing at {canonical_path} — the SRS's requirements have "
                     f"no source to be checked against"))]

    canonical_frs = structural_fr_ids(
        canonical_path.read_text(encoding="utf-8", errors="replace"))
    if not canonical_frs:
        return [Violation(
            check_type="canonical_unstructured", rule_id="SA", severity="info",
            message=("canonical_spec has no `### FR-NN` requirement anchors — "
                     "PRD→SRS coverage cannot be mechanically verified; Agent B "
                     "must confirm fidelity (needs_review)"))]

    if not srs_path.exists():
        return [Violation(
            check_type="srs_missing", rule_id="SA", severity="error",
            message=(f"canonical_spec declares {len(canonical_frs)} FR(s) but "
                     f"SRS.md is missing at {srs_path} — ingestion incomplete"))]

    srs_text = srs_path.read_text(encoding="utf-8", errors="replace")
    srs_frs = structural_fr_ids(srs_text)
    srs_deferred = _deferred_fr_ids(srs_text)

    violations: list[Violation] = []
    for fid in sorted(canonical_frs - srs_frs - srs_deferred):
        violations.append(Violation(
            check_type="dropped_requirement", rule_id=fid, severity="error",
            message=(f"canonical_spec declares {fid} but SRS.md has no such FR "
                     f"(dropped requirement — ingestion must transcribe 100% or "
                     f"record it as {fid}-deferred / NFR-99)")))
    # `### FR-99-deferred: ...` is the framework-blessed way to record an
    # explicit out-of-scope deferral; the dropped-requirement branch already
    # subtracts `srs_deferred` so a heading like that is invisible on the
    # "canonical declared but SRS missing" axis. Symmetric parity on the
    # "SRS declares but canonical doesn't" axis was missing — `FR-99-deferred`
    # would otherwise read as an invented requirement. Subtract `srs_deferred`
    # here too.
    for fid in sorted(srs_frs - canonical_frs - srs_deferred):
        violations.append(Violation(
            check_type="invented_requirement", rule_id=fid, severity="error",
            message=(f"SRS.md declares {fid} with no counterpart in canonical_spec "
                     f"(invented requirement — every FR must trace to a canonical "
                     f"source clause)")))
    violations.extend(_config_key_violations(
        canonical_path.read_text(encoding="utf-8", errors="replace"), layout))
    return violations


# Config-loading patterns that signal "the project has introduced a mechanism
# for reading environment-driven configuration". Until one of these appears in
# src/, the canonical-spec-declared keys cannot have been read and the
# `unread_config_key` check is not yet answerable. corpus 驗證: all 9
# taskq-* projects trigger through one of these four — os.environ (8/9),
# BaseSettings (1/9), SettingsConfigDict (1/9), pydantic_settings (1/9).
# `os.getenv` is stdlib defensive coverage. `ConfigDict` is intentionally
# EXCLUDED: Pydantic `BaseModel.model_config = ConfigDict(...)` is schema
# configuration (forbid extra fields, from_attributes, etc.), not env-driven
# config loading — including it false-positives any project that defines
# `model_config`. Pydantic-settings env loading is covered by
# `SettingsConfigDict` and `pydantic_settings`.
_CONFIG_LOADER_PATTERNS = (
    "os.environ",
    "os.getenv",
    "BaseSettings",
    "SettingsConfigDict",
    "pydantic_settings",
)


def _has_config_loader(src_dir: Path) -> bool:
    """Detect whether any source file uses a config-loading pattern.

    Self-gating helper for `_config_key_violations`: if no file imports or
    uses a config-loading mechanism, the check is not yet answerable and
    should self-pass. Once any config-loader is present, every SPEC §5.1-
    declared key must be read.
    """
    for py in src_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pat in text for pat in _CONFIG_LOADER_PATTERNS):
            return True
    return False


def _config_key_violations(canonical_text: str, layout: "ProjectLayout") -> list:
    """A configuration key the spec declares and the delivered source never reads.

    Round 87 站4, the fourth link of the requirement chain. The FR axis above
    asks whether a requirement's ID survived; this asks whether the thing the
    requirement is ABOUT did. taskq-redo's SPEC §5.1 declares
    `TASKQ_DB_URL` with a default of `sqlite:///./taskq.db`; its SRS keeps the
    name only inside NFR-04's "must not appear in logs" clause; its TEST_SPEC
    never mentions it; and `repository/session.py` hardcodes
    `sqlite:///file:taskq_shared?mode=memory&cache=shared&uri=true` at module
    scope. Every gate passed. `architecture` scored 100.0 — that dimension is
    the code graph's community cohesion, which cannot see a spec.

    Self-gating by artifact presence, the same way `check_spec_alignment`
    decides everything else: no source directory — or a source directory
    that exists but contains no Python source files — means Phase 3 has not
    yet produced code that could have read the key, so no finding. Once the
    tree has introduced a config-loading mechanism (`os.environ` /
    `os.getenv` / `BaseSettings` / `SettingsConfigDict` / `pydantic_settings`)
    the question is answerable and the answer is blocking. Patterns cover
    stdlib `os`, Pydantic v1 `BaseSettings`, Pydantic v2 `BaseSettings` +
    `SettingsConfigDict` + `pydantic_settings`, and dynaconf-style
    `env_prefix`. `ConfigDict` (Pydantic `BaseModel.model_config`) is
    intentionally NOT included — it is schema configuration, not env-driven
    config loading.

    The "any *.py" gate that this replaced was too coarse for P3 early phase:
    schemas and ORM models can appear before the config module does, and the
    moment they do the check would demand every SPEC §5.1 key be read before
    the project even has a place to read it from. Gating by config-loader
    presence keeps the check semantically meaningful — it fires precisely
    when the project has signalled "I am now reading environment-driven
    configuration" — rather than mechanically.

    Measured over the eight corpus projects, all built from the same twelve-key
    SPEC.md: taskq-cc reads all twelve; taskq-renew misses one; taskq-cc-new,
    taskq-super and taskq-advance miss five; taskq-redo, taskq-new and
    taskq-api miss six. `taskq-cc`'s clean sheet is what says the rule
    discriminates rather than simply firing everywhere — three earlier drafts
    of it did fire everywhere, and are recorded in the Round 87 ledger.
    Corpus survey for config-loader coverage (9 taskq-* projects): all
    trigger through one of `os.environ` (8/9), `BaseSettings` / Pydantic
    patterns (1/9).
    """
    keys = spec_config_keys(canonical_text)
    if not keys:
        return []
    src_dir = layout.phase3_development_dir / "src"
    if not src_dir.is_dir() or not _has_config_loader(src_dir):
        return []
    seen: set[str] = set()
    for path in src_dir.rglob("*"):
        if not path.is_file() or path.suffix in (".pyc", ".so"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen.update(k for k in keys if k in text)
    return [
        Violation(
            check_type="unread_config_key", rule_id=key, severity="error",
            message=(f"canonical_spec declares the configuration key {key} and "
                     f"no file under {src_dir.name}/ reads it — the delivered "
                     f"system cannot be configured the way the spec says it "
                     f"can. Read it, or record the omission as a deferral."))
        for key in sorted(keys - seen)
    ]
