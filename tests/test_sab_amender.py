"""Tests for core.quality_gate.sab_amender."""
import json
import pytest

from core.quality_gate.sab_amender import (
    amend_sab,
    discover_modules,
    missing_modules,
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
        assert "03-development/src/taskq/cli.py" in result
        assert "03-development/src/taskq/executor.py" in result

    def test_skips_pycache(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("")
        pycache = src / "__pycache__"
        pycache.mkdir()
        (pycache / "cli.cpython-311.pyc").write_text("")
        # The .pyc is not picked up by rglob("*.py") so we additionally
        # verify the cache directory is excluded by name.
        result = discover_modules(tmp_path)
        assert "03-development/src/taskq/cli.py" in result
        assert all("__pycache__" not in p for p in result)

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
        sab = {
            "layers": [
                {"name": "cli", "modules": ["src/taskq/cli.py"]},
                {"name": "core", "modules": ["src/taskq/executor.py"]},
            ]
        }
        discovered = [
            "src/taskq/cli.py",
            "src/taskq/executor.py",
            "src/taskq/cache.py",
            "src/taskq/breaker.py",
        ]
        assert missing_modules(sab, discovered) == [
            "src/taskq/cache.py",
            "src/taskq/breaker.py",
        ]

    def test_returns_empty_when_all_registered(self):
        sab = {"layers": [{"name": "core", "modules": ["src/x.py"]}]}
        assert missing_modules(sab, ["src/x.py"]) == []

    def test_handles_layers_without_modules_key(self):
        sab = {"layers": [{"name": "empty"}]}
        assert missing_modules(sab, ["src/x.py"]) == ["src/x.py"]


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
        # Create both pre-existing + new files on disk
        for m in sab_modules + ["03-development/src/taskq/cache.py",
                                "03-development/src/taskq/breaker.py"]:
            (tmp_path / m).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / m).write_text("")
        return sab

    def test_amend_adds_new_modules_idempotent(self, tmp_path):
        self._setup_project(
            tmp_path,
            ["03-development/src/taskq/cli.py",
             "03-development/src/taskq/executor.py"],
        )
        added1 = amend_sab(tmp_path)
        assert sorted(added1) == sorted([
            "03-development/src/taskq/cache.py",
            "03-development/src/taskq/breaker.py",
        ])
        # Verify on disk
        sab = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        all_modules = [m for layer in sab["layers"] for m in layer["modules"]]
        assert "03-development/src/taskq/cache.py" in all_modules
        assert "03-development/src/taskq/breaker.py" in all_modules

        # Second call: idempotent, nothing added.
        added2 = amend_sab(tmp_path)
        assert added2 == []

    def test_amend_dry_run_does_not_write(self, tmp_path):
        self._setup_project(
            tmp_path,
            ["03-development/src/taskq/cli.py"],
        )
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
        self._setup_project(
            tmp_path,
            ["03-development/src/taskq/cli.py",
             "03-development/src/taskq/executor.py"],
        )
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