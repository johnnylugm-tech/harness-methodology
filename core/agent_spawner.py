"""
Agent Spawner: Orchestrates agent invocations for Developer and Reviewer roles.

Handles routing between Claude Code headless CLI (claude -p) and Hermes MCP for heterogeneous
reviewing, adhering to the 'Need-to-know' principle for prompt construction.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Optional

from pathlib import Path


# Env markers that tell a nested `claude -p` to fetch OAuth tokens from the
# parent Agent-SDK stream. A headless child has no such stream, so auth fails
# with "SDK getOAuthToken callback failed: Stream closed" → API 401. Always
# strip them; the child must authenticate on its own (keychain OAuth/API key).
_SDK_STREAM_MARKERS = (
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
)


# Substrings in a failed `claude -p` output that signal an environment / API /
# model / network problem (the model could not be reached or used — the agent
# never really ran) rather than a genuine agent-logic error.
#
# Round 19 站1: "stream idle timeout" and "session limit" were added from real
# corpus, not imagination — taskq's P3 run produced 12 `API Error: Stream idle
# timeout - no chunks received` and 1 `You've hit your session limit`, and every
# one classified EXECUTION_ERROR ("the agent's own logic failed", which routes
# into CODE-FIX). "stream closed" and "rate limit" were already here; the two
# sibling phrasings the CLI actually emits were not. tests/fixtures/
# failure_corpus/ now holds that corpus so the next unseen phrasing fails a
# test instead of being silently mislabelled.
_INFRA_ERROR_RE = re.compile(
    r"connection (?:closed|error|reset|refused|aborted)"
    r"|could not connect|connection to .{0,60}closed"
    r"|econnreset|etimedout|enotfound|econnrefused"
    r"|network (?:error|unreachable)"
    r"|\b(?:401|403|404|429|5\d{2})\b"
    r"|unauthorized|authentication|invalid x-api-key|api[\s_-]?key|oauth"
    r"|stream closed|stream idle timeout|getoauthtoken|permission denied"
    r"|rate[\s_-]?limit|session limit|overloaded|quota|credit balance"
    r"|insufficient (?:credit|quota|balance)"
    r"|connector"
    r"|model\b.{0,30}(?:not found|does not exist|unavailable)",
    re.IGNORECASE,
)


# Deterministic environment breakage: the failure output reproduces
# identically on every retry, so no dispatch loop may retry it (P3 2026-07-12
# FR-04 GATE1: a 5.4h silent stall before c1bacf4 taught the fix-round loop
# this one signature). The detection registry lives HERE and only here —
# cli/fr_cmds.py delegates to is_structurally_broken(); a second copy of a
# signature string is the next unswept-sibling incident.
#
# Round 12 站0c (2026-07-16): "claude.ai connectors are disabled" REMOVED.
# Production evidence killed the fatal-env theory twice over: the current
# P3 run's sessions_spawn.log has 76/461 entries whose error_output is
# ONLY this warning (it is the CLI's startup banner whenever an
# ANTHROPIC_API_KEY-style env var is set — printed on successful spawns
# too), and Fix H-G's own data (4/5 same-FR next-dispatches succeeded)
# already showed retries DO succeed with the warning present. The banner
# is now stripped by _extract_dispatch_error() before classification, so
# it can never masquerade as the failure cause again. The registry stays
# (mechanism + bounded-retry loop are signature-driven) — it is simply
# empty until a genuinely deterministic breakage signature is proven.
_STRUCTURAL_FAILURE_SIGNATURES: tuple[str, ...] = ()

# Fix H-G (P3 2026-07-15 round 4): production sessions_spawn.log evidence
# (362 dispatches, 7 STRUCTURAL occurrences) shows the "every retry fails
# identically" premise above is only true ~20% of the time — 4/5 traceable
# same-FR next-dispatches succeeded within the same run/env. The signature
# detection itself stays correct; only the zero-retry reaction was wrong.
# Bounded (not unbounded — that was the 2026-07-12 5.4h stall bug) retry
# absorbs the transient case while still aborting via
# _abort_dispatch_structurally_broken() if the signature survives every
# attempt (identical behavior to today when the env is genuinely dead).
_STRUCTURAL_RETRY_ATTEMPTS = 3  # total attempts, i.e. up to 2 retries
_STRUCTURAL_RETRY_BACKOFF_SECONDS = 5


# Inner-JSON semantic no-op signatures (P3 2026-07-15 FR-03 TDD-RED):
# A sub-agent may exit 0 yet return JSON whose `status` field indicates
# no real progress was made (e.g. agent waits for a human confirmation
# that never arrives, or decides nothing in scope applies). Without this
# check, spawn() reports "complete" and the outer workflow's per-FR slot
# is silently wasted — the per-FR RED→GREEN→IMPROVE chain never advances
# but appears healthy in sessions_spawn.log. This registry applies to
# every call site of spawn() regardless of step.
_INNER_NOOP_SIGNATURES: frozenset[str] = frozenset({
    "AWAITING_CONFIRMATION",
    "NOTHING_TO_DO",
})

# Round 26 — inner statuses meaning "I could not run; here is why", as opposed to
# the no-op statuses above ("I chose not to run"). `INFRA_BLOCKED` is what
# cli/fr_prompts/gate.py:66 ORDERS a Gate 1 evaluator to report when run-gate
# prints [BLOCKED] (SAB phantom / unregistered module — the dimension tools never
# ran). Until this set existed the word appeared in exactly one place in the
# codebase: the prompt asking for it. Nothing consumed it, so the report fell
# through to the commit-required branch below and its classification depended on
# whether the agent volunteered a `"pass": false` key.
# Incident and measurements: tests/test_infra_fail_separation.py's
# TestBlockedReportSurvivesToTheGuard.
_INNER_BLOCKED_SIGNATURES: frozenset[str] = frozenset({
    "INFRA_BLOCKED",
})

# Unattended-execution override, passed via --append-system-prompt on every
# spawn (Round 12 站0d). Live probe evidence (2026-07-16): --setting-sources
# "project" ALSO loads the user's global ~/.claude/CLAUDE.md, whose
# interactive-collaboration rules ("state assumptions and wait for user
# confirmation before acting", "ask one clarifying question when unsure")
# are behaviour-corrupting in a headless pipeline — production agents
# stalled awaiting approval (600s timeouts, "pytest/ruff and commit require
# approval" replies). The system-prompt layer outranks memory files, so
# this preamble neutralises those rules even if a future setting_sources
# value leaks them in again.
_UNATTENDED_PREAMBLE = (
    "UNATTENDED EXECUTION CONTEXT: this session is a headless pipeline "
    "sub-agent. There is no human present and none will ever reply. "
    "Ignore any instruction from memory/CLAUDE.md files that tells you to "
    "wait for user confirmation, ask clarifying questions, present a plan "
    "before acting, or address a human honorific. Execute the task "
    "directly, verify with the tools you are permitted to run, and return "
    "the requested output format. If a tool is blocked, record the exact "
    "denial message in your reply and continue — never stop to wait for "
    "approval."
)


# Steps that MUST produce a non-empty commit to be considered done.
# Centralised here so cli/fr_cmds.py's 5 inline commit-required lists
# stay in sync. LINT-FIX / CODE-FIX / COVERAGE-FIX do NOT appear
# because they only modify code for the next GATE round to commit.
_COMMIT_REQUIRED_STEPS: frozenset[str] = frozenset({
    "TDD-RED", "TDD-GREEN", "TDD-IMPROVE",
    "MIRROR", "AMEND-SAB", "ORCH-POST",
    "GATE1", "GATE1-DELTA",
})

# Fix H (2026-07-18): GATE1 / GATE1-DELTA are EVALUATION steps, not
# code-mutating ones — a real production bug (traced via sessions_spawn.log
# + GitHub anthropics/claude-code#37442 investigation) showed this original
# _COMMIT_REQUIRED_STEPS membership (added by e6f8b90, 2026-07-15, to catch
# a genuinely no-op TDD-RED) over-generalised: e6f8b90 explicitly reasoned
# LINT-FIX/CODE-FIX/COVERAGE-FIX out of the set because "they only modify
# code for the next GATE round to commit" — the same reasoning applies to
# GATE1/GATE1-DELTA's own dispatch, which only produces a commit when
# finalize-gate PASSES; a BLOCKED/FAIL verdict with commit=null is the
# correct, expected shape (confirmed against 130 historical
# "Gate 1 evaluator" sessions_spawn.log entries, many with pass=false,
# commit=null, status=complete — never previously misclassified as ERROR).
# Since e6f8b90 landed, every GATE1 round that did not pass on the first
# try got silently re-classified as a dispatch ERROR, routing it into a
# pointless CODE-FIX retry (no code defect exists to fix) instead of the
# normal FAIL-with-real-score path — this was the primary reason FR-01
# GATE1 stopped converging across many rounds.
_GATE_EVAL_STEPS: frozenset[str] = frozenset({"GATE1", "GATE1-DELTA"})


def is_structurally_broken(output: str) -> bool:
    """True when a dispatch failure is deterministic — retrying cannot succeed."""
    text = output or ""
    return any(sig in text for sig in _STRUCTURAL_FAILURE_SIGNATURES)


# CLI startup banner lines that appear on stderr of EVERY `claude -p`
# invocation under certain env conditions (successful spawns included).
# They are noise, not failure causes: on the current P3 run, 76/461
# sessions_spawn.log entries had error_output consisting of ONLY the
# connectors banner, hiding the real error entirely (Round 12 站0c).
_CLI_STARTUP_NOISE_RE = re.compile(
    r"^\s*⚠?\s*claude\.ai connectors are disabled.*$"
    r"|^\s*·?\s*Unset it to load your organization'?s connectors.*$"
    r"|^\s*Warning: no stdin data received in \d+s, proceeding without it.*$",
    re.MULTILINE,
)


def _denoise_cli_stderr(text: str) -> str:
    """Strip known startup-banner noise lines, preserving everything else."""
    if not text:
        return ""
    cleaned = _CLI_STARTUP_NOISE_RE.sub("", text)
    # collapse the blank lines the substitution leaves behind
    return re.sub(r"\n{2,}", "\n", cleaned).strip()


def _extract_dispatch_error(stdout: str, stderr: str) -> str:
    """Best real-cause extraction from a failed `claude -p` subprocess.

    Priority (Round 12 站0c — closes the 76× banner-only black hole):
    1. stdout parses as the CLI's result JSON → its `result` (and error
       subtype, if any) is the agent-visible failure text.
    2. denoised stderr (startup banners stripped) if anything remains.
    3. raw stdout tail; last resort raw stderr (so the entry is never
       empty when the subprocess produced ANY output).
    """
    stdout = stdout or ""
    stderr = stderr or ""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        parts = []
        subtype = data.get("subtype")
        if data.get("is_error") and subtype:
            parts.append(f"subtype={subtype}")
        result_text = data.get("result")
        if isinstance(result_text, str) and result_text.strip():
            parts.append(result_text.strip())
        if parts:
            return " ".join(parts)
    cleaned_stderr = _denoise_cli_stderr(stderr)
    if cleaned_stderr:
        return cleaned_stderr
    if stdout.strip():
        return stdout.strip()[-1000:]
    return stderr.strip()


def _extract_inner_result_json(text: str) -> dict:
    """Extract the sub-agent's own structured reply from its free-text final
    message (the ``claude -p --output-format json`` envelope's ``result``
    field — a plain string, NOT already a nested JSON object).

    Fix H-2 (2026-07-18): ``_validate_inner_json`` was reading ``status`` /
    ``commit`` / ``pass`` directly off the CLI envelope (``type`` / ``subtype``
    / ``result`` / ``session_id`` / ``usage`` — see the CLI's own
    ``--output-format json`` docs), which never carries those keys — the
    sub-agent's own JSON reply (the one every dispatch prompt's
    "[OUTPUT FORMAT] Return JSON: {...}" instructs it to produce) lives
    INSIDE the envelope's ``result`` string, often wrapped in a markdown
    code fence or preceded by prose/thinking. Confirmed via
    sessions_spawn.log: 130 historical "Gate 1 evaluator" entries recorded
    a status='complete' verdict before this envelope/inner-JSON confusion
    was introduced (100% of dispatches since have registered as ERROR
    instead, because the envelope literally never has these fields).
    ``fr_cmds.py``'s own ``_extract_review_json``/``_extract_agent_output_json``
    already solve this exact unwrapping problem for other call sites — this
    mirrors that scan-every-'{'-with-raw_decode approach so it works whether
    the reply is plain JSON, JSON in a code fence, or JSON after prose.
    Returns {} (not None) so callers can safely chain ``.get(...)`` on a
    missing/malformed reply — matching the "safe default" every other
    branch already assumed of the (mistakenly) envelope-only data.
    """
    text = text or ""
    decoder = json.JSONDecoder()
    last_match: dict | None = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "status" in obj:
            last_match = obj  # prefer the LAST match — the final verdict
            # wins over any JSON the agent may have quoted earlier (e.g. an
            # example from the prompt, or a prior round's result).
    return last_match or {}


def _validate_inner_json(data: dict, step: str | None) -> dict | None:
    """Re-classify an inner-JSON complete result as ERROR when it's a semantic no-op.

    Args:
        data: The parsed CLI envelope emitted by `claude -p --output-format
            json` — has `result` (the sub-agent's free-text reply, as a
            string), `session_id`, `usage`, etc. The sub-agent's OWN
            structured reply is extracted from `data["result"]` via
            `_extract_inner_result_json` before any field is read.
        step: The FR step passed via spawn()'s context={"step": ...}; may be None.
            Used to gate commit-presence check to known commit-required steps.

    Returns:
        None when the inner JSON represents legitimate progress (caller continues).
        An ERROR-shaped dict (with status="ERROR", error_class="EXECUTION_ERROR",
        plus a human-readable "output" describing why) when validation fails.
        The caller should treat this dict as if spawn() had returned it directly.

    The shape mirrors the proc.returncode != 0 branch in spawn() so fr_cmds.py's
    _DISPATCH_ERROR_STATUSES check (line 319) catches both transport and
    semantic failures uniformly.
    """
    raw_reply = data.get("result") or ""
    inner = _extract_inner_result_json(raw_reply)
    inner_status = (inner.get("status") or "").upper()
    if inner_status in _INNER_NOOP_SIGNATURES:
        summary = inner.get("summary", "") or raw_reply
        return _error_result(
            f"Sub-agent exited 0 with semantic no-op status "
            f"{inner_status!r}: {summary!r}",
            raw_reply,
            error_class="EXECUTION_ERROR",
            inner_status=inner_status,
        )
    # Round 26: checked BEFORE the commit-required branch. A reported blocker is
    # not a missing commit — there is nothing to commit when the tools never ran —
    # and its classification must not depend on whether the agent volunteered a
    # `"pass": false` key alongside it.
    if inner_status in _INNER_BLOCKED_SIGNATURES:
        return _error_result(
            f"Sub-agent reported inner status {inner_status!r}: the step could not "
            f"run because a precondition failed. Verbatim sub-agent reply follows — "
            f"it carries the [BLOCKED] diagnostic the prompt asked it to quote.",
            raw_reply,
            error_class="INFRA",
            inner_status=inner_status,
        )
    if step and step in _COMMIT_REQUIRED_STEPS:
        commit = (inner.get("commit") or "").strip()
        # Fix H: a GATE1/GATE1-DELTA verdict of pass=false is a legitimate
        # BLOCKED/FAIL result — finalize-gate only commits on pass, so
        # commit=null is expected there, not a no-op. Only require the
        # commit when the sub-agent itself claims pass=true (a PASS
        # verdict with no commit IS suspicious — keep that check) or when
        # it never gave an explicit pass=false verdict at all (missing/
        # malformed output stays on the safe, stricter default).
        is_gate_blocked = step in _GATE_EVAL_STEPS and inner.get("pass") is False
        if not commit and not is_gate_blocked:
            return _error_result(
                f"Commit-required step {step!r} returned empty commit"
                f" (status={inner_status or '<unset>'!r})",
                raw_reply,
                error_class="EXECUTION_ERROR",
                inner_status=inner_status,
            )
    return None


def _error_result(
    diagnostic: str, raw_reply: str, *, error_class: str, inner_status: str
) -> dict:
    """Build the ERROR dict for a semantically-failed dispatch, evidence intact.

    Round 26 — `output` used to be the diagnostic ALONE, discarding the
    sub-agent's reply. Downstream safety nets string-match this exact field, so
    replacing it silently defeated them: cli/fr_cmds.py's
    `_classify_infra_or_harness_bug` scans for
    harness_bridge._INFRA_FAIL_EVIDENCE_SIGNATURES, which live only in the
    agent's verbatim [BLOCKED] quote. The diagnostic is therefore ADDITIVE —
    first, because humans and the core/failure_modes.py rules key off its
    phrasing, with the raw reply after it.
    """
    output = f"{diagnostic}\n\n{raw_reply}" if raw_reply else diagnostic
    return {
        "output": output,
        "status": "ERROR",
        "error_class": error_class,
        "inner_status": inner_status,
    }


# The `claude -p` result subtype for "I ran out of turns". One string, one home:
# core.failure_modes._is_dispatch_timeout reads it through turn_budget_exhausted()
# rather than repeating the literal, because Round 26 found the two classifiers
# over this output disagreeing — sessions_spawn.log recorded EXECUTION_ERROR while
# run-report's MAST layer read the same text as dispatch_timeout, and the one that
# DECIDES (agent_spawner -> cli/fr_cmds) was the one that could not tell.
_TURN_BUDGET_SIGNATURE = "error_max_turns"


def turn_budget_exhausted(output: str) -> bool:
    """True when the dispatch was cut off at its max-turns ceiling.

    Not a code defect and not an environment fault: the agent was working and the
    budget ended. taskq-plus P3 hit it three times (TDD-RED and TDD-GREEN at
    turn 41 against a ceiling of 40, CODE-FIX at 51 against 50) — 3 of 42
    dispatches, $5.30, 21% of that phase's spend — and each re-dispatch went out
    at the identical ceiling.
    """
    return _TURN_BUDGET_SIGNATURE in (output or "")


def blocked_inner_status_in(text: str) -> str | None:
    """The `_INNER_BLOCKED_SIGNATURES` member named in *text*, if any.

    One definition of "the sub-agent reported a precondition blocker", reachable
    from both directions: live dispatches, where `_validate_inner_json` reads the
    status out of the inner JSON; and entries already on disk, where
    `core/failure_modes._effective_error_class` re-derives the class from
    `error_output` alone. The corpus strips `error_class` on purpose so a registry
    fix reaches historical data — which is why Round 19's mis-filed INFRA_BLOCKED
    entry can be reclassified without rewriting the record.
    """
    if not text:
        return None
    for status in sorted(_INNER_BLOCKED_SIGNATURES):
        if status in text:
            return status
    return None


def _classify_dispatch_error(output: str) -> str:
    """Classify a non-zero `claude -p` failure for sessions_spawn.log observability.

    Returns "STRUCTURAL" when the output carries a known deterministic-breakage
    signature (see _STRUCTURAL_FAILURE_SIGNATURES — retrying can never succeed),
    "INFRA" when the sub-agent itself reported a precondition blocker
    (_INNER_BLOCKED_SIGNATURES — the tools never ran, so there is no quality
    verdict and no code to fix), "TURN_BUDGET" when it was cut off at its
    max-turns ceiling (the agent was working; the budget ended — see
    turn_budget_exhausted), "INFRA_ERROR" when it signals an environment /
    API / model / network problem (the model could not be reached or used), else
    "EXECUTION_ERROR". This lets a run of dispatch ERRORs be recognised as
    environmental instead of mis-diagnosed as a harness bug. Observability label
    only: the entry's `status` stays "ERROR", so the spawner's own control flow is
    unchanged — abort-vs-retry decisions belong to callers (cli/fr_cmds.py reads
    is_structurally_broken).
    """
    if is_structurally_broken(output):
        return "STRUCTURAL"
    if blocked_inner_status_in(output):
        return "INFRA"
    if turn_budget_exhausted(output):
        return "TURN_BUDGET"
    return "INFRA_ERROR" if output and _INFRA_ERROR_RE.search(output) else "EXECUTION_ERROR"


def _child_env() -> dict[str, str]:
    """os.environ copy safe to pass to a spawned `claude -p` subprocess."""
    env = os.environ.copy()
    for key in _SDK_STREAM_MARKERS:
        env.pop(key, None)
    # [env-check] Bug #129/#128/#123 class root cause: orchestrated runs
    # inherit the shell env which may not have .venv/bin in PATH (no
    # VIRTUAL_ENV export). Sub-agent then sees system python (no pytest-cov)
    # and nondeterministically fabricates claims. Inject .venv/bin into PATH
    # so sub-agent sees the project's actual toolchain.
    _project = Path.cwd()
    for _vd in (".venv", "venv"):
        _bindir = _project / _vd / ("Scripts" if os.name == "nt" else "bin")
        if _bindir.is_dir():
            _path = env.get("PATH", "")
            env["PATH"] = str(_bindir) + os.pathsep + _path if _path else str(_bindir)
            env.setdefault("VIRTUAL_ENV", str(_project / _vd))
            break
    return env


def _load_persona(role: str) -> str:
    """Load the persona markdown file for a given role."""
    p = Path("agent_personas") / f"{role.upper()}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_phase_sop(phase: int) -> str:
    """Load the Standard Operating Procedure (SOP) for a specific phase."""
    p = Path("docs") / f"P{phase}_SOP.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# Round 14 站0: fields already present in the `claude -p --output-format
# json` envelope (confirmed live 2026-07-17 against installed claude 2.1.206)
# that were parsed then discarded — only `result`/`session_id`/`commit` were
# ever read. `duration_ms` is deliberately excluded: it duplicates the
# wallclock already measured independently via time.monotonic() (Fix H-F's
# duration_seconds).
_ENVELOPE_TOP_KEYS = ("total_cost_usd", "num_turns", "duration_api_ms")
_ENVELOPE_USAGE_KEYS = (
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)


def _extract_envelope_metrics(data: dict) -> dict[str, Any]:
    """Pull cost/turn/token fields out of a parsed claude -p envelope.

    Defensive against CLI version drift: only keys actually present are
    included, so an older/newer envelope shape never crashes this and never
    pads sessions_spawn.log entries with null-noise.
    """
    metrics: dict[str, Any] = {k: data[k] for k in _ENVELOPE_TOP_KEYS if k in data}
    usage = data.get("usage")
    if isinstance(usage, dict):
        usage_metrics = {k: usage[k] for k in _ENVELOPE_USAGE_KEYS if k in usage}
        if usage_metrics:
            metrics["usage"] = usage_metrics
    return metrics


def _envelope_metrics_from_stdout(stdout: str) -> dict[str, Any]:
    """Envelope metrics off a raw stdout string, or {} if there is no envelope.

    Round 19 站2. spawn()'s failure path already proves this stdout parses:
    _extract_dispatch_error json.loads() it to lift `subtype` and `result` (a
    taskq error_output reading "subtype=success API Error: Stream idle timeout"
    is that code path's own output). The cost and token counts sat in the same
    dict and were simply never read, so a failed dispatch logged no cost at all
    — 2 of taskq's 19 failures carried one, against 50 of 50 successes, while
    those failures burned 1.30h of wall clock. Every run-report cost figure was
    therefore the cost of the happy path.

    Best-effort by contract: non-JSON stdout, a JSON non-object, or an envelope
    without these keys all yield {}. It must never raise — an observability
    field is not worth converting a dispatch failure into a harness crash.
    """
    try:
        data = json.loads(stdout or "")
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _extract_envelope_metrics(data)


class AgentSpawner:
    """
    Spawns developer and reviewer agents via the Claude Code headless CLI.

    All agents use Claude sub-agent for review — no MCP backend configuration required.
    Only the claude CLI must be installed.
    """

    def __init__(self, project_path: Optional[Path] = None):
        self.project_path = Path(project_path) if project_path else None

    def spawn(
        self,
        role: str,
        prompt: str,
        context: dict,
        model: str = "claude",
        task_timeout: int = 300,
        max_turns: int = 20,
        phase: int = 0,
        fr_id: str | None = None,
        phase_sop_override: str | None = None,
        persona_override: str | None = None,
        mcp_config: str | None = None,
        setting_sources: str = "",
        permission_mode: str = "acceptEdits",
        retry_round: int | None = None,
    ) -> dict:
        """
        Spawn an agent with a specific role and prompt.

        Args:
            role: The agent's persona role (e.g., 'developer', 'reviewer').
            prompt: The specific task description.
            context: Additional metadata and state.
            model: Backend model (always 'claude' — only Claude CLI supported).
            task_timeout: Max execution time in seconds.
            max_turns: Max tool-using turns (default 20).
            phase: Current methodology phase.
            fr_id: Optional Functional Requirement ID.
            mcp_config: Path to .mcp.json (relative to project_path), inline
                JSON string, or None. None = empty MCP (current default).
                File not found → stderr warning + fallback to empty MCP.
            setting_sources: Passed to --setting-sources. Measured semantics
                (probed live 2026-07-16, Round 12 站0d — do NOT trust the
                flag name): "" → NO CLAUDE.md memory at all; "user" → user
                CLAUDE.md loads; "project" → project CLAUDE.md AND the
                user's global CLAUDE.md BOTH load (this leak shipped the
                user's interactive-collaboration protocol into headless
                agents, which then stalled awaiting a human confirmation);
                "local" → no user CLAUDE.md. Use "" for isolation.
            permission_mode: Claude Code --permission-mode. "acceptEdits"
                (default) auto-approves file edits, blocks Bash. "bypassPermissions"
                auto-approves all tools including shell commands.

        Returns:
            A dictionary containing the agent's output and status.
        """
        full_prompt = self._build_prompt(role, prompt, context, phase,
                                         phase_sop_override=phase_sop_override,
                                         persona_override=persona_override)

        # Claude Code headless CLI (replaces deprecated claude_code_sdk.Task).
        # Sub-agent isolation (need-to-know): by default, blocks CLAUDE.md,
        # hooks, skills, and MCP. Callers can opt into MCP tools and
        # project-level CLAUDE.md via mcp_config / setting_sources params.
        # OAuth auth works (unlike --bare which forces API key).
        # The spawned agent sees what _build_prompt() packs into the prompt
        # (persona + SOP + task + context) plus any MCP tools granted.
        cli = shutil.which("claude")
        if not cli:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code: "
                "https://code.claude.com/docs/en/installation"
            )
        # Resolve MCP config
        if mcp_config is None:
            mcp_args = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        elif mcp_config.strip().startswith("{"):
            mcp_args = ["--strict-mcp-config", "--mcp-config", mcp_config]
        else:
            resolved = (
                str((self.project_path / mcp_config).resolve())
                if self.project_path else mcp_config
            )
            if not Path(resolved).exists():
                import sys
                print(
                    f"[AgentSpawner] WARNING: MCP config '{resolved}'"
                    f" not found — falling back to isolated mode",
                    file=sys.stderr,
                )
                mcp_args = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            else:
                mcp_args = ["--strict-mcp-config", "--mcp-config", resolved]
        cmd = [
            cli, "-p", full_prompt,
            "--output-format", "json",
            "--setting-sources", setting_sources,
            "--disable-slash-commands",
            *mcp_args,
            "--max-turns", str(max_turns),
            "--permission-mode", permission_mode,
            # Fix G (2026-07-18): --permission-mode alone is not reliable for
            # a headless spawn — anthropics/claude-code#37442 ("Subagents
            # don't inherit bypassPermissions mode from parent session",
            # closed Not Planned) documents that nested spawns can still hit
            # a tool-approval prompt even under bypassPermissions, and there
            # is no human to answer it (Round 12 站0b already caught the
            # symptom of this: preflight_substrate()'s docstring records the
            # 2026-07-16 P3 run stalling on exactly this — "pytest/ruff and
            # commit require approval" — but only added an early-warning
            # probe, not a fix). The Claude Code docs' own guidance for
            # headless mode is to pair permission-mode with --allowedTools
            # as the reliable belt-and-suspenders: "Other shell commands...
            # still need an --allowedTools entry or a permissions.allow
            # rule, otherwise the run aborts when one is attempted."  This
            # does not widen what a bypassPermissions-dispatched sub-agent
            # is already trusted to do — it only makes that trust reliably
            # honored.
            "--allowedTools", "Bash,Read,Edit,Write",
            "--no-session-persistence",
            # Round 12 站0d: every spawn is unattended by definition. The
            # system-prompt layer outranks any CLAUDE.md memory that leaks
            # in via setting_sources (measured: "project" also loads the
            # USER's global CLAUDE.md — interactive rules like "wait for
            # user confirmation before acting" deadlocked headless agents
            # on the 2026-07-16 P3 run).
            "--append-system-prompt", _UNATTENDED_PREAMBLE,
        ]

        # Sub-agent regression guard: snapshot pre-spawn diff so we can
        # compute (post - pre) net changes after the agent finishes.
        # Capture HEAD SHA now so post-spawn diff is measured against the
        # same fixed point even if the agent commits (git diff HEAD would
        # move with the agent's commits, making them invisible).
        pre_sha = self._git_head_sha(self.project_path)
        pre_diff = self._git_diff_numstat(self.project_path)

        # P3 2026-07-15 (Fix H-F): wallclock duration measured around the
        # subprocess.run() so sessions_spawn.log can surface "how long each
        # spawn actually took". Previously reconstruction required
        # timestamp-diffing consecutive records — fragile when timestamps
        # coincidentally overlap (multi-process parallel writers).
        #
        # Fix H-G (P3 2026-07-15 round 4): subprocess.run is wrapped in a
        # bounded retry loop for the STRUCTURAL failure class only (see
        # _STRUCTURAL_RETRY_ATTEMPTS docstring above) — production evidence
        # showed this transient signature self-resolves on retry within the
        # same run/env most of the time. Every attempt is logged
        # individually (dispatch_attempt) so sessions_spawn.log keeps its
        # "one line per subprocess.run" observability granularity. Any
        # other error class, or a REGRESSION_GUARD upgrade, breaks
        # immediately without retrying — unchanged from pre-H-G behavior.
        error_result: dict[str, Any] | None = None
        proc: subprocess.CompletedProcess[str] | None = None
        _spawn_duration: float = 0.0
        _attempt: int = 1
        # Round 14 站0: set once the envelope JSON actually parses (below),
        # covers both the semantic-no-op (_inner_err) and the normal-success
        # branches — a wasted turn still incurred real cost/tokens. Stays
        # None for transport failures (timeout/non-zero exit/non-JSON
        # stdout), where no envelope was ever produced.
        _envelope: dict[str, Any] | None = None
        for _attempt in range(1, _STRUCTURAL_RETRY_ATTEMPTS + 1):
            _spawn_started_at = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=task_timeout,
                    cwd=str(self.project_path.resolve()) if self.project_path else None,
                    env=_child_env(),
                )
            except subprocess.TimeoutExpired:
                _spawn_duration = time.monotonic() - _spawn_started_at
                timeout_result: dict[str, Any] = {
                    "output": f"Agent timed out after {task_timeout}s",
                    "status": "TIMEOUT",
                }
                post_diff = self._git_diff_numstat(self.project_path, base=pre_sha or "HEAD")
                regression_flags = self._dispatch_diff_budget(pre_diff, post_diff, pre_sha=pre_sha)
                self._log_dispatch(
                    role, prompt, timeout_result, phase, fr_id,
                    regression_flags=regression_flags,
                    duration_seconds=round(_spawn_duration, 3),
                    retry_round=retry_round,
                    dispatch_attempt=_attempt,
                )
                if regression_flags:
                    timeout_result = {**timeout_result, "status": "REGRESSION_GUARD", "regression_flags": regression_flags}
                return timeout_result
            _spawn_duration = time.monotonic() - _spawn_started_at
            if proc.returncode == 0:
                error_result = None
                break
            # Round 12 站0c: extract the real cause (result-JSON first,
            # denoised stderr second) instead of raw `stderr or stdout` —
            # the CLI startup banner used to fill the first 500 chars and
            # bury the actual error for 76/461 production entries.
            _err_output = _extract_dispatch_error(proc.stdout, proc.stderr)
            error_result = {
                "output": _err_output,
                "status": "ERROR",
                "exit_code": proc.returncode,
                "error_class": _classify_dispatch_error(_err_output or ""),
            }
            post_diff = self._git_diff_numstat(self.project_path, base=pre_sha or "HEAD")
            regression_flags = self._dispatch_diff_budget(pre_diff, post_diff, pre_sha=pre_sha)
            self._log_dispatch(
                role, prompt, error_result, phase, fr_id,
                regression_flags=regression_flags,
                duration_seconds=round(_spawn_duration, 3),
                retry_round=retry_round,
                dispatch_attempt=_attempt,
                envelope=_envelope_metrics_from_stdout(proc.stdout),
            )
            if regression_flags:
                error_result = {**error_result, "status": "REGRESSION_GUARD", "regression_flags": regression_flags}
                break  # hard reject — not a transient signature, never retry
            if (error_result["error_class"] != "STRUCTURAL"
                    or _attempt == _STRUCTURAL_RETRY_ATTEMPTS):
                break
            time.sleep(_STRUCTURAL_RETRY_BACKOFF_SECONDS)
        if error_result is not None:
            return error_result
        assert proc is not None  # loop always runs >=1 time; error_result is None only after a successful proc
        try:
            data = json.loads(proc.stdout)
            # Round 14 站0: the envelope is real the moment transport JSON
            # parses — even a semantic no-op below incurred real cost/tokens.
            _envelope = _extract_envelope_metrics(data)
            # Inner-JSON semantic validator (P3 2026-07-15 FR-03 FR-03 TDD-RED):
            # A sub-agent may exit 0 with no real progress (e.g.
            # {"status":"AWAITING_CONFIRMATION","commit":""}). Without this
            # re-classification, spawn() reports complete and silently wastes
            # the per-FR slot. See _validate_inner_json() for the rule set.
            _inner_err = _validate_inner_json(data, context.get("step"))
            if _inner_err is not None:
                # Mirror the ERROR shape produced by proc.returncode != 0
                # so callers see a consistent dict regardless of failure mode.
                _inner_err["exit_code"] = 0  # transport success, semantic fail
                post_diff = self._git_diff_numstat(self.project_path, base=pre_sha or "HEAD")
                regression_flags = self._dispatch_diff_budget(pre_diff, post_diff, pre_sha=pre_sha)
                self._log_dispatch(
                    role, prompt, _inner_err, phase, fr_id,
                    regression_flags=regression_flags,
                    duration_seconds=round(_spawn_duration, 3),
                    retry_round=retry_round,
                    dispatch_attempt=_attempt,
                    envelope=_envelope,
                )
                return _inner_err
            result = {
                "output": data.get("result", ""),
                "status": "complete",
                "session_id": data.get("session_id", ""),
                # Fix H-2: the sub-agent's own "commit" field lives inside
                # data["result"] (its free-text reply), not on the envelope
                # itself — see _extract_inner_result_json's docstring.
                "commit": (
                    _extract_inner_result_json(data.get("result") or "")
                    .get("commit") or ""
                ).strip(),
            }
        except (json.JSONDecodeError, AttributeError):
            import sys
            sys.stderr.write(
                f"[AgentSpawner] claude -p returned non-JSON stdout "
                f"(stderr={proc.stderr[:200]!r})\n"
            )
            result = {
                "output": proc.stdout,
                "status": "complete",
            }

        parsed = self._parse_result(result)
        # Sub-agent regression guard: capture post-spawn diff and emit
        # regression flags if the agent made suspicious destructive edits.
        # Use pre_sha as base so the diff covers both committed and
        # uncommitted changes the agent made (HEAD may have moved).
        post_diff = self._git_diff_numstat(self.project_path, base=pre_sha or "HEAD")
        regression_flags = self._dispatch_diff_budget(pre_diff, post_diff, pre_sha=pre_sha)
        # If the guard fired, surface in the parsed result so the caller
        # can treat it as ERROR/REJECT status (Bug #28/#32/#38/#39 pattern).
        if regression_flags and parsed.get("status") == "complete":
            parsed = {**parsed, "status": "REGRESSION_GUARD", "regression_flags": regression_flags}
        self._log_dispatch(
            role, prompt, parsed, phase, fr_id,
            regression_flags=regression_flags,
            duration_seconds=round(_spawn_duration, 3),
            retry_round=retry_round,
            dispatch_attempt=_attempt,
            envelope=_envelope,
        )
        return parsed

    def preflight_substrate(
        self,
        *,
        phase: int = 0,
        mcp_config: str | None = None,
        setting_sources: str = "",
        permission_mode: str = "acceptEdits",
        task_timeout: int = 90,
    ) -> dict:
        """One cheap probe spawn proving the substrate can actually work
        BEFORE a per-FR pipeline burns hours discovering it cannot.

        Round 12 站0b. Production evidence (2026-07-16 P3 run): agents were
        dispatched into an environment where the Bash tool was permission-
        blocked — they stalled awaiting an approval that never comes in a
        headless session (600s timeouts, "Commit-required step returned
        empty commit" ×62, fixers replying "pytest/ruff and commit require
        approval"). A 60-second probe at run-phase entry would have
        surfaced that in one dispatch instead of 140.

        The probe MUST be spawned with the same mcp_config /
        setting_sources / permission_mode the real pipeline dispatches
        use — it is measuring THAT substrate, not an idealized one.

        Detection is marker-based on the agent's echoed command output:
        the canary marker is assembled at probe runtime by python3 itself
        (the prompt only ever contains the two halves), so a stalled or
        hallucinating agent that merely echoes the prompt back cannot
        produce it. This raises the bar; it does not make hallucination
        impossible (same acknowledged trade-off as verify_gate1_qc.py).

        Returns:
            dict with keys ok / pytest_ok / git_ok / canary_ok /
            permission_mode / setting_sources / detail (agent output tail).
        """
        proj = str(self.project_path.resolve()) if self.project_path else "."
        canary_head, canary_tail = "PREFLIGHT_SUBSTRATE_", "CANARY_OK"
        prompt = (
            "You are an unattended environment probe in a headless pipeline. "
            "There is NO human: never ask for permission or confirmation.\n"
            "Run these THREE commands via the Bash tool, in order, and paste "
            "each command's complete output verbatim in your reply. If a "
            "command is blocked or denied, paste the exact denial message "
            "instead and continue with the next command.\n"
            "1. `python3 -m pytest --version`\n"
            f"2. `git -C {proj} commit -m 'preflight-probe' --dry-run` "
            "(exit code 0 or 1 are both fine — this only tests that git "
            "executes; --dry-run commits nothing)\n"
            f"3. `python3 -c 'print(\"{canary_head}\" + \"{canary_tail}\")'`\n"
            "Reply with plain text only: the three outputs, nothing else."
        )
        result = self.spawn(
            role="preflight-probe",
            prompt=prompt,
            context={"phase": phase, "step": "PREFLIGHT"},
            phase=phase,
            task_timeout=task_timeout,
            max_turns=6,
            persona_override="",
            phase_sop_override="",
            mcp_config=mcp_config,
            setting_sources=setting_sources,
            permission_mode=permission_mode,
        )
        output = str(result.get("output") or "")
        pytest_ok = bool(re.search(r"pytest\s+\d+\.\d+", output))
        # `git commit --dry-run` prints repo-status text on every outcome;
        # any of these markers proves git executed (vs. a permission wall).
        git_ok = any(m in output for m in (
            "On branch", "nothing to commit", "no changes added",
            "nothing added to commit", "Changes to be committed",
            "Changes not staged", "Untracked files", "HEAD detached",
        ))
        canary_ok = (canary_head + canary_tail) in output
        ok = (result.get("status") == "complete"
              and pytest_ok and git_ok and canary_ok)
        summary = {
            "output": (
                f"pytest_ok={pytest_ok} git_ok={git_ok} canary_ok={canary_ok} "
                f"permission_mode={permission_mode} "
                f"setting_sources={setting_sources!r} | {output[-300:]}"
            ),
            "status": "PREFLIGHT_OK" if ok else "PREFLIGHT_FAIL",
            "exit_code": result.get("exit_code"),
        }
        self._log_dispatch("preflight-probe", "substrate preflight probe",
                           summary, phase, None)
        return {
            "ok": ok,
            "pytest_ok": pytest_ok,
            "git_ok": git_ok,
            "canary_ok": canary_ok,
            "permission_mode": permission_mode,
            "setting_sources": setting_sources,
            "status": result.get("status"),
            "detail": output[-1500:],
        }

    def _log_dispatch(self, role: str, task: str, result: dict,
                      phase: int, fr_id: str | None,
                      regression_flags: dict | None = None,
                      duration_seconds: float | None = None,
                      retry_round: int | None = None,
                      dispatch_attempt: int | None = None,
                      envelope: dict[str, Any] | None = None) -> None:
        """Auto-record agent dispatch to .methodology/sessions_spawn.log as a
        non-blocking debug trail. (The HR-10 entry-count audit that consumed this
        log was removed — it was agent-writable / not tamper-evident. This stays as
        a dispatch trace for debugging; nothing gates on it.)

        ERROR observability (SESSIONS_SPAWN-OBSERVABILITY):
        Always writes `error_output` (truncated stderr/stdout, ~500 chars) and
        `exit_code` (subprocess returncode) into the log entry. Previously these
        fields were dropped, so ERROR sessions only showed status="ERROR" +
        session_id="" with no clue why the spawn failed — and the workflow's
        retry-on-next-step covered it up. Surfacing stderr lets future ERROR
        rounds debug the real cause instead of treating the symptom.
        """
        if not self.project_path:
            return
        try:
            from core.sessions_spawn_logger import SessionsSpawnLogger
            logger = SessionsSpawnLogger(self.project_path)
            session_id = result.get("session_id", "")
            # error_class ("INFRA_ERROR"/"EXECUTION_ERROR") only on failed
            # dispatches; omit it from complete/SPAWNED entries to avoid noise.
            _extra: dict[str, Any] = {}
            if result.get("error_class"):
                _extra["error_class"] = result["error_class"]
            # Fix H-F (2026-07-15): duration_seconds + retry_round are
            # structured observability added so log readers don't need to
            # timestamp-diff consecutive entries to reconstruct per-spawn
            # timing or to identify which fix-loop iteration produced a
            # given entry. Both are optional — unset means "not measured"
            # / "not in a fix loop" — and the logger skips them via its
            # normal kwargs path.
            if duration_seconds is not None:
                _extra["duration_seconds"] = round(duration_seconds, 3)
            if retry_round is not None:
                _extra["retry_round"] = retry_round
            # Fix H-G (2026-07-15 round 4): which subprocess.run attempt
            # (1-based) produced this entry, within the bounded STRUCTURAL
            # retry loop in spawn(). Distinct from retry_round (fix-loop
            # iteration, a different concept) — unset outside that loop.
            if dispatch_attempt is not None:
                _extra["dispatch_attempt"] = dispatch_attempt
            # Round 19 站1: _validate_inner_json puts `inner_status` on the
            # ERROR dict it returns, but this logger never wrote it out — so
            # core.failure_modes._is_semantic_noop, whose ONLY input is a log
            # entry's `inner_status`, could not match a single real entry in
            # 91 taskq records. The rule was not wrong; the signal it reads
            # never reached the log. Written only when present, so successful
            # dispatches stay unchanged.
            if result.get("inner_status"):
                _extra["inner_status"] = result["inner_status"]
            # Round 14 站0: cost/turns/token fields lifted straight out of
            # the claude -p envelope (see _extract_envelope_metrics) —
            # previously parsed then discarded. Only present when the
            # dispatch produced a real envelope.
            #
            # Round 19 站2 corrected the reach: "never on non-zero-exit" was
            # true of the code, not of the data. A non-zero exit very often
            # still writes a complete envelope to stdout — spawn()'s failure
            # branch now passes it through _envelope_metrics_from_stdout, so a
            # failed dispatch's cost stops being invisible. Genuinely absent
            # only on transport TIMEOUT (no proc, hence no stdout) and on
            # non-JSON stdout.
            if envelope:
                _extra.update(envelope)
            logger.log_spawn(
                role=role, task=task[:200], session_id=session_id,
                status=result.get("status", "SPAWNED"),
                phase=phase, fr_id=fr_id,
                regression_flags=regression_flags or {},
                error_output=(result.get("output") or "")[:500],
                exit_code=result.get("exit_code"),
                **_extra,
            )
        except Exception as e:
            import sys
            sys.stderr.write(f"[AgentSpawner] log_dispatch failed: {e}\n")

    def _build_prompt(self, role: str, prompt: str, context: dict, phase: int,
                      phase_sop_override: str | None = None,
                      persona_override: str | None = None) -> str:
        """Construct the prompt following the need-to-know principle.

        Args:
            phase_sop_override: If None, load full phase SOP from docs/P{phase}_SOP.md.
                If provided (including ""), use this string instead — "" skips SOP entirely
                (used by run-fr-step where context is already self-contained in the prompt).
            persona_override: If None, load persona from agent_personas/{ROLE}.md.
                If provided (including ""), use this string instead — "" skips persona entirely
                (used for STATELESS Agent B reviewer dispatches that must return JSON directly).
        """
        persona = _load_persona(role) if persona_override is None else persona_override
        if phase_sop_override is None:
            sop = _load_phase_sop(context.get("phase", phase))
        else:
            sop = phase_sop_override  # "" → no SOP section added
        parts = []
        if persona:
            parts.append(f"[PERSONA]\n{persona}")
        if sop:
            parts.append(f"[SOP]\n{sop}")
        parts.append(f"[TASK]\n{prompt}")
        ctx_str = "\n".join(
            f"  {k}: {v}" for k, v in context.items() if k != "phase"
        )
        if ctx_str:
            parts.append(f"[CONTEXT]\n{ctx_str}")
        return "\n\n".join(parts)

    def _parse_result(self, result: Any) -> dict:
        """Parse the raw agent result into a standard format."""
        if isinstance(result, dict):
            return result
        return {"output": str(result), "status": "complete"}

    # Sub-agent regression guard (B1 follow-up to Bug #28/#32/#38/#39).
    # TDD-IMPROVE sub-agents made 4 distinct destructive edits in the
    # integration-test E2E (set enum values to None, change sys.exit
    # codes, inject XX...XX markers). The same sub-agent also rewrote
    # tests to match the broken implementation, so Agent B review didn't
    # catch it. To prevent recurrence we capture pre/post git diff
    # statistics on every dispatch and surface them in sessions_spawn.log.
    # A future post-dispatch budget check (Tier 2.1 in
    # HARNESS_IMPROVEMENT_PLAN.md) consumes these fields to fire
    # REGRESSION_GUARD status on suspicious diffs.
    @staticmethod
    def _git_head_sha(repo_root: Path | None) -> Optional[str]:
        """Return the current HEAD SHA, or None on failure."""
        if repo_root is None:
            return None
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_root),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    @staticmethod
    def _git_diff_numstat(
        repo_root: Path | None, base: str = "HEAD"
    ) -> dict[str, tuple[int, int]]:
        """Snapshot of `git diff --numstat <base>` at this moment.

        Returns a dict mapping "<filename>" -> (lines_added, lines_removed)
        for every file that differs from *base* in the working tree (and
        any commits made after *base*). Empty dict on failure — callers
        must handle empty as "no information".

        Pass a SHA captured before a sub-agent runs as *base* so that
        commits made by the agent are included in the post-spawn snapshot
        (``git diff HEAD`` would track the moving HEAD and miss them).
        """
        if repo_root is None:
            return {}
        try:
            r = subprocess.run(
                ["git", "diff", "--numstat", base],
                capture_output=True, text=True, timeout=15,
                cwd=str(repo_root),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if r.returncode != 0:
            return {}
        out: dict[str, tuple[int, int]] = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added_s, removed_s, path = parts
            try:
                # `--numstat` outputs `-` `-` for binary files.
                added = -1 if added_s == "-" else int(added_s)
                removed = -1 if removed_s == "-" else int(removed_s)
            except ValueError:
                continue
            out[path] = (added, removed)
        return out

    def _dispatch_diff_budget(
        self, pre: dict, post: dict, pre_sha: Optional[str] = None
    ) -> dict:
        """Compute regression flags from pre/post dispatch diff snapshots.

        *pre_sha* is the HEAD SHA captured before the sub-agent ran.
        Pass it so the XX-marker check uses the same fixed base ref as
        *post* (committed XX markers are only visible via
        ``git diff <pre_sha>``, not ``git diff HEAD``).

        Returns a dict suitable for inclusion in sessions_spawn.log entry
        as the `regression_flags` field. Flags are warnings only; the
        authoritative gate is the explicit check in cmd_run_fr_step.
        """
        flags: dict[str, Any] = {}
        # Compute net change per file: (added, removed) under post
        # MINUS (added, removed) under pre. Then classify.
        suspect_lines_removed: list[tuple[str, int]] = []
        suspect_xx_markers: list[str] = []
        diff_base = pre_sha or "HEAD"
        all_paths = set(pre) | set(post)
        for path in all_paths:
            pre_a, pre_r = pre.get(path, (0, 0))
            post_a, post_r = post.get(path, (0, 0))
            net_removed = (post_r - pre_r)
            net_added = (post_a - pre_a)
            # Lines-removed > 30% of pre-existing line count → suspicious.
            # We don't have line counts without re-reading; use a softer
            # absolute threshold: 50+ lines removed in a single file in
            # a 30-min TDD step is almost certainly a destructive edit.
            if net_removed > 50:
                # AST-based refinement: ignore docstring/comment mass deletions in Python
                if path.endswith(".py"):
                    try:
                        logical_removed = self._calculate_logical_removal(path, pre_sha)
                        if logical_removed is not None and logical_removed <= 50:
                            continue  # Actual code logic removed is within safe limits
                    except Exception as exc:
                        print(f"[WARN] ghost-detection: logical-removal probe failed "
                              f"for {path}, treating as a suspect removal: {exc}")
                suspect_lines_removed.append((path, net_removed))
            # Look for XX...XX mutator markers left in source — this is
            # the exact pattern TDD-IMPROVE introduced in Bug #39.
            # Use diff_base (= pre_sha when available) so markers in
            # committed changes are also scanned.
            if path.endswith(".py") and net_added > 0:
                try:
                    r = subprocess.run(
                        ["git", "diff", diff_base, "--", path],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(self.project_path.resolve()) if self.project_path else ".",
                    )
                    if re.search(r"^\+.*XX[a-zA-Z_]+XX", r.stdout, re.MULTILINE):
                        suspect_xx_markers.append(path)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if suspect_lines_removed:
            flags["lines_removed>50"] = suspect_lines_removed[:5]
        if suspect_xx_markers:
            flags["xx_markers_introduced"] = suspect_xx_markers
        return flags

    def _calculate_logical_removal(self, path: str, pre_sha: Optional[str]) -> Optional[int]:
        """Calculate net removed lines ignoring comments and docstrings for Python."""
        import ast
        import subprocess

        def get_logical_lines(source: str) -> Optional[int]:
            if not source.strip():
                return 0
            try:
                parsed = ast.parse(source)
            except SyntaxError:
                # Syntactically invalid source (mid-edit) — we can't determine
                # logical line count reliably, so return None to skip exemption.
                return None

            class DocstringRemover(ast.NodeTransformer):
                def _remove_docstring(self, node):
                    self.generic_visit(node)
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                        node.body = node.body[1:]
                    return node
                def visit_Module(self, node): return self._remove_docstring(node)
                def visit_ClassDef(self, node): return self._remove_docstring(node)
                def visit_FunctionDef(self, node): return self._remove_docstring(node)
                def visit_AsyncFunctionDef(self, node): return self._remove_docstring(node)

            parsed = DocstringRemover().visit(parsed)
            try:
                return len(ast.unparse(parsed).splitlines())
            except Exception as exc:
                print(f"[WARN] get_logical_lines: ast.unparse failed: {exc}")
                return None

        if not self.project_path:
            return None

        try:
            diff_base = pre_sha or "HEAD"
            r_pre = subprocess.run(
                ["git", "show", f"{diff_base}:{path}"],
                capture_output=True, encoding="utf-8", errors="replace",
                cwd=str(self.project_path), timeout=10,
            )
            pre_source = r_pre.stdout if r_pre.returncode == 0 else ""
            if not pre_source:
                # If we couldn't read the original file, we can't reliably parse it.
                # Fall back to raw diff line counts.
                return None

            post_path = self.project_path / path
            post_source = post_path.read_text(encoding="utf-8", errors="replace") if post_path.exists() else ""

            pre_lines = get_logical_lines(pre_source)
            post_lines = get_logical_lines(post_source)
            if pre_lines is None or post_lines is None:
                return None
            return pre_lines - post_lines
        except Exception as exc:
            print(f"[WARN] logical-line diff probe failed for {path}: {exc}")
            return None
