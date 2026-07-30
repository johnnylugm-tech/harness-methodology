"""Round 26 — the SAB module paths Gate 1 enforces are visible while code is written.

Phase 2 fixes the dotted module names for each FR in `.methodology/SAB.json` before
any code exists. Gate 1 then BLOCKS on a PHANTOM — a declared module the codebase
does not have (cli/gate_cmds.py::_check_sab_module_alignment, scoped to the FR's own
modules by _filter_phantoms_for_fr). Until this station the implementing agent was
never shown those names: `build_tdd_green_prompt` took no SAB input at all, and
`build_tdd_red_prompt` went further and offered `cli.py` as the CLI entry-point
example — a layout the SAB may forbid.

taskq-plus P3 paid for it twice in one phase:
  * `fix(FR-02): relocate executor to service/ to satisfy SAB phantom check`
  * FR-05, where SAB declared `taskq_plus.cli.main` while the test and the
    implementation had settled on a flat `taskq_plus/cli.py`: three GATE1
    dispatches on the same phantom, then the FR restarted from RED to rewrite the
    layout. All five FRs gated in that phase needed a SAB amendment.

The rendered block itself is pinned byte-equal by tests/test_fr_prompt_snapshots.py
(the fixture now carries a two-module SAB, the multi-module shape of the incident).
What this module covers is the behaviour a golden cannot: that the block is scoped
to the right FR, and that a missing or broken SAB degrades to silence instead of
taking the prompt down with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.fr_prompts._shared import _sab_binding_block  # noqa: E402
from cli.fr_prompts.tdd import (  # noqa: E402
    build_tdd_green_prompt,
    build_tdd_improve_prompt,
    build_tdd_red_prompt,
)

_SRC = "03-development/src"

_SAB = {
    "layers": [
        {"name": "cli", "modules": [{"name": "pkg.cli.main"}]},
        {"name": "service", "modules": [{"name": "pkg.service.exec"}]},
    ],
    "fr_module_traceability": {
        "FR-05": ["pkg.cli.main", "pkg.cli.commands"],
        "FR-02": "pkg.service.exec",
    },
}


def _project(tmp_path: Path, sab: object | None = _SAB) -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    if sab is not None:
        (meth / "SAB.json").write_text(
            sab if isinstance(sab, str) else json.dumps(sab), encoding="utf-8"
        )
    return tmp_path


class TestScoping:
    def test_only_this_frs_modules_appear(self, tmp_path):
        block = _sab_binding_block(_project(tmp_path), "FR-05", _SRC)
        assert "pkg.cli.main" in block
        assert "pkg.cli.commands" in block
        assert "pkg.service.exec" not in block, (
            "another FR's modules are not this FR's constraint — the gate scopes "
            "phantoms per FR and the prompt must match that scope"
        )

    def test_a_single_string_entry_is_accepted(self, tmp_path):
        """fr_module_traceability values may be a string OR a list (17a99c3)."""
        block = _sab_binding_block(_project(tmp_path), "FR-02", _SRC)
        assert "pkg.service.exec" in block

    def test_both_on_disk_shapes_are_offered(self, tmp_path):
        """A declared name is satisfied by a leaf module or a package of that name
        — `discover_modules_at` registers both, so the prompt must not imply one."""
        block = _sab_binding_block(_project(tmp_path), "FR-02", _SRC)
        assert f"{_SRC}/pkg/service/exec.py" in block
        assert f"{_SRC}/pkg/service/exec/__init__.py" in block

    def test_the_amendment_command_is_named(self, tmp_path):
        """The escape hatch has to be in the prompt, or "do not drift" is a dead
        end and the agent drifts anyway."""
        block = _sab_binding_block(_project(tmp_path), "FR-05", _SRC)
        assert "--resolve-phantom" in block
        assert "ADR.md" in block


class TestDegradesToSilence:
    """A prompt must not fail to render because an optional artifact is missing.

    The gate keeps its own independent check either way, so silence here loses
    guidance, never enforcement.
    """

    @pytest.mark.parametrize("sab, fr_id", [
        (None, "FR-05"),                                   # no SAB.json at all
        (_SAB, "FR-99"),                                   # FR not in traceability
        ({"layers": []}, "FR-05"),                         # no traceability key
        ({"fr_module_traceability": {"FR-05": []}}, "FR-05"),   # empty list
        ({"fr_module_traceability": {"FR-05": 17}}, "FR-05"),   # wrong type
        ("{ not json", "FR-05"),                           # unreadable
        ("[]", "FR-05"),                                   # not an object
    ])
    def test_empty_block(self, tmp_path, sab, fr_id):
        assert _sab_binding_block(_project(tmp_path, sab), fr_id, _SRC) == ""


class TestEveryTddStepCarriesIt:
    """RED writes the test that fixes the import name; GREEN writes the module;
    IMPROVE can move it. All three can create the phantom, so all three are told."""

    @pytest.mark.parametrize("builder", [
        build_tdd_red_prompt, build_tdd_green_prompt, build_tdd_improve_prompt,
    ])
    def test_declared_paths_reach_the_prompt(self, tmp_path, builder, monkeypatch):
        proj = _project(tmp_path)
        (proj / "01-requirements").mkdir(parents=True, exist_ok=True)
        srs = proj / "01-requirements" / "SRS.md"
        srs.write_text("### FR-05: CLI\n\nMUST work.\n\n---\n", encoding="utf-8")
        # TDD-RED reaches for CRG; keep it silent and deterministic.
        monkeypatch.setattr(
            "harness.crg_bridge.CRGBridge",
            type("_Stub", (), {"__init__": lambda self, *a, **k: None,
                               "semantic_search": lambda self, *a, **k: {"results": []}}),
            raising=False,
        )
        prompt = builder("FR-05", 3, proj, srs, "03-development/tests/test_fr05.py", _SRC)
        assert "[SAB — BINDING MODULE PATHS]" in prompt
        assert "pkg.cli.main" in prompt


class TestTheContradictionIsGone:
    """TDD-RED used to name `cli.py` as THE CLI entry-point module and teach
    `cli.main([...])`. That is the layout taskq-plus's SAB forbade, and the test
    file written from it is what pulled the implementation to the phantom name."""

    def test_red_no_longer_hardcodes_a_module_layout(self, tmp_path, monkeypatch):
        proj = _project(tmp_path)
        (proj / "01-requirements").mkdir(parents=True, exist_ok=True)
        srs = proj / "01-requirements" / "SRS.md"
        srs.write_text("### FR-05: CLI\n\nMUST work.\n\n---\n", encoding="utf-8")
        monkeypatch.setattr(
            "harness.crg_bridge.CRGBridge",
            type("_Stub", (), {"__init__": lambda self, *a, **k: None,
                               "semantic_search": lambda self, *a, **k: {"results": []}}),
            raising=False,
        )
        prompt = build_tdd_red_prompt(
            "FR-05", 3, proj, srs, "03-development/tests/test_fr05.py", _SRC)
        assert "(cli.py, __main__.py, config.py)" not in prompt
        assert "`cli.main([\"submit\", cmd])`" not in prompt
        # ...and it points at the authority instead.
        assert "[SAB — BINDING MODULE PATHS] block above" in prompt
