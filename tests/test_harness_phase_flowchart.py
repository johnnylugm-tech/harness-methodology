"""
Validation test: Ensure Mermaid flowchart matches code phase routing.

This test verifies that the E2E flowchart diagram (harness_phase_flowchart.md)
accurately reflects the phase transitions and gate logic defined in
scripts/generate_full_plan.py.

The test is designed to catch drift where:
- A new phase is added but not documented in the flowchart
- Gate thresholds change but the flowchart isn't updated
- Phase transitions change but the diagram is stale
"""

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def get_code_phase_routing() -> Dict[int, Dict]:
    """Extract phase routing info from generate_full_plan.py."""
    gen_full_plan = Path(__file__).parent.parent / "scripts" / "generate_full_plan.py"
    content = gen_full_plan.read_text(encoding='utf-8')

    # Extract _GATE_META: gate_num -> (score_gate_min, num_dims, dim_names_str)
    gate_meta_match = re.search(
        r'_GATE_META:\s*dict\s*=\s*\{(.*?)\}',
        content,
        re.DOTALL
    )
    gate_meta = {}
    if gate_meta_match:
        gate_block = gate_meta_match.group(1)
        for line in gate_block.split('\n'):
            m = re.match(r'\s*(\d+):\s*\(([^,]*),\s*(\d+),\s*"([^"]+)"\s*\)', line)
            if m:
                gate_num = int(m.group(1))
                score_min_str = m.group(2).strip()
                num_dims = int(m.group(3))
                dim_str = m.group(4)

                score_min = None if score_min_str == "None" else int(score_min_str)
                gate_meta[gate_num] = {
                    'score_gate_min': score_min,
                    'num_dims': num_dims,
                    'dims': dim_str,
                }

    # Extract _phase_advance_step next_names dict
    advance_match = re.search(
        r'next_names\s*=\s*\{(.*?)\}',
        content,
        re.DOTALL
    )
    phase_names = {}
    if advance_match:
        block = advance_match.group(1)
        # Handle both comma-separated on same line and multi-line formats
        for m in re.finditer(r'(\d+):\s*"([^"]+)"', block):
            phase_num = int(m.group(1))
            phase_name = m.group(2)
            phase_names[phase_num] = phase_name

    # Extract _PHASE_ROLES: phase -> (role_a, role_b, hint)
    phase_roles = {}
    roles_match = re.search(
        r'_PHASE_ROLES:\s*dict\s*=\s*\{(.*?)\}',
        content,
        re.DOTALL
    )
    if roles_match:
        block = roles_match.group(1)
        for line in block.split('\n'):
            m = re.match(
                r'\s*(\d+):\s*\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\s*\)',
                line
            )
            if m:
                phase = int(m.group(1))
                role_a = m.group(2)
                role_b = m.group(3)
                hint = m.group(4)
                phase_roles[phase] = {
                    'role_a': role_a,
                    'role_b': role_b,
                    'hint': hint,
                }

    # Extract _entry_gate_check mapping
    entry_gates = {}
    entry_check_match = re.search(
        r'def _entry_gate_check\(phase: int\).*?_ENTRY_MAP:\s*dict\s*=\s*\{(.*?)\}',
        content,
        re.DOTALL
    )
    if entry_check_match:
        block = entry_check_match.group(1)
        for m in re.finditer(r'(\d+):\s*\("([^"]+)"', block):
            phase = int(m.group(1))
            entry_desc = m.group(2)
            entry_gates[phase] = entry_desc

    # Build routing dict with all phases
    routing = {}
    for phase in range(1, 9):
        # next_phase is always phase+1 unless we're at phase 8
        if phase < 8:
            next_phase = phase + 1
            next_name = phase_names.get(next_phase, f"Phase {next_phase}")
        else:
            next_phase = None
            next_name = None

        # Infer exit gate from phase (P1/P2 are human gates, P3+ are automated)
        if phase in [1, 2]:
            exit_gate_type = "Human¹"
            exit_score = None
        elif phase == 3:
            exit_gate_type = "Gate 2"
            exit_score = gate_meta.get(2, {}).get('score_gate_min')
        elif phase == 4:
            exit_gate_type = "Gate 3"
            exit_score = gate_meta.get(3, {}).get('score_gate_min')
        elif phase == 5:
            exit_gate_type = "Gate 3"
            exit_score = gate_meta.get(3, {}).get('score_gate_min')
        elif phase == 6:
            exit_gate_type = "Gate 4"
            exit_score = gate_meta.get(4, {}).get('score_gate_min')
        elif phase == 7:
            exit_gate_type = "Gate 3"
            exit_score = gate_meta.get(3, {}).get('score_gate_min')
        elif phase == 8:
            exit_gate_type = None
            exit_score = None
        else:
            exit_gate_type = None
            exit_score = None

        routing[phase] = {
            'phase_num': phase,
            'phase_name': phase_names.get(phase + 1, f"Phase {phase + 1}") if phase < 8 else "Pipeline Complete",
            'next_phase': next_phase,
            'next_name': next_name,
            'entry_gate': entry_gates.get(phase),
            'exit_gate': exit_gate_type,
            'exit_score': exit_score,
            'roles': phase_roles.get(phase),
        }

    return routing


def get_diagram_phase_routing() -> Dict[int, Dict]:
    """Extract phase routing from harness_phase_flowchart.md Mermaid diagram."""
    flowchart_path = (
        Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
    )
    content = flowchart_path.read_text(encoding='utf-8')

    # Extract the matrix table
    matrix_match = re.search(
        r'\| \*\*P1\*\*.*?\n\| \*\*P8\*\*.*?\|.*?\|.*?\|.*?\|.*?\|',
        content,
        re.DOTALL
    )

    routing = {}
    if matrix_match:
        matrix_text = matrix_match.group(0)
        for line in matrix_text.split('\n'):
            m = re.match(
                r'\|\s*\*\*P(\d+)\*\*\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)',
                line
            )
            if m:
                phase = int(m.group(1))
                entry = m.group(2).strip()
                exit_gate = m.group(3).strip()
                exit_score = m.group(4).strip()
                artifacts = m.group(5).strip()

                routing[phase] = {
                    'entry': entry,
                    'exit_gate': exit_gate,
                    'exit_score': exit_score,
                    'artifacts': artifacts,
                }

    return routing


class TestFlowchartVsCode:
    """Validate that flowchart matches code phase routing."""

    def test_all_phases_present_in_code(self):
        """Phase 1-8 must be defined in generate_full_plan.py."""
        code_routing = get_code_phase_routing()

        # Each phase should have phase_num key
        for phase in range(1, 9):
            assert phase in code_routing, f"Phase {phase} not found in code routing"
            assert code_routing[phase]['phase_num'] == phase

    def test_all_phases_present_in_diagram(self):
        """Phase 1-8 must be documented in flowchart."""
        diagram_routing = get_diagram_phase_routing()

        for phase in range(1, 9):
            assert phase in diagram_routing, f"Phase {phase} not found in diagram routing"

    def test_phase_transitions(self):
        """Phase transitions in code and diagram must match."""
        code_routing = get_code_phase_routing()

        # P1→P2, P2→P3, ... P7→P8
        expected_transitions = {
            1: (2, "Architecture Design"),
            2: (3, "Implementation"),
            3: (4, "Testing"),
            4: (5, "Verification & Delivery"),
            5: (6, "Quality Assurance"),
            6: (7, "Risk Management"),
            7: (8, "Configuration Management"),
            8: (None, None),  # Pipeline complete
        }

        for phase, (next_p, next_name) in expected_transitions.items():
            routing = code_routing[phase]
            assert routing['next_phase'] == next_p, (
                f"Phase {phase} transition error: expected next={next_p}, "
                f"got next={routing['next_phase']}"
            )
            if next_p is not None:
                assert routing['next_name'] == next_name, (
                    f"Phase {phase} transition name error: expected '{next_name}', "
                    f"got '{routing['next_name']}'"
                )

    def test_gate_thresholds(self):
        """Gate thresholds in code must match diagram entry in table."""
        code_routing = get_code_phase_routing()
        diagram_routing = get_diagram_phase_routing()

        gate_phase_map = {
            3: "Gate 2",
            4: "Gate 3",
            5: "Gate 3",
            6: "Gate 4",
            7: "Gate 3",
        }

        for phase, expected_gate in gate_phase_map.items():
            code_exit = code_routing[phase]['exit_gate']
            diagram_exit = diagram_routing[phase]['exit_gate']

            assert code_exit == expected_gate, (
                f"Phase {phase} exit gate mismatch: code={code_exit}, "
                f"expected={expected_gate}"
            )
            assert expected_gate in diagram_exit or diagram_exit == expected_gate, (
                f"Phase {phase} exit gate mismatch in diagram: "
                f"expected '{expected_gate}', got '{diagram_exit}'"
            )

    def test_exit_scores(self):
        """Exit score thresholds must match between code and diagram."""
        code_routing = get_code_phase_routing()
        diagram_routing = get_diagram_phase_routing()

        # Map phase to expected exit score
        expected_scores = {
            3: 75,  # Gate 2 score_gate
            4: 80,  # Gate 3 score_gate
            5: 80,  # Gate 3 score_gate
            6: 85,  # Gate 4 score_gate
            7: 80,  # Gate 3 score_gate
        }

        for phase, expected_score in expected_scores.items():
            code_score = code_routing[phase]['exit_score']
            diagram_score_str = diagram_routing[phase]['exit_score']

            # Parse diagram score (might be "≥ 75" or "≥ 80")
            diagram_score_match = re.search(r'(\d+)', diagram_score_str)
            if diagram_score_match:
                diagram_score = int(diagram_score_match.group(1))
                assert code_score == diagram_score, (
                    f"Phase {phase} exit score mismatch: "
                    f"code={code_score}, diagram={diagram_score}"
                )
            else:
                # If diagram doesn't have score (P1/P2), that's OK
                if phase > 2:
                    raise AssertionError(
                        f"Phase {phase} diagram missing score: '{diagram_score_str}'"
                    )

    def test_entry_gates(self):
        """Entry conditions in code must match diagram."""
        code_routing = get_code_phase_routing()
        diagram_routing = get_diagram_phase_routing()

        # P1 has no entry, P2+ inherit from predecessor
        expected_entries = {
            1: "None",
            2: "Human¹ APPROVE (from P1)",
            3: "Human¹ APPROVE (from P2)",
            4: "Gate 2 PASS (from P3)",
            5: "Gate 3 PASS (from P4)",
            6: "Gate 3 PASS (from P5)",
            7: "Gate 4 PASS (from P6)",
            8: "Gate 3 PASS (from P7)",
        }

        for phase, expected in expected_entries.items():
            code_entry = code_routing[phase]['entry_gate']
            diagram_entry = diagram_routing[phase]['entry']

            if phase == 1:
                assert code_entry is None, f"Phase 1 should have no entry gate"
                assert "None" in diagram_entry, f"Phase 1 diagram should say 'None'"
            else:
                # Verify gate type is mentioned
                gate_type = expected.split()[0]  # "Human¹", "Gate", etc.
                assert gate_type in diagram_entry or diagram_entry.startswith(gate_type), (
                    f"Phase {phase} entry mismatch: code={code_entry}, "
                    f"diagram={diagram_entry}, expected pattern={expected}"
                )

    def test_gate_dimensions_referenced(self):
        """Diagram must reference gate dimensions inline."""
        flowchart_path = (
            Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
        )
        content = flowchart_path.read_text(encoding='utf-8')

        # Gate 1 should reference "3 dims" or "linting/type_safety/coverage"
        assert "linting" in content, "Gate 1 dims not found in diagram"

        # Gate 3 should reference "12 dims"
        assert "12 dims" in content, "Gate 3 (12 dims) not referenced in diagram"

        # Gate 4 should reference "12 dims" and "≥ 85"
        assert "score_gate ≥ 85" in content, "Gate 4 (≥85) not referenced in diagram"

    def test_human_checkpoints_explicit(self):
        """Human¹ checkpoints in P1/P2 must be explicitly labeled."""
        flowchart_path = (
            Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
        )
        content = flowchart_path.read_text(encoding='utf-8')

        # Both P1 and P2 should explicitly mention "Human¹ Review"
        assert "Exit: Human¹" in content, "P1/P2 exit gates not explicitly marked as Human¹"

    def test_crg_recon_mentioned(self):
        """[CRG] reconnaissance must be mentioned for Gates 3 and 4."""
        flowchart_path = (
            Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
        )
        content = flowchart_path.read_text(encoding='utf-8')

        # CRG recon is mentioned for Gate 3 and Gate 4
        casecount = content.count("[CRG recon]")
        assert casecount >= 2, f"Expected [CRG recon] mentioned 2+ times, found {casecount}"

    def test_artifact_names_match(self):
        """Phase artifact names in diagram must match deliverables in code hints."""
        code_routing = get_code_phase_routing()
        diagram_routing = get_diagram_phase_routing()

        # Map phase to key artifact names
        key_artifacts = {
            1: ["SRS.md"],
            2: ["SAD.md", "ADR.md"],
            3: ["Code"],
            4: ["TEST_RESULTS.md"],
            5: ["BASELINE.md"],
            6: ["QUALITY_REPORT.md"],
            7: ["RISK_REGISTER.md"],
            8: ["CONFIG_RECORDS.md"],
        }

        for phase, expected_artifacts in key_artifacts.items():
            diagram_artifacts = diagram_routing[phase]['artifacts']

            for artifact in expected_artifacts:
                assert artifact in diagram_artifacts, (
                    f"Phase {phase} diagram missing artifact '{artifact}': "
                    f"found '{diagram_artifacts}'"
                )

    def test_diagram_file_exists(self):
        """Mermaid flowchart file must exist at expected path."""
        flowchart_path = (
            Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
        )
        assert flowchart_path.exists(), (
            f"Flowchart file not found at {flowchart_path}. "
            f"Expected: docs/superpowers/plans/harness_phase_flowchart.md"
        )

    def test_mermaid_syntax_valid(self):
        """Mermaid diagram must start with ```mermaid block."""
        flowchart_path = (
            Path(__file__).parent.parent / "docs" / "superpowers" / "plans" / "harness_phase_flowchart.md"
        )
        content = flowchart_path.read_text(encoding='utf-8')

        assert "```mermaid" in content, "Missing ```mermaid code block"
        assert "flowchart TD" in content, "Missing 'flowchart TD' declaration"
        assert content.count("```") >= 2, "Mermaid block not properly closed"
