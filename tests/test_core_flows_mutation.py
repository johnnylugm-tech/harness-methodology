"""Top-10 (+4 补充) mutation-targeted tests covering the main flows most vulnerable to mutation.

Selection rationale:
  harness/tool_runners.py had 50.2% kill rate because run_tool() dispatch dict,
  _SKIP_TOOLS, _DEFAULT_TIMEOUTS, and tool command construction had ZERO tests.
  These 6 tests cover those paths directly via mock subprocess.

  core/quality_gate/sab_parser.py had 78% — kept the 4 highest-coverage tests for
  derive_gate_score_overrides (NFR→gate floor) and SAB auto-derive pipeline.

Targeted kill rate: ≥ 70%.
"""
import json
import subprocess
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# T1: run_tool — SKIP_TOOLS returns ("", -1) and never spawns subprocess
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_skip_tools_returns_empty_minus_one():
    """Mutating _SKIP_TOOLS set (e.g. "mutmut"→"XXmutmutXX") breaks this."""
    from harness.tool_runners import run_tool
    with patch("subprocess.run") as mock_sp:
        out, rc = run_tool("mutmut", "/tmp")
        assert out == ""
        assert rc == -1
        mock_sp.assert_not_called()  # must NOT spawn subprocess for skip-list tools

    with patch("subprocess.run") as mock_sp2:
        out2, rc2 = run_tool("scancode", "/tmp")
        assert out2 == ""
        assert rc2 == -1
        mock_sp2.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T2: run_tool — correct command dispatched for "ruff"
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_dispatches_correct_ruff_command(tmp_path):
    """Mutating "ruff" key in cmds dict (→"XXruffXX") makes this fail."""
    from harness.tool_runners import run_tool

    mock_result = MagicMock()
    mock_result.stdout = '{"generalDiagnostics": []}'
    mock_result.stderr = ""
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result) as mock_sp:
        out, rc = run_tool("ruff", str(tmp_path))

    args = mock_sp.call_args[0][0]          # first positional arg = command list
    assert args[0] == "ruff"               # binary name correct
    assert "check" in args                 # subcommand present
    assert str(tmp_path) in args           # project root passed
    assert "--output-format" in args       # format flag key present
    fmt_idx = args.index("--output-format")
    assert args[fmt_idx + 1] == "json", "ruff --output-format value must be 'json'"  # kills id=54
    assert rc == 0


# ─────────────────────────────────────────────────────────────────────────────
# T3: run_tool — correct command dispatched for "mypy"
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_dispatches_correct_mypy_command(tmp_path):
    """Mutating "mypy" key in cmds dict (→"XXmypyXX") makes this fail."""
    from harness.tool_runners import run_tool

    mock_result = MagicMock(stdout="Success: no issues found", stderr="", returncode=0)
    with patch("subprocess.run", return_value=mock_result) as mock_sp:
        out, rc = run_tool("mypy", str(tmp_path))

    args = mock_sp.call_args[0][0]
    assert args[0] == "mypy"
    assert str(tmp_path) in args
    assert "--ignore-missing-imports" in args
    assert "--no-color-output" in args
    assert "Success" in out   # stdout passthrough confirmed
    assert rc == 0


# ─────────────────────────────────────────────────────────────────────────────
# T4: run_tool — timeout respected and propagated
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_timeout_returns_minus_two(tmp_path):
    """Mutating timeout value or the -2 return breaks this."""
    from harness.tool_runners import run_tool

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ruff", 30)):
        out, rc = run_tool("ruff", str(tmp_path))

    assert rc == -2
    assert "TIMEOUT" in out
    assert "ruff" in out


# ─────────────────────────────────────────────────────────────────────────────
# T5: run_tool — unknown tool returns ("", -1)  (not in cmds dict)
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_unknown_tool_returns_empty_minus_one(tmp_path):
    """Mutating the dict lookup fallback ("" / -1) is caught here."""
    from harness.tool_runners import run_tool

    with patch("subprocess.run") as mock_sp:
        out, rc = run_tool("nonexistent-tool-xyz", str(tmp_path))

    assert out == ""
    assert rc == -1
    mock_sp.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T6: run_tool — timeout_override overrides _DEFAULT_TIMEOUTS
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_timeout_override_is_used(tmp_path):
    """Mutating 'if timeout_override is not None' branch makes this fail."""
    from harness.tool_runners import run_tool

    captured_timeout = []

    def fake_run(cmd, **kwargs):
        captured_timeout.append(kwargs.get("timeout"))
        return MagicMock(stdout="", stderr="", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        run_tool("ruff", str(tmp_path), timeout_override=99)

    assert captured_timeout == [99]   # override must be used, not _DEFAULT_TIMEOUTS["ruff"]


# ─────────────────────────────────────────────────────────────────────────────
# T7: sab_parser — derive_gate_score_overrides uses standard floor
# ─────────────────────────────────────────────────────────────────────────────
def test_derive_gate_score_overrides_uses_standard_floor():
    """Mutating _GATE_DIMENSION_STANDARD values or _NFR_TYPE_TO_DIM breaks this."""
    from core.quality_gate.sab_parser import derive_gate_score_overrides

    nfr_dim_map = {"NFR-01": "performance", "NFR-02": "security"}
    nfr_trace = {
        "NFR-01": {"type": "performance", "target": "fast"},
        "NFR-02": {"type": "security",    "target": "secure"},
    }
    result = derive_gate_score_overrides(nfr_dim_map, nfr_trace)

    assert "performance" in result
    assert "security" in result
    # must use GATE_DIMENSION_STANDARD floor (performance=75, security=80)
    assert result["performance"] == 75.0
    assert result["security"] == 80.0


# ─────────────────────────────────────────────────────────────────────────────
# T8: sab_parser — explicit ≥N in NFR target overrides standard floor
# ─────────────────────────────────────────────────────────────────────────────
def test_derive_gate_score_overrides_explicit_target_wins():
    """Mutating the ≥N regex or the 'take max' logic breaks this."""
    from core.quality_gate.sab_parser import derive_gate_score_overrides

    nfr_dim_map = {"NFR-04": "security"}
    nfr_trace = {"NFR-04": {"type": "security", "target": "PII recall ≥ 95%"}}
    result = derive_gate_score_overrides(nfr_dim_map, nfr_trace)

    # 95 > standard floor 80 → explicit target must win
    assert result["security"] == 95.0


# ─────────────────────────────────────────────────────────────────────────────
# T9: sab_parser — NFR type not in _NFR_TYPE_TO_DIM → advisory_only, not dim_map
# ─────────────────────────────────────────────────────────────────────────────
def test_advisory_nfr_types_excluded_from_dimension_mapping(tmp_path):
    """Mutating _NFR_ADVISORY_TYPES or the advisory-branch logic breaks this."""
    from core.quality_gate.sab_parser import extract_sab_from_sad

    sad = tmp_path / "SAD.md"
    sad.write_text(
        "<!-- SAB:START -->\n"
        "```yaml\n"
        "sab:\n"
        "  version: '1.0'\n"
        "  phase: 2\n"
        "  project: test\n"
        "  modules: [src/]\n"
        "  nfr_traceability:\n"
        "    NFR-A: {type: deployability, target: 'deploy fast', module: ops}\n"
        "    NFR-B: {type: security,      target: 'secure',     module: app}\n"
        "```\n"
        "<!-- SAB:END -->\n",
        encoding="utf-8",
    )
    spec = extract_sab_from_sad(str(sad))
    assert spec is not None

    # deployability has no gate dimension tool → must appear in advisory_only, not dim_map
    assert "deployability" in spec.advisory_only
    assert "NFR-A" not in spec.nfr_dimension_mapping

    # security is a real dimension → must appear in dim_map
    assert "NFR-B" in spec.nfr_dimension_mapping
    assert spec.nfr_dimension_mapping["NFR-B"] == "security"


# ─────────────────────────────────────────────────────────────────────────────
# T10: sab_parser — gate_score_overrides floor propagation end-to-end
# ─────────────────────────────────────────────────────────────────────────────
def test_extract_sab_gate_score_overrides_end_to_end(tmp_path):
    """End-to-end: SAD with NFR → SABSpec.gate_score_overrides has correct floors.
    Mutating any step in the pipeline (NFR type→dim, derive, SABSpec storage) breaks this."""
    from core.quality_gate.sab_parser import extract_sab_from_sad

    sad = tmp_path / "SAD.md"
    sad.write_text(
        "<!-- SAB:START -->\n"
        "```yaml\n"
        "sab:\n"
        "  version: '1.0'\n"
        "  phase: 2\n"
        "  project: test\n"
        "  modules: [src/]\n"
        "  nfr_traceability:\n"
        "    NFR-1: {type: performance, target: 'p95 < 3s',   module: pipeline}\n"
        "    NFR-2: {type: security,    target: '≥ 90',       module: auth}\n"
        "    NFR-3: {type: reliability, target: 'uptime',     module: health}\n"
        "```\n"
        "<!-- SAB:END -->\n",
        encoding="utf-8",
    )
    spec = extract_sab_from_sad(str(sad))
    assert spec is not None

    gso = spec.gate_score_overrides
    assert "performance" in gso      # performance NFR → floor
    assert "security" in gso         # security NFR → floor, explicit ≥90 wins
    assert "error_handling" in gso   # reliability → error_handling dimension

    assert gso["performance"] == 75.0   # standard floor
    assert gso["security"] == 90.0      # explicit ≥90 > standard 80
    assert gso["error_handling"] == 80.0  # standard floor for reliability→error_handling


# ─────────────────────────────────────────────────────────────────────────────
# T11: run_tool — default timeout from _DEFAULT_TIMEOUTS actually passed to subprocess
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_default_timeout_passed_to_subprocess(tmp_path):
    """Mutating _DEFAULT_TIMEOUTS values (30→31, 60→61) breaks this.
    Covers mutants 4-31 (every timeout dict value mutation).
    IMPORTANT: assert hard-coded expected values, NOT _DEFAULT_TIMEOUTS[key] —
    asserting against the same constant that was mutated makes the test trivially pass."""
    from harness.tool_runners import run_tool

    captured_ruff = []
    captured_mypy = []
    captured_bandit = []

    def fake_run(cmd, **kwargs):
        # cmd[0] identifies the tool
        captured_ruff.append(kwargs.get("timeout")) if cmd[0] == "ruff" else None
        captured_mypy.append(kwargs.get("timeout")) if cmd[0] == "mypy" else None
        captured_bandit.append(kwargs.get("timeout")) if cmd[0] == "bandit" else None
        return MagicMock(stdout="", stderr="", returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        run_tool("ruff", str(tmp_path))
        run_tool("mypy", str(tmp_path))
        run_tool("bandit", str(tmp_path))

    assert captured_ruff == [30],    "ruff default timeout must be 30s"
    assert captured_mypy == [60],    "mypy default timeout must be 60s"
    assert captured_bandit == [60],  "bandit default timeout must be 60s"

    # Cover all remaining cmds-dict tools with their expected timeout values.
    # Hard-code values: asserting against _DEFAULT_TIMEOUTS[key] re-reads the mutated value.
    expected_timeouts = {
        "pyright": 60, "pytest-cov": 120, "pytest": 120,
        "gitleaks": 30, "radon-cc": 30, "radon-mi": 30,
        "pytest-benchmark": 180, "pytest-cov-integration": 180,
    }
    for tool_name, expected in expected_timeouts.items():
        captured_t = []
        def _fake(cmd, **kwargs):
            captured_t.append(kwargs.get("timeout"))
            return MagicMock(stdout="", stderr="", returncode=0)
        with patch("subprocess.run", side_effect=_fake):
            run_tool(tool_name, str(tmp_path))
        assert captured_t == [expected], f"{tool_name} timeout must be {expected}s, got {captured_t}"


# ─────────────────────────────────────────────────────────────────────────────
# T12: run_tool — bandit and pytest-cov dispatch correctly
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_dispatches_all_cmds_tools(tmp_path):
    """Mutating any tool key in the cmds dict (→'XXtoolXX') breaks this.
    Covers mutants 46-132 (all tool key + flag mutations)."""
    from harness.tool_runners import run_tool

    mock_result = MagicMock(stdout="", stderr="", returncode=0)

    # Each entry: (tool_name, expected_binary, required_flags)
    # Flags listed are literals from tool_runners.py — mutations to any one are caught.
    tool_checks = [
        ("ruff",       "ruff",     ["--output-format", "json", "check"]),
        ("mypy",       "mypy",     ["--ignore-missing-imports", "--no-color-output", "--no-error-summary"]),
        ("pyright",    "pyright",  ["--outputjson"]),
        ("pytest-cov", "pytest",   ["--cov", "--cov-report=term-missing", "-q", "--tb=no", "--no-header"]),
        ("pytest",     "pytest",   ["-q", "--tb=no", "--no-header"]),
        ("gitleaks",   "gitleaks", ["detect"]),
        ("bandit",     "bandit",   ["-r", "-f", "json", "--exit-zero"]),
        ("radon-cc",   "radon",    ["cc", "-j", "--min"]),
        ("radon-mi",   "radon",    ["mi", "-j"]),
    ]
    for tool_name, expected_bin, required_flags in tool_checks:
        with patch("subprocess.run", return_value=mock_result) as mock_sp:
            run_tool(tool_name, str(tmp_path))
        args = mock_sp.call_args[0][0]
        assert args[0] == expected_bin, f"{tool_name}: expected binary '{expected_bin}', got '{args[0]}'"
        for flag in required_flags:
            assert flag in args, f"{tool_name}: flag '{flag}' missing from {args}"


# ─────────────────────────────────────────────────────────────────────────────
# T13: run_tool — scancode is in SKIP_TOOLS (second member matters)
# ─────────────────────────────────────────────────────────────────────────────
def test_run_tool_scancode_also_skipped():
    """Mutating the second member of _SKIP_TOOLS frozenset ('scancode'→'XXscancodeXX') breaks this.
    Covers mutants 2-3 (second set member mutation)."""
    from harness.tool_runners import run_tool

    with patch("subprocess.run") as mock_sp:
        out, rc = run_tool("scancode", "/tmp")
    assert rc == -1
    assert out == ""
    mock_sp.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T14-T25: compute_tool_score oracle tests
#
# Rationale: score_dict (L400-449, 38 mutants) and scorer_logic (L450-649,
# ~67 mutants) survived because existing tests call run_tool() with stdout=""
# — the scorer is dispatched but never validated.  These tests call
# compute_tool_score() with realistic outputs and assert exact numeric scores.
#
# Design rule: expected values are HARD-CODED literals; never reference the
# constant or formula being tested (re-reads the mutated value → always passes).
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_tool_score_ruff_scorer():
    """1 violation → 98.0. Kills score_dict["ruff"] dispatch and -2 deduction."""
    from harness.tool_runners import compute_tool_score

    out = '[{"code": "E501", "message": "line too long", "filename": "f.py"}]'
    assert compute_tool_score("ruff", out, 0) == 98.0


def test_compute_tool_score_mypy_scorer():
    """2 errors → 90.0. Kills score_dict["mypy"] dispatch and -5-per-error deduction."""
    from harness.tool_runners import compute_tool_score

    out = "f.py:1: error: incompatible type\nf.py:2: error: no attribute"
    assert compute_tool_score("mypy", out, 1) == 90.0


def test_compute_tool_score_pytest_pass_rate():
    """5 passed / 7 total → 71.4. Kills passed_m/failed_m regex and pass-rate arithmetic."""
    from harness.tool_runners import compute_tool_score

    out = "5 passed, 2 failed in 1.23s"
    # Hard-code 71.4 — never compute from the mutated source.
    assert compute_tool_score("pytest", out, 1) == 71.4


def test_compute_tool_score_pytest_cov_coverage():
    """TOTAL line with 80% → 80.0. Kills TOTAL regex and float conversion."""
    from harness.tool_runners import compute_tool_score

    out = "TOTAL  1000  200  80%\n5 passed in 1.23s"
    assert compute_tool_score("pytest-cov", out, 0) == 80.0


def test_compute_tool_score_bandit_severity():
    """1 HIGH issue → 90.0. Kills score_dict["bandit"] and HIGH deduction (-10)."""
    from harness.tool_runners import compute_tool_score

    out = json.dumps({"results": [{"issue_severity": "HIGH", "issue_text": "exec used"}]})
    assert compute_tool_score("bandit", out, 0) == 90.0


def test_compute_tool_score_radon_cc_complexity():
    """Function with CC=12 (>10 threshold) → 95.0. Kills >10 comparison and -5 deduction."""
    from harness.tool_runners import compute_tool_score

    out = json.dumps({"file.py": [{"complexity": 12, "type": "function", "name": "foo"}]})
    assert compute_tool_score("radon-cc", out, 0) == 95.0


def test_compute_tool_score_radon_mi_average():
    """Two files MI 80.0 + 60.0 → avg 70.0. Kills mi field access and average arithmetic."""
    from harness.tool_runners import compute_tool_score

    out = json.dumps({"a.py": {"mi": 80.0, "rank": "A"}, "b.py": {"mi": 60.0, "rank": "B"}})
    assert compute_tool_score("radon-mi", out, 0) == 70.0


def test_compute_tool_score_benchmark_unit_scaling():
    """Kills to_ms literal mutations (1.0→2.0) and both comparison thresholds.

    600ms < 1000ms → 100.0 (no penalty)
    1500ms > 1000ms → 75.0 (−25)
    3500ms > 3000ms → 50.0 (−50)

    With to_ms["ms"]=2.0 mutation: 600*2=1200>1000 → 75.0 ≠ 100.0 → KILL.
    """
    from harness.tool_runners import compute_tool_score

    fast = "Name (time in ms)\n  test_fast   600.0   700.0\n"
    assert compute_tool_score("pytest-benchmark", fast, 0) == 100.0

    slow = "Name (time in ms)\n  test_slow   1500.0   2000.0\n"
    assert compute_tool_score("pytest-benchmark", slow, 0) == 75.0

    heavy = "Name (time in ms)\n  test_heavy   3500.0   4000.0\n"
    assert compute_tool_score("pytest-benchmark", heavy, 0) == 50.0


def test_compute_tool_score_gitleaks_leak_detected():
    """Leaks found → 0.0. Kills 'No leaks' string check and rc==0 condition."""
    from harness.tool_runners import compute_tool_score

    out = "WRN leaks found: 2 leaks detected"
    assert compute_tool_score("gitleaks", out, 1) == 0.0


def test_compute_tool_score_assertion_quality():
    """7/10 test fns asserted → 70.0. Kills asserted/total arithmetic and round()."""
    from harness.tool_runners import compute_tool_score

    out = json.dumps({"total": 10, "asserted": 7, "zero_assert": []})
    assert compute_tool_score("ast-assertions", out, 0) == 70.0


def test_compute_tool_score_error_handling_coverage():
    """4/5 files have handlers → 80.0. Kills with_handler/total arithmetic."""
    from harness.tool_runners import compute_tool_score

    out = json.dumps({"total": 5, "with_handler": 4, "no_handler": ["plain.py"]})
    assert compute_tool_score("ast-error-handling", out, 0) == 80.0


def test_compute_tool_score_all_tools_dispatch():
    """All 9 subprocess tools return the expected score for valid output.

    Kills every score_dict key mutation ("ruff"→"XXruffXX" etc.):
    a wrong scorer produces a different numeric result for tool-specific output.
    """
    from harness.tool_runners import compute_tool_score

    cases = [
        # tool, output, returncode, expected_score
        ("ruff",      "[]",                                        0,  100.0),
        ("mypy",      "Success: no issues found",                  0,  100.0),
        ("pyright",   '{"summary": {"errorCount": 0}}',            0,  100.0),
        ("pytest",    "10 passed in 1.2s",                         0,  100.0),
        # pytest-cov: no TOTAL line → fallback to pass-rate (10/10 = 100)
        ("pytest-cov","10 passed in 1.2s",                         0,  100.0),
        ("gitleaks",  "No leaks found",                            0,  100.0),
        ("bandit",    '{"results": []}',                           0,  100.0),
        ("radon-cc",  "{}",                                        0,  100.0),
        # radon-mi with one file: MI=90.0 → 90.0 (distinguishes from radon-cc which → 100)
        ("radon-mi",  '{"f.py": {"mi": 90.0, "rank": "A"}}',      0,  90.0),
    ]
    for tool, output, rc, expected in cases:
        score = compute_tool_score(tool, output, rc)
        assert score == expected, f"{tool}: expected {expected}, got {score}"
