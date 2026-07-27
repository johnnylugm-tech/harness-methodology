"""Round 24 — EnvCheckContext.evaluation_prompt()'s env-var CLASSIFICATION
RULE must recognize test/dev-only opt-in flags (env vars a project's own
docs describe as gating a test-only/development-only code path, explicitly
off/disabled/rejected by default in production) as optional_missing, not
required. Closes the gap that produced inconsistent required-vs-optional
classification of the same documented env var across two independent
Claude Code phase-workflow runs on the same, unchanged project state:
wf_8b3a3f79-12b (Phase 4) classified the var optional_missing (ready=true);
wf_4fe2125c-48d (Phase 5) classified the same var required + present:false
(ready=false), a false env-check FAIL.

Root cause: the prior rule only distinguished "has a documented default
value" (e.g. DATABASE_URL) vs "no default" — it had no bucket for "the
var is intentionally absent by design" opt-in flags, a distinct semantic
shape project docs commonly use for test/dev-only feature gates. Left
unclassified, the LLM had to guess which bucket to use, guessing
differently across runs.
"""

from harness.harness_bridge import EnvCheckContext


def _make_ctx() -> EnvCheckContext:
    return EnvCheckContext(
        project_root="/tmp/sim-project",
        phase=5,
        fr_id=None,
        ssi_schemas_dir="/tmp/sim-project/harness/ssi/schemas",
        work_dir="/tmp/sim-project/.sessi-work",
    )


def test_classification_rule_recognizes_test_dev_opt_in_flags_as_optional():
    prompt = _make_ctx().evaluation_prompt()
    assert "TEST/DEV-ONLY OPT-IN FLAG" in prompt, (
        "evaluation_prompt()'s CLASSIFICATION RULE must give test/dev-only "
        "opt-in flags (env vars a project's docs describe as off/disabled/"
        "rejected by default in production) their own optional_missing "
        "bucket — without it, the LLM has to guess between required and "
        "optional_missing for this doc-language shape, producing "
        "inconsistent env-check verdicts across separate runs of the same "
        "project state (observed: wf_8b3a3f79-12b vs wf_4fe2125c-48d)."
    )
    assert "off/disabled/rejected by default in production" in prompt


def test_classification_rule_still_covers_documented_default_case():
    """Regression guard: the new bucket is an addition, not a replacement
    of the existing documented-default rule."""
    prompt = _make_ctx().evaluation_prompt()
    assert "HAS documented default?" in prompt
    assert "optional_missing (name)" in prompt
