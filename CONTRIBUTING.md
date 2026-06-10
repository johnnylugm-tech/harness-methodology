# Contributing & Maintaining

Everything you need to add a module, cut a release, or hack on the harness
behind this repository.

---

## Table of contents

- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Anatomy of a module](#anatomy-of-a-module)
- [Adding a new module](#adding-a-new-module)
- [Adding a new auto-fix strategy](#adding-a-new-auto-fix-strategy)
- [Versioning rules](#versioning-rules)
- [Cutting a release](#cutting-a-release)
- [CI / GitHub Actions](#ci--github-actions)
- [PR gate checks](#pr-gate-checks)
- [Troubleshooting](#troubleshooting)

---

## Quick start

```bash
git clone https://github.com/johnnylugm-tech/harness-methodology.git
cd harness-methodology

# List all modules and their versions
python3 scripts/list-modules.py

# Validate all manifests + cross-references
python3 scripts/list-modules.py --validate
python3 scripts/validate_cross_refs.py

# Run the full test suite
python3 -m pytest tests/ -v --tb=short
```

---

## Repository layout

```text
.
├── SKILL.md                            ← Agent contract (YAML frontmatter + instructions)
├── harness_cli.py                      ← Main CLI entry point
├── CONTRIBUTING.md                     ← This file
├── INTEGRATION.md                      ← Integration guide for target projects
├── constitution/
│   ├── CONSTITUTION.md                 ← Team constitution (12 quality dimensions)
│   └── ...
├── core/
│   ├── auto_fix/                       ← AutoFixEngine (classify → fix → verify → loop)
│   │   ├── manifest.json
│   │   ├── __init__.py                 ← FixStrategy, FixContext, FixResult, AutoFixEngine
│   │   ├── classifier.py              ← CLASSIFICATION_TABLE (31 entries)
│   │   ├── strategies.py              ← STRATEGY_REGISTRY (13 strategies)
│   │   └── guardrails.py              ← pre-fix safety, post-fix drift, rollback
│   ├── quality_gate/                   ← 12-dim scoring engine + Phase Truth verifier
│   │   ├── manifest.json
│   │   └── ...
│   └── adapters/                       ← Phase hook adapters
│       ├── manifest.json
│       └── ...
├── enforcement/                        ← Policy engine, framework enforcer, hard rules
│   ├── manifest.json
│   └── ...
├── harness/                            ← Pipeline bridge, git strategy, CRG, tracking
│   ├── manifest.json
│   └── ...
├── detection/                          ← Drift detection, ensemble scoring
│   ├── manifest.json
│   └── ...
├── gap_detector/                       ← M3 gap analysis
│   ├── manifest.json
│   └── ...
├── kill_switch/                        ← M1 circuit breaker
│   ├── manifest.json
│   └── ...
├── steering/                           ← Convergence steering loop
│   ├── manifest.json
│   └── ...
├── templates/                          ← Scaffold templates (plan, SRS, SAD, etc.)
├── tests/                              ← pytest test suite
├── scripts/                            ← Maintainer tooling
│   ├── list-modules.py                 ← Module inventory scanner + validator
│   ├── validate_cross_refs.py          ← Cross-reference integrity checker
│   ├── create_release.sh               ← Legacy release helper
│   └── ...
├── .github/workflows/
│   ├── harness_ci.yml                  ← PR gate: lint + test + validate
│   └── release.yml                     ← Tag-driven release workflow
└── docs/                               ← Framework documentation
```

---

## Anatomy of a module

Every module in this repo follows the same minimal shape:

```text
<module-name>/
├── manifest.json       ← required: name / version / category / description / depends_on / compat
├── __init__.py         ← required: module exports
└── ...                 ← module-specific implementation files
```

### manifest.json schema

```json
{
  "name": "<lower_underscore_name>",
  "version": "<semver>",
  "category": "core | detection | infrastructure | safety | control",
  "description": "<one-line description, at least 20 chars>",
  "depends_on": ["<other_module_name>", ...],
  "compat": {
    "python": ">=3.10",
    "harness-methodology": ">=2.3.0"
  }
}
```

The `name` field **must match the directory name** — `scripts/list-modules.py --validate` will fail otherwise.

### SKILL.md frontmatter

```yaml
---
name: harness-methodology
version: 2.7.0
description: |
  <what the framework does>
  Use when: <trigger conditions>
  Not applicable: <exclusion conditions>
---
```

The `name` field **must be `harness-methodology`** — CI validates this.

---

## Adding a new module

1. Create the module directory with at minimum `manifest.json` + `__init__.py`.
   Start with `version: "0.1.0"` if it's experimental, or `1.0.0` if ready.
2. Choose the correct `category`:
   - `core` — framework internals (auto_fix, quality_gate, adapters)
   - `detection` — problem detectors (enforcement, detection, gap_detector)
   - `infrastructure` — pipeline plumbing (harness)
   - `safety` — circuit breakers (kill_switch)
   - `control` — feedback loops (steering)
3. Declare `depends_on` accurately — this is validated at CI time.
4. Add the module path to `MANIFEST_DIRS` in `scripts/list-modules.py`.
5. Run `python3 scripts/list-modules.py --validate` to verify.
6. Add tests in `tests/`.
7. Open a PR. CI will re-run validation.

---

## Maintaining the language toolchain registry

Tool↔dimension resolution for every supported target language lives in
`harness/toolchains/registry.py` (`TOOL_SPECS` + `DIMENSION_TOOLS`); file
patterns and the state.json language reader live in
`core/utils/lang_patterns.py` (core must not import harness, so the single
source sits core-side and toolchains delegates).

Rules:

1. **R8 completeness**: a language may only appear in `DIMENSION_TOOLS` with
   ALL 14 tool-scored dimensions covered —
   `tests/test_toolchain_registry.py` enforces this against the gate YAMLs.
2. **Pinned versions only**: scorer tools are pinned `==` (requirements.txt)
   or exact devDependencies (`templates/js_toolchain/package.json`); semgrep
   rules are vendored in `harness/toolchains/semgrep_rules/`.
3. **Same scorer, same schema**: prefer reusing an existing scorer by making
   the new tool emit a compatible report (precedent: `js-mi` emits radon-mi
   JSON) over adding a near-duplicate scorer.
4. Changing a `cmd`, scorer coefficient, or vendored rule changes SCORES —
   treat it like a threshold change (PR review + calibration note in
   `docs/ADDING_LANGUAGE_SUPPORT_SOP.md` appendix).

Adding a whole language: follow `docs/ADDING_LANGUAGE_SUPPORT_SOP.md`
step-by-step (it is the authoritative checklist; this section is just the
registry-local rules).

## Adding a new auto-fix strategy

When adding a new fix strategy, three files **must** be updated in lockstep:

1. **`core/auto_fix/classifier.py`** — Add entry in `CLASSIFICATION_TABLE`:
   ```python
   "source/problem_key": {
       "strategy": FixStrategy.AUTO_FIX,  # or AUTO_FIX_WITH_VERIFICATION
       "confidence": 85.0,
       "max_rounds": 3,
       "problem_type": "new_problem_type",
   },
   ```

2. **`core/auto_fix/strategies.py`** — Add strategy function + registry entry:
   ```python
   def fix_new_problem(context, project_root: Path) -> Tuple[bool, str, float]:
       """Fix description."""
       ...

   # In STRATEGY_REGISTRY:
   "new_problem_type": fix_new_problem,
   ```

3. **`tests/test_auto_fix.py`** — Add test for the new strategy.

4. Run `python3 scripts/validate_cross_refs.py` to verify cross-reference integrity.

**Critical rule**: Every non-HUMAN_REQUIRED `problem_type` in `CLASSIFICATION_TABLE` must have a corresponding callable in `STRATEGY_REGISTRY`. CI enforces this.

---

## Versioning rules

Each module is versioned **independently** with [SemVer](https://semver.org/).
The framework itself has a repo-level version in `SKILL.md` frontmatter.

| Change | Bump |
|---|---|
| Typo fixes, doc updates, minor refactors | **patch** (1.0.0 → 1.0.1) |
| New strategy, new detection check, workflow changes | **minor** (1.0.0 → 1.1.0) |
| Removed modules, renamed problem_types, breaking API changes | **major** (1.0.0 → 2.0.0) |

For initial releases (module with no prior tag), the manifest version is used as-is.

---

## Cutting a release

```bash
# 1. Verify everything is clean
python3 scripts/list-modules.py --validate
python3 scripts/validate_cross_refs.py
python3 -m pytest tests/ -v --tb=short

# 2. Commit all changes and push
git add .
git commit -m "release: v2.4.0"
git push origin main

# 3. Tag and push the tag (CI takes over from here)
git tag v2.4.0
git push origin v2.4.0
```

The [`release.yml`](./.github/workflows/release.yml) workflow will:
1. Validate all manifests + cross-references
2. Run the full test suite
3. Build a wheel with `pip wheel .`
4. Generate a SHA-256 checksum
5. Create a GitHub Release with the wheel + checksum attached
6. Generate release notes from `git log`

To recall a release:

```bash
git tag -d v2.4.0
git push origin :refs/tags/v2.4.0
gh release delete v2.4.0 --yes
```

> Prefer bumping the version over overwriting — immutability is the point.

---

## CI / GitHub Actions

Two workflows:

### [`harness_ci.yml`](./.github/workflows/harness_ci.yml)

Runs on every PR and push to `main`:
1. **Lint** — `ruff check .`
2. **Unit tests** — `pytest tests/ -v --tb=short`
3. **validate-manifests** — `python scripts/list-modules.py --validate`
4. **validate-cross-refs** — `python scripts/validate_cross_refs.py`

### [`release.yml`](./.github/workflows/release.yml)

Triggered by pushing a tag matching `v*`:
1. Parse tag → version
2. Validate + test
3. Build wheel + sha256
4. Create GitHub Release

---

## PR gate checks

Before merging, CI must pass all four jobs:

| Job | What it checks | Exit code |
|---|---|---|
| Lint (ruff) | Python code style | 0 on clean |
| Unit tests | Full pytest suite (1800+ tests) | 0 on pass |
| validate-manifests | All manifest.json valid + SKILL.md frontmatter correct | 0 on valid |
| validate-cross-refs | CLASSIFICATION_TABLE ↔ STRATEGY_REGISTRY consistency | 0 on consistent |

All checks can be run locally:
```bash
ruff check .
pytest tests/ -v --tb=short
python3 scripts/list-modules.py --validate
python3 scripts/validate_cross_refs.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `validate-manifests` fails: `missing required field: depends_on` | New manifest.json is missing a required field | Add the missing field, see schema above |
| `validate-manifests` fails: `depends_on 'X' not found` | Dependency declared but no manifest for it | Either create the missing manifest or remove the dependency |
| `validate-cross-refs` fails: `missing from STRATEGY_REGISTRY` | Added a new problem_type to CLASSIFICATION_TABLE but no strategy function | Add the strategy in strategies.py + register it |
| `validate-cross-refs` fails: `not referenced in CLASSIFICATION_TABLE` | STRATEGY_REGISTRY key has no corresponding entry in classifier | Either add the entry or remove the registry key (may be dead code) |
| `pytest` fails with `ImportError` | Missing Python dependency | `pip install -r requirements.txt` |
| `list-modules.py` exits 1 | manifest.json syntax error or invalid JSON | Check JSON syntax, validate with `python3 -m json.tool <manifest>` |
| Release workflow fails after tag push | Version drift or test failure | Check CI logs, fix issue, delete and re-create tag |

---

## Design notes

- **Why separate `manifest.json` instead of `__init__.py` metadata?**
  We want the manifest to be machine-readable JSON without importing Python
  at runtime, and to decouple `version` / `compat` / `depends_on` from the
  module's runtime code.
- **Why per-module SemVer instead of repo-wide versioning?**
  Modules evolve at very different cadences (auto_fix changes frequently;
  kill_switch, detection rarely). Coupling them punishes downstream pinning.
- **Why validate cross-references in CI?**
  CLASSIFICATION_TABLE and STRATEGY_REGISTRY must be kept in sync. A PR that
  adds a strategy but forgets the registry entry causes a runtime error. CI
  catches this deterministically.
