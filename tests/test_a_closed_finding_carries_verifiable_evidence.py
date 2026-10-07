"""Round 115 站2 — a confirmed critical/high finding is closed by evidence the gate can check.

Measured on the corpus. Gate 3's adversarial_review accepted:

  * `resolved` with any non-empty `fix_commit` — the unit test pinned
    "abc123def456" as a passing fix;
  * `resolved` with any `repro_test` path that is a file, `../outside.py`
    included (`root / repro` is not confined; the old traversal test passed
    only because `../../etc/passwd` happened not to exist from a tmp dir);
  * `refuted` with any non-empty sentence. taskq-new's resolver turned a
    finding two independent verifiers had confirmed CRITICAL
    (`api.middleware#1`) and two HIGH ones into `refuted`, one of them
    conceding "only AC-5.3 (cross-worker consistency) is unmet", and Gate 3
    passed. The party that wrote the code overruled its reviewers in prose.

What the resolver is told to do (spec_phase4: RED repro, minimal fix, GREEN,
one `fix(<module>)` commit, record both) is what 28 of 28 resolved blocking
findings in the corpus did: the fix commit exists, is an ancestor of HEAD, and
its own diff touches the finding's file and the repro test. So:

  * resolved (critical/high): fix_commit AND repro_test; the commit is real,
    on HEAD's history, and changes both files; the repro lies under the test
    directory the framework's suite runs (`ProjectLayout.active_test_dir`) —
    otherwise the gate's own run never executes it (taskq-final keeps three
    of its four repros in a root `tests/` the suite does not run);
  * refuted (critical/high): an adjudication two independent verifiers gave,
    recorded by `adjudicate-bug-hunt` against the refutation as it now reads.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from core.quality_gate.bug_hunt_verifier import REPORT_RELPATH, verify_bug_hunt_report

REPO = Path(__file__).resolve().parents[1]
SRC = "03-development/src/pkg/auth.py"
REPRO = "03-development/tests/test_bug_hunt_auth.py"


def _git(proj: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(proj), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "03-development" / "src" / "pkg").mkdir(parents=True)
    (proj / "03-development" / "tests").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / SRC).write_text("def check(key):\n    return True\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    return proj


def _fix(proj: Path, *, with_repro: bool = True, touch_src: bool = True) -> str:
    if touch_src:
        (proj / SRC).write_text("def check(key):\n    return key == 'k'\n", encoding="utf-8")
    if with_repro:
        (proj / REPRO).write_text("def test_rejects_wrong_key():\n    assert True\n", encoding="utf-8")
    (proj / "notes.txt").write_text("x\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "fix(auth): reject wrong key")
    return _git(proj, "rev-parse", "HEAD")


def _report(proj: Path, resolution: dict, severity: str = "critical") -> None:
    finding = {
        "id": "auth#1", "module": "auth", "lens": "correctness", "severity": severity,
        "title": "accepts any key", "file": SRC, "line_start": 2,
        "reasoning": f"{SRC}:2 returns True", "confidence": "high",
        "confirmed": True, "resolution": resolution,
    }
    doc = {"generated_at": "t", "git_sha": "x", "lenses": ["correctness"], "findings": [finding]}
    path = proj / REPORT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _reasons(proj: Path) -> list[str]:
    return verify_bug_hunt_report(str(proj)).reasons


# ── resolved ────────────────────────────────────────────────────────────────

def test_a_fix_commit_that_changed_the_file_and_its_repro_closes_the_finding(tmp_path):
    proj = _project(tmp_path)
    sha = _fix(proj)
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": REPRO})
    assert _reasons(proj) == []


def test_a_sha_that_is_not_a_commit_does_not_close_it(tmp_path):
    proj = _project(tmp_path)
    _fix(proj)
    _report(proj, {"status": "resolved", "fix_commit": "abc123def456", "repro_test": REPRO})
    assert any("not a commit" in r for r in _reasons(proj))


def test_a_commit_off_heads_history_does_not_close_it(tmp_path):
    proj = _project(tmp_path)
    _git(proj, "checkout", "-q", "-b", "side")
    sha = _fix(proj)
    _git(proj, "checkout", "-q", "-")
    (proj / REPRO).parent.mkdir(parents=True, exist_ok=True)
    (proj / REPRO).write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": REPRO})
    assert any("not on HEAD's history" in r for r in _reasons(proj))


def test_a_commit_that_did_not_touch_the_finding_does_not_close_it(tmp_path):
    proj = _project(tmp_path)
    sha = _fix(proj, touch_src=False)
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": REPRO})
    assert any(SRC in r and "does not change" in r for r in _reasons(proj))


def test_a_repro_the_fix_commit_did_not_write_does_not_close_it(tmp_path):
    proj = _project(tmp_path)
    (proj / REPRO).write_text("def test_old():\n    assert True\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "an older test")
    sha = _fix(proj, with_repro=False)
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": REPRO})
    assert any(REPRO in r and "does not change" in r for r in _reasons(proj))


def test_a_blocking_finding_needs_both_the_fix_and_the_repro(tmp_path):
    proj = _project(tmp_path)
    sha = _fix(proj)
    _report(proj, {"status": "resolved", "fix_commit": sha})
    assert any("fix_commit and repro_test" in r for r in _reasons(proj))
    _report(proj, {"status": "resolved", "repro_test": REPRO})
    assert any("fix_commit and repro_test" in r for r in _reasons(proj))


def test_a_repro_outside_the_project_is_refused(tmp_path):
    proj = _project(tmp_path)
    sha = _fix(proj)
    (tmp_path / "outside.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": "../outside.py"})
    assert any("not under" in r for r in _reasons(proj))


def test_a_repro_symlinked_out_of_the_test_directory_is_refused(tmp_path):
    proj = _project(tmp_path)
    (tmp_path / "elsewhere.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    link = proj / "03-development" / "tests" / "test_link.py"
    link.symlink_to(tmp_path / "elsewhere.py")
    sha = _fix(proj)
    _report(proj, {"status": "resolved", "fix_commit": sha,
                   "repro_test": "03-development/tests/test_link.py"})
    assert any("not under" in r for r in _reasons(proj))


def test_a_repro_the_frameworks_suite_does_not_run_is_refused(tmp_path):
    """taskq-final: `tests/` at the root while 03-development/tests exists."""
    proj = _project(tmp_path)
    (proj / "tests").mkdir()
    (proj / SRC).write_text("def check(key):\n    return key == 'k'\n", encoding="utf-8")
    (proj / "tests" / "test_repro.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "fix(auth): x")
    sha = _git(proj, "rev-parse", "HEAD")
    _report(proj, {"status": "resolved", "fix_commit": sha, "repro_test": "tests/test_repro.py"})
    assert any("not under 03-development/tests" in r for r in _reasons(proj))


def test_a_non_blocking_finding_keeps_the_either_or_rule_but_not_a_fake_sha(tmp_path):
    proj = _project(tmp_path)
    sha = _fix(proj)
    _report(proj, {"status": "resolved", "fix_commit": sha}, severity="medium")
    assert _reasons(proj) == []
    _report(proj, {"status": "resolved", "fix_commit": "abc123"}, severity="medium")
    assert any("not a commit" in r for r in _reasons(proj))


# ── refuted ─────────────────────────────────────────────────────────────────

_REFUTATION = "guarded at 03-development/src/pkg/auth.py:1 by the caller"


def _adjudication(text: str, verdict: str = "upheld") -> dict:
    return {"verdict": verdict,
            "evidence": ["caller.py:10 checks the key first", "auth.py:1 is unreachable otherwise"],
            "refutation_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def test_a_resolvers_refutation_of_a_confirmed_finding_needs_an_adjudication(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION})
    assert any("adjudicat" in r for r in _reasons(proj))


def test_an_upheld_adjudication_of_the_refutation_as_written_closes_it(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION,
                   "adjudication": _adjudication(_REFUTATION)})
    assert _reasons(proj) == []


def test_a_rejected_adjudication_does_not(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION,
                   "adjudication": _adjudication(_REFUTATION, "rejected")})
    assert any("rejected" in r for r in _reasons(proj))


def test_a_refutation_rewritten_after_its_adjudication_is_not_covered_by_it(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION + " and more",
                   "adjudication": _adjudication(_REFUTATION)})
    assert any("changed after" in r for r in _reasons(proj))


def test_an_adjudication_without_cited_lines_does_not_count(tmp_path):
    proj = _project(tmp_path)
    adj = _adjudication(_REFUTATION)
    adj["evidence"] = ["looks fine", "agreed"]
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION, "adjudication": adj})
    assert any("citing a line" in r for r in _reasons(proj))


# ── the CLI the workflow drives ─────────────────────────────────────────────

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "harness_cli.py"), *args],
                          capture_output=True, text=True, cwd=str(REPO))


def test_pending_lists_open_and_unadjudicated_refutations(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION})
    out = _cli("bug-hunt-pending", "--project", str(proj), "--json")
    assert out.returncode == 1, out.stdout + out.stderr
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc["test_dir"] == "03-development/tests"
    assert [(p["id"], p["status"]) for p in doc["pending"]] == [("auth#1", "refuted")]
    assert doc["pending"][0]["refute_evidence"] == _REFUTATION


def test_adjudicate_binds_the_refutation_as_it_now_reads(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "refuted", "refute_evidence": _REFUTATION})
    src = proj / ".sessi-work" / "bug_hunt" / "adj-1.json"
    src.parent.mkdir(parents=True)
    src.write_text(json.dumps({"id": "auth#1", "verdict": "upheld",
                               "evidence": ["caller.py:10 checks", "auth.py:1 unreachable"]}))
    out = _cli("adjudicate-bug-hunt", "--project", str(proj), "--from", str(src))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ADJUDICATED id=auth#1 verdict=upheld" in out.stdout
    assert _reasons(proj) == []
    after = _cli("bug-hunt-pending", "--project", str(proj), "--json")
    assert after.returncode == 0
    assert json.loads(after.stdout.strip().splitlines()[-1])["pending"] == []


def test_adjudicate_refuses_a_finding_that_is_not_a_refuted_confirmed_one(tmp_path):
    proj = _project(tmp_path)
    _report(proj, {"status": "open"})
    src = proj / ".sessi-work" / "bug_hunt" / "adj-1.json"
    src.parent.mkdir(parents=True)
    src.write_text(json.dumps({"id": "auth#1", "verdict": "upheld", "evidence": ["a:1", "b:2"]}))
    out = _cli("adjudicate-bug-hunt", "--project", str(proj), "--from", str(src))
    assert out.returncode != 0
