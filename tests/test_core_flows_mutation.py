"""Top-10 (+4 补充) mutation-targeted tests covering the main flows most vulnerable to mutation.

Selection rationale:
  harness/tool_runners.py had 50.2% kill rate because run_tool() dispatch dict,
  _SKIP_TOOLS, _DEFAULT_TIMEOUTS, and tool command construction had ZERO tests.
  These 6 tests cover those paths directly via mock subprocess.

  core/quality_gate/sab_parser.py had 78% — kept the 4 highest-coverage tests for
  derive_gate_score_overrides (NFR→gate floor) and SAB auto-derive pipeline.

Targeted kill rate: ≥ 70%.
"""
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
    assert "--output-format" in args       # json format flag present
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

    # Each entry: (tool_name, expected_binary, required_flag_or_None)
    tool_checks = [
        ("ruff",               "ruff",    "--output-format"),
        ("mypy",               "mypy",    "--ignore-missing-imports"),
        ("pyright",            "pyright", "--outputjson"),
        ("pytest-cov",         "pytest",  "--cov"),
        ("pytest",             "pytest",  "-q"),
        ("gitleaks",           "gitleaks","detect"),
        ("bandit",             "bandit",  "-r"),
        ("radon-cc",           "radon",   "cc"),
        ("radon-mi",           "radon",   "mi"),
    ]
    for tool_name, expected_bin, required_flag in tool_checks:
        with patch("subprocess.run", return_value=mock_result) as mock_sp:
            run_tool(tool_name, str(tmp_path))
        args = mock_sp.call_args[0][0]
        assert args[0] == expected_bin, f"{tool_name}: expected binary '{expected_bin}', got '{args[0]}'"
        if required_flag:
            assert required_flag in args, f"{tool_name}: required flag '{required_flag}' missing from {args}"


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
