"""Artifact parsers for the phase-plan generator (scripts/plangen).

Moved verbatim from scripts/generate_full_plan.py (Round 3 Station M2 — the
byte-equal proof is tests/test_plangen_golden.py). Everything here READS
project artifacts (SRS/SAD/TEST_PLAN/QUALITY_REPORT/RISK_REGISTER/
CONFIG_RECORDS) or probes the harness itself (_get_harness_version);
prose/block builders live in blocks.py and the dispatcher/CLI stay in the
scripts/generate_full_plan.py facade.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from core.utils.project_layout import ProjectLayout

try:
    from core.quality_gate.sab_parser import ALL_NFR_TYPES as _ALL_NFR_TYPES
except ImportError as exc:
    raise ImportError(
        "generate_full_plan requires the harness tree on PYTHONPATH "
        "(could not import core.quality_gate.sab_parser.ALL_NFR_TYPES). "
        "Refusing to silently fall back to a hand-duplicated NFR list, "
        "which would re-introduce the very drift this module is meant to prevent."
    ) from exc

try:
    from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX
except ImportError as exc:
    raise ImportError(
        "generate_full_plan requires the harness tree on PYTHONPATH "
        "(could not import core.quality_gate.parsers.SRS_SUBSECTION_PREFIX). "
        "Refusing to silently fall back to a hand-duplicated regex fragment, "
        "which would re-introduce the very drift this module is meant to prevent."
    ) from exc
_NFR_TYPES_CHECK = (
    "All NFR `type` values from legal values "
    f"({'/'.join(_ALL_NFR_TYPES)})?"
)


def nfr_types_check_satisfied(check_string: str, all_nfr_types) -> list[str]:
    """Return the list of NFR types from *all_nfr_types* absent from
    *check_string*. Empty list = every type is present.

    Extracted from the drift guard test so the guard is genuinely callable
    and the negative test can assert it raises when the string is neutered —
    not just hand-trace the loop."""
    return [t for t in all_nfr_types if t not in check_string]


def _get_harness_version() -> str:
    """Read harness version from pyproject.toml (stdlib only, no tomllib needed)."""
    try:
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"  # parents: plangen/ -> scripts/ -> repo root (moved in M2)
        content = pyproject.read_text()
        # Anchor to [project] table to avoid matching dependency version strings
        m = re.search(r'\[project\]\n.*?\nversion\s*=\s*"([^"]+)"', content, re.DOTALL)
        if not m:
            m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        return m.group(1) if m else "2.4.0"
    except Exception:
        return "2.4.0"


_HARNESS_VERSION = _get_harness_version()

# ============================================================================
# Phase-Specific Parsers
# ============================================================================

_FENCE = re.compile(r'```(?P<tag>[a-zA-Z]*)\s*\n(?P<body>.*?)```', re.DOTALL)
_PLACEHOLDER = re.compile(r'\{[^}]*\}')


def _is_template_stub(data: dict) -> bool:
    """True when this block is harness/templates/SRS.md's example, unfilled.

    Two independent tells, because a project may delete either one: the
    `project` field still holding `{project_name}`, or every FR description
    that exists still being a `{placeholder}`. Blocks with no descriptions at
    all are NOT stubs — several real SRS blocks carry only ids and module
    lists, and calling those stubs would discard live data.
    """
    if _PLACEHOLDER.fullmatch(str(data.get("project", ""))):
        return True
    frs = data.get("functional_requirements") or []
    descs = [
        str(fr.get("description"))
        for fr in frs
        if isinstance(fr, dict) and fr.get("description")
    ]
    return bool(descs) and all(_PLACEHOLDER.search(d) for d in descs)


def srs_machine_block(content: str) -> "Optional[dict]":
    """The SRS's machine-readable requirements block, parsed, or None.

    Found by CONTENT, not by heading: every fenced JSON object in the file is
    parsed, and the one carrying `functional_requirements` is the block.
    Sentinels and section titles are agent-authored decoration; the key is
    not.

    The two heading-based paths this replaces (a `<!-- FR:START -->` sentinel
    pair, and a `## Appendix A` / `## FR Block` heading) both failed on the
    same live file. Measured on taskq-full's SRS.md at 0fadc4bd — 1116 lines,
    8 FRs and 12 NFRs under `## 10. AC ↔ Module Traceability
    (machine-readable)`, no sentinels anywhere — both paths missed it and the
    parser returned {} in silence, so every consumer read the file as
    declaring no FR metadata.

    Widening the heading match was tried first and reverted: on a later
    snapshot of the same project it matched the project's *unfilled template
    stub* two sections earlier and handed downstream a placeholder FR-01. A
    parser that finds the wrong block is worse than one that finds none. That
    is why stubs are filtered by content here (`_is_template_stub`) and why
    an ambiguous file — two filled candidates — returns None with a
    diagnostic rather than picking one.

    None means "no block found", "the block is not JSON", or "more than one
    block claims to be it" — never "the block is empty" (Round 31's
    parse-failure rule). Every case says so on stderr; the not-found case used
    to say nothing at all, which is the fourth silent abstention of the class
    Round 30 站3 cleared three of.

    Extracted so `illegal_nfr_vocabulary` reads the same block by the same
    rules rather than growing a second detection contract beside this one.
    """
    candidates: list[dict] = []
    for m in _FENCE.finditer(content):
        body = m.group("body")
        try:
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            if m.group("tag").lower() == "json":
                print(
                    f"[srs] WARNING: FR Block JSON malformed — {exc}",
                    file=sys.stderr,
                )
            continue
        if isinstance(data, dict) and "functional_requirements" in data:
            candidates.append(data)

    filled = [d for d in candidates if not _is_template_stub(d)]
    if len(filled) == 1:
        return filled[0]

    if not candidates:
        print(
            "[srs] WARNING: no machine-readable requirements block found — no "
            "fenced JSON object in this SRS carries a `functional_requirements` "
            "key. Downstream consumers will see this SRS as declaring no FR "
            "metadata.",
            file=sys.stderr,
        )
    elif not filled:
        print(
            "[srs] WARNING: the only machine-readable block(s) in this SRS are "
            "still the unfilled template example (placeholder project name / FR "
            "descriptions). Fill in harness/templates/SRS.md's FR block.",
            file=sys.stderr,
        )
    else:
        print(
            f"[srs] WARNING: {len(filled)} fenced JSON blocks carry "
            "`functional_requirements`; refusing to guess which one is "
            "authoritative. Keep exactly one.",
            file=sys.stderr,
        )
    return None


def _parse_srs_fr_block_json(content: str) -> Dict[str, Dict]:
    """Extract machine-readable FR metadata from the SRS's requirements block.

    Returns {fr_id: {implementation_modules, acceptance_criteria,
    verification_method, title, description}} — fields absent in the JSON
    are simply omitted from the inner dict; the caller must .get(...) with
    a default. FR IDs are zero-padded to FR-01 form regardless of input.
    """
    data = srs_machine_block(content)
    if data is None:
        return {}

    out: Dict[str, Dict] = {}
    for fr in data.get('functional_requirements', []):
        fid = fr.get('id')
        if not fid:
            continue
        # Normalize: strip any "FR-1" → "FR-01" inconsistency (defense in
        # depth — parse_srs_fr_sections also zfills at line ~104)
        id_m = re.match(r'^FR-(\d+)$', fid)
        if id_m:
            fid = f"FR-{id_m.group(1).zfill(2)}"
        out[fid] = {
            k: fr[k] for k in
            ('implementation_modules', 'acceptance_criteria',
             'verification_method', 'title', 'description')
            if k in fr
        }
    return out


def parse_srs_fr_sections(srs_path) -> List[Dict]:
    """Parse SRS.md to extract FR sections"""
    if srs_path is None:
        return []
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return []

    content = srs_path.read_text(encoding='utf-8')

    # Also read SAD.md for complete FR list
    repo_path = srs_path.parent.parent
    layout = ProjectLayout(repo_path)
    sad_path = layout.sad_path
    if sad_path.exists():
        sad_content = sad_path.read_text(encoding='utf-8')
        content += "\n" + sad_content

    # Extract machine-readable FR metadata from Appendix A JSON block
    # BEFORE regex section parse so we can merge structural fields
    # (implementation_modules / acceptance_criteria / verification_method)
    # into the returned fr dict. Section-body regex fields (test_cases,
    # requirements) are still populated by the regex below.
    fr_json_meta = _parse_srs_fr_block_json(content)

    # Find all FR sections (FR-01 to FR-99).
    # Char class `[\s:—–-]` accepts five heading separators:
    #   whitespace  — space (real INGESTION MODE SRS, where the FR title follows
    #                 FR-NN as a space + Chinese title, e.g. `### 3.1 FR-01 任務提交與驗證`)
    #   :           — template-style colon (existing behavior, kept for back-compat)
    #   —           — INGESTION MODE em-dash (canonical for SRS authored from SPEC.md)
    #   –           — en-dash (some editors auto-convert em-dash)
    #   -           — hyphen (CLI / quick-write fallback)
    #
    # SRS_SUBSECTION_PREFIX accepts an SRS subsection number between the
    # heading hashes and FR-NN — e.g. `### 3.1 FR-01` is the natural form
    # when an SRS uses §3 Functional Requirements / §3.1 FR-01 / §3.2 FR-02
    # TOC numbering. Without this prefix the same lookbehind false-positives
    # a structurally complete SRS (such as this repo's own
    # 01-requirements/SRS.md) as having zero FR sections.
    fr_pattern = re.compile(
        r'(#{2,3}\s*' + SRS_SUBSECTION_PREFIX + r'FR-(\d+)[\s:—–-][^\n]+\n\n)(.*?)'
        r'(?=\n---\n|\n#{2,3}\s*' + SRS_SUBSECTION_PREFIX + r'FR-\d+|$)',
        re.DOTALL,
    )

    frs = []
    for m in fr_pattern.finditer(content):
        fr_num = f"FR-{m.group(2).zfill(2)}"
        title = re.sub(r'^#{2,3}\s+', '', m.group(1).strip().split('\n')[0])
        details = m.group(3).strip()

        # Extract description (matches Chinese SRS format)
        desc_match = re.search(r'\*\*Description\*\*:(.+?)(?:\n|$)', details, re.DOTALL)
        desc = desc_match.group(1).strip() if desc_match else ""

        # Extract test cases (matches Chinese SRS format)
        test_cases = re.findall(r'[Tt]est [Cc]ases?:[^"]+"([^"]+)"[^"]+"([^"]+)"', details)

        # Extract key requirements
        req_lines = []
        if 'Content' in details:
            content_section = details.split('Content')[1].split('**')[0].strip()
            req_lines = [line.strip() for line in content_section.split('\n') if line.strip() and line.strip().startswith('-')]

        # Merge Appendix A JSON metadata into the fr dict. JSON wins for
        # title/description (machine-authored, structural); section-body
        # regex wins for test_cases/requirements (JSON has no such fields).
        json_meta = fr_json_meta.get(fr_num, {})
        frs.append({
            'fr': fr_num,
            'title': json_meta.get('title') or title,
            'desc': json_meta.get('description') or desc,
            'test_cases': test_cases,
            'requirements': req_lines,
            'implementation_modules': json_meta.get('implementation_modules', []),
            'acceptance_criteria': json_meta.get('acceptance_criteria', []),
            'verification_method': json_meta.get('verification_method', ''),
            'raw_details': details[:500],
        })

    # Fallback: if no section-format FRs found, try table-format extraction.
    # Many projects write SRS.md FRs as a markdown table (| FR-01 | desc | ... |)
    # rather than ### FR-01: section headers.  This fallback extracts at least the
    # FR IDs and descriptions so the plan generator can produce per-FR task blocks.
    #
    # `(?:\.[\w-]*)?` accepts an optional `.AC1` / `.AC3` qualifier between
    # the FR number and the closing pipe — SRS AC tables frequently use a
    # row like `| FR-01.AC1 | non-empty ...` and the original `\s*\|` would
    # have failed the match (next char after `01` is `.`).
    if not frs:
        table_re = re.compile(
            r'^\|\s*FR-(\d+)(?:\.[\w-]*)?\s*\|\s*(.+?)\s*\|',
            re.MULTILINE,
        )
        seen = set()
        for m in table_re.finditer(content):
            fr_num = f"FR-{m.group(1).zfill(2)}"
            if fr_num in seen:
                continue
            seen.add(fr_num)
            desc = m.group(2).strip()
            # Truncate overly long table-cell descriptions
            if len(desc) > 200:
                desc = desc[:197] + "..."
            frs.append({
                'fr': fr_num,
                'title': f"{fr_num}: {desc[:80]}",
                'desc': desc,
                'test_cases': [],
                'requirements': [],
                'raw_details': desc,
            })

    if not frs:
        print(
            "[generate_full_plan] WARNING: No FR sections found in SRS.md.\n"
            "  Expected formats:\n"
            "    - Section heading '### FR-01: Title' / '### FR-01 — Title'\n"
            "    - Subsection-numbered heading '### 3.1 FR-01 title'\n"
            "    - Table row '| FR-01 | desc |' (with optional '.AC1' qualifier)\n"
            "  The generated plan will have no per-FR task blocks. Verify SRS.md format.",
            file=sys.stderr,
        )

    return frs


def parse_sad_modules(repo_path: Path) -> Dict:
    """Parse SAD.md to get FR -> module mapping"""
    layout = ProjectLayout(repo_path)
    sad_paths = [
        layout.sad_path,
    ]

    for sad_path in sad_paths:
        if not sad_path.exists():
            continue

        content = sad_path.read_text(encoding='utf-8')

        # Method 1: inline pattern — FR-01 ... `app/models/schema.py`
        simple_pattern = re.compile(r'FR-(\d+)[^\n]*?`?(?:app/|03-development/src/)([^\s`]+)`?', re.DOTALL)
        modules = {}
        seen = set()

        for m in simple_pattern.finditer(content):
            fr_num = m.group(1)
            if fr_num in seen:
                continue
            file_path = m.group(2) or ""
            seen.add(fr_num)

            if '/' in file_path:
                filename = file_path.split('/')[-1].replace('.py', '')
                if not file_path.startswith('03-development'):
                    file_path = f"03-development/src/{file_path}"
                modules[f"FR-{fr_num}"] = {
                    'module': filename,
                    'file': file_path
                }

        if modules:
            return modules

        # Method 2: JSON SAB block — "FR-01": "app.models"
        sab_match = re.search(r'"FR-\d+":\s*"([^"]+)"', content)
        if sab_match:
            sab_pattern = re.compile(r'"(FR-\d+)":\s*"([^"]+)"')
            for m in sab_pattern.finditer(content):
                fr_key = m.group(1)
                if fr_key in seen:
                    continue
                seen.add(fr_key)
                module_path = m.group(2)
                # Handle "a.b + c.d" style multi-module entries — take the last one
                if ' + ' in module_path:
                    print(
                        f"[generate_full_plan] WARNING: FR {fr_key} maps to multiple modules ({module_path}).\n"
                        f"  Only the last module ({module_path.split(' + ')[-1]}) will be assigned to Agent A.",
                        file=sys.stderr,
                    )
                    module_path = module_path.split(' + ')[-1]
                modules[fr_key] = {
                    'module': module_path.split('.')[-1] if '.' in module_path else module_path,
                    'file': f"03-development/src/{module_path.replace('.', '/')}.py"
                        if '.' in module_path
                        else f"03-development/src/{module_path}.py",
                }

        if modules:
            return modules

    return {}


def parse_test_plan(repo_path: Path) -> List[Dict]:
    """Parse TEST_PLAN.md to extract test requirements"""
    layout = ProjectLayout(repo_path)
    test_plan_paths = [
        layout.test_plan_path,
    ]

    for tp_path in test_plan_paths:
        if not tp_path.exists():
            continue

        content = tp_path.read_text(encoding='utf-8')

        test_pattern = re.compile(r'(###\s+\d+\.\d+\s+[^\n]+\n)(.*?)(?=\n###|\n##|\Z)', re.DOTALL)
        tests = []

        for m in test_pattern.finditer(content):
            title = m.group(1).strip().replace('### ', '')
            details = m.group(2).strip()[:300]
            tests.append({
                'title': title,
                'details': details
            })

        if tests:
            return tests

    return []


def parse_quality_report(repo_path: Path) -> Dict:
    """Parse QUALITY_REPORT.md"""
    layout = ProjectLayout(repo_path)
    qr_paths = [
        layout.phase6_quality_dir / "QUALITY_REPORT.md",
    ]

    for qr_path in qr_paths:
        if not qr_path.exists():
            continue

        content = qr_path.read_text(encoding='utf-8')

        # Matches both ASCII colon and full-width colon
        metrics = re.findall(r'\*\*([^\*]+)\*\*:(.+?)(?:\n|$)', content)

        return {
            'metrics': [(k.strip(), v.strip()) for k, v in metrics],
            'content_preview': content[:500]
        }

    return {}


def parse_risk_register(repo_path: Path) -> List[Dict]:
    """Parse RISK_REGISTER.md"""
    layout = ProjectLayout(repo_path)
    rr_paths = [
        layout.phase7_risk_dir / "RISK_REGISTER.md",
    ]

    for rr_path in rr_paths:
        if not rr_path.exists():
            continue

        content = rr_path.read_text(encoding='utf-8')

        risk_pattern = re.compile(r'\|\s*([^\|]+)\s*\|.*?\|.*?\|.*?\|', re.MULTILINE)
        risks = []
        for m in risk_pattern.finditer(content):
            risk_name = m.group(1).strip()
            if risk_name and len(risk_name) > 3:
                risks.append({'name': risk_name})

        if risks:
            return risks[:20]

    return []


def parse_config_records(repo_path: Path) -> List[Dict]:
    """Parse CONFIG_RECORDS.md"""
    layout = ProjectLayout(repo_path)
    cr_paths = [
        layout.phase8_config_dir / "CONFIG_RECORDS.md",
    ]

    for cr_path in cr_paths:
        if not cr_path.exists():
            continue

        content = cr_path.read_text(encoding='utf-8')

        config_pattern = re.compile(r'\|[ \t]*([^\|\n]+)[ \t]*\|[^\|\n]*?\|[^\|\n]*?\|', re.MULTILINE)
        configs = []
        for m in config_pattern.finditer(content):
            config_name = m.group(1).strip()
            if config_name and len(config_name) > 3:
                configs.append({'name': config_name})

        if configs:
            return configs[:20]

    return []


def parse_srs_fr_nfr_xref(srs_path) -> Dict[str, List[str]]:
    """Parse the FR Cross-Reference table in SRS.md §2 to extract NFR associations.

    Many SRS documents store FR-to-NFR mapping in a dedicated cross-reference
    table with an 'NFR Association' column (rather than embedding NFR IDs inside
    individual FR descriptions).  This function finds that table and returns a
    ``{fr_id: [nfr_id, ...]}`` mapping so the plan generator can produce the
    correct NFR Coverage section.

    Returns {} when the table is absent or cannot be parsed.
    """
    if srs_path is None:
        return {}
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return {}

    content = srs_path.read_text(encoding="utf-8")

    # Locate the table header that contains 'NFR Association' (case-insensitive).
    header_re = re.compile(r'^(?:\|[^|\n]*)+\|\s*NFR\s*Association\s*\|', re.IGNORECASE | re.MULTILINE)
    header_match = header_re.search(content)
    if not header_match:
        return {}

    # Determine which column index holds 'NFR Association'.
    header_line = header_match.group(0)
    # Bug H3 fix: keep cell positions so nfr_col_idx lines up with rows below
    # (rows may have empty middle cells — `if c.strip()` dropped them and
    # shifted column indices, silently associating FRs with the wrong column).
    cols = [c.strip() for c in header_line.split('|')[1:-1]]
    nfr_col_idx = next(
        (i for i, c in enumerate(cols) if 'nfr' in c.lower() and 'assoc' in c.lower()),
        -1,
    )
    if nfr_col_idx == -1:
        return {}

    # Parse rows that immediately follow the header (stop at blank line or new section).
    fr_nfr_map: Dict[str, List[str]] = {}
    rest = content[header_match.end():]
    for line in rest.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            if line:
                break   # non-table content → end of table
            continue
        # Skip separator rows (|---|---|)
        if re.match(r'^\|[\s\-|]+\|$', line):
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if not any(cells):
            continue
        # First cell must be a bare FR-XX id
        fr_match = re.match(r'^(FR-\d+)$', cells[0])
        if not fr_match:
            continue
        fr_id = f"FR-{fr_match.group(1).split('-')[1].zfill(2)}"
        if nfr_col_idx < len(cells):
            nfr_ids = [f"NFR-{n.zfill(2)}" for n in re.findall(r'NFR-(\d+)', cells[nfr_col_idx])]
            if nfr_ids:
                fr_nfr_map[fr_id] = nfr_ids

    return fr_nfr_map


def parse_srs_nfr_sections(srs_path: Optional[Path]) -> List[Dict]:
    """Parse SRS.md to extract NFR sections"""
    if srs_path is None:
        return []
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return []

    content = srs_path.read_text(encoding='utf-8')

    # Note: pattern uses full-width colon to match Chinese-formatted SRS
    nfr_pattern = re.compile(r'(### NFR-(\d+):[^\n]+\n\n)(.*?)(?=\n---\n|\n###|\n##|\Z)', re.DOTALL)

    nfrs = []
    for m in nfr_pattern.finditer(content):
        nfr_num = f"NFR-{m.group(2).zfill(2)}"
        title = m.group(1).strip().split('\n')[0].replace('### ', '')
        details = m.group(3).strip()[:400]

        nfrs.append({
            'nfr': nfr_num,
            'title': title,
            'details': details
        })

    # Fallback: table-format NFR extraction (| NFR-01 | Performance | desc | method |)
    if not nfrs:
        table_re = re.compile(
            r'^\|\s*NFR-(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|',
            re.MULTILINE,
        )
        seen = set()
        for m in table_re.finditer(content):
            nfr_num = f"NFR-{m.group(1).zfill(2)}"
            if nfr_num in seen:
                continue
            seen.add(nfr_num)
            nfr_type = m.group(2).strip()
            desc = m.group(3).strip()
            if len(desc) > 400:
                desc = desc[:397] + "..."
            nfrs.append({
                'nfr': nfr_num,
                'title': f"NFR-{m.group(1).zfill(2)}: {nfr_type}",
                'details': desc,
            })

    if not nfrs:
        print(
            "[generate_full_plan] WARNING: No NFR sections found in SRS.md.\n"
            "  Expected format: '### NFR-01: Title' sections or '| NFR-01 | Type | desc |' table rows.\n"
            "  The generated plan will have no NFR summary section.",
            file=sys.stderr,
        )

    return nfrs
