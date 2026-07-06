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
    phantom_modules,
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