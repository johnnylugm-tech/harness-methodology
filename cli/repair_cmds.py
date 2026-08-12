"""repair-harness — verify a harness fix, then land it (Round 48 站3).

The multi-agent half of the repair (reproduce → root-cause hypothesis →
adversarial review → edit) is `.claude/workflows/harness-repair.js`. This
command is the half that must be the same every time: it re-measures what the
agent claims, applies the policy the round settled on, and either lands the fix
or refuses with the reason.

TWO PHASES, BECAUSE THE TREE CHANGES BETWEEN THEM

  --check-repro   BEFORE any edit. Runs the ticket's reproduction against the
                  current tree and expects it to FAIL. A report that will not
                  reproduce is a claim, and Round 33 measured the price of
                  fixing one: two rounds chased a symptom whose premise was
                  false, and the class survived both.

  --land          AFTER the edit, on a dirty tree. Stashes the fix, runs the
                  reproduction again (it must FAIL — that is the counter-proof),
                  restores the fix, then runs the policy checks and the
                  six-check self-gate. Commits and optionally pushes only if
                  every one of those is green.

The counter-proof is done by the framework, not attested by the agent. Round 27
is the reason: the party being judged must not supply the verdict.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _harness_root(project: Path) -> Path:
    """The harness checkout a repair operates on.

    `<project>/harness` when the framework is mounted as a submodule (the
    shape all six live projects use), otherwise the project itself — which is
    what dogfooding looks like, and which must not need a second code path.
    """
    submodule = project / "harness"
    return submodule if (submodule / "harness_cli.py").is_file() else project


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def _load_ticket(path: Path) -> "dict | None":
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[BLOCKED] repair-harness: ticket {path} is not readable JSON: {exc}\n"
              f"  Fix: re-run the halt through `harness_cli.py record-block`, which "
              f"writes the ticket this command reads.", file=sys.stderr)
        return None
    if not isinstance(data, dict) or not data.get("repro"):
        print(f"[BLOCKED] repair-harness: ticket {path} carries no `repro` command.\n"
              f"  A repair with no way to reproduce the failure cannot verify that "
              f"the failure is real, which is the first thing this command checks.\n"
              f"  Fix: add \"repro\": \"<command that fails today>\" to the ticket.",
              file=sys.stderr)
        return None
    return data


def _print_refusal(reason: str, detail: "list[str] | None" = None) -> None:
    print(f"\n[BLOCKED] repair-harness: {reason}", file=sys.stderr)
    for line in detail or []:
        print(f"    {line}", file=sys.stderr)


def cmd_repair_harness(args: argparse.Namespace) -> int:
    from core.harness_repair import (
        RepairPreconditions, changed_paths, checkout_plan,
        forbidden_edit_violations, generated_file_violations,
        guard_count_violations, push_failure_reason, reproduce, run_self_gate,
    )

    if args.check_repro == args.land:
        # Neither flag would silently land; both is a contradiction. Requiring
        # exactly one keeps "verify" and "commit and push" from ever being the
        # same invocation by accident.
        _print_refusal(
            "give exactly one of --check-repro (before the edit) or --land "
            "(after it)")
        return 1

    project = Path(args.project).resolve()
    root = _harness_root(project)
    ticket = _load_ticket(Path(args.ticket))
    if ticket is None:
        return 1
    repro_cmd = str(ticket["repro"])

    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()

    if args.check_repro:
        # Branch normalisation happens HERE, before anything is edited, because
        # this is the last moment the tree is clean. Doing it after the fix —
        # which is what the first version of this command did, between the
        # self-gate and the commit — makes `git checkout main` swap every file
        # main has moved on while carrying the fix across, so the tree the six
        # checks measured is not the tree that gets committed. Round 44's
        # finding, reproduced inside the repair executor.
        plan = checkout_plan(current_branch=branch, dirty_paths=changed_paths(root))
        if plan.refusal:
            _print_refusal(plan.refusal)
            return 1
        if plan.must_checkout:
            co = _git(root, "checkout", plan.target_branch)
            if co.returncode != 0:
                _print_refusal(
                    f"could not check out {plan.target_branch} — HEAD was "
                    + (f"detached at {branch}" if branch == "HEAD"
                       else f"on branch {branch!r}") +
                    f", and a repair must land on {plan.target_branch}, which "
                    f"is the ref every consuming project tracks",
                    [(co.stderr or "").strip()[-300:]])
                return 1
            print(f"[repair-harness] moved the submodule from {branch} to "
                  f"{plan.target_branch} before any edit")

        reproduced = reproduce(root, repro_cmd)
        if reproduced is None:
            _print_refusal(
                "the reproduction command could not be executed at all — that is "
                "neither 'reproduced' nor 'did not reproduce', and it is not "
                "rounded to either")
            return 1
        print(json.dumps({"reproduced": reproduced, "harness_root": str(root)}))
        if not reproduced:
            _print_refusal(RepairPreconditions(False, False).refusal)
            return 1
        return 0

    # ---- --land -----------------------------------------------------------
    dirty = changed_paths(root)
    if not dirty:
        _print_refusal(
            "nothing to land — the harness tree has no changes. Make the fix "
            "first; this command verifies and commits it, it does not author it")
        return 1

    target = checkout_plan(current_branch=branch, dirty_paths=dirty).target_branch
    if branch != target:
        _print_refusal(
            f"the harness submodule is on {branch!r}, not {target!r}, so the "
            f"tree the self-gate is about to measure is not the tree that "
            f"would be committed",
            [f"HEAD is detached at {branch}." if branch == "HEAD" else
             f"HEAD is on branch {branch}.",
             f"Run `repair-harness --check-repro` first: it moves the "
             f"submodule to {target} while the tree is still clean, which is "
             f"the only point at which moving it is safe.",
             "Nothing was measured, stashed or committed."])
        return 1

    violations: list[str] = []
    violations += generated_file_violations(dirty)
    violations += forbidden_edit_violations(dirty)
    guards_rel = "tests/REGRESSION_GUARDS.yaml"
    if guards_rel in dirty:
        before = _git(root, "show", f"HEAD:{guards_rel}").stdout or ""
        after = (root / guards_rel).read_text(encoding="utf-8")
        violations += guard_count_violations(before_text=before, after_text=after)
    if violations:
        _print_refusal("the change violates a repair policy", violations)
        return 1

    # The counter-proof: put the fix aside and confirm the failure comes back.
    stash = _git(root, "stash", "push", "--include-untracked", "-m",
                 "repair-harness counter-proof")
    if stash.returncode != 0:
        _print_refusal(
            "could not stash the fix to run the counter-proof, so root-causality "
            "is unproven and nothing is committed",
            [(stash.stderr or "").strip()[-300:]])
        return 1
    try:
        without_fix = reproduce(root, repro_cmd)
    finally:
        restore = _git(root, "stash", "pop")
        if restore.returncode != 0:
            print(
                "[FATAL] repair-harness: the fix was stashed for the counter-proof "
                "and `git stash pop` failed. The work is NOT lost — recover it with "
                f"`git -C {root} stash list` and `git stash pop`. Nothing was "
                "committed.", file=sys.stderr)
            return 1

    pre = RepairPreconditions(reproduced=True, counterproof_red=bool(without_fix))
    if not pre.may_commit:
        _print_refusal(pre.refusal)
        return 1

    gate = run_self_gate(root)
    if not gate.green:
        _print_refusal(
            "the harness self-gate is not green, so this fix is not pushed",
            [f"{name}: {'could not run' if gate.results.get(name) is None else 'FAILED'}"
             for name in gate.blocking]
            + ["Nothing was committed. Fix the failing check(s) and re-run --land."])
        return 1

    for path in dirty:
        _git(root, "add", "--", path)
    message = (
        f"fix(harness): {ticket.get('step', 'repair')} — "
        f"{str(ticket.get('message', ''))[:60]}\n\n"
        f"Reproduced before the fix, and the counter-proof went red without it.\n"
        f"Self-gate green on all six checks.\n\n"
        f"Block signature: {ticket.get('signature', 'unknown')}\n"
        f"Phase: {ticket.get('phase')}  Step: {ticket.get('step')}\n"
    )
    commit = _git(root, "commit", "-m", message)
    if commit.returncode != 0:
        _print_refusal("the commit did not land",
                       [(commit.stderr or commit.stdout or "").strip()[-300:]])
        return 1
    sha = (_git(root, "rev-parse", "HEAD").stdout or "").strip()[:12]
    print(f"[repair-harness] committed {sha} on {target}")

    if not args.push:
        print("[repair-harness] --push not given; the fix is committed locally only")
        return 0
    push = _git(root, "push", "origin", target)
    if push.returncode != 0:
        stderr = (push.stderr or "").strip()
        known = push_failure_reason(stderr)
        _print_refusal(
            "the commit landed locally but `git push` failed — it is NOT rolled "
            "back",
            [stderr[-300:]]
            + ([known] if known else
               ["The cause is not one this command recognises; read the git "
                "output above rather than a guess."])
            + [f"When it is resolved: git -C {root} push origin {target}"])
        return 1
    print(f"[repair-harness] pushed {sha} to origin/{target}")

    signature = ticket.get("signature")
    if signature:
        from core.workflow_blocks import UnknownBlockError, resolve_block
        try:
            resolve_block(project, signature, resolution=f"repair-harness pushed {sha}")
            print(f"[repair-harness] block {signature} marked resolved")
        except UnknownBlockError as exc:
            print(f"[WARN] repair-harness: {exc}", file=sys.stderr)
    return 0


def register(sub) -> None:
    rh = sub.add_parser(
        "repair-harness",
        help="Verify and land a fix to harness-methodology itself: reproduce, "
             "counter-prove, run the six-check self-gate, commit, push "
             "(Round 48)",
    )
    rh.add_argument("--project", default=".", help="Project root (default: .)")
    rh.add_argument("--ticket", required=True,
                    help="Path to the repair ticket JSON (carries the block "
                         "signature and the `repro` command)")
    rh.add_argument("--check-repro", action="store_true",
                    help="Phase 1: confirm the reported failure reproduces on "
                         "this tree, BEFORE anything is edited")
    rh.add_argument("--land", action="store_true",
                    help="Phase 2: counter-prove the fix, run the self-gate, "
                         "commit")
    rh.add_argument("--push", action="store_true",
                    help="With --land: push to origin once the self-gate is green")
    rh.set_defaults(func=cmd_repair_harness)
