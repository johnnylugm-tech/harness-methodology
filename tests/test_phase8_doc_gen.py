"""Tests for scripts/phase8_doc_gen.py deterministic doc generator."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "phase8_doc_gen.py"


def _run(project: Path, output_dir: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--project", str(project)]
    if output_dir:
        cmd.extend(["--output-dir", str(output_dir)])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


class TestPhase8DocGen:
    def _seed(self, tmp_path: Path, *, with_state=True, with_manifest=True):
        """Create minimal .methodology/ + a git repo (for git describe)."""
        (tmp_path / ".methodology").mkdir()
        if with_state:
            (tmp_path / ".methodology" / "state.json").write_text(json.dumps({
                "current_phase": 8,
                "phase_truth_passed": True,
                "project": "taskq",
            }))
        if with_manifest:
            (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps({
                "quality_targets": {"min_coverage": 80},
                "gate_results": {"gate1": {
                    "FR-01": {"score": 96.8, "passed": True},
                    "FR-02": {"score": 95.6, "passed": True},
                }},
            }))
        # git init so git describe / rev-parse work
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_path, check=True)
        return tmp_path

    def test_generates_both_files(self, tmp_path):
        self._seed(tmp_path)
        r = _run(tmp_path)
        assert r.returncode == 0, r.stderr
        config = tmp_path / "08-config" / "CONFIG_RECORDS.md"
        release = tmp_path / "08-config" / "RELEASE_CHECKLIST.md"
        assert config.exists()
        assert release.exists()

    def test_config_records_uses_template_placeholders(self, tmp_path):
        self._seed(tmp_path)
        _run(tmp_path)
        text = (tmp_path / "08-config" / "CONFIG_RECORDS.md").read_text()
        # Template uses {project_name}, {version}, {release_date}
        # These should be filled (no literal {placeholders} remain) or
        # safely left as ${key} — Template safe_substitute keeps them.
        assert "{project_name}" not in text  # converted to ${...} then substituted
        assert "${project_name}" not in text

    def test_template_double_braces_pass_through_literally(self, tmp_path):
        """`{{var}}` (markdown-style literal braces around a placeholder)
        must pass through unchanged. The pre-fix regex matched the inner
        `{var}` of `{{var}}`, producing `{${var}}` and rendering as
        ``{value}`` — silently corrupting templates that used double
        braces for visual formatting.

        Tested by importing ``_render_template`` directly with a synthetic
        template (the script loads templates from the repo-relative
        ``templates/`` dir, so we cannot inject via the file path here)."""
        from scripts.phase8_doc_gen import _render_template

        synthetic = tmp_path / "tpl.md"
        synthetic.write_text(
            "a {project_name} b {{config}} c {d} d {{e}} f",
            encoding="utf-8",
        )
        out = _render_template(
            synthetic, {"project_name": "X", "d": "Y", "config": "ignored", "e": "ignored"}
        )
        # Regular single-brace placeholders are substituted.
        assert "X" in out and "Y" in out
        # Double-brace placeholders pass through literally.
        assert "{{config}}" in out, (
            f"double-brace {{config}} mangled; output:\n{out!r}"
        )
        assert "{{e}}" in out, (
            f"double-brace {{e}} mangled; output:\n{out!r}"
        )
        # And we did NOT produce a stray {value} artifact.
        assert "{X}" not in out and "{Y}" not in out

    def test_missing_methodology_returns_error(self, tmp_path):
        # No .methodology dir → must exit non-zero, not crash.
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        r = _run(tmp_path)
        assert r.returncode != 0
        assert ".methodology/" in r.stderr or "not found" in r.stderr.lower()

    def test_output_dir_override(self, tmp_path):
        self._seed(tmp_path)
        custom = tmp_path / "alt-out"
        r = _run(tmp_path, custom)
        assert r.returncode == 0
        assert (custom / "CONFIG_RECORDS.md").exists()
        assert (custom / "RELEASE_CHECKLIST.md").exists()

    def test_is_deterministic_across_runs(self, tmp_path):
        """Two consecutive runs with same input must produce byte-equal
        output (timestamps excepted). Verify the body content matches."""
        self._seed(tmp_path)
        _run(tmp_path, output_dir=tmp_path / "run1")
        first_config = (tmp_path / "run1" / "CONFIG_RECORDS.md").read_text()
        _run(tmp_path, output_dir=tmp_path / "run2")
        second_config = (tmp_path / "run2" / "CONFIG_RECORDS.md").read_text()
        # Strip release_date line (the only thing that varies per second).
        def _strip_date(t):
            return "\n".join(
                line for line in t.splitlines()
                if "Release Date" not in line
            )
        assert _strip_date(first_config) == _strip_date(second_config)