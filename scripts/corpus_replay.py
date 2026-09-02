#!/usr/bin/env python3
"""Score the delivered trees this framework already judged, with today's judge.

Round 88 站2. Every gate verdict records WHO produced it —
`core/harness_provenance.py::enforcer_surface`, since Round 19 站3 — and every
project records the commit its phase exit was taken on, in
`state.json::phase_completed[N].sha`. Nothing ever put the two together and
asked what a later enforcer says about an earlier tree.

WHAT THAT QUESTION ANSWERS, MEASURED ON THE NINE TREES THAT HAVE ONE

    project        accepted as        today says      delta
    taskq-super    87 declared/15     123/51          +36/+36
    taskq-new      81/17              116/52          +35/+35
    taskq-api      86/0               113/0           +27/0
    taskq-advance  89/23              97/31           +8/+8
    taskq-cc       118/47             124/47          +6/0
    taskq-cc-new   104/40             104/15          0/-25   (a loosening)
    taskq-redo     130/0              130/0           0/0

Seven of nine trees are judged differently today than when they were accepted,
two by about thirty-six declarations, and one moved the other way with nobody
reviewing the loosening. The eighth line is the one that matters: taskq-redo,
the project whose delivered quality Round 87 found had degraded, is the only
one that CANNOT move — 130 correctly-named stubs satisfy any parser, so a
project that answered every criterion with a stub is immune to every
improvement in the thing that reads them, while the projects that left
criteria honestly undelivered absorb all of it.

WHAT THIS DOES NOT COVER, ON PURPOSE

Only checks that are pure functions of the tree. `check_ac_deferral_targets`
is excluded although it is the most interesting candidate: it calls
`run_suite`, and a suite measured on an archived tree measures the MACHINE
(no `.venv`, so pytest exits 2), not the framework version. A baseline that
moves when someone installs a package is noise. Coverage and mutation are out
for the same reason.

The consequence is stated rather than hidden: a change to the outcome-aware
half of delivery — Round 87 站1's own subject — does not move this baseline.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

__all__ = ["CORPUS_ROOT", "corpus_projects", "corpus_vector", "frozen_tree_sha",
           "replay", "diff_against"]

HARNESS_ROOT = Path(__file__).resolve().parents[1]

#: Where the delivered projects live: this framework's sibling directories.
#: Derived, not written down — `tests/test_no_hardcoded_paths.py` is right that
#: a framework constant must not name a consuming project, and a replay that
#: only works on one machine's directory names is a replay nobody else can run.
#: Overridable with `--corpus`. A machine without a corpus measures nothing and
#: says so; CI is such a machine, like it is for the seven corpus tests that
#: already skip there.
CORPUS_ROOT = HARNESS_ROOT.parent

#: The phase whose exit verdict pins the tree. P3 is where the delivered source
#: first exists and where Round 87's degradation was paid for.
REPLAY_PHASE = "3"

BASELINE_PATH = HARNESS_ROOT / "tests" / "corpus_verdict_baseline.json"


def corpus_projects(corpus_root: Path = CORPUS_ROOT) -> list[str]:
    """Every harness-managed project beside this one, by directory name.

    Discovered rather than listed. A hand-written list would name consuming
    projects inside framework code (which `test_no_hardcoded_paths` forbids,
    and rightly — it is the same leak shape as a prompt naming somebody's
    repo), and it would silently stop watching a project that was renamed.

    A project with no `phase_completed["3"]` still appears here and is
    recorded as unmeasured downstream: Round 46's rule, a witness that cannot
    attend has to say so rather than vanish from the list.
    """
    if not corpus_root.is_dir():
        return []
    return sorted(
        d.name for d in corpus_root.iterdir()
        if d.is_dir() and d.resolve() != HARNESS_ROOT
        and (d / ".methodology" / "state.json").is_file()
    )


def frozen_tree_sha(project: Path, phase: str = REPLAY_PHASE) -> "str | None":
    """The commit this project's phase-exit verdict was taken on, or None."""
    from core.state_io import load_state

    if not (project / ".methodology" / "state.json").is_file():
        return None
    entry = (load_state(project, lenient=True).get("phase_completed") or {}).get(phase) or {}
    sha = entry.get("sha")
    return sha if isinstance(sha, str) and len(sha) == 40 else None


def _fr_ids(tree: Path) -> list[str]:
    import re
    spec = tree / "SPEC.md"
    if not spec.is_file():
        return []
    return sorted(set(re.findall(
        r"^### (FR-\d+)", spec.read_text(encoding="utf-8", errors="replace"), re.M)))


def _metrics() -> "dict[str, object]":
    """`{name: callable(tree) -> int}`, each importing what it needs itself.

    Per-metric and lazily imported on purpose. The first version imported all
    five at the top of `corpus_vector`, so replaying an ENFORCER FROM BEFORE A
    METRIC EXISTED raised ModuleNotFoundError and produced no vector at all —
    measured against nine historical commits, of which eight predate
    `criteria_review`. That shape is Round 46's: a witness that cannot attend
    reports nothing rather than reporting its absence. The same applies
    forward: a metric this framework later removes must show up as "no longer
    measurable", not as a crash and not as zero.
    """
    def spec_declared(tree: Path) -> int:
        from core.quality_gate.spec_coverage import spec_coverage_report
        return spec_coverage_report(tree)["declared"]

    def spec_undelivered(tree: Path) -> int:
        from core.quality_gate.spec_coverage import spec_coverage_report
        return len(spec_coverage_report(tree)["missing"])

    def spec_unread_rows(tree: Path) -> int:
        from core.quality_gate.spec_coverage import spec_coverage_report
        return len(spec_coverage_report(tree)["unread"])

    def runtime_test_seams_(tree: Path) -> int:
        from core.quality_gate.test_seam_in_production import runtime_test_seams
        return len(runtime_test_seams(tree))

    def spec_alignment_violations(tree: Path) -> int:
        from core.quality_gate.spec_alignment import check_spec_alignment
        return len(check_spec_alignment(tree))

    def frs_total(tree: Path) -> int:
        return len(_fr_ids(tree))

    def frs_without_tests(tree: Path) -> int:
        from core.quality_gate.criteria_review import review_sources
        return sum(1 for fr in _fr_ids(tree) if not review_sources(tree, fr)["test_files"])

    def frs_without_requirement_text(tree: Path) -> int:
        from core.quality_gate.parsers import extract_fr_section
        spec = tree / "SPEC.md"
        if not spec.is_file():
            return 0
        return sum(1 for fr in _fr_ids(tree) if not extract_fr_section(spec, fr))

    return {
        "spec_declared": spec_declared,
        "spec_undelivered": spec_undelivered,
        "spec_unread_rows": spec_unread_rows,
        "runtime_test_seams": runtime_test_seams_,
        "spec_alignment_violations": spec_alignment_violations,
        "frs_total": frs_total,
        "frs_without_tests": frs_without_tests,
        "frs_without_requirement_text": frs_without_requirement_text,
    }


def corpus_vector(tree: Path) -> dict:
    """What today's enforcer says about one delivered tree.

    Every entry is a pure function of the bytes under *tree*: no subprocess,
    no suite run, no network. Verified deterministic across three runs from
    three different working directories.

    A metric this enforcer cannot compute — because the module implementing it
    does not exist in this version, or it raised — is recorded as `None`, and
    `diff_against` reports the transition to and from `None` as a change in
    what this gate covers. Never zero: Rounds 32/35.
    """
    vector: dict = {}
    for name, fn in _metrics().items():
        try:
            vector[name] = fn(tree)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 — see docstring
            print(f"[corpus-replay] {tree.name}: metric `{name}` not measurable "
                  f"by this enforcer ({type(exc).__name__}: {str(exc)[:80]})",
                  file=sys.stderr)
            vector[name] = None
            vector.setdefault("_unmeasured_metrics", {})[name] = (
                f"{type(exc).__name__}: {str(exc)[:120]}")
    return vector


def _archive(project: Path, sha: str, into: Path) -> "str | None":
    """Extract *sha*'s tree into *into*. Returns an error string, or None.

    `git archive` writes nothing to the source repository — no index, no
    worktree, no reflog entry — which is what keeps the corpus read-only.
    """
    proc = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(project), "archive", sha], capture_output=True, check=False)
    if proc.returncode != 0:
        return (f"could-not-measure: git archive could not reach {sha[:12]} "
                f"(exit {proc.returncode}) — the commit may have been gc'd, or "
                f"this is a shallow clone")
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(into)  # nosec B202 — our own archive of our own commit
    except (tarfile.TarError, OSError) as exc:
        return f"could-not-measure: archive of {sha[:12]} did not extract ({exc})"
    return None


def replay(corpus_root: Path = CORPUS_ROOT) -> dict:
    """`{project: vector | {"unmeasured": reason}}` for every corpus project.

    An unreachable tree is recorded as unmeasured, never as zero — Rounds
    32/35: a measurement that could not be taken is not a finding, and a
    baseline that reads it as one would ratchet a project down for a
    repository state that has nothing to do with the framework.
    """
    out: dict = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td).resolve()
        for name in corpus_projects(corpus_root):
            project = corpus_root / name
            if not (project / ".methodology").is_dir():
                out[name] = {"unmeasured": "no .methodology/ — project not present"}
                continue
            sha = frozen_tree_sha(project)
            if sha is None:
                out[name] = {"unmeasured":
                             f"no 40-char sha in phase_completed[{REPLAY_PHASE}]"}
                continue
            tree = tmp / name
            tree.mkdir(parents=True, exist_ok=True)
            err = _archive(project, sha, tree)
            if err:
                out[name] = {"unmeasured": err, "sha": sha[:12]}
                continue
            vector = corpus_vector(tree)
            vector["sha"] = sha[:12]
            out[name] = vector
    return out


def diff_against(baseline: dict, current: dict) -> list[str]:
    """Human-readable lines for every verdict that moved. Empty when none did.

    An entry that goes from measured to unmeasured (or back) is reported too:
    losing the ability to measure a tree is a change in what this gate covers,
    and silently dropping it would be the shape Round 46 named — a witness
    that skips itself.
    """
    lines: list[str] = []
    for name in sorted(set(baseline) | set(current)):
        was, now = baseline.get(name), current.get(name)
        if was is None:
            lines.append(f"{name}: not in the baseline — add it with its reason")
            continue
        if now is None:
            lines.append(f"{name}: in the baseline but not replayed — "
                         f"remove it with its reason, or restore the project")
            continue
        if ("unmeasured" in was) != ("unmeasured" in now):
            lines.append(f"{name}: {'became' if 'unmeasured' in now else 'stopped being'} "
                         f"unmeasurable ({now.get('unmeasured') or was.get('unmeasured')})")
            continue
        if "unmeasured" in now:
            continue
        for key in sorted(set(was) | set(now)):
            if key in ("sha", "_unmeasured_metrics"):
                continue
            old_v, new_v = was.get(key), now.get(key)
            if old_v == new_v:
                continue
            if new_v is None:
                why = (now.get("_unmeasured_metrics") or {}).get(key, "")
                lines.append(f"{name}.{key}: {old_v} -> NO LONGER MEASURABLE"
                             + (f" ({why})" if why else ""))
            elif old_v is None:
                lines.append(f"{name}.{key}: was not measurable -> {new_v}")
            else:
                lines.append(f"{name}.{key}: {old_v} -> {new_v}")
    return lines


#: The four things a moved verdict has to say. Round 88 站3.
#:
#: R73/R74/R83 each moved this same measurement and each could have written
#: the first two — "the parser was dropping rows, and now it is not" is true
#: and is the point. Nobody was asked for the last two, and the first project
#: below the new line answered it with 29 correctly-named stubs. `cheapest_
#: satisfaction` is the question that would have surfaced them before they
#: shipped; `discriminating_signal` is what the framework would need in order
#: to tell that answer from the honest one, and writing "none" there is a
#: legitimate answer that puts the gap on the record instead of in a project.
NOTE_PARTS: tuple[str, ...] = (
    "moved", "why_right", "cheapest_satisfaction", "discriminating_signal",
)

#: Long enough that "ok" and "n/a" do not pass, short enough not to reward
#: padding. The same anti-rubber-stamp minimum `agent_b_approvals` uses.
MIN_NOTE_CHARS = 40


def note_defects(previous: dict, current: dict) -> list[str]:
    """Why *current*'s notes do not account for what moved since *previous*.

    Pure, so the guard for it does not need a corpus or a git history: the
    caller supplies both baselines. `previous` empty means the baseline is
    being created rather than moved, and creation needs no note — there is no
    earlier verdict being overruled.
    """
    defects: list[str] = []
    for name in sorted(current):
        entry = current[name]
        if not isinstance(entry, dict):
            continue
        note = entry.get("_note")
        was = previous.get(name)
        changed = [
            key for key in sorted(set(entry) | set(was or {}))
            if key not in ("sha", "_note", "_unmeasured_metrics")
            and (was or {}).get(key) != entry.get(key)
        ] if isinstance(was, dict) else []

        if changed and not isinstance(note, dict):
            defects.append(
                f"{name}: {', '.join(changed)} moved with no `_note` — a tree "
                f"that was already accepted cannot be re-judged silently")
            continue
        if not isinstance(note, dict):
            continue
        missing = [p for p in NOTE_PARTS
                   if len(str(note.get(p, "")).strip()) < MIN_NOTE_CHARS]
        if missing:
            defects.append(
                f"{name}: `_note` is missing or too short on {missing} — all "
                f"four parts are required, and \"there is no such signal\" is a "
                f"valid `discriminating_signal` as long as it says so")
        for key in changed:
            if key not in str(note.get("moved", "")):
                defects.append(
                    f"{name}: `_note.moved` does not name `{key}`, which moved "
                    f"{(was or {}).get(key)} -> {entry.get(key)}")
    return defects


def _cli() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--corpus", default=str(CORPUS_ROOT),
                    help=f"corpus root (default: {CORPUS_ROOT})")
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline — only alongside the note that "
                         "explains every moved number in the same commit")
    ap.add_argument("--json", action="store_true", help="print the vector as JSON")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    # "Is there a corpus here", not "does this directory exist". On a CI runner
    # the parent directory of a checkout DOES exist and holds no delivered
    # project, so the first version's `not corpus.is_dir()` never fired and the
    # replay reported all fifteen baseline entries as vanished — this gate's
    # own first push turned CI red for exactly that. The gate is local by
    # construction; the condition has to name what makes it local.
    if not corpus_projects(corpus):
        print(f"[corpus-replay] SKIP: no harness-managed project beside "
              f"{corpus} — this gate is local (a CI runner has no delivered "
              f"tree to replay, like the corpus tests that already skip there)")
        return 0

    current = replay(corpus)
    if args.json:
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0

    measured = [n for n, v in current.items() if "unmeasured" not in v]
    print(f"[corpus-replay] {len(measured)} tree(s) replayed, "
          f"{len(current) - len(measured)} unmeasurable")

    if args.update:
        # Carry every existing `_note` across. Dropping them would let one
        # `--update` erase the record of why a previous verdict moved, which is
        # the whole content this file exists to hold.
        if BASELINE_PATH.is_file():
            old = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
            for name, entry in current.items():
                note = (old.get(name) or {}).get("_note") if isinstance(old.get(name), dict) else None
                if note is not None and isinstance(entry, dict):
                    entry["_note"] = note
        BASELINE_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[corpus-replay] baseline rewritten -> {BASELINE_PATH.name}")
        return 0

    if not BASELINE_PATH.is_file():
        print(f"[corpus-replay] BLOCKED: {BASELINE_PATH.name} is missing.\n"
              f"  Fix: python3 scripts/corpus_replay.py --update, and write the "
              f"note that explains it in the same commit.")
        return 1

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    moved = diff_against(baseline, current)
    if not moved:
        print("[corpus-replay] every delivered tree is judged as the baseline records ✓")
        return 0

    print(f"\n[BLOCKED] this change moves the verdict on {len(moved)} recorded "
          f"measurement(s) of already-delivered work:")
    for line in moved:
        print(f"    • {line}")
    print(
        "\n  A tree that was accepted cannot be re-judged silently. Update the "
        "baseline in THIS commit and, in `tests/corpus_verdict_baseline.json`'s "
        "note for each moved project, state all four:\n"
        "    1. the numbers, before -> after\n"
        "    2. why the direction is right\n"
        "    3. the CHEAPEST way a project can satisfy the new verdict\n"
        "    4. the signal that tells that apart from the honest way, with its "
        "corpus measurement — or that there is none\n"
        "  Points 3 and 4 are the ones Round 87 found missing: R73/R74/R83 each "
        "answered 1 and 2, and the first project below the line answered the new "
        "bar with 29 correctly-named stubs.\n"
        "  Fix: python3 scripts/corpus_replay.py --update, then write the note."
    )
    return 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(_cli())
