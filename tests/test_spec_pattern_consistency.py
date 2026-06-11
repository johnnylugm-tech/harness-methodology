"""v2.9 B1 — NP-pattern table consistency between protocol and template.

The tts-new root cause for missed concurrency cases was TABLE DRIFT: the
project's TEST_SPEC carried an 8-pattern copy while derive_test_cases.md
defined 15, so NP-13 (concurrency) could never activate. These tests pin the
protocol's pattern set and force the template to enumerate every one of them.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DERIVE = (REPO_ROOT / "harness" / "ssi" / "prompts" /
          "derive_test_cases.md").read_text(encoding="utf-8")
TEMPLATE = (REPO_ROOT / "templates" / "TEST_SPEC.md").read_text(encoding="utf-8")


def _patterns(text: str) -> set:
    return set(re.findall(r"\bNP-(\d{2})\b", text))


class TestPatternTableConsistency:
    def test_protocol_defines_15_patterns(self):
        step1 = DERIVE.split("## Step 1:")[1].split("## Step 1b:")[0]
        assert _patterns(step1) == {f"{i:02d}" for i in range(1, 16)}

    def test_template_enumerates_every_protocol_pattern(self):
        """The template activation table must list ALL patterns — a shortened
        copy silently disables the missing ones for the whole project."""
        # Split on the standalone horizontal rule, not "---" (which would hit
        # the markdown table divider |----|).
        section = TEMPLATE.split("## NFR Pattern Activation")[1].split("\n---\n")[0]
        assert _patterns(section) >= _patterns(
            DERIVE.split("## Step 1:")[1].split("## Step 1b:")[0]
        )

    def test_step_1b_exists_with_architecture_triggers(self):
        assert "## Step 1b: Architecture-Risk Triggers" in DERIVE
        step1b = DERIVE.split("## Step 1b:")[1].split("## Step 2:")[0]
        # The four risk traits and their forced patterns
        for trait, pattern in [
            ("shared mutable state", "NP-13"),
            ("external process", "NP-15"),
            ("network client", "NP-07"),
            ("cache", "NP-07"),
        ]:
            assert trait in step1b, f"missing risk trait: {trait}"
            assert pattern in step1b
        # Spec-side enforcement contract
        assert "tests/integration/" in step1b
        assert "REJECT" in step1b

    def test_q6_forbids_skipping_forced_patterns(self):
        q6 = DERIVE.split("### Q6:")[1].split("### Q7:")[0]
        assert "may NOT be skipped" in q6
        assert "Q6/1b/NP-{ID}" in q6

    def test_agent_b_checklist_has_architecture_risk_item(self):
        assert "Architecture-risk coverage (Step 1b)" in DERIVE

    def test_template_records_trigger_source(self):
        section = TEMPLATE.split("## NFR Pattern Activation")[1].split("\n---\n")[0]
        assert "SAD:" in section and "SRS:" in section
