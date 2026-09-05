"""SSOT-driven manifest scaffolder for project-level dependency files.

When a project has SPEC.md / SAD.md / SRS.md declaring its required runtime
dependencies but has not yet materialized a requirements.txt, this module
transcribes the SSOT content into a requirements.txt skeleton.

This is NOT inference: the user wrote the dependency names into the SSOT
during P0/P1/P2 (e.g., SPEC.md §0 line 38 lists "fastapi / sqlalchemy /
alembic / uvicorn", SPEC.md §5.3 line 323 lists `import-linter` /
`pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx`). The framework is
executing the user's confirmed decisions, not guessing what to install.

Versions are NOT written — SSOT rarely declares specific versions, and writing
them would be the framework's inference (Round 47 站5b spirit: don't become the
author of the project's deliverables). The skeleton uses placeholder names;
project authors pin versions per SAD.md §4.7 (= pinned) via pip-compile or
manually before commit.

Existing manifests are preserved: if the user has already authored a
requirements.txt, this module does NOT overwrite it. The scaffold only fires
when the file is absent.

Filtering strategy: an allowlist of known Python PyPI package names is applied
to all parser output. This is necessary because SSOTs mention many tokens
(scope names like `read`/`write`, internal module names like `taskq_api.config`,
schema field names like `revoked_at`) that look like valid PEP 508 names but
are NOT PyPI packages. The allowlist is intentionally conservative — extending
it for new projects is a one-line addition to `_KNOWN_PYPI_PACKAGES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScaffoldOutcome:
    """Result of one `scaffold_project_manifest_from_ssot` call.

    - `manifest_path`: where the skeleton was written, or None if nothing was.
    - `source_files`: SSOT files that were actually parsed.
    - `dependencies`: dep names extracted from SSOT (in encounter order, deduped).
    - `warnings`: parse failures / non-fatal issues.
    """

    manifest_path: Optional[Path] = None
    source_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Allowlist of known PyPI package names. Conservative by design — extending
# for new projects is a one-line addition. Tokens NOT in this set are filtered
# out by the parsers, even if they appear in backticks in the SSOT.
_KNOWN_PYPI_PACKAGES: frozenset[str] = frozenset({
    # Web framework stack (taskq-super §2)
    "fastapi", "pydantic", "pydantic-settings", "sqlalchemy", "alembic",
    "uvicorn", "httpx",
    # Web framework transitive (often mentioned alongside)
    "starlette", "anyio", "h11", "click",
    # Dev / QA tooling (taskq-super §5.3 line 323)
    "import-linter", "pip-licenses", "mutmut", "pytest-benchmark",
    "pytest", "pytest-cov", "pytest-asyncio", "ruff", "mypy",
    "types-httpx", "coverage", "pip-tools",
})

# Common Python stdlib / internal tokens that should NOT be treated as deps.
_STDLIB_NAMES: frozenset[str] = frozenset({
    "asyncio", "json", "sys", "os", "re", "pathlib", "typing",
    "dataclasses", "datetime", "logging", "functools", "collections",
    "contextlib", "itertools", "subprocess", "shlex", "hashlib",
    "uuid", "enum", "abc", "io", "tempfile", "shutil",
    "time", "threading", "multiprocessing", "concurrent", "signal",
    "socket", "ssl", "http", "urllib", "email", "csv",
    "tomllib", "typing_extensions",
})

# PEP 508 normalized name pattern (lowercase, letters/digits/_/-/.).
# Names starting with digit or containing uppercase are rejected.
_PEP508_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def _is_known_pypi(token: str) -> bool:
    """Allowlist gate — reject tokens that aren't known PyPI packages."""
    return token in _KNOWN_PYPI_PACKAGES


def _normalize_token(token: str) -> Optional[str]:
    """Normalize a candidate dep token; return None if invalid."""
    token = token.strip().lower()
    if not token or len(token) > 50:
        return None
    if not _PEP508_NAME.match(token):
        return None
    if token in _STDLIB_NAMES:
        return None
    return token


def _filter_known(deps: list[str], warnings: list[str], source: str) -> list[str]:
    """Apply PEP 508 + stdlib + allowlist filtering; record rejected tokens."""
    filtered: list[str] = []
    for raw in deps:
        stripped = raw.strip().lower()
        norm = _normalize_token(raw)
        if norm is None:
            # Round 64 站5 — a rejected token used to leave with a bare
            # `continue`, so a whole dev-deps cell that the splitter failed to
            # split (one token full of separators, which the PEP 508 name
            # regex refuses) contributed zero dependencies and said nothing.
            # The scaffold then shipped a project without its dev
            # dependencies, which is the outcome 6e7942e set out to fix.
            #
            # Empty and stdlib tokens stay quiet: both are expected on every
            # project, and a warning that always fires is not a warning.
            if stripped and stripped not in _STDLIB_NAMES:
                warnings.append(
                    f"{source}: {raw!r} is not a valid package name — if the "
                    f"cell lists several packages, this parser did not split it"
                )
            continue
        if not _is_known_pypi(norm):
            warnings.append(f"{source}: filtered out non-PyPI token {raw!r}")
            continue
        if norm not in filtered:
            filtered.append(norm)
    return filtered


def _parse_spec_section0_intent(spec_path: Path) -> tuple[list[str], list[str]]:
    """Parse SPEC.md §0 '本輪設計意圖' table — concise dep list per row.

    Pattern (SPEC.md line 38, taskq-super example):
      依賴樹淺(2 個直接依賴) | fastapi / sqlalchemy / alembic / uvicorn + 其 transitive deps | NFR-07
    Cells use " / " as separator; "+ transitive deps" suffixes are stripped.
    """
    raw_deps: list[str] = []
    warnings: list[str] = []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"could not read {spec_path}: {exc}")
        return [], warnings

    # Find rows in §0 intent table that mention dep keywords
    intent_keywords = r"(?:依賴樹|整合測試|HTTP\s*層|ORM|migration|async|資料驗證|分層)"
    row_pattern = re.compile(
        rf"\|[^|\n]*{intent_keywords}[^|\n]*\|\s*([^|\n]+?)\s*\|\s*[A-Z]+-[0-9]+",
    )
    for cell in row_pattern.findall(text):
        # Split by " / " separator
        for raw in re.split(r"\s*/\s*", cell):
            # Strip markdown bold, backticks, parens, "+ transitive deps"
            raw = re.sub(r"\*\*", "", raw)
            raw = re.sub(r"\+.*$", "", raw)
            raw = re.sub(r"[`*()]", "", raw)
            raw = raw.strip()
            first = raw.split()[0] if raw.split() else ""
            raw_deps.append(first.lower())

    return _filter_known(raw_deps, warnings, "SPEC.md §0"), warnings


def _parse_spec_section2(spec_path: Path) -> tuple[list[str], list[str]]:
    """Parse SPEC.md §2 技術架構 table — first token of column 2 per row.

    Column 2 often starts with the package name (possibly backticked / bold).
    """
    raw_deps: list[str] = []
    warnings: list[str] = []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"could not read {spec_path}: {exc}")
        return [], warnings

    # Find §2 section bounds
    sec2_start = re.search(r"^##\s*2[.\s]", text, re.MULTILINE)
    if not sec2_start:
        return [], warnings
    sec2_end = re.search(r"^##\s+3[.\s]", text[sec2_start.end():], re.MULTILINE)
    sec2_text = text[sec2_start.end():sec2_start.end() + sec2_end.start()] if sec2_end else text[sec2_start.end():]

    # Row pattern: | <col1> | <col2 first token, possibly `...` or **...**> ... |
    # Note: [^|\n]+ (not [^|]+) prevents cross-row matching at line breaks.
    row_pattern = re.compile(
        r"\|\s*[^|\n]+\s*\|\s*[`*]*([a-zA-Z][a-zA-Z0-9_.\-]*)[`*]*[^|\n]*\|"
    )
    for raw in row_pattern.findall(sec2_text):
        # Skip submodule paths (e.g., asyncio.TaskGroup → take only first segment)
        first = raw.split(".")[0]
        raw_deps.append(first)

    return _filter_known(raw_deps, warnings, "SPEC.md §2"), warnings


def _split_dep_cell(cell_text: str) -> list[str]:
    """Split a dev-deps table cell into candidate package names.

    Round 64 站5 — on BOTH separators, because neither is canonical.
    `templates/SRS.md` has no §2.9 dev-deps table at all and nothing under
    `scripts/` tells an author which one to use, so `, ` and ` / ` are two
    guesses about a format the framework never specified. 6e7942e replaced
    the first guess with the second and described the first as the bug; the
    failure it was fixing — an unsplit cell becomes one token, the PEP 508
    name regex refuses it, and the project is scaffolded without its dev
    dependencies — simply moved to the other half of the input space.
    """
    out: list[str] = []
    for raw in re.split(r"\s*[,/]\s*", cell_text):
        raw = re.sub(r"[`*]", "", raw).strip()
        if raw:
            out.append(raw)
    return out


def _parse_spec_dev_deps_table(spec_path: Path) -> tuple[list[str], list[str]]:
    """Parse SPEC.md §5.3 line 323 dev-deps inline code-block.

    Pattern (SPEC.md line 323):
      | `requirements-dev.txt` | `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx` | NFR-06/07/08/10 |
    """
    raw_deps: list[str] = []
    warnings: list[str] = []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"could not read {spec_path}: {exc}")
        return [], warnings

    # Find the requirements-dev.txt row
    line_match = re.search(
        r"\|\s*`requirements-dev\.txt`\s*\|\s*([^|]+?)\s*\|",
        text,
    )
    if not line_match:
        return [], warnings

    cell_text = line_match.group(1)
    for raw in _split_dep_cell(cell_text):
        raw_deps.append(raw)

    return _filter_known(raw_deps, warnings, "SPEC.md §5.3 dev-deps"), warnings


def _parse_srs_section29(srs_path: Path) -> tuple[list[str], list[str]]:
    """Parse SRS.md §2.9 Configuration files table — dev-deps cell.

    SRS.md mirrors SPEC.md §5.3 line 323 in structure:
      | `requirements-dev.txt` | `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx` | NFR-06/07/08/10 |

    Splits on either separator — see `_split_dep_cell` for why neither one
    gets to be the canonical one.
    """
    raw_deps: list[str] = []
    warnings: list[str] = []
    try:
        text = srs_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"could not read {srs_path}: {exc}")
        return [], warnings

    line_match = re.search(
        r"\|\s*`requirements-dev\.txt`\s*\|\s*([^|]+?)\s*\|",
        text,
    )
    if not line_match:
        return [], warnings

    cell_text = line_match.group(1)
    for raw in _split_dep_cell(cell_text):
        raw_deps.append(raw)

    return _filter_known(raw_deps, warnings, "SRS.md §2.9 dev-deps"), warnings


def _parse_sad_targeted(sad_path: Path) -> tuple[list[str], list[str]]:
    """Parse SAD.md for specific known-package mentions.

    SAD.md typically mentions package names in:
      - §4.7 NFR-07 design landing: `pip-compile`, `pip-licenses`
      - §2.2 / line 22 / line 53: `uvicorn` (launch command)
      - line 106: `pydantic-settings` (in the FR→Module table)
      - integration test mention: `httpx` (in `httpx.AsyncClient(...)`)

    Targeted regex — only matches patterns where a known package appears in
    a clearly-package-mentioning context, not arbitrary backticks.
    """
    raw_deps: list[str] = []
    warnings: list[str] = []
    try:
        text = sad_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"could not read {sad_path}: {exc}")
        return [], warnings

    # Targeted patterns: each must be a known package in a specific context
    targeted_patterns = [
        # `uvicorn <module>:app` (launch command)
        (r"`(uvicorn)\s+[a-zA-Z_][\w.]*:[a-zA-Z_]\w*`", "uvicorn"),
        # `pip-licenses <args>`
        (r"`(pip-licenses)\b", "pip-licenses"),
        # `pip-compile <args>` — pip-compile is the CLI of pip-tools
        (r"`(pip-compile)\b", "pip-tools"),
        # `(pydantic-settings)` (parenthetical mention in table cell)
        (r"\((pydantic-settings)\)", "pydantic-settings"),
        # `httpx.AsyncClient(...)`
        (r"`(httpx)\.", "httpx"),
        # `fastapi` standalone mention
        (r"`(fastapi)\b", "fastapi"),
        # `pydantic` standalone mention
        (r"`(pydantic)\b", "pydantic"),
        # `sqlalchemy` standalone mention
        (r"`(sqlalchemy)\b", "sqlalchemy"),
        # `alembic` standalone mention
        (r"`(alembic)\b", "alembic"),
    ]

    for pattern, package in targeted_patterns:
        if re.search(pattern, text):
            raw_deps.append(package)

    return _filter_known(raw_deps, warnings, "SAD.md targeted"), warnings


_SCAFFOLD_BANNER = "AUTO-SCAFFOLDED FROM SSOT"
_SCAFFOLD_LEDGER = "SSOT scaffold wrote "
# A requirement line that names its own version, or is a pip option / a
# direct URL reference. Anything else is a bare package name.
_PINNED_LINE = re.compile(r"[=<>~!]=|===|\s@\s|^-|\bfile:|\bgit\+")


def _framework_scaffolded_the_manifest(project: Path, manifest: Path) -> bool:
    """Did this framework write that file?

    Two witnesses, in this order. The degradation ledger row
    `gate:env-repair` / "SSOT scaffold wrote <name>" is the framework's own
    record of authorship and survives editing of the file. The banner
    comment inside the file is the fallback, for a tree whose ledger was
    reset — and it is only a fallback, because one line removes it, and a
    check a comment can clear is a check whose cheapest satisfaction is
    deleting the warning rather than doing the work.
    """
    ledger = project / ".methodology" / "degradations.jsonl"
    if ledger.is_file():
        try:
            for line in ledger.read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if _SCAFFOLD_LEDGER + manifest.name in line:
                    return True
        except OSError:
            pass  # fall through to the banner; an unreadable ledger is not a verdict
    try:
        return _SCAFFOLD_BANNER in manifest.read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False


def unfinished_scaffolded_manifest(project: "str | Path") -> "str | None":
    """Why a framework-written manifest is not finished, or None.

    Round 99 站4. `scaffold_project_manifest_from_ssot` writes
    `requirements.txt` for a project that has none and stamps it "REVIEW AND
    PIN VERSIONS BEFORE COMMIT", then records a `gate:env-repair` row owned
    by `harness`. Neither statement had a reader. Measured over the 17
    corpus projects: 8 carry the ledger row, 5 of those ship every
    dependency unpinned (omnibot-new 1/1, taskq-cc-new 12/12, taskq-redo
    10/10, taskq-super 11/11, taskq-wow 10/10), and 3 did the review — cc,
    final and new are fully pinned, which is what makes the obligation
    satisfiable rather than a new tax.

    What is enforced is the sentence the framework already wrote, about the
    file the framework already wrote. Not whether the manifest is right: a
    project declaring PostgreSQL and shipping no DBAPI driver is the
    finding this came from, and detecting THAT needs a
    technology-to-package table, which is domain knowledge and is
    deliberately not here.

    Python-only by construction, not by a language branch: the scaffolder
    returns early for any other language, so nothing else can carry either
    witness.
    """
    project = Path(project)
    manifest = project / "requirements.txt"
    if not manifest.is_file():
        return None
    if not _framework_scaffolded_the_manifest(project, manifest):
        return None
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    unpinned = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
        and not _PINNED_LINE.search(line.strip())
    ]
    if not unpinned:
        return None
    shown = ", ".join(unpinned[:8]) + ("…" if len(unpinned) > 8 else "")
    return (
        f"{len(unpinned)} dependenc(ies) in requirements.txt carry no "
        f"version. This framework scaffolded that file from the project's "
        f"own SSOTs and marked it 'REVIEW AND PIN VERSIONS BEFORE COMMIT'; "
        f"nothing has reviewed it since: {shown}\n"
        f"    → read the extracted list against SAD.md / SPEC.md / SRS.md — "
        f"a runtime the SSOT declares and the scaffold could not name is "
        f"missing from it, not just unpinned — then pin each line "
        f"(`pip-compile --output-file=requirements.lock requirements.txt`, "
        f"or by hand) and re-run. Deleting the banner comment does not "
        f"clear this."
    )


def scaffold_project_manifest_from_ssot(
    project_root: "str | Path",
    language: str = "python",
) -> ScaffoldOutcome:
    """Read SSOTs and write a requirements.txt skeleton if absent.

    Returns a ScaffoldOutcome. `manifest_path=None` means nothing was written
    (existing manifest preserved, no SSOT found, parse failure, or non-Python).

    Idempotent: if `requirements.txt` already exists, returns immediately with
    a warning, never overwriting user-authored content.

    Fail-soft: SSOT parse failures are recorded as warnings; the function
    returns the partial result instead of raising.
    """
    outcome = ScaffoldOutcome()

    if language != "python":
        outcome.warnings.append(
            f"language={language!r} not supported; scaffold is Python-only"
        )
        return outcome

    root = Path(project_root)
    manifest = root / "requirements.txt"

    if manifest.is_file():
        outcome.warnings.append(
            f"{manifest.name} already exists; not overwriting "
            "(user-authored manifest preserved)"
        )
        return outcome

    from core.utils.project_layout import ProjectLayout

    layout = ProjectLayout(root)
    # SSOT source paths — order matters (priority of authority):
    #   SAD.md > SPEC.md > SRS.md
    # SAD.md is the P2 final design; SPEC.md is P0 draft; SRS.md restates SPEC.
    sad_path = layout.phase2_architecture_dir / "SAD.md"
    spec_path = root / "SPEC.md"
    srs_path = layout.phase1_requirements_dir / "SRS.md"

    all_deps: list[str] = []

    # SAD.md — targeted regex for known packages in specific contexts
    if sad_path.is_file():
        deps, warns = _parse_sad_targeted(sad_path)
        for d in deps:
            if d not in all_deps:
                all_deps.append(d)
        if deps or not warns:
            outcome.source_files.append("SAD.md")
        outcome.warnings.extend(warns)

    # SPEC.md §0 intent table — concise runtime deps
    if spec_path.is_file():
        deps, warns = _parse_spec_section0_intent(spec_path)
        for d in deps:
            if d not in all_deps:
                all_deps.append(d)
        if deps or not warns:
            outcome.source_files.append("SPEC.md")
        outcome.warnings.extend(warns)

        # SPEC.md §2 技術架構 table
        deps, warns = _parse_spec_section2(spec_path)
        for d in deps:
            if d not in all_deps:
                all_deps.append(d)
        outcome.warnings.extend(warns)

        # SPEC.md §5.3 line 323 dev-deps
        deps, warns = _parse_spec_dev_deps_table(spec_path)
        for d in deps:
            if d not in all_deps:
                all_deps.append(d)
        outcome.warnings.extend(warns)

    # SRS.md §2.9 — restated manifest list (dev-deps)
    if srs_path.is_file():
        deps, warns = _parse_srs_section29(srs_path)
        for d in deps:
            if d not in all_deps:
                all_deps.append(d)
        if deps or not warns:
            outcome.source_files.append("SRS.md")
        outcome.warnings.extend(warns)

    if not all_deps:
        outcome.warnings.append("no dependencies extracted from any SSOT")
        return outcome

    outcome.dependencies = all_deps

    # Compose requirements.txt skeleton (no versions — SSOT did not declare them).
    # Versions will be pinned by project author per SAD.md §4.7 (= pinned)
    # via `pip-compile` or manual editing before commit.
    sources = ", ".join(outcome.source_files) if outcome.source_files else "(none)"
    lines = [
        f"# {root.name} — auto-scaffolded from SSOT",
        "# Generated by harness.ssot_manifest.scaffold_project_manifest_from_ssot",
        f"# Sources parsed: {sources}",
        f"# Dependencies extracted: {len(all_deps)}",
        "#",
        "# WARNING: AUTO-SCAFFOLDED FROM SSOT - REVIEW AND PIN VERSIONS BEFORE COMMIT",
        "#",
        "# Versions NOT pinned - SSOT did not declare versions. SAD.md section 4.7 requires",
        "# `==` pinned. Run `pip-compile --output-file=requirements.lock requirements.txt`",
        "# or pin manually. See NFR-07 in SPEC.md.",
        "",
    ]
    for dep in all_deps:
        lines.append(dep)
    lines.append("")  # trailing newline

    try:
        manifest.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        outcome.warnings.append(f"could not write {manifest}: {exc}")
        outcome.manifest_path = None
        return outcome

    outcome.manifest_path = manifest
    return outcome
