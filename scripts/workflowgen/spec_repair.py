"""`harness-repair` assembly — the workflow that fixes the framework itself.

Round 48 站4. The eight phase workflows and run-all all measure a PROJECT. This
one measures harness-methodology, and it is the only workflow whose subject is
the code doing the measuring — so every step it takes is arranged so that the
framework, not the agent, holds the verdict:

  reproduce   `repair-harness --check-repro` runs the ticket's command and
              reports its exit code. The agent transcribes the number.
  hypothesis  an A agent locates the defect and says what it thinks the cause
              is. Prose, deliberately unschematised — this is the one step
              whose output IS the work.
  challenge   a B agent argues against the hypothesis before any edit lands.
  fix         the A agent edits harness, under the four standing rules below.
  land        `repair-harness --land` stashes the fix, re-runs the
              reproduction (it must come back RED), restores the fix, checks
              the policy, runs the six-check self-gate, commits and pushes.
              The agent transcribes THAT exit code too.

The four rules are repeated in every prompt that can edit, because a repair
agent reading a workflow stack trace reaches for `.claude/workflows/*.js`
first — that is the file the trace names, and 883e9ca is what happens next.
They are also enforced by `repair-harness --land`, which is the only reason
the prompts can be short: the prompt asks, the CLI decides.

Entry: `Workflow({ scriptPath: '<repo>/harness/.claude/workflows/harness-repair.js',
args: { repo, ticket } })`, where `ticket` is the path record-block wrote.
"""
from __future__ import annotations

from . import js_blocks as B
from .spec_shared import _render_meta

_HEADER = """\
// harness-repair — fix a defect in harness-methodology itself
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/spec_repair.py. Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write
//
// Launched when `harness_cli.py record-block` classifies a halt as
// owner=harness (core/fault_owner.py). It is NOT launched for owner=unknown:
// "could not prove the project's fault, so go edit the framework" would hand a
// repair agent a standing motive to change the judge.
//
// Every verdict here comes from a harness CLI exit code, never from the
// agent's prose — same rule the eight phase workflows follow, and it matters
// more here because the agent is editing the thing that produces the verdict.
"""

_META_PHASES = [
    "Ticket", "Reproduce", "Root Cause", "Adversarial Review",
    "Fix", "Land", "Handover",
]

_DESCRIPTION = (
    "Repair a harness-methodology defect: reproduce, root-cause, "
    "adversarially review, fix, counter-prove, self-gate, push"
)

# Stated once, injected into every prompt that can write. Enforced by
# `repair-harness --land` regardless of whether the agent read them.
_RULES = (
    "  + 'STANDING RULES (the land step enforces all four; breaking one wastes the round):\\n'\n"
    "  + '  R1. NEVER edit .claude/workflows/*.js. They are generated. Edit the matching\\n'\n"
    "  + '      scripts/workflowgen/spec_*.py or js_blocks.py, then run\\n'\n"
    "  + '      `python3 scripts/workflowgen/generate_workflows.py --write`.\\n'\n"
    "  + '  R2. NEVER edit harness/gate_configs/*.yaml. Those are thresholds every\\n'\n"
    "  + '      enforcer shares, including CI. Lowering one is not a fix.\\n'\n"
    "  + '  R3. NEVER remove an entry from tests/REGRESSION_GUARDS.yaml. Guards only grow.\\n'\n"
    "  + '  R4. The fix must make the reproduction pass AND stay necessary: reverting it\\n'\n"
    "  + '      must turn the reproduction red again. A change that is not load-bearing\\n'\n"
    "  + '      did not fix anything.\\n'\n"
)

_TICKET_BLOCK = """\
// ---- ticket path (args.ticket) ----
// No default. A repair with no ticket has no reproduction command and no block
// signature, so it has nothing to verify and nothing to close.
if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
const TICKET = (args && typeof args === 'object' && typeof args.ticket === 'string') ? args.ticket : ''
if (!TICKET) {
  return { error: 'harness-repair: args.ticket is required (the path harness_cli.py record-block wrote)', note: 'Launch with Workflow({ scriptPath, args: { repo, ticket } }).' }
}
// The harness checkout being repaired. Submodule layout in every live project;
// the repo itself when harness is dogfooding on itself.
const HROOT = REPO + '/harness'
log('ticket = ' + TICKET)
"""


def _render_ticket() -> str:
    return (
        B.render_phase_header("Ticket")
        + "log('Read the repair ticket and confirm it names harness as the owner')\n"
        + "const ticketReport = await agent(\n"
        + "  'You MUST use the Bash tool. Run exactly:\\n'\n"
        + "  + '`cat ' + TICKET + '`\\n'\n"
        + "  + 'Then report via the StructuredOutput tool: pass = true ONLY if the JSON has an \\\"owner\\\" of \\\"harness\\\" AND a non-empty \\\"repro\\\" string; reason = the ticket\\u2019s signature, phase, step and repro command, transcribed verbatim.',\n"
        + "  { label: 'repair-ticket', phase: 'Ticket', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(ticketReport && ticketReport.pass === true)) {\n"
        + "  return { error: 'harness-repair: the ticket does not name harness as the owner, or carries no repro command', "
        + "reason: ticketReport ? String(ticketReport.reason ?? '').slice(-400) : 'agent returned null', "
        + "note: 'Only owner=harness blocks route here. A project-owned block belongs in the ordinary fix loop; an unknown-owner block stops and is recorded, not repaired.' }\n"
        + "}\n"
        + "log('  ticket: ' + String(ticketReport.reason ?? '').slice(0, 200))\n"
    )


def _render_reproduce() -> str:
    return (
        B.render_phase_header("Reproduce")
        + "log('Reality first: the reported failure must reproduce on this tree')\n"
        + "const reproCmd = PY + ' ' + HROOT + '/harness_cli.py repair-harness --project ' + REPO + ' --ticket ' + TICKET + ' --check-repro'\n"
        + "const reproRc = await agent(\n"
        + "  'You MUST use the Bash tool. Run exactly:\\n`' + reproCmd + '; echo RC=$?`\\n'\n"
        + "  + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC=. Do not interpret the output, do not fix anything, do not retry.',\n"
        + "  { label: 'repair-repro', phase: 'Reproduce', agentType: 'general-purpose', schema: RC_SCHEMA },\n"
        + ")\n"
        + "if (!(reproRc && reproRc.rc === 0)) {\n"
        + "  return { error: 'harness-repair: the reported failure did not reproduce', "
        + "rc: reproRc ? reproRc.rc : null, "
        + "note: 'A report is a claim; the reproduction is the evidence. Nothing in harness is edited on the strength of a report alone — re-check the ticket\\u2019s repro command, or close the block as not-reproducible.' }\n"
        + "}\n"
    )


def _render_root_cause() -> str:
    return (
        B.render_phase_header("Root Cause")
        + "log('Locate the defect in harness — hypothesis only, no edits yet')\n"
        + "const hypothesis = await agent(\n"
        + "  'YOU ARE THE HARNESS ROOT-CAUSE ANALYST. A block was attributed to harness-methodology itself.\\n'\n"
        + "  + 'HARNESS CHECKOUT: ' + HROOT + '\\nTICKET: ' + TICKET + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Read the ticket, then read the harness code the failing command runs. Reproduce it yourself if that helps you localise it.\\n'\n"
        + "  + 'Produce, in prose:\\n'\n"
        + "  + '  1. The exact file and line where harness does the wrong thing.\\n'\n"
        + "  + '  2. WHY it is wrong — the property that is violated, not the symptom.\\n'\n"
        + "  + '  3. The smallest change that would make it right, and which existing test would have caught it.\\n'\n"
        + "  + '  4. Whether the defect is in a GENERATED file (.claude/workflows/*.js). If so, name the scripts/workflowgen/ source that produces it — that is what gets edited.\\n\\n'\n"
        + "  + 'DO NOT EDIT ANYTHING in this step. A hypothesis that has not been challenged is not a diagnosis.\\n'\n"
        + _RULES
        + "  ,\n"
        + "  { label: 'repair-hypothesis', phase: 'Root Cause', agentType: 'general-purpose' },\n"
        + ")\n"
        + "const hypothesisText = String(hypothesis ?? '').slice(0, 6000)\n"
        + "if (!hypothesisText.trim()) {\n"
        + "  return { error: 'harness-repair: the root-cause step produced nothing', "
        + "note: 'No diagnosis means no fix. Re-launch, or escalate the ticket to a human.' }\n"
        + "}\n"
    )


def _render_review() -> str:
    return (
        B.render_phase_header("Adversarial Review")
        + "log('Challenge the hypothesis before any edit lands')\n"
        + "const review = await agent(\n"
        + "  'YOU ARE THE ADVERSARIAL REVIEWER. Another agent diagnosed a harness-methodology defect. Your job is to try to falsify the diagnosis, not to confirm it.\\n'\n"
        + "  + 'HARNESS CHECKOUT: ' + HROOT + '\\n\\nTHE DIAGNOSIS:\\n' + hypothesisText + '\\n\\n'\n"
        + "  + 'Check, by reading the code and running commands yourself:\\n'\n"
        + "  + '  - Is the named line actually reached by the failing command? If not, the diagnosis is wrong.\\n'\n"
        + "  + '  - Does the proposed change fix the CAUSE, or only silence the symptom the ticket reported?\\n'\n"
        + "  + '  - Would the change alter a verdict for projects that are NOT failing? Name them if so.\\n'\n"
        + "  + '  - Is the defect really in harness, or is the project at fault after all?\\n\\n'\n"
        + "  + 'Report via the StructuredOutput tool: pass = true ONLY if the diagnosis survives all four questions; reason = your findings, and when pass=false, what the diagnosis got wrong.',\n"
        + "  { label: 'repair-review', phase: 'Adversarial Review', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(review && review.pass === true)) {\n"
        + "  return { error: 'harness-repair: the diagnosis did not survive adversarial review', "
        + "reason: review ? String(review.reason ?? '').slice(-800) : 'reviewer returned null', "
        + "note: 'Stopping here is the cheap outcome. A rejected diagnosis costs one round; an unchallenged one that lands on main costs every project that tracks it.' }\n"
        + "}\n"
    )


def _render_fix_and_land() -> str:
    return (
        B.render_phase_header("Fix")
        # Both boxes are opened once, before the loop: the rounds re-enter the
        # same two boxes rather than opening a new pair each time, which is how
        # the progress view stays readable at 3 rounds.
        + "phase('Land')\nphase('Fix')\n"
        + "const MAX_REPAIR_ROUNDS = 3\n"
        + "let landed = false\n"
        + "let lastLand = null\n"
        + "for (let round = 1; round <= MAX_REPAIR_ROUNDS && !landed; round++) {\n"
        + "  log('repair round ' + round + '/' + MAX_REPAIR_ROUNDS)\n"
        + "  await agent(\n"
        + "    'YOU ARE THE HARNESS FIXER. Apply the reviewed diagnosis to the harness checkout.\\n'\n"
        + "    + 'HARNESS CHECKOUT: ' + HROOT + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'THE DIAGNOSIS (already survived adversarial review):\\n' + hypothesisText + '\\n\\n'\n"
        + "    + (lastLand ? 'THE PREVIOUS LAND ATTEMPT WAS REFUSED. Its output is below — read it as the fix instruction and address exactly what it names:\\n' + lastLand + '\\n\\n' : '')\n"
        + "    + 'Make the change. Add or extend a test that fails without it. Do not commit — the next step commits, and only if it can prove the fix is load-bearing and the self-gate is green.\\n\\n'\n"
        + _RULES
        + "    ,\n"
        + "    { label: 'repair-fix-r' + round, phase: 'Fix', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  const landCmd = PY + ' ' + HROOT + '/harness_cli.py repair-harness --project ' + REPO + ' --ticket ' + TICKET + ' --land --push'\n"
        + "  const landRc = await agent(\n"
        + "    'You MUST use the Bash tool. Run exactly:\\n`' + landCmd + ' 2>&1; echo RC=$?`\\n'\n"
        + "    + 'This command does the verifying: it stashes the fix, re-runs the reproduction (which must fail without the fix), restores it, checks the repair policy, runs the six-check self-gate, then commits and pushes.\\n'\n"
        + "    + 'Report via the StructuredOutput tool: rc = the EXACT integer after RC=. Do NOT edit anything in this step, do NOT retry, and do NOT interpret a nonzero rc as success.',\n"
        + "    { label: 'repair-land-r' + round, phase: 'Land', agentType: 'general-purpose', schema: RC_SCHEMA },\n"
        + "  )\n"
        + "  if (landRc && landRc.rc === 0) { landed = true; break }\n"
        + "  lastLand = 'rc=' + (landRc ? landRc.rc : 'null')\n"
        + "  log('  land refused (rc=' + (landRc ? landRc.rc : 'null') + ') — feeding the refusal back into the next round')\n"
        + "}\n"
        + "if (!landed) {\n"
        + "  return { error: 'harness-repair: the fix did not land in ' + MAX_REPAIR_ROUNDS + ' rounds', "
        + "last: lastLand, "
        + "note: 'Nothing was pushed. The harness checkout still holds the attempted fix — inspect it, or `git -C <repo>/harness checkout -- .` to discard. A human decides from here; the loop deliberately does not spend a fourth round on a refusal it has already failed to clear three times (Round 41 站3).' }\n"
        + "}\n"
    )


def _render_handover() -> str:
    return (
        B.render_phase_header("Handover")
        + "log('Fix pushed to harness-methodology main — the project must now move its submodule pointer')\n"
        + "return {\n"
        + "  workflow: 'harness-repair',\n"
        + "  repaired: true,\n"
        + "  ticket: TICKET,\n"
        + "  next: [\n"
        + "    'git -C ' + REPO + '/harness pull --ff-only origin main',\n"
        + "    'git -C ' + REPO + ' add harness && git -C ' + REPO + ' commit -m \"chore(harness): bump submodule past repair\"',\n"
        + "    'relaunch run-all — the block that stopped it is recorded as resolved, and run-all re-checks it rather than trusting this claim',\n"
        + "  ],\n"
        + "  note: 'The repair marked its block resolved in .methodology/workflow_blocks.jsonl. If the same coordinate blocks again on the next run, the fix did not hold — that is checked, not assumed.',\n"
        + "}\n"
    )


def generate_repair() -> str:
    from .generate_workflows import _inject_dispatch_wrapper, _wrap_top_level_boundary

    parts = [
        _HEADER,
        "",
        _render_meta(
            name="harness-repair",
            description=_DESCRIPTION,
            phases=_META_PHASES,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        "",
        _TICKET_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA"]),
        _render_ticket(),
        _render_reproduce(),
        _render_root_cause(),
        _render_review(),
        _render_fix_and_land(),
        _render_handover(),
    ]
    return _wrap_top_level_boundary(_inject_dispatch_wrapper("\n".join(parts)))
