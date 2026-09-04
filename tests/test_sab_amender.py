"""Tests for core.quality_gate.sab_amender.

These tests assert the dotted-module form contract: discover_modules emits
dotted names (``taskq.cli``), missing_modules compares against the
normalised registered set, and amend_sab writes the dotted form back into
SAB.json. This mirrors `_check_sab_module_alignment` in harness_cli.py —
the two functions MUST share a representation so an amend run actually
closes the BLOCKED state the gate enforces (see sab_amender.discover_modules
docstring).
"""
import json
import pytest

from core.quality_gate.sab_amender import (
    amend_sab,
    discover_modules,
    missing_modules,
    normalize_sab_module_to_dotted,
    phantom_modules,
    sab_module_candidate,
)


pytestmark = [pytest.mark.core]


# ---------------------------------------------------------------------------
# TestDiscoverModules
# ---------------------------------------------------------------------------

class TestDiscoverModules:
    def test_discovers_py_files_under_src(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("")
        (src / "executor.py").write_text("")
        result = discover_modules(tmp_path)
        assert "taskq.cli" in result
        assert "taskq.executor" in result

    def test_skips_pycache_and_init(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("")
        (src / "__init__.py").write_text("")  # package marker, not a module
        pycache = src / "__pycache__"
        pycache.mkdir()
        (pycache / "cli.cpython-311.pyc").write_text("")
        result = discover_modules(tmp_path)
        assert "taskq.cli" in result
        assert all("__pycache__" not in p for p in result)
        assert all(not p.endswith("__init__") for p in result)

    def test_empty_when_src_dir_missing(self, tmp_path):
        assert discover_modules(tmp_path) == []

    def test_sorted_deterministically(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for name in ("zebra.py", "alpha.py", "middle.py"):
            (src / name).write_text("")
        result = discover_modules(tmp_path, src_dir="src")
        assert result == sorted(result)

    def test_registers_package_style_modules(self, tmp_path):
        """Round 6 station 3: a directory containing __init__.py (no leaf
        .py file of the same name) must be registered under its own dotted
        name — a SAB entry may name a PACKAGE, not just a leaf module (see
        detection.drift_detector.sab_module_to_path_variants, which already
        expands an __init__.py candidate). Prior to this fix, discover_modules
        excluded __init__.py-marked directories entirely, making a
        legitimate package-style registration look "phantom" to Gate 1's
        phantom_modules even though P4 preflight correctly found it."""
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "cli").mkdir()
        (src / "cli" / "__init__.py").write_text("")  # package, no cli.py leaf
        (src / "executor.py").write_text("")
        result = discover_modules(tmp_path)
        assert "taskq.cli" in result  # the package itself, not a leaf module
        assert "taskq" in result  # the top-level package too
        assert "taskq.executor" in result  # existing leaf-module behavior unaffected
        assert all(not p.endswith("__init__") for p in result)


# ---------------------------------------------------------------------------
# TestSabModuleCandidate
# ---------------------------------------------------------------------------

class TestSabModuleCandidate:
    """sab_module_candidate is the shared dict-unwrap primitive: it extracts
    the physical-location candidate string from a SAB modules entry, without
    the further dotted-name normalization normalize_sab_module_to_dotted
    applies. scripts/generate_sab.py's path-rewrite step needs this raw
    (pre-normalization) candidate to run the same on-disk existence check
    string-shaped entries get — see TestGenerateSabDictShapedModules."""

    def test_dict_with_implemented_in_returns_implemented_in(self):
        assert sab_module_candidate(
            {"name": "app.cli", "implemented_in": "app.interface.cli"}
        ) == "app.interface.cli"

    def test_dict_without_implemented_in_falls_back_to_name(self):
        assert sab_module_candidate({"name": "app.executor"}) == "app.executor"

    def test_dict_implemented_in_blank_falls_back_to_name(self):
        assert sab_module_candidate(
            {"name": "app.core", "implemented_in": ""}
        ) == "app.core"

    def test_dict_implemented_in_non_string_falls_back_to_name(self):
        assert sab_module_candidate(
            {"name": "app.core", "implemented_in": None}
        ) == "app.core"

    def test_dict_missing_both_fields_returns_none(self):
        assert sab_module_candidate({"foo": "bar"}) is None
        assert sab_module_candidate({}) is None

    def test_plain_string_passes_through_unchanged(self):
        assert sab_module_candidate("app.cli") == "app.cli"
        assert sab_module_candidate("src/app/cli.py") == "src/app/cli.py"

    def test_non_dict_non_str_passes_through_unchanged(self):
        for val in (None, 42, [], True, 3.14):
            assert sab_module_candidate(val) is val


# ---------------------------------------------------------------------------
# TestNormalizeSabModuleToDotted
# ---------------------------------------------------------------------------

class TestNormalizeSabModuleToDotted:
    """SAB `modules` entries may be dict-shaped ({"name": ..., "implemented_in":
    ...}) — the official schema form rendered by
    sab_parser.render_canonical_sab_template() for a module whose logical
    name differs from its physical location. Before this fix, dict entries
    silently normalised to None (isinstance(mod, str) guard), making the
    registered set permanently empty for any SAB.json using this form."""

    def test_dict_with_implemented_in_uses_implemented_in(self):
        assert normalize_sab_module_to_dotted(
            {"name": "app.cli", "implemented_in": "app.interface.cli"}
        ) == "app.interface.cli"

    def test_dict_without_implemented_in_falls_back_to_name(self):
        assert normalize_sab_module_to_dotted({"name": "app.executor"}) == "app.executor"

    def test_dict_missing_both_fields_returns_none(self):
        assert normalize_sab_module_to_dotted({"foo": "bar"}) is None
        assert normalize_sab_module_to_dotted({}) is None

    def test_dict_implemented_in_blank_falls_back_to_name(self):
        assert normalize_sab_module_to_dotted(
            {"name": "app.core", "implemented_in": ""}
        ) == "app.core"

    def test_dict_implemented_in_non_string_falls_back_to_name(self):
        assert normalize_sab_module_to_dotted(
            {"name": "app.core", "implemented_in": None}
        ) == "app.core"

    def test_dict_extracted_string_is_directory_marker_returns_none(self):
        assert normalize_sab_module_to_dotted({"name": "app/legacy/"}) is None

    def test_dict_implemented_in_path_form_normalises(self):
        assert normalize_sab_module_to_dotted(
            {"name": "app.cli", "implemented_in": "03-development/src/app/cli.py"}
        ) == "app.cli"

    def test_plain_string_dotted_unchanged(self):
        assert normalize_sab_module_to_dotted("app.cli") == "app.cli"

    def test_plain_string_path_form_unchanged(self):
        assert normalize_sab_module_to_dotted("src/app/cli.py") == "app.cli"

    def test_non_dict_non_str_returns_none(self):
        for bad in (None, 42, [], True, 3.14):
            assert normalize_sab_module_to_dotted(bad) is None


# ---------------------------------------------------------------------------
# TestMissingModules
# ---------------------------------------------------------------------------

class TestMissingModules:
    def test_returns_only_modules_not_in_any_layer(self):
        # Registered set is in dotted form; same as what discover_modules emits.
        sab = {
            "layers": [
                {"name": "cli", "modules": ["taskq.cli"]},
                {"name": "core", "modules": ["taskq.executor"]},
            ]
        }
        discovered = [
            "taskq.cli",
            "taskq.executor",
            "taskq.cache",
            "taskq.breaker",
        ]
        assert missing_modules(sab, discovered) == [
            "taskq.cache",
            "taskq.breaker",
        ]

    def test_returns_empty_when_all_registered(self):
        sab = {"layers": [{"name": "core", "modules": ["taskq.x"]}]}
        assert missing_modules(sab, ["taskq.x"]) == []

    def test_normalises_path_form_when_registered(self):
        """SAB entries in path form should be normalised to dotted before
        comparison, so a registered path-form entry matches a discovered
        dotted entry."""
        sab = {"layers": [{"name": "core", "modules": ["src/taskq/x.py"]}]}
        # discovered emits dotted; registered entry normalised to 'taskq.x'
        assert missing_modules(sab, ["taskq.x"]) == []

    def test_handles_layers_without_modules_key(self):
        sab = {"layers": [{"name": "empty"}]}
        assert missing_modules(sab, ["taskq.x"]) == ["taskq.x"]

    def test_dict_shaped_registered_entry_counts_as_registered(self):
        sab = {"layers": [{"name": "interface", "modules": [
            {"name": "app.cli", "implemented_in": "app.interface.cli"}
        ]}]}
        assert missing_modules(sab, ["app.interface.cli"]) == []


# ---------------------------------------------------------------------------
# TestPhantomModules
# ---------------------------------------------------------------------------

class TestPhantomModules:
    """Mirror of TestMissingModules but for the reverse direction:

    SAB declares modules that the codebase does not implement. This was the
    silent gap that let P2 SAB.json plan `taskq.config` / `taskq.models`
    layers survive into P4 uncaught — Gate 1 alignment only checked the
    unregistered direction (codebase → SAB), not the phantom direction
    (SAB → codebase). Closing this is the root-cause fix for the P4
    preflight BLOCK on Layer config/models 'missing from codebase'.
    """

    def test_returns_only_modules_not_on_disk(self):
        sab = {
            "layers": [
                {"name": "core", "modules": ["taskq.cli", "taskq.cache"]},
                {"name": "config", "modules": ["taskq.config"]},
                {"name": "models", "modules": ["taskq.models"]},
            ]
        }
        discovered = ["taskq.cli", "taskq.cache"]  # config/models absent on disk
        assert phantom_modules(sab, discovered) == ["taskq.config", "taskq.models"]

    def test_returns_empty_when_all_implemented(self):
        sab = {"layers": [{"name": "core", "modules": ["taskq.cli"]}]}
        assert phantom_modules(sab, ["taskq.cli"]) == []

    def test_normalises_path_form_when_registered(self):
        """Path-form SAB entries must normalise to dotted before comparing
        against discovered (dotted) modules. A registered path form
        ``src/taskq/x.py`` matched against discovered ``taskq.x`` is NOT
        phantom."""
        sab = {"layers": [{"name": "core", "modules": ["src/taskq/x.py"]}]}
        assert phantom_modules(sab, ["taskq.x"]) == []

    def test_skips_fr_id_placeholders(self):
        """FR-XX entries in SAB are traceability anchors, not file paths —
        even if no on-disk file matches, they aren't phantoms."""
        sab = {"layers": [{"name": "spec", "modules": ["FR-01", "FR-02"]}]}
        assert phantom_modules(sab, []) == []

    def test_skips_directory_markers(self):
        """Trailing-slash entries (directory groupings, not module paths)
        should not surface as phantoms — ``normalize_sab_module_to_dotted``
        returns ``None`` for them and ``phantom_modules`` skips ``None``."""
        sab = {"layers": [{"name": "group", "modules": ["taskq/legacy/"]}]}
        assert phantom_modules(sab, []) == []

    def test_deterministic_sorted_output(self):
        """Multiple phantoms must come back in sorted order so callers can
        write stable test assertions and stable log output."""
        sab = {
            "layers": [
                {"name": "L", "modules": ["taskq.zebra", "taskq.alpha", "taskq.middle"]}
            ]
        }
        result = phantom_modules(sab, [])
        assert result == sorted(result)
        assert result == ["taskq.alpha", "taskq.middle", "taskq.zebra"]

    def test_handles_layers_without_modules_key(self):
        """A layer with no 'modules' key is degenerate but must not crash."""
        sab = {"layers": [{"name": "empty"}]}
        assert phantom_modules(sab, []) == []

    def test_dict_shaped_registered_entry_not_phantom_when_implemented(self):
        sab = {"layers": [{"name": "interface", "modules": [
            {"name": "app.cli", "implemented_in": "app.interface.cli"}
        ]}]}
        assert phantom_modules(sab, ["app.interface.cli"]) == []


# ---------------------------------------------------------------------------
# TestAmendSab
# ---------------------------------------------------------------------------

class TestAmendSab:
    def _setup_project(self, tmp_path, sab_modules: list[str]):
        (tmp_path / ".methodology").mkdir()
        sab = {
            "version": "1.0",
            "layers": [
                {"name": "cli", "modules": list(sab_modules[:1]),
                 "allowed_dependencies": ["core"]},
                {"name": "core", "modules": list(sab_modules[1:]),
                 "allowed_dependencies": []},
            ],
        }
        (tmp_path / ".methodology" / "SAB.json").write_text(json.dumps(sab))
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        # Create one .py file per dotted module — flattened to the leaf
        # segment is enough because discover_modules only reads the leaf.
        for m in sab_modules + ["taskq.cache", "taskq.breaker"]:
            leaf = m.split(".")[-1] + ".py"
            (src / leaf).write_text("")
        return sab

    def test_amend_adds_new_modules_idempotent(self, tmp_path):
        self._setup_project(
            tmp_path,
            ["taskq.cli", "taskq.executor"],
        )
        added1 = amend_sab(tmp_path)
        assert sorted(added1) == sorted(["taskq.cache", "taskq.breaker"])
        # Verify on disk — written in dotted form (matches discover output).
        sab = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        all_modules = [m for layer in sab["layers"] for m in layer["modules"]]
        assert "taskq.cache" in all_modules
        assert "taskq.breaker" in all_modules

        # Second call: idempotent, nothing added.
        added2 = amend_sab(tmp_path)
        assert added2 == []

    def test_amend_dry_run_does_not_write(self, tmp_path):
        self._setup_project(tmp_path, ["taskq.cli"])
        before = (tmp_path / ".methodology" / "SAB.json").read_text()
        added = amend_sab(tmp_path, dry_run=True)
        assert len(added) == 2  # cache + breaker would be added
        after = (tmp_path / ".methodology" / "SAB.json").read_text()
        assert before == after, "dry_run must not modify SAB.json"

    def test_amend_noop_when_sab_absent(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        assert amend_sab(tmp_path) == []

    def test_amend_preserves_existing_layers_and_deps(self, tmp_path):
        """amend_sab must only touch layer.modules — not allowed_dependencies
        or any other field."""
        self._setup_project(tmp_path, ["taskq.cli", "taskq.executor"])
        before_sab = json.loads(
            (tmp_path / ".methodology" / "SAB.json").read_text()
        )
        amend_sab(tmp_path)
        after_sab = json.loads(
            (tmp_path / ".methodology" / "SAB.json").read_text()
        )
        # Same number of layers, same deps
        assert len(before_sab["layers"]) == len(after_sab["layers"])
        for b, a in zip(before_sab["layers"], after_sab["layers"]):
            assert b["name"] == a["name"]
            assert b.get("allowed_dependencies") == a.get("allowed_dependencies")
        # version unchanged
        assert before_sab["version"] == after_sab["version"]

# ---------------------------------------------------------------------------
# Round 26 — the SAB -> code direction, and the layer choice that was a guess.
# ---------------------------------------------------------------------------

class TestLayerChoiceFollowsTheModuleName:
    """A module whose path names a declared layer belongs in that layer.

    Before Round 26 every non-underscore module went to `layers[-1]` ("least
    risky"), which is only least risky when the last layer is a catch-all. In
    taskq-plus the last layer is `config`, so an amend run filed `taskq_plus.cli`,
    `taskq_plus.service` and `taskq_plus.storage` there — leaving SAB.json's own
    layering in direct contradiction with the `cli > observability > service >
    storage > models` order that project's NFR-06 enforces via `.importlinter`.
    """

    LAYERS = {"layers": [{"name": n} for n in
                         ("models", "storage", "service", "observability", "cli", "config")]}

    @pytest.mark.parametrize("module_path, expected", [
        ("taskq_plus/cli/main.py", "cli"),
        ("taskq_plus.service.executor", "service"),
        ("taskq_plus/storage/atomic.py", "storage"),
        ("taskq_plus/observability/audit.py", "observability"),
        # A PACKAGE registered under its own dotted name: the last segment IS
        # the layer (Round 6 站3's package-style registration shape).
        ("taskq_plus.cli", "cli"),
        # Deepest match wins when two segments both name layers.
        ("taskq_plus/storage/models/row.py", "models"),
    ])
    def test_a_declared_layer_in_the_path_wins(self, module_path, expected):
        from core.quality_gate.sab_amender import _heuristic_layer_choice
        assert _heuristic_layer_choice(self.LAYERS, module_path) == expected

    def test_the_taskq_plus_regression_specifically(self):
        """`taskq_plus.cli` must not land in `config` again."""
        from core.quality_gate.sab_amender import _heuristic_layer_choice
        assert _heuristic_layer_choice(self.LAYERS, "taskq_plus.cli") != "config"

    def test_underscore_helper_still_prefers_core(self):
        from core.quality_gate.sab_amender import _heuristic_layer_choice
        sab = {"layers": [{"name": "core"}, {"name": "infra"}]}
        assert _heuristic_layer_choice(sab, "pkg/_helper.py") == "core"

    def test_unmatched_path_still_falls_back_to_the_last_layer(self):
        from core.quality_gate.sab_amender import _heuristic_layer_choice
        assert _heuristic_layer_choice(self.LAYERS, "taskq_plus/util/misc.py") == "config"

    def test_no_layers_returns_core(self):
        from core.quality_gate.sab_amender import _heuristic_layer_choice
        assert _heuristic_layer_choice({}, "pkg/x.py") == "core"


class TestResolvePhantom:
    """The direction `amend_sab` never had: SAB declares, code does not have it.

    The gate's message offers "(a) implement them, OR (b) amend SAB.json" and
    only (a) had tooling, so taskq-plus P3 resolved a wrong Phase 2 guess by
    rewriting production code to match it — twice.
    """

    def _project(self, tmp_path, *, on_disk=("pkg.cli",)):
        src = tmp_path / "03-development" / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        for dotted in on_disk:
            leaf = dotted.split(".")[-1]
            (src / f"{leaf}.py").write_text("", encoding="utf-8")
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "SAB.json").write_text(json.dumps({
            "layers": [
                {"name": "service", "modules": [{"name": "pkg.service"}]},
                {"name": "cli", "modules": [
                    {"name": "pkg.cli.main"}, {"name": "pkg.cli.commands"}]},
            ],
            "fr_module_traceability": {
                "FR-05": ["pkg.cli.main", "pkg.cli.commands"],
                "FR-02": "pkg.service",
            },
        }), encoding="utf-8")
        return tmp_path

    def _sab(self, project):
        return json.loads((project / ".methodology" / "SAB.json").read_text())

    def test_retarget_rewrites_layer_and_traceability_together(self, tmp_path):
        from core.quality_gate.sab_amender import resolve_phantom
        proj = self._project(tmp_path)
        resolve_phantom(proj, "pkg.cli.main", to="pkg.cli",
                        reason="FR-05 is one click group; the declared split has no consumer")
        sab = self._sab(proj)
        cli_layer = [L for L in sab["layers"] if L["name"] == "cli"][0]
        dotted = [m["name"] if isinstance(m, dict) else m for m in cli_layer["modules"]]
        assert "pkg.cli" in dotted and "pkg.cli.main" not in dotted
        # Leaving traceability behind is how a resolved phantom comes back as an
        # ownership miss in _filter_phantoms_for_fr.
        assert "pkg.cli" in sab["fr_module_traceability"]["FR-05"]
        assert "pkg.cli.main" not in sab["fr_module_traceability"]["FR-05"]

    def test_drop_removes_without_a_replacement(self, tmp_path):
        from core.quality_gate.sab_amender import resolve_phantom
        proj = self._project(tmp_path)
        resolve_phantom(proj, "pkg.cli.commands", to=None, drop=True,
                        reason="FR-05 no longer needs a separate commands module")
        sab = self._sab(proj)
        cli_layer = [L for L in sab["layers"] if L["name"] == "cli"][0]
        dotted = [m["name"] if isinstance(m, dict) else m for m in cli_layer["modules"]]
        assert "pkg.cli.commands" not in dotted
        assert "pkg.cli.commands" not in sab["fr_module_traceability"]["FR-05"]

    def test_the_reason_lands_in_adr(self, tmp_path):
        from core.quality_gate.sab_amender import resolve_phantom
        proj = self._project(tmp_path)
        reason = "FR-05 is one click group; the declared split has no consumer"
        resolve_phantom(proj, "pkg.cli.main", to="pkg.cli", reason=reason)
        # Round 97: through the layout, not a hand-built path. `adr_path` now
        # resolves to the existing ADR (either layout) and otherwise to where
        # init-project deploys the template — `02-architecture/adr/ADR.md`.
        # Hard-coding the old join here is what let the writer and the reader
        # point at different files on all eleven corpus projects.
        from core.utils.project_layout import ProjectLayout
        adr = ProjectLayout(proj).adr_path.read_text(encoding="utf-8")
        assert "Architecture Amendment" in adr
        assert reason in adr
        assert "pkg.cli.main" in adr and "pkg.cli" in adr

    @pytest.mark.parametrize("kwargs, needle", [
        ({"to": "pkg.cli", "reason": "too short"}, "at least 20 characters"),
        ({"to": None, "reason": "a perfectly adequate justification here"},
         "exactly one of"),
        ({"to": "pkg.cli", "drop": True,
          "reason": "a perfectly adequate justification here"}, "exactly one of"),
        ({"to": "pkg.nowhere", "reason": "a perfectly adequate justification here"},
         "swap one phantom for another"),
    ])
    def test_refusals(self, tmp_path, kwargs, needle):
        from core.quality_gate.sab_amender import (
            PhantomResolutionError,
            resolve_phantom,
        )
        proj = self._project(tmp_path)
        with pytest.raises(PhantomResolutionError, match=needle):
            resolve_phantom(proj, "pkg.cli.main", **kwargs)

    def test_a_module_that_exists_is_not_a_phantom(self, tmp_path):
        """This is not a rename tool for working code."""
        from core.quality_gate.sab_amender import (
            PhantomResolutionError,
            resolve_phantom,
        )
        proj = self._project(tmp_path)
        with pytest.raises(PhantomResolutionError, match="not a phantom"):
            resolve_phantom(proj, "pkg.cli", to="pkg.cli",
                            reason="a perfectly adequate justification here")

    def test_a_refused_amendment_writes_nothing(self, tmp_path):
        from core.quality_gate.sab_amender import (
            PhantomResolutionError,
            resolve_phantom,
        )
        proj = self._project(tmp_path)
        before = (proj / ".methodology" / "SAB.json").read_text(encoding="utf-8")
        with pytest.raises(PhantomResolutionError):
            resolve_phantom(proj, "pkg.cli.main", to="pkg.cli", reason="nope")
        assert (proj / ".methodology" / "SAB.json").read_text(encoding="utf-8") == before
        assert not (proj / "02-architecture" / "ADR.md").exists()

    def test_an_unknown_module_is_refused(self, tmp_path):
        from core.quality_gate.sab_amender import (
            PhantomResolutionError,
            resolve_phantom,
        )
        proj = self._project(tmp_path)
        with pytest.raises(PhantomResolutionError, match="nothing to amend"):
            resolve_phantom(proj, "pkg.never.declared", to="pkg.cli",
                            reason="a perfectly adequate justification here")
